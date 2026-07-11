"""
structure_model.py
-------------------
Language-agnostic, template-agnostic representation of a long report.
content_parser.py builds these objects from raw input; docx_renderer.py
consumes them to produce a styled .docx file. Nothing here knows about
Word, python-docx, or any specific template.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


# ---------------------------------------------------------------------------
# Language helpers
# ---------------------------------------------------------------------------

ARABIC_RANGES = (
    (0x0600, 0x06FF),   # Arabic
    (0x0750, 0x077F),   # Arabic Supplement
    (0x08A0, 0x08FF),   # Arabic Extended-A
    (0xFB50, 0xFDFF),   # Arabic Presentation Forms-A
    (0xFE70, 0xFEFF),   # Arabic Presentation Forms-B
)


def is_arabic_char(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in ARABIC_RANGES)


def detect_lang(text: str) -> str:
    """Return 'ar' if the majority of letter characters are Arabic, else 'en'."""
    ar = sum(1 for c in text if is_arabic_char(c))
    other = sum(1 for c in text if c.isalpha() and not is_arabic_char(c))
    if ar == 0 and other == 0:
        return "en"
    return "ar" if ar >= other else "en"


class LangMode(str, Enum):
    ENGLISH = "en"
    ARABIC = "ar"
    BILINGUAL = "bilingual"   # document mixes both; each block/run tagged individually


# ---------------------------------------------------------------------------
# Inline content
# ---------------------------------------------------------------------------

@dataclass
class Run:
    text: str
    lang: str = "en"          # 'ar' or 'en', auto-detected if not supplied
    bold: bool = False
    italic: bool = False
    underline: bool = False
    link: Optional[str] = None

    def __post_init__(self):
        if not self.lang:
            self.lang = detect_lang(self.text)


def runs_from_text(text: str, **kwargs) -> List[Run]:
    """Split a raw string into per-language runs so RTL/LTR shaping is correct
    even inside a single mixed-language sentence."""
    if not text:
        return [Run(text="", **kwargs)]
    runs: List[Run] = []
    current = text[0]
    current_lang = detect_lang(text[0]) if text[0].isalpha() else None
    for ch in text[1:]:
        ch_lang = detect_lang(ch) if ch.isalpha() else current_lang
        if ch_lang == current_lang or not ch.isalpha():
            current += ch
        else:
            runs.append(Run(text=current, lang=current_lang or "en", **kwargs))
            current = ch
            current_lang = ch_lang
    runs.append(Run(text=current, lang=current_lang or "en", **kwargs))
    return runs


# ---------------------------------------------------------------------------
# Block-level content
# ---------------------------------------------------------------------------

class BlockType(str, Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    TABLE = "table"
    IMAGE = "image"
    QUOTE = "quote"
    PAGE_BREAK = "page_break"
    CALLOUT = "callout"       # highlighted box, useful for consulting-style templates
    KPI = "kpi"               # row of KPI stat cards (value + label + trend icon)
    CHART = "chart"           # data-driven chart rendered by chart_engine
    TIMELINE = "timeline"     # milestone timeline diagram
    PROCESS = "process"       # step-by-step process/workflow diagram


@dataclass
class Block:
    type: BlockType


@dataclass
class Heading(Block):
    level: int = 1             # 1-4
    runs: List[Run] = field(default_factory=list)
    type: BlockType = BlockType.HEADING


@dataclass
class Paragraph(Block):
    runs: List[Run] = field(default_factory=list)
    type: BlockType = BlockType.PARAGRAPH


@dataclass
class ListBlock(Block):
    items: List[List[Run]] = field(default_factory=list)
    ordered: bool = False
    type: BlockType = BlockType.LIST


@dataclass
class TableBlock(Block):
    rows: List[List[List[Run]]] = field(default_factory=list)   # rows -> cells -> runs
    header_row: bool = True
    caption: Optional[str] = None
    type: BlockType = BlockType.TABLE


@dataclass
class ImageBlock(Block):
    path: str = ""
    caption: Optional[str] = None
    width_inches: Optional[float] = None
    type: BlockType = BlockType.IMAGE


@dataclass
class Quote(Block):
    runs: List[Run] = field(default_factory=list)
    attribution: Optional[str] = None
    type: BlockType = BlockType.QUOTE


@dataclass
class Callout(Block):
    runs: List[Run] = field(default_factory=list)
    title: Optional[str] = None
    tone: str = "info"        # info | warning | success
    type: BlockType = BlockType.CALLOUT


@dataclass
class PageBreak(Block):
    type: BlockType = BlockType.PAGE_BREAK


# ---------------------------------------------------------------------------
# Designer-grade visual blocks
# ---------------------------------------------------------------------------

# icon keyword -> (glyph, semantic tone). Tones map to template colors at
# render time: good -> success green, bad -> accent/red, neutral -> secondary.
KPI_ICONS = {
    "up":        ("▲", "good"),
    "up-bad":    ("▲", "bad"),      # e.g. costs went up
    "down":      ("▼", "bad"),
    "down-good": ("▼", "good"),    # e.g. complaints went down
    "star":      ("★", "neutral"),
    "dot":       ("●", "neutral"),
    "flat":      ("■", "neutral"),
    "check":     ("✔", "good"),
    "warn":      ("!", "bad"),
    "none":      ("", "neutral"),
    # topical icons: rendered as real vector art in the infographic KPI
    # strip (icon_library); the glyphs here are only the plain-text
    # fallback used by the editable native card table
    "shield":    ("▣", "neutral"),
    "person":    ("●", "neutral"),
    "people":    ("●", "neutral"),
    "money":     ("◆", "neutral"),
    "growth":    ("▲", "good"),
    "clock":     ("◔", "neutral"),
    "target":    ("◎", "neutral"),
    "gear":      ("✱", "neutral"),
    "doc":       ("▤", "neutral"),
    "lock":      ("▪", "neutral"),
    "building":  ("▦", "neutral"),
    "chart":     ("▥", "neutral"),
}


@dataclass
class KpiItem:
    value: str                 # the big number, e.g. "24%" or "5.2M"
    label: str                 # short description under the number
    icon: str = "none"         # key into KPI_ICONS
    lang: str = ""

    def __post_init__(self):
        if not self.lang:
            self.lang = detect_lang(self.label)


@dataclass
class KpiBlock(Block):
    items: List[KpiItem] = field(default_factory=list)
    type: BlockType = BlockType.KPI


@dataclass
class ChartBlock(Block):
    chart_type: str = "bar"    # bar | column | line | area | pie | donut
    title: Optional[str] = None
    categories: List[str] = field(default_factory=list)
    # each series: (name, values aligned with categories)
    series: List[tuple] = field(default_factory=list)
    caption: Optional[str] = None
    type: BlockType = BlockType.CHART


@dataclass
class TimelineBlock(Block):
    items: List[tuple] = field(default_factory=list)   # (marker, description)
    title: Optional[str] = None
    type: BlockType = BlockType.TIMELINE


@dataclass
class ProcessBlock(Block):
    steps: List[str] = field(default_factory=list)
    title: Optional[str] = None
    type: BlockType = BlockType.PROCESS


# ---------------------------------------------------------------------------
# Document-level content
# ---------------------------------------------------------------------------

@dataclass
class Section:
    title: str
    blocks: List[Block] = field(default_factory=list)
    subsections: List["Section"] = field(default_factory=list)
    lang: Optional[str] = None   # override; else inferred from title


@dataclass
class DocumentMeta:
    title: str = "Untitled Report"
    subtitle: Optional[str] = None
    author: Optional[str] = None
    organization: Optional[str] = None
    date: Optional[str] = None
    confidentiality: Optional[str] = None   # e.g. "Confidential", "سري"
    lang_mode: Optional[LangMode] = LangMode.BILINGUAL   # None = auto-detect from content


@dataclass
class ReportDocument:
    meta: DocumentMeta
    sections: List[Section] = field(default_factory=list)
    appendices: List[Section] = field(default_factory=list)
    include_toc: bool = True
    include_cover: bool = True

    def all_sections(self) -> List[Section]:
        return self.sections + self.appendices
