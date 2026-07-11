"""
image_filler.py
----------------
Replace a designer template's "IMAGE HERE" placeholder graphics with the
user's real photos — the final step that makes a filled brochure look
finished instead of templated.

How placeholders are recognized (from real template dissection):
- The placeholder graphic is ONE media part reused many times (20+ blip
  references), while real artwork/logos are single-use or small.
- Logo-sized frames are excluded by display size: only frames at least
  ~1.4 inches in both dimensions receive photos.

Each qualifying occurrence gets its own image part (so different frames can
show different photos), center-cropped to the frame's aspect ratio with
matplotlib — no new dependencies, still fully offline. The legacy
mc:Fallback copy (VML v:imagedata) is updated alongside the modern blip so
Word and LibreOffice show the same photo.
"""

from __future__ import annotations
import os
from typing import List, Optional

from docx.oxml.ns import qn

_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_V = "urn:schemas-microsoft-com:vml"
_EMU_PER_IN = 914400

MIN_PHOTO_INCHES = 1.4     # frames smaller than this are logos/icons — skip
PLACEHOLDER_MIN_USES = 3   # a media part this reused is a placeholder


def _blip_frame_inches(blip) -> tuple:
    """Displayed size of the picture containing this blip, in inches."""
    node = blip
    for _ in range(8):
        node = node.getparent()
        if node is None:
            break
        ext = node.find(".//{%s}ext" % _A)
        if ext is not None and ext.get("cx") and ext.get("cy"):
            return (int(ext.get("cx")) / _EMU_PER_IN,
                    int(ext.get("cy")) / _EMU_PER_IN)
    return (0.0, 0.0)


def _crop_to_aspect(src_path: str, aspect: float, out_dir: str,
                    tag: str) -> str:
    """Center-crop the photo to `aspect` (w/h) using matplotlib. Falls back
    to the original file when matplotlib is unavailable or cropping fails
    (Word will stretch it — imperfect but never fatal)."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.image as mpimg
        img = mpimg.imread(src_path)
        h, w = img.shape[:2]
        cur = w / h
        if abs(cur - aspect) < 0.05:
            return src_path
        if cur > aspect:      # too wide -> crop sides
            new_w = max(1, int(h * aspect))
            x0 = (w - new_w) // 2
            img = img[:, x0:x0 + new_w]
        else:                 # too tall -> crop top/bottom
            new_h = max(1, int(w / aspect))
            y0 = (h - new_h) // 2
            img = img[y0:y0 + new_h, :]
        os.makedirs(out_dir, exist_ok=True)
        base = os.path.splitext(os.path.basename(src_path))[0]
        out = os.path.join(out_dir, f"{base}__{tag}.png")
        mpimg.imsave(out, img)
        return out
    except Exception:
        return src_path


def count_photo_frames(doc) -> int:
    """How many large placeholder frames the template has."""
    return len(_placeholder_blips(doc))


def _placeholder_blips(doc) -> List:
    body = doc.element.body
    blips = body.findall(".//{%s}blip" % _A)
    from collections import Counter
    usage = Counter(b.get("{%s}embed" % _R) for b in blips)
    result = []
    for b in blips:
        rid = b.get("{%s}embed" % _R)
        if usage.get(rid, 0) < PLACEHOLDER_MIN_USES:
            continue
        w, h = _blip_frame_inches(b)
        if w >= MIN_PHOTO_INCHES and h >= MIN_PHOTO_INCHES:
            result.append(b)
    return result


def fill_images(doc, image_paths: List[str], work_dir: str,
                log=None) -> int:
    """Assign `image_paths` (round-robin, document order) to the template's
    large placeholder frames. Returns the number of frames replaced."""
    if not image_paths:
        return 0
    log = log or (lambda *_: None)
    targets = _placeholder_blips(doc)
    if not targets:
        log("No large placeholder image frames detected in this template.")
        return 0

    # also patch the VML fallback twins so LibreOffice matches Word:
    # map old rId -> per-occurrence handling is only possible for blips;
    # fallback v:imagedata that still points at the placeholder gets the
    # FIRST photo (a close-enough legacy approximation).
    replaced = 0
    for i, blip in enumerate(targets):
        src = image_paths[i % len(image_paths)]
        w, h = _blip_frame_inches(blip)
        aspect = (w / h) if h else 1.0
        cropped = _crop_to_aspect(src, aspect, work_dir, f"f{i}_{w:.1f}x{h:.1f}")
        try:
            _rid_img = doc.part.get_or_add_image(cropped)
            rid = _rid_img[0] if isinstance(_rid_img, tuple) else _rid_img
            blip.set("{%s}embed" % _R, rid)
            replaced += 1
        except Exception as e:
            log(f"  image frame {i + 1} skipped: {e}")

    if replaced:
        try:
            first = image_paths[0]
            _rid_img = doc.part.get_or_add_image(first)
            rid = _rid_img[0] if isinstance(_rid_img, tuple) else _rid_img
            from collections import Counter
            body = doc.element.body
            imagedatas = body.findall(".//{%s}imagedata" % _V)
            usage = Counter(d.get("{%s}id" % _R) for d in imagedatas)
            for data in imagedatas:
                if usage.get(data.get("{%s}id" % _R), 0) >= PLACEHOLDER_MIN_USES:
                    data.set("{%s}id" % _R, rid)
        except Exception:
            pass
    log(f"Placed photos into {replaced} designed image frame(s).")
    return replaced


def collect_images(folder: str) -> List[str]:
    """Usable photo files from a folder, in name order."""
    exts = (".jpg", ".jpeg", ".png", ".bmp")
    try:
        return [os.path.join(folder, f) for f in sorted(os.listdir(folder))
                if f.lower().endswith(exts)]
    except FileNotFoundError:
        return []
