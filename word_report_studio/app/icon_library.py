"""
icon_library.py
----------------
Offline vector iconography. Every icon is drawn programmatically with
matplotlib patches — no image assets, no fonts to install, no network —
so icons are always available and always themed from the active template's
palette. Used by the infographic KPI strip, section-divider badges, and
anywhere else a visual needs a pictorial anchor.

All draw functions render into a normalized 0..1 box centered at (cx, cy)
with height `s`, in a color `c` on axes `ax` (aspect must be equal).
"""

from __future__ import annotations
import re
from typing import Callable, Dict, Optional

try:
    from matplotlib.patches import (Circle, FancyBboxPatch, Polygon, Rectangle,
                                    Wedge, Arc)
    from matplotlib.lines import Line2D
    import matplotlib.transforms as mtransforms
    _HAS_MPL = True
except ImportError:  # pragma: no cover
    _HAS_MPL = False


def _lw(s: float) -> float:
    """Line width scaled to icon size (s in axes/data units at DPI 180)."""
    return max(2.0, s * 26)


# ---------------------------------------------------------------------------
# Icon drawing primitives — each keeps to simple geometry so the result
# reads cleanly at 0.2-0.6 inch sizes.
# ---------------------------------------------------------------------------

def _person(ax, cx, cy, s, c, d="white"):
    ax.add_patch(Circle((cx, cy + 0.26 * s), 0.16 * s, color=c, zorder=6))
    ax.add_patch(FancyBboxPatch((cx - 0.24 * s, cy - 0.48 * s), 0.48 * s, 0.42 * s,
                                boxstyle="round,pad=0,rounding_size=" + str(0.20 * s),
                                color=c, zorder=6))


def _people(ax, cx, cy, s, c, d="white"):
    _person(ax, cx - 0.17 * s, cy - 0.03 * s, 0.72 * s, c, d)
    _person(ax, cx + 0.19 * s, cy + 0.06 * s, 0.86 * s, c, d)


def _shield(ax, cx, cy, s, c, d="white"):
    pts = [(cx, cy + 0.5 * s), (cx + 0.38 * s, cy + 0.32 * s),
           (cx + 0.38 * s, cy - 0.05 * s), (cx, cy - 0.5 * s),
           (cx - 0.38 * s, cy - 0.05 * s), (cx - 0.38 * s, cy + 0.32 * s)]
    ax.add_patch(Polygon(pts, closed=True, color=c, zorder=6))
    ax.add_line(Line2D([cx - 0.16 * s, cx - 0.04 * s, cx + 0.18 * s],
                       [cy + 0.02 * s, cy - 0.14 * s, cy + 0.16 * s],
                       lw=_lw(s), color=d, zorder=7,
                       solid_capstyle="round"))


def _check(ax, cx, cy, s, c, d="white"):
    ax.add_patch(Circle((cx, cy), 0.46 * s, color=c, zorder=6))
    ax.add_line(Line2D([cx - 0.20 * s, cx - 0.05 * s, cx + 0.24 * s],
                       [cy - 0.00 * s, cy - 0.18 * s, cy + 0.18 * s],
                       lw=_lw(s), color=d, zorder=7,
                       solid_capstyle="round"))


def _warning(ax, cx, cy, s, c, d="white"):
    pts = [(cx, cy + 0.48 * s), (cx + 0.44 * s, cy - 0.40 * s),
           (cx - 0.44 * s, cy - 0.40 * s)]
    ax.add_patch(Polygon(pts, closed=True, color=c, zorder=6))
    ax.add_line(Line2D([cx, cx], [cy + 0.22 * s, cy - 0.08 * s],
                       lw=_lw(s) * 0.9, color=d, zorder=7,
                       solid_capstyle="round"))
    ax.add_patch(Circle((cx, cy - 0.26 * s), 0.045 * s, color=d, zorder=7))


def _growth(ax, cx, cy, s, c, d="white"):
    # rising arrow over mini bars
    for i, h in enumerate((0.28, 0.44, 0.62)):
        ax.add_patch(Rectangle((cx - 0.42 * s + i * 0.30 * s, cy - 0.46 * s),
                               0.20 * s, h * s, color=c, alpha=0.55, zorder=5))
    ax.add_line(Line2D([cx - 0.40 * s, cx + 0.05 * s, cx + 0.38 * s],
                       [cy - 0.18 * s, cy + 0.10 * s, cy + 0.40 * s],
                       lw=_lw(s) * 0.8, color=c, zorder=6, solid_capstyle="round"))
    ax.add_patch(Polygon([(cx + 0.38 * s, cy + 0.44 * s),
                          (cx + 0.14 * s, cy + 0.38 * s),
                          (cx + 0.34 * s, cy + 0.20 * s)], color=c, zorder=6))


