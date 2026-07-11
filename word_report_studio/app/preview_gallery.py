"""
preview_gallery.py
---------------------
Builds a comparison view across all generated layout options:
- a manifest (list of dicts) the GUI can render as cards
- real first-page thumbnail PNGs via PyMuPDF (fitz), if installed, from the
  exported PDFs
- a self-contained offline HTML contact sheet as a fallback/extra deliverable
  (no external resources, works with just the color palette even if no PDF
  thumbnails exist)
"""

from __future__ import annotations
import os
from typing import Dict, List, Optional

try:
    import fitz  # PyMuPDF
    HAS_FITZ = True
except ImportError:
    HAS_FITZ = False

from .layout_engine import LayoutOption


def render_thumbnails(pdf_paths: Dict[str, str], out_dir: str, dpi: int = 110) -> Dict[str, str]:
    """pdf_paths: {option_id: pdf_path}. Returns {option_id: thumbnail_png_path}.
    Silently returns {} if PyMuPDF isn't installed -- callers should fall back
    to the color-swatch cards instead of failing."""
    if not HAS_FITZ:
        return {}
    os.makedirs(out_dir, exist_ok=True)
    thumbs: Dict[str, str] = {}
    for option_id, pdf_path in pdf_paths.items():
        if not pdf_path or pdf_path.startswith("ERROR"):
            continue
        try:
            doc = fitz.open(pdf_path)
            page = doc.load_page(0)
            matrix = fitz.Matrix(dpi / 72, dpi / 72)
            pix = page.get_pixmap(matrix=matrix)
            out_path = os.path.join(out_dir, f"{option_id}_thumb.png")
            pix.save(out_path)
            thumbs[option_id] = out_path
            doc.close()
        except Exception as e:
            print(f"[preview_gallery] thumbnail failed for {option_id}: {e}")
    return thumbs


def build_manifest(options: List[LayoutOption], docx_paths: Dict[str, str],
                    pdf_paths: Optional[Dict[str, str]] = None,
                    thumb_paths: Optional[Dict[str, str]] = None) -> List[dict]:
    pdf_paths = pdf_paths or {}
    thumb_paths = thumb_paths or {}
    manifest = []
    for opt in options:
        colors = opt.template.resolved_colors(opt.variant)
        manifest.append({
            "option_id": opt.option_id,
            "label": opt.label,
            "template_id": opt.template.id,
            "template_name": opt.template.name,
            "category": opt.template.category,
            "variant_label": opt.variant.label,
            "score": round(opt.score, 2),
            "colors": {
                "primary": colors.primary, "secondary": colors.secondary,
                "accent": colors.accent, "text": colors.text,
            },
            "docx_path": docx_paths.get(opt.option_id),
            "pdf_path": pdf_paths.get(opt.option_id),
            "thumb_path": thumb_paths.get(opt.option_id),
        })
    return manifest


def write_html_contact_sheet(manifest: List[dict], out_path: str, doc_title: str = "Report"):
    """Writes a single self-contained offline HTML file (no external assets)
    so the user can flip through every generated option side by side before
    opening any .docx."""
    cards = []
    for m in manifest:
        colors = m["colors"]
        if m.get("thumb_path") and os.path.isfile(m["thumb_path"]):
            rel = os.path.relpath(m["thumb_path"], os.path.dirname(out_path))
            visual = f'<img src="{rel}" style="width:100%;border-radius:6px;border:1px solid #ddd;">'
        else:
            visual = (
                f'<div style="height:180px;border-radius:6px;background:#{colors["primary"]};'
                f'display:flex;align-items:center;justify-content:center;color:#fff;'
                f'font-family:Georgia,serif;font-size:18px;text-align:center;padding:12px;">'
                f'{doc_title}</div>'
            )
        cards.append(f"""
        <div style="border:1px solid #e2e2e2;border-radius:10px;padding:14px;width:300px;
                    box-shadow:0 1px 4px rgba(0,0,0,0.08);font-family:Arial,sans-serif;">
          {visual}
          <h3 style="margin:10px 0 2px 0;font-size:15px;color:#222;">{m['label']}</h3>
          <div style="font-size:12px;color:#777;margin-bottom:8px;">
            {m['category'].title()} template &middot; score {m['score']}
          </div>
          <div style="display:flex;gap:6px;">
            <span style="width:18px;height:18px;border-radius:4px;background:#{colors['primary']};display:inline-block;"></span>
            <span style="width:18px;height:18px;border-radius:4px;background:#{colors['secondary']};display:inline-block;"></span>
            <span style="width:18px;height:18px;border-radius:4px;background:#{colors['accent']};display:inline-block;"></span>
          </div>
          <div style="font-size:11px;color:#999;margin-top:8px;word-break:break-all;">
            {os.path.basename(m['docx_path']) if m.get('docx_path') else ''}
          </div>
        </div>""")

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{doc_title} - Layout Options</title>
</head>
<body style="background:#f5f5f7;margin:0;padding:24px;">
  <h1 style="font-family:Arial,sans-serif;color:#222;">Layout Options: {doc_title}</h1>
  <p style="font-family:Arial,sans-serif;color:#555;">
    Generated fully offline. Open any .docx from the output folder to edit in Word.
  </p>
  <div style="display:flex;flex-wrap:wrap;gap:16px;margin-top:16px;">
    {''.join(cards)}
  </div>
</body>
</html>"""
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path
