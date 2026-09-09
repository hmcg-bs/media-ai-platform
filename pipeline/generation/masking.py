"""Round 6 (2026-08-29): builds a Flux-Fill-compatible inpaint mask that
protects the product region -- and critically, its own label text -- from
ever being regenerated.

Why this exists: Flux Kontext Pro (the prior background-generation
mechanism, see background.py's own history) has no mask input at all --
every edit re-renders the *entire* image, product included, and diffusion
models are notoriously unreliable at re-rendering small legible text.
Confirmed live (v8 smoke test, review agent): "Text on the product label is
blurry and illegible... small icons and text at the bottom of the bottle are
unreadable." Prompt instructions ("keep the label text exactly as shown")
only ever *ask* the model not to touch the product -- they can't structurally
prevent it. Masking can: Flux Fill Pro's own field description (confirmed
against Replicate's schema) is explicit -- "Black areas will be preserved
while white areas will be inpainted." A masked-out product region is pixel-
identical in the output, full stop, because the model is never allowed to
touch it -- not because it was asked nicely.

Generation v2 (2026-08-30) adds two more consumers of the same mask
convention: `compute_product_bbox` reads the product's exact geometry
straight off this same alpha cutout -- known immediately after
BackgroundRemoverClient.remove_background() returns, well before any
generation call, since the product's pixels never move afterward -- so
layout_planner.py no longer has to wait for a vision model to look at an
already-generated frame and guess where the product landed. And
`build_targeted_erase_mask` reuses this file's own dilate/feather machinery
to build a mask for surgical duplicate-product repair (background.py's
repair_duplicate_region): black everywhere except a feathered white patch
over just the hallucinated region, so Flux Fill re-touches only that one
spot instead of the whole frame.
"""

from __future__ import annotations

import io

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from pipeline.generation.layout import BoundingBox

# Grows the product silhouette outward before masking so no edge/label pixel
# is left exposed to inpainting -- background-remover's alpha can have soft,
# slightly-shrunk edges relative to the product's real boundary. Kept small:
# every pixel inside this margin is *raw original background*, preserved
# verbatim (not inpainted) -- too wide a margin leaves a visible ring of the
# product photo's own old background sitting untouched next to Flux Fill's
# newly generated scene, seen live as a hard white halo around the product.
_DILATE_MARGIN_PX = 4
_ALPHA_PRESENCE_THRESHOLD = 10
# Softens the preserve/inpaint boundary from a hard 0/255 cliff into a
# gradient a few pixels wide -- confirmed live this is what actually removes
# the halo (shrinking the dilation margin alone wasn't enough; the *hard
# edge itself*, not just its width, is what read as a seam).
_FEATHER_BLUR_RADIUS = 3


