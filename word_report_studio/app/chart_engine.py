"""
chart_engine.py
----------------
Offline, template-theme-aware visual generator. Renders charts, timelines,
and process diagrams as high-resolution PNG images (in memory, no temp
files) using matplotlib, so docx_renderer.py can embed them directly.

Every visual is colored from the active template's ColorSpec so all charts
in a generated option match that option's palette — this is what keeps the
output looking like one designer made the whole document.

Arabic text in chart labels is shaped with arabic_reshaper + python-bidi
(matplotlib itself does not do bidi/joining). If those packages are missing
the text still renders, just unjoined — generation never fails because of it.
"""

from __future__ import annotations
import io
from typing import List, Optional, Sequence, Tuple

try:
    import matplotlib
    matplotlib.use("Agg")  # offline, headless
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch
    _HAS_MATPLOTLIB = True
except ImportError:  # pragma: no cover - optional dependency path
    matplotlib = None
    plt = None
    FancyBboxPatch = None
    _HAS_MATPLOTLIB = False

from .structure_model import detect_lang

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
    _HAS_SHAPING = True
except ImportError:  # pragma: no cover - optional dependency
    _HAS_SHAPING = False

# Fonts with Arabic coverage available on a stock Windows/Office machine.
_FONT_STACK = ["Segoe UI", "Tahoma", "Arial", "DejaVu Sans"]

DPI = 180


def is_available() -> bool:
    """Return whether PNG chart rendering dependencies are installed."""
    return _HAS_MATPLOTLIB


def _require_matplotlib():
    if not _HAS_MATPLOTLIB:
        raise RuntimeError(
            "matplotlib is not installed; install requirements.txt to enable charts"
        )


def shape_text(text: str) -> str:
    """Make Arabic render joined and right-to-left inside matplotlib."""
    if not text or not _HAS_SHAPING:
        return text
    if detect_lang(text) != "ar" and not any("؀" <= c <= "ۿ" for c in text):
        return text
    try:
        return get_display(arabic_reshaper.reshape(text))
    except Exception:
        return text


def _hex(c: str) -> str:
    return c if c.startswith("#") else f"#{c}"


def _tint(hex_color: str, factor: float) -> str:
    """Mix a hex color toward white. factor=0 -> original, 1 -> white."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    r = round(r + (255 - r) * factor)
    g = round(g + (255 - g) * factor)
    b = round(b + (255 - b) * factor)
    return f"#{r:02X}{g:02X}{b:02X}"


def build_palette(colors) -> List[str]:
    """Ordered series palette derived from a template ColorSpec."""
    base = [_hex(colors.secondary), _hex(colors.primary), _hex(colors.accent)]
    extra = [_tint(base[0], 0.45), _tint(base[1], 0.45), _tint(base[2], 0.45)]
    return base + extra


_ARABIC_COVERAGE_CACHE: dict = {}


def _font_covers_arabic(family: str) -> bool:
    """matplotlib picks ONE font per text object (no per-glyph fallback to
    the rest of the stack), so a template display font without Arabic glyphs
    silently erases the Arabic half of bilingual labels. Check the actual
    cmap before trusting a font with label duty."""
    if family in _ARABIC_COVERAGE_CACHE:
        return _ARABIC_COVERAGE_CACHE[family]
    try:
        from matplotlib import font_manager
        from matplotlib.ft2font import FT2Font
        path = font_manager.findfont(
            font_manager.FontProperties(family=family), fallback_to_default=False)
        font = FT2Font(path)
        # shape_text() reshapes Arabic into Presentation Forms (U+FE70-FEFF),
        # so base-block coverage alone is not enough — probe both. Lusail,
        # for instance, has base Arabic but no presentation forms, which
        # blanked every Arabic chart label until this checked the right block.
        covers = (font.get_char_index(0x0628) != 0      # ب base BEH
                  and font.get_char_index(0xFE91) != 0)  # ﺑ BEH INITIAL FORM
    except Exception:
        covers = False
    _ARABIC_COVERAGE_CACHE[family] = covers
    return covers


def _contains_arabic(*texts) -> bool:
    return any("؀" <= ch <= "ۿ" for t in texts if t for ch in str(t))


def _set_font_stack(font_family: Optional[str], needs_arabic: bool):
    plt.rcParams["font.family"] = "sans-serif"
    if font_family and needs_arabic and not _font_covers_arabic(font_family):
        font_family = None  # keep Arabic labels over template typography
    plt.rcParams["font.sans-serif"] = ([font_family] if font_family else []) + _FONT_STACK


def _new_figure(width_in: float = 6.4, height_in: float = 3.4,
                font_family: Optional[str] = None,
                needs_arabic: bool = False):
    _require_matplotlib()
    _set_font_stack(font_family, needs_arabic)
    fig, ax = plt.subplots(figsize=(width_in, height_in), dpi=DPI)
    fig.patch.set_facecolor("white")
    return fig, ax


def _style_axes(ax, colors):
    """Design-system chart chrome: no box, hairline grid, axis in a muted
    shade of the template's primary color."""
    axis_c = _tint(_hex(colors.primary), 0.45)
    label_c = _tint(_hex(colors.primary), 0.15)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color(axis_c)
    ax.tick_params(colors=label_c, labelsize=9)
    ax.yaxis.grid(True, color="#E9ECF1", linewidth=0.8)
    ax.set_axisbelow(True)