def _money(ax, cx, cy, s, c, d="white"):
    # banknote: flat note with a center-ring watermark and edge ticks
    ax.add_patch(FancyBboxPatch((cx - 0.50 * s, cy - 0.26 * s), 1.00 * s, 0.52 * s,
                                boxstyle="round,pad=0,rounding_size=" + str(0.05 * s),
                                color=c, zorder=6))
    ax.add_patch(Circle((cx, cy), 0.15 * s, fill=False, lw=_lw(s) * 0.5,
                        edgecolor=d, zorder=7))
    for dx in (-0.36, 0.36):
        ax.add_line(Line2D([cx + dx * s, cx + dx * s],
                           [cy - 0.10 * s, cy + 0.10 * s],
                           lw=_lw(s) * 0.5, color=d, zorder=7,
                           solid_capstyle="round"))


def _clock(ax, cx, cy, s, c, d="white"):
    ax.add_patch(Circle((cx, cy), 0.46 * s, color=c, zorder=6))
    ax.add_patch(Circle((cx, cy), 0.36 * s, color=d, zorder=7))
    ax.add_line(Line2D([cx, cx], [cy, cy + 0.24 * s], lw=_lw(s) * 0.7,
                       color=c, zorder=8, solid_capstyle="round"))
    ax.add_line(Line2D([cx, cx + 0.16 * s], [cy, cy], lw=_lw(s) * 0.7,
                       color=c, zorder=8, solid_capstyle="round"))


def _target(ax, cx, cy, s, c, d="white"):
    ax.add_patch(Circle((cx, cy), 0.46 * s, color=c, zorder=6))
    ax.add_patch(Circle((cx, cy), 0.32 * s, color=d, zorder=7))
    ax.add_patch(Circle((cx, cy), 0.19 * s, color=c, zorder=8))
    ax.add_patch(Circle((cx, cy), 0.07 * s, color=d, zorder=9))


def _gear(ax, cx, cy, s, c, d="white"):
    for ang in range(0, 360, 45):
        t = mtransforms.Affine2D().rotate_deg_around(cx, cy, ang) + ax.transData
        ax.add_patch(Rectangle((cx - 0.075 * s, cy + 0.26 * s), 0.15 * s, 0.20 * s,
                               color=c, zorder=5, transform=t))
    ax.add_patch(Circle((cx, cy), 0.32 * s, color=c, zorder=6))
    ax.add_patch(Circle((cx, cy), 0.13 * s, color=d, zorder=7))


def _doc(ax, cx, cy, s, c, d="white"):
    ax.add_patch(FancyBboxPatch((cx - 0.32 * s, cy - 0.46 * s), 0.64 * s, 0.92 * s,
                                boxstyle="round,pad=0,rounding_size=" + str(0.06 * s),
                                color=c, zorder=6))
    for i in range(3):
        y = cy + (0.16 - i * 0.16) * s
        ax.add_line(Line2D([cx - 0.18 * s, cx + 0.18 * s], [y, y],
                           lw=_lw(s) * 0.5, color=d, zorder=7,
                           solid_capstyle="round"))


def _lock(ax, cx, cy, s, c, d="white"):
    ax.add_patch(Arc((cx, cy + 0.10 * s), 0.44 * s, 0.50 * s, theta1=0, theta2=180,
                     lw=_lw(s) * 0.8, color=c, zorder=6))
    ax.add_patch(FancyBboxPatch((cx - 0.34 * s, cy - 0.46 * s), 0.68 * s, 0.55 * s,
                                boxstyle="round,pad=0,rounding_size=" + str(0.08 * s),
                                color=c, zorder=7))
    ax.add_patch(Circle((cx, cy - 0.16 * s), 0.07 * s, color=d, zorder=8))


def _building(ax, cx, cy, s, c, d="white"):
    ax.add_patch(Rectangle((cx - 0.36 * s, cy - 0.48 * s), 0.72 * s, 0.90 * s,
                           color=c, zorder=6))
    for r in range(3):
        for col in range(2):
            ax.add_patch(Rectangle((cx - 0.22 * s + col * 0.26 * s,
                                    cy + 0.16 * s - r * 0.26 * s),
                                   0.16 * s, 0.14 * s, color=d, zorder=7))


def _chart(ax, cx, cy, s, c, d="white"):
    for i, h in enumerate((0.35, 0.62, 0.48, 0.80)):
        ax.add_patch(Rectangle((cx - 0.42 * s + i * 0.24 * s, cy - 0.44 * s),
                               0.16 * s, h * 0.9 * s, color=c, zorder=6))


def _star(ax, cx, cy, s, c, d="white"):
    import math
    pts = []
    for i in range(10):
        r = 0.48 * s if i % 2 == 0 else 0.20 * s
        a = math.pi / 2 + i * math.pi / 5
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    ax.add_patch(Polygon(pts, closed=True, color=c, zorder=6))


