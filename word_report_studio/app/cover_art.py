"""
cover_art.py
-------------
Paints full-page, poster-quality report covers as high-resolution PNG images
(fully offline, using Pillow). This is what makes covers look *designed*
rather than assembled from Word paragraphs: real geometry, overlapping color
shapes, and precise typography — things Word layout primitives can't do.

Three art styles, all colored from the template palette:

  diagonal  — strategy-consulting look: deep color field sliced diagonally,
              accent seam, title on generous whitespace
  blocks    — modern annual-report look: saturated background, overlapping
              color blocks, oversized ghost year numeral
  frame     — formal/royal look: double rule frame, centered symmetry,
              ornament row, serif typography

Text is split into per-language runs (structure_model.runs_from_text) so a
single mixed title like "Annual Report / التقرير السنوي" draws each script
with the right typeface: Lusail for Latin (per the design system) and a
shaping-friendly stack for Arabic. Pillow has no bundled text-shaping engine
(no libraqm), so Lusail's own Arabic glyphs can't be reached by feeding it
reshaper output — reshaping only produces presentation-form codepoints that
Lusail's font file doesn't map. Arabic runs use chart_engine.shape_text
(reshaper + bidi) with Segoe UI/Tahoma/Arial, the same combination already
proven correct by chart_engine's own output.

The cover text lives inside the image (not editable in Word). Everything
after page 1 remains fully editable — this is the standard trade-off any
design-grade cover makes.
"""

from __future__ import annotations
import io
import math
import os
import re
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from .chart_engine import shape_text
from .structure_model import runs_from_text

# A4 at 200 dpi
PAGE_W, PAGE_H = 1654, 2339
_FONT_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")

# (regular, bold) files with full Arabic coverage, checked in order
_FONT_STACKS = {
    "modern": [("segoeui.ttf", "segoeuib.ttf"), ("tahoma.ttf", "tahomabd.ttf"),
               ("arial.ttf", "arialbd.ttf")],
    "serif": [("times.ttf", "timesbd.ttf"), ("georgia.ttf", "georgiab.ttf"),
              ("arial.ttf", "arialbd.ttf")],
    # Lusail: bilingual Qatar identity typeface; falls back to Segoe UI
    "lusail": [("Lusail-Regular.ttf", "Lusail-Bold.otf"),
               ("Lusail-Regular.otf", "Lusail-Bold.otf"),
               ("segoeui.ttf", "segoeuib.ttf"), ("arial.ttf", "arialbd.ttf")],
}


def _rgb(hex_color: str) -> Tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _mix(c1, c2, t: float):
    return tuple(round(a + (b - a) * t) for a, b in zip(c1, c2))