def _finish(fig, title: Optional[str], colors) -> io.BytesIO:
    if title:
        fig.suptitle(shape_text(title), fontsize=12, fontweight="bold",
                     color=_hex(colors.primary), fontfamily="sans-serif")
    fig.tight_layout(rect=(0, 0, 1, 0.94) if title else None)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=DPI, bbox_inches="tight",
                facecolor="white", pad_inches=0.15)
    plt.close(fig)
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

def render_chart(chart_type: str, categories: Sequence[str],
                 series: Sequence[Tuple[str, Sequence[float]]],
                 title: Optional[str], colors,
                 font_family: Optional[str] = None) -> io.BytesIO:
    """series: [(series_name, [values aligned to categories]), ...]"""
    _require_matplotlib()
    chart_type = (chart_type or "bar").lower()
    palette = build_palette(colors)
    needs_arabic = _contains_arabic(title, *categories,
                                    *(name for name, _v in series))
    cats = [shape_text(c) for c in categories]

    if chart_type in ("pie", "donut"):
        _set_font_stack(font_family, needs_arabic)
        return _render_pie(cats, series, title, colors, palette,
                           donut=(chart_type == "donut"))

    if chart_type in ("pictogram", "progress", "funnel", "versus"):
        _set_font_stack(font_family, needs_arabic)
        values = list(series[0][1]) if series else []
        raw_cats = list(categories)
        if chart_type == "pictogram":
            return _render_pictogram(raw_cats, values, title, colors, palette)
        if chart_type == "progress":
            return _render_progress(raw_cats, values, title, colors, palette)
        if chart_type == "funnel":
            return _render_funnel(raw_cats, values, title, colors, palette)
        return _render_versus(raw_cats, values, title, colors, palette)

    fig, ax = _new_figure(font_family=font_family, needs_arabic=needs_arabic)
    _style_axes(ax, colors)
    n_series = max(len(series), 1)
    x = range(len(cats))

    if chart_type in ("bar", "column"):
        width = 0.72 / n_series
        for si, (name, values) in enumerate(series):
            offs = [xi + si * width - (0.72 - width) / 2 for xi in x]
            bars = ax.bar(offs, list(values), width=width,
                          color=palette[si % len(palette)],
                          label=shape_text(name) if name else None, zorder=3)
            if n_series == 1 and len(values) <= 12:
                ax.bar_label(bars, fmt="%g", fontsize=8, color="#444444", padding=2)
        ax.set_xticks(list(x))
        ax.set_xticklabels(cats)

    elif chart_type in ("line", "area"):
        for si, (name, values) in enumerate(series):
            color = palette[si % len(palette)]
            ax.plot(list(x), list(values), marker="o", markersize=4.5,
                    linewidth=2.2, color=color,
                    label=shape_text(name) if name else None, zorder=3)
            if chart_type == "area":
                ax.fill_between(list(x), list(values), alpha=0.18, color=color, zorder=2)
        ax.set_xticks(list(x))
        ax.set_xticklabels(cats)

    else:  # unknown type -> horizontal bar fallback, still renders something
        name, values = series[0] if series else ("", [])
        ax.barh(list(x), list(values), color=palette[0], zorder=3)
        ax.set_yticks(list(x))
        ax.set_yticklabels(cats)

    if any(name for name, _ in series) and len(series) > 1:
        ax.legend(frameon=False, fontsize=9, loc="upper left")
    return _finish(fig, title, colors)


def _render_pie(cats, series, title, colors, palette, donut: bool) -> io.BytesIO:
    _require_matplotlib()
    values = list(series[0][1]) if series else []
    fig, ax = plt.subplots(figsize=(5.2, 3.6), dpi=DPI)
    fig.patch.set_facecolor("white")
    wedge_props = {"width": 0.42, "edgecolor": "white", "linewidth": 2} if donut \
        else {"edgecolor": "white", "linewidth": 2}
    total = sum(values) or 1
    ax.pie(values, labels=cats, colors=palette[: max(len(values), 1)] * 4,
           autopct=lambda p: f"{p:.0f}%" if p >= 4 else "",
           pctdistance=0.79 if donut else 0.6,
           textprops={"fontsize": 9, "color": "#333333",
                      "fontfamily": "sans-serif"},
           wedgeprops=wedge_props, startangle=90, counterclock=False)
    if donut:
        ax.text(0, 0, f"{total:g}", ha="center", va="center", fontsize=13,
                fontweight="bold", color=_hex(colors.primary))
    ax.set_aspect("equal")
    return _finish(fig, title, colors)


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------

def render_timeline(items: Sequence[Tuple[str, str]], title: Optional[str],
                    colors) -> io.BytesIO:
    """items: [(marker e.g. '2021' or 'Q1', description), ...]"""
    _require_matplotlib()
    n = max(len(items), 1)
    fig, ax = _new_figure(width_in=6.6, height_in=2.6)
    ax.set_xlim(-0.55, n - 0.45)
    ax.set_ylim(-1.35, 1.35)
    ax.axis("off")

    line_color = _tint(_hex(colors.secondary), 0.35)
    ax.plot([-0.3, n - 0.7], [0, 0], color=line_color, linewidth=2.5, zorder=1)

    palette = build_palette(colors)
    for i, (marker, text) in enumerate(items):
        color = palette[i % 3]
        ax.scatter([i], [0], s=170, color=color, zorder=3, edgecolors="white",
                   linewidths=2)
        above = i % 2 == 0
        y_lab, y_txt = (0.42, 0.86) if above else (-0.52, -0.98)
        ax.text(i, y_lab, shape_text(marker), ha="center",
                va="bottom" if above else "top",
                fontsize=10.5, fontweight="bold", color=_hex(colors.primary))
        ax.text(i, y_txt, _wrap_shaped(text, 18), ha="center",
                va="bottom" if above else "top",
                fontsize=8.5, color="#444444")
    return _finish(fig, title, colors)


# ---------------------------------------------------------------------------
# Process / workflow diagram
# ---------------------------------------------------------------------------

def render_process(steps: Sequence[str], title: Optional[str], colors) -> io.BytesIO:
    _require_matplotlib()
    n = max(len(steps), 1)
    fig, ax = _new_figure(width_in=6.6, height_in=1.7 if n <= 5 else 2.2)
    ax.set_xlim(0, n)
    ax.set_ylim(0, 1)
    ax.axis("off")

    palette = build_palette(colors)
    box_w, gap = 0.82, 0.18
    for i, step in enumerate(steps):
        x0 = i + gap / 2
        color = palette[i % 3]
        box = FancyBboxPatch((x0, 0.28), box_w, 0.44,
                             boxstyle="round,pad=0.02,rounding_size=0.06",
                             linewidth=0, facecolor=color, zorder=3)
        ax.add_patch(box)
        ax.text(x0 + box_w / 2, 0.5, _wrap_shaped(step, 14),
                ha="center", va="center", fontsize=9, fontweight="bold",
                color="white", zorder=4)
        ax.text(x0 + 0.075, 0.66, str(i + 1), ha="center", va="center",
                fontsize=8, color="white", alpha=0.75, zorder=4)
        if i < n - 1:
            ax.annotate("", xy=(x0 + box_w + gap * 0.85, 0.5),
                        xytext=(x0 + box_w + gap * 0.15, 0.5),
                        arrowprops={"arrowstyle": "-|>", "color": "#8A8A8A",
                                    "linewidth": 1.6}, zorder=2)
    return _finish(fig, title, colors)