def _arrow_up(ax, cx, cy, s, c, d="white"):
    ax.add_patch(Polygon([(cx, cy + 0.46 * s), (cx + 0.34 * s, cy + 0.04 * s),
                          (cx + 0.14 * s, cy + 0.04 * s), (cx + 0.14 * s, cy - 0.44 * s),
                          (cx - 0.14 * s, cy - 0.44 * s), (cx - 0.14 * s, cy + 0.04 * s),
                          (cx - 0.34 * s, cy + 0.04 * s)],
                         closed=True, color=c, zorder=6))


def _arrow_down(ax, cx, cy, s, c, d="white"):
    ax.add_patch(Polygon([(cx, cy - 0.46 * s), (cx + 0.34 * s, cy - 0.04 * s),
                          (cx + 0.14 * s, cy - 0.04 * s), (cx + 0.14 * s, cy + 0.44 * s),
                          (cx - 0.14 * s, cy + 0.44 * s), (cx - 0.14 * s, cy - 0.04 * s),
                          (cx - 0.34 * s, cy - 0.04 * s)],
                         closed=True, color=c, zorder=6))


ICONS: Dict[str, Callable] = {
    "person": _person, "people": _people, "shield": _shield, "check": _check,
    "warning": _warning, "warn": _warning, "growth": _growth, "money": _money,
    "clock": _clock, "target": _target, "gear": _gear, "doc": _doc,
    "lock": _lock, "building": _building, "chart": _chart, "star": _star,
    "up": _arrow_up, "down": _arrow_down, "down-good": _arrow_down,
    "up-bad": _arrow_up, "dot": _target, "flat": _chart,
}


def draw_icon(ax, key: str, cx: float, cy: float, size: float, color: str,
              detail: str = "white"):
    """Draw icon `key` centered at (cx, cy) with height `size` in `color`.
    `detail` is the color for internal cut-out lines — pass the surface
    color behind the icon when drawing a white icon on a colored disc,
    otherwise the details are invisible."""
    fn = ICONS.get(key)
    if fn is None:
        fn = _target
    fn(ax, cx, cy, size, color, detail)


def has_icon(key: str) -> bool:
    return key in ICONS


# ---------------------------------------------------------------------------
# Topic detection — map a title/label to an icon, bilingual
# ---------------------------------------------------------------------------

_TOPIC_RULES = [
    ("shield",   ("security", "أمن", "الأمن", "حماية", "protection", "policy",
                  "سياسة", "السياسة", "cyber", "سيبراني")),
    ("people",   ("staff", "employee", "hr", "team", "موظف", "الموظف", "موارد بشرية",
                  "الموارد البشرية", "فريق", "عملاء", "client", "customer", "عميل")),
    ("money",    ("revenue", "financ", "budget", "cost", "sales", "إيراد", "الإيراد",
                  "مالي", "المالية", "ميزانية", "تكاليف", "مبيعات")),
    ("growth",   ("growth", "performance", "trend", "نمو", "أداء", "الأداء", "اتجاه")),
    ("target",   ("goal", "objective", "target", "strategy", "هدف", "أهداف",
                  "استراتيجية", "الاستراتيجية", "خطة", "plan", "roadmap", "خارطة")),
    ("gear",     ("operation", "process", "method", "عمليات", "العمليات", "منهجية",
                  "إجراء", "الإجراءات", "تشغيل")),
    ("doc",      ("report", "document", "appendix", "methodology", "تقرير", "وثيقة",
                  "مستند", "ملحق", "منهج")),
    ("clock",    ("timeline", "schedule", "milestone", "زمني", "جدول", "محطات",
                  "مواعيد")),
    ("warning",  ("risk", "issue", "challenge", "مخاطر", "المخاطر", "تحديات",
                  "مشكلات", "تحذير")),
    ("check",    ("compliance", "quality", "audit", "امتثال", "الامتثال", "جودة",
                  "الجودة", "تدقيق", "مراجعة")),
    ("lock",     ("access", "password", "encrypt", "privacy", "وصول", "الوصول",
                  "تشفير", "خصوصية", "الخصوصية", "كلمة المرور")),
    ("building", ("office", "branch", "facility", "ministry", "مكتب", "مكاتب",
                  "فرع", "فروع", "وزارة", "الوزارة", "مقر")),
    ("chart",    ("metric", "kpi", "indicator", "statistic", "مؤشر", "مؤشرات",
                  "إحصاء")),
    ("people",   ("training", "awareness", "تدريب", "التدريب", "توعية", "التوعية")),
]


def topic_icon(text: Optional[str]) -> Optional[str]:
    """Best icon for a title/label, or None when nothing matches."""
    if not text:
        return None
    low = text.lower()
    for key, words in _TOPIC_RULES:
        if any(w in low for w in words):
            return key
    return None