def _dilate_white_region(mask: Image.Image, dilate_px: int) -> Image.Image:
    """Grows a mask's white (255) region outward by ~dilate_px pixels via
    repeated small MaxFilter passes -- shared by build_inpaint_mask (grows
    the *preserved* product silhouette) and build_targeted_erase_mask (grows
    the *erased* repair patch), since both need identical growth behavior,
    including the dilate_px==0 no-op guard: a naive single MaxFilter pass
    still grew the region by ~2px even at "no dilation" (a real bug found
    and fixed in Round 6)."""
    if dilate_px <= 0:
        return mask
    for _ in range(max(1, dilate_px // 2)):
        mask = mask.filter(ImageFilter.MaxFilter(5))
    return mask


def _feather(mask: Image.Image, feather_radius: int) -> Image.Image:
    """Softens a mask's hard 0/255 boundary into a gradient -- confirmed live
    (Round 6) this is what actually removes a visible seam/halo at an edit
    boundary, not just narrowing the dilation margin."""
    if feather_radius <= 0:
        return mask
    return mask.filter(ImageFilter.GaussianBlur(radius=feather_radius))


def build_inpaint_mask(
    rgba_cutout_bytes: bytes,
    dilate_px: int = _DILATE_MARGIN_PX,
    feather_radius: int = _FEATHER_BLUR_RADIUS,
) -> bytes:
    """rgba_cutout_bytes: an RGBA cutout (BackgroundRemoverClient.remove_background)
    whose alpha channel marks where the product is. Returns a grayscale PNG
    in Flux Fill's own convention: black over the product (preserved, grown
    by `dilate_px` for a safety margin), white everywhere else (inpainted --
    the background Flux Fill is free to regenerate), with the boundary
    between them feathered by `feather_radius` so the edit blends instead of
    leaving a hard-seam ring of the original background around the product."""
    cutout = Image.open(io.BytesIO(rgba_cutout_bytes)).convert("RGBA")
    alpha = cutout.split()[-1]

    # Binarize first -- soft/anti-aliased alpha fringe pixels around the
    # product's edge still count as "product present" for masking purposes.
    product_mask = alpha.point(lambda p: 255 if p > _ALPHA_PRESENCE_THRESHOLD else 0)
    product_mask = _dilate_white_region(product_mask, dilate_px)

    # Invert into Flux Fill's convention: black (0) = preserve, white (255) =
    # inpaint -- the opposite of "product present".
    inpaint_mask = product_mask.point(lambda p: 0 if p > 0 else 255)
    inpaint_mask = _feather(inpaint_mask, feather_radius)

    buf = io.BytesIO()
    inpaint_mask.convert("L").save(buf, format="PNG")
    return buf.getvalue()


def compute_product_bbox(
    rgba_cutout_bytes: bytes, threshold: int = _ALPHA_PRESENCE_THRESHOLD
) -> BoundingBox:
    """The product's bounding box read straight off its own alpha cutout --
    available immediately after BackgroundRemoverClient.remove_background()
    returns, well before Flux Fill ever runs. Generation v2's layout planner
    (layout_planner.py) uses this instead of asking a vision model to look
    at an already-generated frame and guess where the product landed, since
    the product's pixels are structurally guaranteed (via build_inpaint_mask)
    to never move from here on. Coordinates are 0-1 fractions of the
    cutout's own dimensions, matching every other bounding box in this
    package's convention."""
    cutout = Image.open(io.BytesIO(rgba_cutout_bytes)).convert("RGBA")
    alpha = np.array(cutout.split()[-1])
    present = alpha > threshold
    rows = np.any(present, axis=1)
    cols = np.any(present, axis=0)
    if not rows.any() or not cols.any():
        raise ValueError(
            "compute_product_bbox: cutout has no foreground pixels above threshold "
            f"{threshold} -- background-remover likely failed on this input"
        )
    y0, y1 = int(np.argmax(rows)), int(len(rows) - 1 - np.argmax(rows[::-1]))
    x0, x1 = int(np.argmax(cols)), int(len(cols) - 1 - np.argmax(cols[::-1]))
    width_px, height_px = cutout.size
    return BoundingBox(
        x=x0 / width_px, y=y0 / height_px,
        width=(x1 - x0 + 1) / width_px, height=(y1 - y0 + 1) / height_px,
    )


def build_targeted_erase_mask(
    canvas_width: int,
    canvas_height: int,
    region: BoundingBox,
    dilate_px: int = 8,
    feather_radius: int = 4,
) -> bytes:
    """A black canvas (preserve everything) with a feathered white patch over
    just `region` -- Flux Fill's own convention, applied to one small area
    instead of the whole background. Generation v2's surgical duplicate-
    repair path (background.py's repair_duplicate_region) uses this so a
    detected hallucinated object can be erased and re-filled with one small
    edit instead of a full from-scratch regeneration. Reuses this module's
    own dilate/feather helpers (`_dilate_white_region`, `_feather`) so the
    growth/softening behavior exactly matches build_inpaint_mask's."""
    x0 = round(region.x * canvas_width)
    y0 = round(region.y * canvas_height)
    x1 = round((region.x + region.width) * canvas_width)
    y1 = round((region.y + region.height) * canvas_height)

    erase_mask = Image.new("L", (canvas_width, canvas_height), 0)
    ImageDraw.Draw(erase_mask).rectangle([x0, y0, x1, y1], fill=255)
    erase_mask = _dilate_white_region(erase_mask, dilate_px)
    erase_mask = _feather(erase_mask, feather_radius)

    buf = io.BytesIO()
    erase_mask.save(buf, format="PNG")
    return buf.getvalue()