# ---------------------------------------------------------------------------
# Advanced infographic types — pictogram, progress, funnel, versus
# ---------------------------------------------------------------------------

def _render_pictogram(cats, values, title, colors, palette) -> io.BytesIO:
    """Classic infographic: rows of person icons, filled in proportion to
    each value (percentages fill n/10 of 10 icons; other scales normalize
    to the largest value)."""
    from . import icon_library
    n = max(len(cats), 1)
    is_pct = values and all(0 <= v <= 100 for v in values)
    scale = 100.0 if is_pct else (max(values) if values else 1) or 1

    fig, ax = plt.subplots(figsize=(6.2, 0.62 * n + 0.5), dpi=DPI)
    fig.patch.set_facecolor("white")
    ax.set_xlim(0, 15.5)
    ax.set_ylim(0, n)
    ax.set_aspect("equal")
    ax.axis("off")
    dim = _tint(_hex(colors.primary), 0.87)

    for i, (cat, val) in enumerate(zip(cats, values)):
        y = n - i - 0.5
        color = palette[i % 3]
        ax.text(4.0, y, _wrap_shaped(str(cat), 26), ha="right", va="center",
                fontsize=8.5, color=_tint(_hex(colors.primary), 0.15))
        filled = val / scale * 10.0
        for k in range(10):
            frac = min(max(filled - k, 0.0), 1.0)
            icon_color = color if frac >= 0.5 else dim
            icon_library.draw_icon(ax, "person", 4.75 + k * 0.78, y, 0.62,
                                   icon_color)
        label = f"{val:g}%" if is_pct else f"{val:g}"
        ax.text(12.9, y, label, ha="left", va="center", fontsize=11,
                fontweight="bold", color=_hex(colors.primary))
    return _finish(fig, title, colors)


def _render_progress(cats, values, title, colors, palette) -> io.BytesIO:
    """Rounded progress bars with the value at the bar tip. Percentages run
    against 100; other scales normalize to the largest value."""
    from matplotlib.patches import FancyBboxPatch
    n = max(len(cats), 1)
    is_pct = values and all(0 <= v <= 100 for v in values)
    scale = 100.0 if is_pct else (max(values) if values else 1) or 1

    fig, ax = plt.subplots(figsize=(6.2, 0.58 * n + 0.5), dpi=DPI)
    fig.patch.set_facecolor("white")
    ax.set_xlim(0, 14)
    ax.set_ylim(0, n)
    ax.axis("off")

    bar_x, bar_w, bar_h = 4.3, 8.0, 0.30
    for i, (cat, val) in enumerate(zip(cats, values)):
        y = n - i - 0.5
        color = palette[i % 3]
        ax.text(4.0, y, _wrap_shaped(str(cat), 26), ha="right", va="center",
                fontsize=8.5, color=_tint(_hex(colors.primary), 0.15))
        ax.add_patch(FancyBboxPatch(
            (bar_x, y - bar_h / 2), bar_w, bar_h,
            boxstyle=f"round,pad=0,rounding_size={bar_h / 2}",
            color=_tint(_hex(colors.primary), 0.90), zorder=3))
        w = max(bar_w * (val / scale), bar_h)
        ax.add_patch(FancyBboxPatch(
            (bar_x, y - bar_h / 2), w, bar_h,
            boxstyle=f"round,pad=0,rounding_size={bar_h / 2}",
            color=color, zorder=4))
        label = f"{val:g}%" if is_pct else f"{val:g}"
        ax.text(bar_x + bar_w + 0.25, y, label, ha="left", va="center",
                fontsize=10.5, fontweight="bold", color=_hex(colors.primary))
    return _finish(fig, title, colors)