def _font(kind: str, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    for regular, boldface in _FONT_STACKS.get(kind, _FONT_STACKS["modern"]):
        path = os.path.join(_FONT_DIR, boldface if bold else regular)
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _run_font(kind_latin: str, size: int, bold: bool, is_arabic: bool) -> ImageFont.FreeTypeFont:
    """Pillow has no real text-shaping engine (no libraqm on this build), so
    a single font can't be handed mixed-script text: reshaper output uses
    Arabic presentation-form codepoints that most non-Arabic-authoring fonts
    (Lusail included) don't map, and Pillow won't run Lusail's own GSUB
    tables to shape plain Arabic itself. So Latin runs get the requested
    typeface (Lusail per the design system) and Arabic runs get the same
    shaping-friendly stack chart_engine already uses successfully."""
    return _font("modern", size, bold) if is_arabic else _font(kind_latin, size, bold)


def _wrap_mixed(text: str, kind_latin: str, size: int, max_w: int,
                bold: bool = False) -> List[List[Tuple[str, ImageFont.FreeTypeFont]]]:
    """Split into language runs (structure_model.runs_from_text), shape the
    Arabic ones, then word-wrap the mixed sequence to max_w. Returns lines
    as [(shaped_piece, font), ...] ready to draw left-to-right."""
    lines: List[List[Tuple[str, ImageFont.FreeTypeFont, bool]]] = []
    current: List[Tuple[str, ImageFont.FreeTypeFont, bool]] = []
    current_w = 0.0
    for run in runs_from_text(text):
        is_ar = run.lang == "ar"
        font = _run_font(kind_latin, size, bold, is_ar)
        words = run.text.split(" ")
        for wi, w in enumerate(words):
            trailing = " " if wi < len(words) - 1 else ""
            if w == "" and trailing == "":
                continue
            # shape the WORD only — bidi reordering eats trailing spaces,
            # which is how Arabic words ended up glued together on covers
            shaped = (shape_text(w) if is_ar else w) + trailing
            piece_w = font.getlength(shaped)
            if current and current_w + piece_w > max_w:
                lines.append(current)
                current, current_w = [], 0.0
            current.append((shaped, font, is_ar))
            current_w += piece_w
    if current:
        lines.append(current)

    # within each line, consecutive Arabic pieces must render right-to-left:
    # reverse each Arabic segment, keeping the spaces between words
    def fix_rtl(line):
        out, i = [], 0
        while i < len(line):
            if not line[i][2]:
                out.append(line[i])
                i += 1
                continue
            j = i
            while j < len(line) and line[j][2]:
                j += 1
            seg = line[i:j][::-1]
            had_trailing = line[j - 1][0].endswith(" ")
            texts = [p[0].rstrip(" ") for p in seg]
            texts = [t + " " for t in texts[:-1]] + \
                    [texts[-1] + (" " if had_trailing else "")]
            out.extend((t, seg[k][1], True) for k, t in enumerate(texts))
            i = j
        return out

    return [fix_rtl(l) for l in lines[:4]]


def _draw_mixed(draw, lines, x, y, fill, align="left", max_w=0,
                line_gap=1.18, base_size=40) -> int:
    """Draw lines produced by _wrap_mixed; returns y after the last line."""
    line_h = int(base_size * line_gap)
    for line in lines:
        total_w = sum(font.getlength(piece) for piece, font, *_ in line)
        if align == "center":
            lx = x + (max_w - total_w) / 2
        elif align == "right":
            lx = x + max_w - total_w
        else:
            lx = x
        for piece, font, *_ in line:
            draw.text((lx, y), piece, font=font, fill=fill)
            lx += font.getlength(piece)
        y += line_h
    return y


def _extract_year(meta: dict) -> Optional[str]:
    # subtitle/title year (e.g. "Fiscal Year 2025") is the report's real
    # year; the publication date is only a fallback
    for field in ("subtitle", "title", "date"):
        v = meta.get(field) or ""
        m = re.search(r"(19|20)\d{2}", v)
        if m:
            return m.group(0)
    return None


# ---------------------------------------------------------------------------
# Style: diagonal (strategy / consulting)
# ---------------------------------------------------------------------------

def _style_diagonal(img, draw, meta, colors):
    primary, secondary, accent = _rgb(colors.primary), _rgb(colors.secondary), _rgb(colors.accent)
    white = (255, 255, 255)

    # Deep color field slashed diagonally across the top
    draw.polygon([(0, 0), (PAGE_W, 0), (PAGE_W, 620), (0, 1050)], fill=primary)
    # accent seam parallel to the cut
    draw.polygon([(0, 1050), (PAGE_W, 620), (PAGE_W, 648), (0, 1078)], fill=accent)
    # faint echo line lower down for depth
    draw.polygon([(0, 1120), (PAGE_W, 690), (PAGE_W, 696), (0, 1126)],
                 fill=_mix(primary, (255, 255, 255), 0.75))

    margin = 130
    org = (meta.get("organization") or "").upper()
    if org:
        org_lines = _wrap_mixed(org, "modern", 44, PAGE_W - 2 * margin, bold=True)
        _draw_mixed(draw, org_lines, margin, 118, white, base_size=44)
        draw.rectangle([margin, 190, margin + 160, 198], fill=accent)

    year = _extract_year(meta)
    if year:
        f_year = _font("modern", 230, bold=True)
        draw.text((PAGE_W - margin - f_year.getlength(year), 250), year,
                  font=f_year, fill=_mix(primary, white, 0.16))

    # Title block on the whitespace below the diagonal
    lines = _wrap_mixed(meta.get("title", ""), "modern", 118, PAGE_W - 2 * margin, bold=True)
    y = _draw_mixed(draw, lines, margin, 1290, primary, base_size=118)
    if meta.get("subtitle"):
        y += 26
        sub_lines = _wrap_mixed(meta["subtitle"], "modern", 54, PAGE_W - 2 * margin)
        y = _draw_mixed(draw, sub_lines, margin, y, secondary, base_size=54)

    # Meta strip at the bottom
    fy = PAGE_H - 300
    draw.rectangle([margin, fy, margin + 700, fy + 6], fill=accent)
    parts = [p for p in (meta.get("author"), meta.get("date")) if p]
    if parts:
        meta_lines = _wrap_mixed("     ".join(parts), "modern", 42, PAGE_W - 2 * margin)
        _draw_mixed(draw, meta_lines, margin, fy + 40, _rgb(colors.text), base_size=42)
    if meta.get("confidentiality"):
        conf_lines = _wrap_mixed(meta["confidentiality"], "modern", 36,
                                 PAGE_W - 2 * margin, bold=True)
        _draw_mixed(draw, conf_lines, margin, fy + 130, accent, base_size=36)


# ---------------------------------------------------------------------------
# Style: blocks (modern annual report)
# ---------------------------------------------------------------------------

def _style_blocks(img, draw, meta, colors):
    primary, secondary, accent = _rgb(colors.primary), _rgb(colors.secondary), _rgb(colors.accent)
    white = (255, 255, 255)

    draw.rectangle([0, 0, PAGE_W, PAGE_H], fill=primary)

    # Overlapping translucent geometry on the right
    overlay = Image.new("RGBA", (PAGE_W, PAGE_H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rectangle([PAGE_W - 620, -200, PAGE_W + 300, 900],
                 fill=secondary + (170,))
    od.rectangle([PAGE_W - 980, 560, PAGE_W - 380, 1240],
                 fill=accent + (150,))
    od.ellipse([PAGE_W - 480, 1020, PAGE_W + 240, 1740],
               fill=_mix(primary, white, 0.18) + (120,))
    od.rectangle([-150, PAGE_H - 620, 520, PAGE_H + 100],
                 fill=secondary + (110,))
    img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"), (0, 0))
    draw = ImageDraw.Draw(img)

    margin = 140
    org = (meta.get("organization") or "").upper()
    if org:
        org_lines = _wrap_mixed(org, "modern", 46, PAGE_W - 2 * margin, bold=True)
        _draw_mixed(draw, org_lines, margin, 130, white, base_size=46)

    # Oversized ghost year
    year = _extract_year(meta)
    if year:
        f_year = _font("modern", 420, bold=True)
        draw.text((margin - 14, 420), year, font=f_year,
                  fill=_mix(primary, white, 0.13))

    lines = _wrap_mixed(meta.get("title", ""), "modern", 128,
                        PAGE_W - 2 * margin - 200, bold=True)
    y = _draw_mixed(draw, lines, margin, 1030, white, base_size=128)
    draw.rectangle([margin, y + 30, margin + 260, y + 46], fill=accent)
    if meta.get("subtitle"):
        sub_lines = _wrap_mixed(meta["subtitle"], "modern", 56, PAGE_W - 2 * margin - 100)
        _draw_mixed(draw, sub_lines, margin, y + 110, _mix(primary, white, 0.78), base_size=56)

    parts = [p for p in (meta.get("author"), meta.get("date")) if p]
    if parts:
        meta_lines = _wrap_mixed("     ".join(parts), "modern", 42, PAGE_W - 2 * margin)
        _draw_mixed(draw, meta_lines, margin, PAGE_H - 240,
                    _mix(primary, white, 0.85), base_size=42)
    if meta.get("confidentiality"):
        conf_lines = _wrap_mixed(meta["confidentiality"], "modern", 36,
                                 PAGE_W - 2 * margin, bold=True)
        _draw_mixed(draw, conf_lines, margin, PAGE_H - 155, accent, base_size=36)


# ---------------------------------------------------------------------------
# Style: frame (formal / royal)
# ---------------------------------------------------------------------------

def _style_frame(img, draw, meta, colors):
    primary, accent = _rgb(colors.primary), _rgb(colors.accent)
    cream = (252, 250, 245)
    draw.rectangle([0, 0, PAGE_W, PAGE_H], fill=cream)

    # Double frame: substantial outer rule + fine inner rule
    draw.rectangle([70, 70, PAGE_W - 70, PAGE_H - 70], outline=primary, width=10)
    draw.rectangle([100, 100, PAGE_W - 100, PAGE_H - 100], outline=accent, width=3)
    # corner ticks
    for cx, cy in ((70, 70), (PAGE_W - 70, 70), (70, PAGE_H - 70), (PAGE_W - 70, PAGE_H - 70)):
        draw.rectangle([cx - 22, cy - 22, cx + 22, cy + 22], fill=primary)

    center_w = PAGE_W - 2 * 180
    org = (meta.get("organization") or "").upper()
    if org:
        org_lines = _wrap_mixed(org, "serif", 52, center_w, bold=True)
        _draw_mixed(draw, org_lines, 180, 260, primary, align="center",
                    max_w=center_w, base_size=52)

    def ornament_row(y):
        cx = PAGE_W // 2
        draw.rectangle([cx - 260, y + 8, cx + 260, y + 11], fill=accent)
        for dx in (-300, 0, 300):
            x = cx + dx
            draw.polygon([(x, y - 14), (x + 14, y + 9), (x, y + 32), (x - 14, y + 9)],
                         fill=accent if dx == 0 else primary)

    ornament_row(430)

    lines = _wrap_mixed(meta.get("title", ""), "serif", 116, center_w, bold=True)
    total_h = len(lines) * int(116 * 1.25)
    y0 = (PAGE_H - total_h) // 2 - 160
    y = _draw_mixed(draw, lines, 180, y0, primary, align="center",
                    max_w=center_w, line_gap=1.25, base_size=116)
    if meta.get("subtitle"):
        y += 40
        sub_lines = _wrap_mixed(meta["subtitle"], "serif", 54, center_w)
        y = _draw_mixed(draw, sub_lines, 180, y, _mix(primary, (90, 90, 90), 0.35),
                        align="center", max_w=center_w, base_size=54)
    ornament_row(y + 90)

    yb = PAGE_H - 420
    for part in (meta.get("author"), meta.get("date")):
        if part:
            part_lines = _wrap_mixed(part, "serif", 44, center_w)
            yb = _draw_mixed(draw, part_lines, 180, yb, _rgb(colors.text),
                             align="center", max_w=center_w, base_size=44)
            yb += 8
    if meta.get("confidentiality"):
        conf_lines = _wrap_mixed(meta["confidentiality"], "serif", 38, center_w, bold=True)
        _draw_mixed(draw, conf_lines, 180, yb + 30, accent, align="center",
                    max_w=center_w, base_size=38)


# ---------------------------------------------------------------------------
# Style: executive (Executive Premium design system — light & dark)
# ---------------------------------------------------------------------------

def _executive(img, draw, meta, colors, dark: bool):
    """Minimal executive cover: quiet ground, concentric-arc geometry in the
    corner, thin gold rule, large Lusail title. McKinsey-meets-Doha look."""
    navy, royal, gold = _rgb(colors.primary), _rgb(colors.secondary), _rgb(colors.accent)
    if dark:
        bg, title_c, sub_c, meta_c = navy, (255, 255, 255), _mix(navy, (255, 255, 255), 0.62), _mix(navy, (255, 255, 255), 0.75)
        arc_a, arc_b = gold, _mix(navy, (255, 255, 255), 0.22)
    else:
        bg, title_c, sub_c, meta_c = (252, 252, 253), navy, royal, (94, 107, 120)
        arc_a, arc_b = gold, _mix((255, 255, 255), navy, 0.16)

    draw.rectangle([0, 0, PAGE_W, PAGE_H], fill=bg)

    # Concentric quarter-arcs anchored to the top-right corner — abstract,
    # flat, no gradients (per the design system's iconography rules).
    cx, cy = PAGE_W + 60, -60
    for i, r in enumerate(range(280, 1080, 100)):
        color = arc_a if i % 3 == 0 else arc_b
        width = 7 if i % 3 == 0 else 4
        draw.arc([cx - r, cy - r, cx + r, cy + r], start=90, end=180,
                 fill=color, width=width)
    # small solid anchor dot grid, bottom-left
    for gy in range(6):
        for gx in range(6):
            if gx + gy < 7:
                x = 120 + gx * 44
                y = PAGE_H - 380 + gy * 44
                draw.ellipse([x, y, x + 7, y + 7],
                             fill=arc_b if (gx + gy) % 2 else arc_a)

    margin = 150
    org = (meta.get("organization") or "").upper()
    if org:
        org_lines = _wrap_mixed(org, "lusail", 46, PAGE_W - 2 * margin, bold=True)
        _draw_mixed(draw, org_lines, margin, 150, title_c, base_size=46)
        draw.rectangle([margin, 224, margin + 150, 230], fill=gold)

    lines = _wrap_mixed(meta.get("title", ""), "lusail", 122,
                        PAGE_W - 2 * margin - 160, bold=True)
    y = _draw_mixed(draw, lines, margin, 880, title_c, line_gap=1.16, base_size=122)
    # thin gold rule under the title
    draw.rectangle([margin, y + 34, margin + 430, y + 40], fill=gold)
    if meta.get("subtitle"):
        sub_lines = _wrap_mixed(meta["subtitle"], "lusail", 54, PAGE_W - 2 * margin - 100)
        _draw_mixed(draw, sub_lines, margin, y + 96, sub_c, base_size=54)

    # bottom meta block
    fy = PAGE_H - 330
    draw.rectangle([margin, fy, PAGE_W - margin, fy + 3],
                   fill=gold if dark else _mix((255, 255, 255), navy, 0.25))
    parts = [p for p in (meta.get("author"), meta.get("date")) if p]
    if parts:
        meta_lines = _wrap_mixed("      ".join(parts), "lusail", 40, PAGE_W - 2 * margin)
        _draw_mixed(draw, meta_lines, margin, fy + 42, meta_c, base_size=40)
    if meta.get("confidentiality"):
        conf_lines = _wrap_mixed(meta["confidentiality"], "lusail", 36,
                                 PAGE_W - 2 * margin, bold=True)
        _draw_mixed(draw, conf_lines, margin, fy + 44, gold, align="right",
                    max_w=PAGE_W - 2 * margin, base_size=36)


def _style_executive_light(img, draw, meta, colors):
    _executive(img, draw, meta, colors, dark=False)


def _style_executive_dark(img, draw, meta, colors):
    _executive(img, draw, meta, colors, dark=True)


# ---------------------------------------------------------------------------
# Style: geometric (Islamic eight-point star band — dignified, regional)
# ---------------------------------------------------------------------------

def _star8(draw, cx, cy, r_out, r_in, fill, rot=0.0):
    pts = []
    for i in range(16):
        r = r_out if i % 2 == 0 else r_in
        a = math.pi / 8 * i + rot
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    draw.polygon(pts, fill=fill)


def _style_geometric(img, draw, meta, colors):
    primary, secondary, accent = _rgb(colors.primary), _rgb(colors.secondary), _rgb(colors.accent)
    white = (255, 255, 255)

    band_h = 980
    # lattice drawn on its own image so stars clip cleanly at the band edge
    band = Image.new("RGB", (PAGE_W, band_h), primary)
    band_draw = ImageDraw.Draw(band)
    step = 210
    for row in range(-1, band_h // step + 2):
        for col in range(-1, PAGE_W // step + 2):
            cx = col * step + (step // 2 if row % 2 else 0)
            cy = row * step
            tone = _mix(primary, white, 0.10 if (row + col) % 2 else 0.05)
            _star8(band_draw, cx, cy, 92, 38, tone, rot=math.pi / 8)
            _star8(band_draw, cx, cy, 46, 19, _mix(primary, white, 0.16))
            if (row + col) % 4 == 0:
                _star8(band_draw, cx, cy, 20, 8, accent)
    img.paste(band, (0, 0))
    # gold rule closing the band
    draw.rectangle([0, band_h, PAGE_W, band_h + 10], fill=accent)
    draw.rectangle([0, band_h + 18, PAGE_W, band_h + 22],
                   fill=_mix(accent, white, 0.5))

    margin = 130
    org = (meta.get("organization") or "").upper()
    if org:
        org_lines = _wrap_mixed(org, "modern", 44, PAGE_W - 2 * margin, bold=True)
        _draw_mixed(draw, org_lines, margin, 100, white, base_size=44)

    lines = _wrap_mixed(meta.get("title", ""), "modern", 116,
                        PAGE_W - 2 * margin, bold=True)
    y = _draw_mixed(draw, lines, margin, band_h + 150, primary, base_size=116)
    if meta.get("subtitle"):
        y += 26
        sub = _wrap_mixed(meta["subtitle"], "modern", 54, PAGE_W - 2 * margin)
        y = _draw_mixed(draw, sub, margin, y, secondary, base_size=54)

    year = _extract_year(meta)
    if year:
        f_year = _font("modern", 150, bold=True)
        draw.text((PAGE_W - margin - f_year.getlength(year), PAGE_H - 560),
                  year, font=f_year, fill=_mix(primary, white, 0.82))

    fy = PAGE_H - 300
    draw.rectangle([margin, fy, margin + 700, fy + 6], fill=accent)
    parts = [p for p in (meta.get("author"), meta.get("date")) if p]
    if parts:
        _draw_mixed(draw, _wrap_mixed("     ".join(parts), "modern", 42,
                                      PAGE_W - 2 * margin),
                    margin, fy + 40, _rgb(colors.text), base_size=42)
    if meta.get("confidentiality"):
        _draw_mixed(draw, _wrap_mixed(meta["confidentiality"], "modern", 36,
                                      PAGE_W - 2 * margin, bold=True),
                    margin, fy + 130, accent, base_size=36)


# ---------------------------------------------------------------------------
# Style: contours (topographic flow lines — analytical, modern)
# ---------------------------------------------------------------------------

def _style_contours(img, draw, meta, colors):
    primary, secondary, accent = _rgb(colors.primary), _rgb(colors.secondary), _rgb(colors.accent)
    white = (255, 255, 255)

    # nested hand-drawn contour rings around an off-page focus point
    focus_x, focus_y = PAGE_W + 150, 420
    for i in range(26):
        r = 180 + i * 88
        tone = _mix(white, secondary, max(0.06, 0.30 - i * 0.011))
        width = 7 if i % 5 == 0 else 3
        bbox = [focus_x - r, focus_y - r * 0.86, focus_x + r, focus_y + r * 0.86]
        draw.ellipse(bbox, outline=tone, width=width)
    # accent ring pair
    for r in (515, 529):
        draw.ellipse([focus_x - r, focus_y - r * 0.86, focus_x + r, focus_y + r * 0.86],
                     outline=accent, width=5)
    # solid base panel to seat the title
    draw.rectangle([0, 1210, PAGE_W, PAGE_H], fill=white)
    draw.rectangle([0, 1210, PAGE_W, 1218], fill=_mix(secondary, white, 0.55))

    margin = 130
    org = (meta.get("organization") or "").upper()
    if org:
        org_lines = _wrap_mixed(org, "modern", 44, PAGE_W - 2 * margin, bold=True)
        _draw_mixed(draw, org_lines, margin, 118, primary, base_size=44)
        draw.rectangle([margin, 190, margin + 160, 198], fill=accent)

    lines = _wrap_mixed(meta.get("title", ""), "modern", 118,
                        PAGE_W - 2 * margin, bold=True)
    y = _draw_mixed(draw, lines, margin, 1340, primary, base_size=118)
    if meta.get("subtitle"):
        y += 26
        sub = _wrap_mixed(meta["subtitle"], "modern", 54, PAGE_W - 2 * margin)
        y = _draw_mixed(draw, sub, margin, y, secondary, base_size=54)

    fy = PAGE_H - 300
    draw.rectangle([margin, fy, margin + 700, fy + 6], fill=accent)
    parts = [p for p in (meta.get("author"), meta.get("date")) if p]
    if parts:
        _draw_mixed(draw, _wrap_mixed("     ".join(parts), "modern", 42,
                                      PAGE_W - 2 * margin),
                    margin, fy + 40, _rgb(colors.text), base_size=42)
    if meta.get("confidentiality"):
        _draw_mixed(draw, _wrap_mixed(meta["confidentiality"], "modern", 36,
                                      PAGE_W - 2 * margin, bold=True),
                    margin, fy + 130, accent, base_size=36)


# ---------------------------------------------------------------------------
# Style: halftone (dot-gradient field — energetic, contemporary)
# ---------------------------------------------------------------------------

def _style_halftone(img, draw, meta, colors):
    primary, secondary, accent = _rgb(colors.primary), _rgb(colors.secondary), _rgb(colors.accent)
    white = (255, 255, 255)

    draw.rectangle([0, 0, PAGE_W, 900], fill=primary)
    # halftone dots dissolving downward out of the color field
    step = 56
    for row in range(30):
        y = 900 + row * step * 0.62
        radius = max(2.0, 17 - row * 0.62)
        for col in range(-1, PAGE_W // step + 2):
            x = col * step + (step // 2 if row % 2 else 0)
            # dots thin out towards the right for a sweep effect
            if (col * 7 + row * 13) % 10 < (10 - row // 3):
                tone = primary if row < 7 else _mix(primary, white, min(0.75, row * 0.05))
                draw.ellipse([x - radius, y - radius, x + radius, y + radius],
                             fill=tone)
    # accent chip
    draw.rectangle([PAGE_W - 480, 690, PAGE_W - 130, 900], fill=accent)

    margin = 130
    org = (meta.get("organization") or "").upper()
    if org:
        org_lines = _wrap_mixed(org, "modern", 44, PAGE_W - 2 * margin, bold=True)
        _draw_mixed(draw, org_lines, margin, 118, white, base_size=44)

    year = _extract_year(meta)
    if year:
        f_year = _font("modern", 120, bold=True)
        draw.text((PAGE_W - 455, 730), year, font=f_year, fill=white)

    lines = _wrap_mixed(meta.get("title", ""), "modern", 118,
                        PAGE_W - 2 * margin, bold=True)
    y = _draw_mixed(draw, lines, margin, 1490, primary, base_size=118)
    if meta.get("subtitle"):
        y += 26
        sub = _wrap_mixed(meta["subtitle"], "modern", 54, PAGE_W - 2 * margin)
        y = _draw_mixed(draw, sub, margin, y, secondary, base_size=54)

    fy = PAGE_H - 300
    draw.rectangle([margin, fy, margin + 700, fy + 6], fill=accent)
    parts = [p for p in (meta.get("author"), meta.get("date")) if p]
    if parts:
        _draw_mixed(draw, _wrap_mixed("     ".join(parts), "modern", 42,
                                      PAGE_W - 2 * margin),
                    margin, fy + 40, _rgb(colors.text), base_size=42)
    if meta.get("confidentiality"):
        _draw_mixed(draw, _wrap_mixed(meta["confidentiality"], "modern", 36,
                                      PAGE_W - 2 * margin, bold=True),
                    margin, fy + 130, accent, base_size=36)


# ---------------------------------------------------------------------------
# Full-page section opener — chapters open like poster spreads, not strips
# ---------------------------------------------------------------------------

def render_section_opener(number: str, title: str, colors,
                          motif: str = "stars") -> io.BytesIO:
    """A full designed chapter-opening page: deep color field, a giant ghost
    numeral, a decorative motif, and the chapter title in display type."""
    primary = _rgb(colors.primary)
    accent = _rgb(colors.accent)
    white = (255, 255, 255)

    img = Image.new("RGB", (PAGE_W, PAGE_H), primary)
    draw = ImageDraw.Draw(img)

    if motif == "stars":
        step = 260
        for row in range(-1, PAGE_H // step + 2):
            for col in range(-1, PAGE_W // step + 2):
                cx = col * step + (step // 2 if row % 2 else 0)
                cy = row * step
                _star8(draw, cx, cy, 70, 28,
                       _mix(primary, white, 0.05 if (row + col) % 2 else 0.03),
                       rot=math.pi / 8)
    elif motif == "contours":
        fx, fy = PAGE_W + 220, PAGE_H + 180
        for i in range(30):
            r = 260 + i * 120
            draw.ellipse([fx - r, fy - r * 0.9, fx + r, fy + r * 0.9],
                         outline=_mix(primary, white, 0.07), width=4)
    else:  # "grid"
        for x in range(0, PAGE_W, 190):
            draw.line([(x, 0), (x, PAGE_H)], fill=_mix(primary, white, 0.04), width=2)
        for y in range(0, PAGE_H, 190):
            draw.line([(0, y), (PAGE_W, y)], fill=_mix(primary, white, 0.04), width=2)

    # giant ghost numeral bleeding off the right edge
    f_ghost = _font("modern", 1150, bold=True)
    draw.text((PAGE_W - f_ghost.getlength(number) + 210, 180), number,
              font=f_ghost, fill=_mix(primary, white, 0.10))
    # crisp accent numeral echo
    f_num = _font("modern", 150, bold=True)
    draw.text((150, 300), number, font=f_num, fill=_rgb(colors.accent))
    draw.rectangle([150, 500, 470, 512], fill=accent)

    # chapter title in display scale on the lower half
    lines = _wrap_mixed(title or "", "modern", 132, PAGE_W - 340, bold=True)
    y = _draw_mixed(draw, lines, 150, 1280, white, base_size=132)
    # thin closing rule
    draw.rectangle([150, y + 60, 620, y + 66], fill=_mix(primary, white, 0.35))

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf


_STYLES = {"diagonal": _style_diagonal, "blocks": _style_blocks, "frame": _style_frame,
           "executive_light": _style_executive_light,
           "executive_dark": _style_executive_dark,
           "geometric": _style_geometric, "contours": _style_contours,
           "halftone": _style_halftone}


def render_cover_png(art_style: str, meta: dict, colors) -> io.BytesIO:
    """meta keys: title, subtitle, organization, author, date, confidentiality."""
    img = Image.new("RGB", (PAGE_W, PAGE_H), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    painter = _STYLES.get(art_style, _style_diagonal)
    painter(img, draw, meta, colors)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf
