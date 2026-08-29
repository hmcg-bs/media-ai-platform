"""Background + product element (Generation v1).

Round 6 (2026-08-29) rewrite: switched from Flux Kontext Pro's whole-image
edit to Flux Fill Pro's masked inpainting. Flux Kontext re-renders the
*entire* frame on every call, product included -- confirmed live (v8 smoke
test review) that this was silently garbling the product's own label text
("Text on the product label is blurry and illegible... small icons and text
at the bottom of the bottle are unreadable") no matter how the prompt worded
"keep the label exactly as shown," because a prompt instruction can only ever
*ask*, never structurally prevent, a diffusion model from touching pixels.

The fix: BackgroundRemoverClient produces an alpha matte of the product,
masking.py turns that into a Flux-Fill mask (black=preserve, white=inpaint,
confirmed against Replicate's own schema), and FluxFillClient regenerates
only the masked-out background. The product's own pixels -- including every
word of label text -- are never sent through the diffusion process at all,
so they come back byte-for-byte unchanged. This also narrows what the model
needs to be told: since the product region is structurally protected, the
prompt no longer needs to describe preserving it, only the scene itself --
paired with an explicit no-text instruction to address the separate,
still-heuristic complaint that the model sometimes hallucinates its own
headline/CTA-like text into the background it's asked to fill.

Round 5 (2026-08-28) fix, still in force: `_guide_to_scene_description` used
to keep only `higher_is_better` signals and silently drop every
`lower_is_better` one -- including the real, live finding that
`background_style=Studio` is `lower_is_better`. With no matching positive
signal, it fell through to a hardcoded default of "a clean, high-contrast
studio background" -- the exact thing the data says to avoid, confirmed live
by every one of the top 5 real ads by composite success score having
`background_style=Busy`, none plain/studio. Now surfaces avoid-signals
explicitly and prefers a StyleBrief (style_reference.py) over the guide-only
description whenever one is available.

Round 7 (2026-08-29): style_reference.py stopped grounding the StyleBrief in
reference-ad images -- the guide's own statistics are now authoritative for
every dimension they measure, and reference ads moved to a post-generation
comparison role (feature_fidelity.py) instead of a generation-input one.
`_scene_description`'s own logic here is unaffected by that move (it already
just consumes whatever StyleBrief it's given), but the docstring above is
corrected so it doesn't misstate where the StyleBrief's content comes from.
"""

from __future__ import annotations

from pipeline.clients.replicate_client import BackgroundRemoverClient, FluxFillClient
from pipeline.generation.guide import GenerationGuide
from pipeline.generation.masking import build_inpaint_mask
from pipeline.generation.style_reference import StyleBrief

_SCENE_DIMENSIONS = {"background_style", "dominant_color", "contrast_ratio_type"}
# Never default to this -- confirmed live (2026-08-28) that real top ads by
# composite success score are Busy, not Studio; kept only as the absolute
# last resort if neither a style brief nor any guide directive says anything.
_LAST_RESORT_FALLBACK = (
    "a detailed, real-world lifestyle or contextual scene with visible "
    "surrounding elements -- not a plain, empty studio background"
)
# Round 6: the product region is now mask-protected, so this instruction only
# has to govern the *background* fill -- but a diffusion model asked to
# "make an ad-like scene" still tends toward hallucinating its own headline/
# CTA-style text or props unless told explicitly and repeatedly not to.
# Live-verified (2026-08-29): the first version of this instruction (text-
# only prohibition) still let Flux Fill paint a *second*, smaller, garbled
# copy of the product bottle into the open background -- masking protects
# the pixels it's given, it doesn't stop the model inventing a new instance
# of "a labeled bottle" elsewhere, since ad-style product photography is
# exactly what it's biased toward. Rule (2) below, added after that finding,
# eliminated it in the next live run.
#
# Round 8 (2026-08-29), found live: even with rules (1)-(3) below, Flux Fill
# rendered a full ghost paragraph of ad copy into the background -- confirmed
# by zooming in, its text legibly echoed fragments of the *intention* string
# verbatim ("...Joint Suppleme[nt]... for Caring Dog Owners..."). Root cause:
# the old prompt embedded the raw intention text as "Context for the scene:
# {intention}" with no instruction not to render it -- and intention strings
# are themselves marketing-copy-shaped, which is exactly the training-data
# pattern a diffusion model biases toward reproducing literally. This
# instruction is now placed FIRST in the prompt (primacy matters for
# instruction-following) and explicitly names the intention text as
# non-renderable context, not a caption to paint.
#
# Live-verified after the Round 8 fix above: the ghost-paragraph and
# duplicate-full-product failures are gone, but a narrower leak remained --
# the model rendered the product's own brand name ("wuffes") onto an
# unrelated prop (a drinking glass), a loophole in rule (2)'s enumerated
# object list (bottle/jar/container/package/box never mentioned "glass" or
# "cup"). Rule (1) below now names props explicitly rather than leaving
# "no markings on anything" as a softer tail clause in rule (3) -- the
# specific, repeated enumeration in the old rule (2) was clearly more
# salient to the model than the general clause, so the fix is to make the
# text ban itself exhaustively concrete, not to add a 4th rule.
_NO_TEXT_INSTRUCTION = (
    "This is a pure background/environment fill, not a finished ad layout. "
    "Absolute rules for the filled area, all equally important: (1) zero "
    "text of any kind, anywhere in the scene, on any surface or object -- "
    "no words, letters, numbers, logos, watermarks, signage, labels, "
    "taglines, or typography, including on props, glasses, cups, dishes, or "
    "any other item, under any circumstances; (2) do not depict any bottle, "
    "jar, container, package, box, or product of any kind -- there is "
    "already exactly one product in this image (outside the filled area) "
    "and it must remain the only one, never duplicated, echoed, or repeated "
    "anywhere in the background; (3) environment and props only (e.g. a "
    "surface, furniture, plants, a pet, natural light), always completely "
    "blank and unmarked. Any headline, call-to-action, or price text "
    "belongs to a separate design layer added afterward and must never be "
    "rendered into this image."
)


def _guide_to_scene_description(guide: GenerationGuide) -> str:
    """Only the visual directives that describe a *scene/background*
    property translate into an edit instruction -- copy-style and CTA-type
    directives don't describe anything the background fill renders here.
    Both directions matter: a `lower_is_better` signal becomes an explicit
    "avoid X" instruction instead of being silently discarded."""
    prefer: list[str] = []
    avoid: list[str] = []
    for s in guide.visual_directives:
        if s.dimension not in _SCENE_DIMENSIONS or not s.value:
            continue
        label = f"{s.dimension.replace('_', ' ')} of '{s.value}'"
        (prefer if s.direction == "higher_is_better" else avoid).append(label)

    if not prefer and not avoid:
        return _LAST_RESORT_FALLBACK

    parts = []
    if prefer:
        parts.append("use " + ", ".join(prefer))
    if avoid:
        parts.append("avoid " + ", ".join(avoid))
    return "; ".join(parts)


def _scene_description(guide: GenerationGuide, style_brief: StyleBrief | None) -> str:
    """A StyleBrief (style_reference.py) is the guide's own directives already
    translated into concrete creative language -- prefer it outright over the
    guide-only description when available, rather than trying to merge two
    potentially-conflicting scene texts into one prompt."""
    if style_brief is not None:
        palette = ", ".join(style_brief.dominant_color_palette) or "no specific palette"
        return f"{style_brief.background_treatment} (color palette: {palette})"
    return _guide_to_scene_description(guide)


def generate_background_and_product(
    bg_remover_client: BackgroundRemoverClient,
    flux_fill_client: FluxFillClient,
    product_photo_bytes: bytes,
    *,
    intention: str,
    guide: GenerationGuide,
    style_brief: StyleBrief | None = None,
) -> bytes:
    scene = _scene_description(guide, style_brief)
    cutout = bg_remover_client.remove_background(product_photo_bytes)
    mask = build_inpaint_mask(cutout)
    prompt = (
        f"{_NO_TEXT_INSTRUCTION} "
        f"Fill in a new background and surrounding scene to achieve: {scene}. "
        f"Mood and setting only, for visual tone -- these words describe the "
        f"intended feeling, they are not a caption or tagline and must never "
        f"be rendered as text in the image: {intention}."
    )
    return flux_fill_client.inpaint(product_photo_bytes, mask, prompt)