def _render_funnel(cats, values, title, colors, palette) -> io.BytesIO:
    """Centered funnel: each stage's width is proportional to its value —
    pipelines, conversion stages, recruitment rounds."""
    from matplotlib.patches import Polygon
    n = max(len(cats), 1)
    top = max(values) if values else 1

    fig, ax = plt.subplots(figsize=(6.0, 0.66 * n + 0.5), dpi=DPI)
    fig.patch.set_facecolor("white")
    ax.set_xlim(0, 10)
    ax.set_ylim(0, n)
    ax.axis("off")

    widths = [max((v / top) * 7.6, 1.6) for v in values] + [1.2]
    for i, (cat, val) in enumerate(zip(cats, values)):
        y1, y0 = n - i, n - i - 0.88
        w_top, w_bot = widths[i], widths[i + 1] if i + 1 < len(widths) else widths[i]
        cx = 5.0
        ax.add_patch(Polygon([
            (cx - w_top / 2, y1), (cx + w_top / 2, y1),
            (cx + w_bot / 2, y0), (cx - w_bot / 2, y0)],
            closed=True, color=palette[i % 3], zorder=3))
        ax.text(cx, (y0 + y1) / 2, f"{val:g}", ha="center", va="center",
                fontsize=11.5, fontweight="bold", color="white", zorder=4)
        ax.text(9.9, (y0 + y1) / 2, _wrap_shaped(str(cat), 18), ha="right",
                va="center", fontsize=8.5,
                color=_tint(_hex(colors.primary), 0.15))
    return _finish(fig, title, colors)


def _render_versus(cats, values, title, colors, palette) -> io.BytesIO:
    """A-vs-B comparison: two bold panels with a VS medallion between.
    With more than two categories, falls back to bars."""
    if len(cats) != 2 or len(values) != 2:
        fig, ax = _new_figure()
        _style_axes(ax, colors)
        ax.bar(range(len(cats)), values,
               color=[palette[i % 3] for i in range(len(cats))], zorder=3)
        ax.set_xticks(range(len(cats)))
        ax.set_xticklabels([shape_text(str(c)) for c in cats])
        return _finish(fig, title, colors)

    from matplotlib.patches import FancyBboxPatch, Circle
    fig, ax = plt.subplots(figsize=(6.0, 2.3), dpi=DPI)
    fig.patch.set_facecolor("white")
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 4)
    ax.axis("off")

    for i, (cat, val, x0) in enumerate(((cats[0], values[0], 0.3),
                                        (cats[1], values[1], 5.3))):
        color = palette[i % 3]
        ax.add_patch(FancyBboxPatch((x0, 0.5), 4.4, 3.0,
                                    boxstyle="round,pad=0,rounding_size=0.25",
                                    color=_tint(color, 0.90), zorder=2))
        ax.add_patch(FancyBboxPatch((x0, 3.28), 4.4, 0.22,
                                    boxstyle="round,pad=0,rounding_size=0.10",
                                    color=color, zorder=3))
        ax.text(x0 + 2.2, 2.35, f"{val:g}", ha="center", va="center",
                fontsize=26, fontweight="bold", color=_hex(colors.primary),
                zorder=4)
        ax.text(x0 + 2.2, 1.15, _wrap_shaped(str(cat), 22), ha="center",
                va="center", fontsize=9,
                color=_tint(_hex(colors.primary), 0.2), zorder=4)

    ax.add_patch(Circle((5.0, 2.0), 0.55, color=_hex(colors.accent), zorder=5))
    ax.text(5.0, 2.0, "VS", ha="center", va="center", fontsize=12,
            fontweight="bold", color="white", zorder=6)
    return _finish(fig, title, colors)


# ---------------------------------------------------------------------------
# Infographic KPI strip — gauges for percentages, icon badges for counts
# ---------------------------------------------------------------------------

def _parse_pct(value: str) -> Optional[float]:
    import re
    m = re.match(r"^\s*[+-]?(\d+(?:\.\d+)?)\s*[%٪]\s*$", value.strip())
    if not m:
        return None
    v = float(m.group(1))
    return v if 0 <= v <= 100 else None


