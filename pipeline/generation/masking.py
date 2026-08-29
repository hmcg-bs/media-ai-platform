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
"""

from __future__ import annotations

import io

from PIL import Image, ImageFilter

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

    # MaxFilter's kernel must be odd-sized; approximate the requested pixel
    # margin by repeating a small filter rather than one huge kernel. Must
    # skip entirely when dilate_px == 0 -- a naive max(1, dilate_px // 2)
    # forced one MaxFilter pass even at "no dilation," silently growing the
    # preserved region by ~2px regardless of the requested margin.
    if dilate_px > 0:
        for _ in range(max(1, dilate_px // 2)):
            product_mask = product_mask.filter(ImageFilter.MaxFilter(5))

    # Invert into Flux Fill's convention: black (0) = preserve, white (255) =
    # inpaint -- the opposite of "product present".
    inpaint_mask = product_mask.point(lambda p: 0 if p > 0 else 255)

    if feather_radius > 0:
        inpaint_mask = inpaint_mask.filter(ImageFilter.GaussianBlur(radius=feather_radius))

    buf = io.BytesIO()
    inpaint_mask.convert("L").save(buf, format="PNG")
    return buf.getvalue()