def render_kpi_strip(items: Sequence[Tuple[str, str, str]], colors,
                     font_family: Optional[str] = None) -> io.BytesIO:
    """Designer infographic for KPI figures.
    items: [(value_str, label, icon_key), ...] — '87%' becomes a donut gauge
    with the number inside; a count becomes an icon badge + big number.
    Everything is themed from the template palette."""
    _require_matplotlib()
    from . import icon_library

    items = list(items)[:4]
    n = max(len(items), 1)
    needs_ar = _contains_arabic(*(lbl for _v, lbl, _i in items))
    _set_font_stack(font_family, needs_ar)

    fig_w = min(6.6, 1.75 * n + 0.4)
    fig, ax = plt.subplots(figsize=(fig_w, 2.35), dpi=DPI)
    fig.patch.set_facecolor("white")
    ax.set_xlim(0, n)
    ax.set_ylim(0, 1)
    ax.set_aspect(1.0 / (n / fig_w * 2.35))  # keep circles circular
    ax.axis("off")

    palette = build_palette(colors)
    from matplotlib.patches import Circle, Wedge

    for i, (value, label, icon_key) in enumerate(items):
        cx = i + 0.5
        cy = 0.66
        color = palette[i % 3]
        ring_bg = _tint(color, 0.85)
        pct = _parse_pct(value)
        R, rw = 0.30, 0.075

        if pct is not None:
            # donut gauge with the number in the middle
            ax.add_patch(Wedge((cx, cy), R, 0, 360, width=rw,
                               facecolor=ring_bg, zorder=3))
            ax.add_patch(Wedge((cx, cy), R, 90 - 3.6 * pct, 90, width=rw,
                               facecolor=color, zorder=4))
            ax.text(cx, cy + 0.015, shape_text(value.strip()), ha="center",
                    va="center", fontsize=15, fontweight="bold",
                    color=_hex(colors.primary), zorder=5)
            topic = icon_library.topic_icon(label)
            if topic:
                ax.add_patch(Circle((cx + R * 0.82, cy - R * 0.82), 0.085,
                                    color=color, zorder=6))
                icon_library.draw_icon(ax, topic, cx + R * 0.82, cy - R * 0.82,
                                       0.115, "white", detail=color)
        else:
            # icon badge above a big number
            badge_key = (icon_key if icon_library.has_icon(icon_key or "")
                         and icon_key not in ("dot", "flat", "none") else None)
            badge_key = badge_key or icon_library.topic_icon(label) or "chart"
            ax.add_patch(Circle((cx, cy + 0.10), 0.155, color=ring_bg, zorder=3))
            icon_library.draw_icon(ax, badge_key, cx, cy + 0.10, 0.21, color,
                                   detail=ring_bg)
            ax.text(cx, cy - 0.20, shape_text(value.strip()), ha="center",
                    va="center", fontsize=16, fontweight="bold",
                    color=_hex(colors.primary), zorder=5)

        ax.text(cx, 0.16, _wrap_shaped(label, 18), ha="center", va="center",
                fontsize=7.5, color=_tint(_hex(colors.primary), 0.25), zorder=5)

    fig.tight_layout(pad=0.4)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=DPI, bbox_inches="tight",
                facecolor="white", pad_inches=0.12)
    plt.close(fig)
    buf.seek(0)
    return buf


def render_icon_badge(icon_key: str, color_hex: str,
                      size_in: float = 0.55) -> io.BytesIO:
    """A single themed icon badge (white icon on a colored disc) — used on
    section divider bands."""
    _require_matplotlib()
    from . import icon_library
    from matplotlib.patches import Circle
    fig, ax = plt.subplots(figsize=(size_in, size_in), dpi=DPI)
    fig.patch.set_alpha(0)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.add_patch(Circle((0.5, 0.5), 0.48, color=_hex(color_hex), zorder=2))
    icon_library.draw_icon(ax, icon_key, 0.5, 0.5, 0.55, "white",
                           detail=_hex(color_hex))
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=DPI, transparent=True, pad_inches=0)
    plt.close(fig)
    buf.seek(0)
    return buf


def _wrap_shaped(text: str, width: int) -> str:
    """Wrap on the LOGICAL text first, then bidi-shape each line separately.
    Shaping before wrapping scrambles word order in mixed AR/EN labels,
    because bidi reordering is only valid within a single rendered line."""
    wrapped = _wrap(text, width)
    return "\n".join(shape_text(line) for line in wrapped.split("\n"))


def _wrap(text: str, width: int) -> str:
    """Naive word wrap for labels inside fixed-size shapes."""
    words = text.split()
    lines, current = [], ""
    for w in words:
        if current and len(current) + 1 + len(w) > width:
            lines.append(current)
            current = w
        else:
            current = f"{current} {w}".strip()
    if current:
        lines.append(current)
    return "\n".join(lines[:3])
