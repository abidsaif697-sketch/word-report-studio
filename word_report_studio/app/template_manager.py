"""
template_manager.py
--------------------
Loads designer-grade template *specifications* (pure metadata: colors, fonts,
page setup, cover style, table style) from templates/<id>/metadata.json.

Templates are metadata-driven rather than binary .docx files. docx_renderer.py
builds every document from scratch with python-docx using this metadata, which
is more reliable offline (no dependency on a specific Word install to author
reference .docx files) and makes it trivial to spin off color/cover variants
for the "multiple options" requirement.
"""

from __future__ import annotations
import json
import os
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any


@dataclass
class PageSpec:
    size: str = "A4"                # A4 | Letter
    margin_top_in: float = 1.0
    margin_bottom_in: float = 1.0
    margin_left_in: float = 1.0
    margin_right_in: float = 1.0
    mirror_margins: bool = False

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PageSpec":
        return cls(**{**cls().__dict__, **(d or {})})


@dataclass
class ColorSpec:
    primary: str = "1F3864"
    secondary: str = "2E74B5"
    accent: str = "C00000"
    text: str = "222222"
    muted: str = "6E6E6E"
    background: str = "FFFFFF"
    # semantic colors (trend arrows, KPI tones, callouts)
    success: str = "2E8B57"
    warning: str = "E69A00"
    error: str = "B3261E"
    info: str = "2F80ED"
    # neutral palette (cards, borders, dividers)
    surface: str = "FFFFFF"
    border: str = "D9DEE5"
    divider: str = "ECEFF3"

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ColorSpec":
        return cls(**{**cls().__dict__, **(d or {})})


@dataclass
class FontSpec:
    heading_en: str = "Calibri Light"
    body_en: str = "Calibri"
    heading_ar: str = "Traditional Arabic"
    body_ar: str = "Arial"

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "FontSpec":
        return cls(**{**cls().__dict__, **(d or {})})


@dataclass
class FontSizeSpec:
    h1: int = 28
    h2: int = 20
    h3: int = 16
    h4: int = 13
    body: int = 11
    caption: int = 9

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "FontSizeSpec":
        return cls(**{**cls().__dict__, **(d or {})})


@dataclass
class CoverSpec:
    style: str = "centered_band"    # centered_band | side_bar | minimal_top | full_bleed_footer | image_hero
    show_confidentiality: bool = True
    art: str = "diagonal"           # for image_hero covers: diagonal | blocks | frame

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CoverSpec":
        return cls(**{**cls().__dict__, **(d or {})})


@dataclass
class HeaderFooterSpec:
    show_page_numbers: bool = True
    show_section_title: bool = True
    footer_text: str = ""

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "HeaderFooterSpec":
        return cls(**{**cls().__dict__, **(d or {})})


@dataclass
class Variant:
    variant_id: str = "default"
    label: str = "Default"
    colors_override: Dict[str, str] = field(default_factory=dict)
    cover_style_override: Optional[str] = None
    art_override: Optional[str] = None    # different cover art per variant

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Variant":
        return cls(
            variant_id=d.get("variant_id", "default"),
            label=d.get("label", d.get("variant_id", "Default")),
            colors_override=d.get("colors_override", {}),
            cover_style_override=d.get("cover_style_override"),
            art_override=d.get("art_override"),
        )


@dataclass
class TemplateSpec:
    id: str
    name: str
    category: str = "corporate"
    description: str = ""
    supports_lang: List[str] = field(default_factory=lambda: ["en", "ar", "bilingual"])
    page: PageSpec = field(default_factory=PageSpec)
    colors: ColorSpec = field(default_factory=ColorSpec)
    fonts: FontSpec = field(default_factory=FontSpec)
    font_sizes: FontSizeSpec = field(default_factory=FontSizeSpec)
    cover: CoverSpec = field(default_factory=CoverSpec)
    header_footer: HeaderFooterSpec = field(default_factory=HeaderFooterSpec)
    table_style: str = "Light Grid Accent 1"
    toc: bool = True
    line_spacing: float = 1.25          # body text line height
    section_dividers: bool = False      # chapter-opener band before each top section
    variants: List[Variant] = field(default_factory=lambda: [Variant(variant_id="default", label="Default")])

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TemplateSpec":
        return cls(
            id=d["id"],
            name=d.get("name", d["id"]),
            category=d.get("category", "corporate"),
            description=d.get("description", ""),
            supports_lang=d.get("supports_lang", ["en", "ar", "bilingual"]),
            page=PageSpec.from_dict(d.get("page")),
            colors=ColorSpec.from_dict(d.get("colors")),
            fonts=FontSpec.from_dict(d.get("fonts")),
            font_sizes=FontSizeSpec.from_dict(d.get("font_sizes")),
            cover=CoverSpec.from_dict(d.get("cover")),
            header_footer=HeaderFooterSpec.from_dict(d.get("header_footer")),
            table_style=d.get("table_style", "Light Grid Accent 1"),
            toc=d.get("toc", True),
            line_spacing=d.get("line_spacing", 1.25),
            section_dividers=d.get("section_dividers", False),
            variants=[Variant.from_dict(v) for v in d.get("variants", [])] or
                      [Variant(variant_id="default", label="Default")],
        )

    def resolved_colors(self, variant: Variant) -> ColorSpec:
        base = asdict(self.colors)
        base.update(variant.colors_override or {})
        return ColorSpec(**base)

    def resolved_cover_style(self, variant: Variant) -> str:
        return variant.cover_style_override or self.cover.style

    def resolved_cover_art(self, variant: Variant) -> str:
        return variant.art_override or self.cover.art


# ---------------------------------------------------------------------------
# Built-in fallback templates (used if templates/ folder is empty/missing so
# the app always has something to render with).
# ---------------------------------------------------------------------------

def _builtin_templates() -> List[TemplateSpec]:
    return [
        TemplateSpec.from_dict({
            "id": "corporate_modern",
            "name": "Corporate Modern",
            "category": "corporate",
            "description": "Clean modern corporate report. Good all-purpose default.",
            "colors": {"primary": "1F3864", "secondary": "2E74B5", "accent": "C00000"},
            "cover": {"style": "centered_band"},
            "variants": [
                {"variant_id": "default", "label": "Navy / Modern"},
                {"variant_id": "teal", "label": "Teal Accent", "colors_override": {"primary": "0F5C5C", "secondary": "13898A", "accent": "E07A00"}},
                {"variant_id": "charcoal", "label": "Charcoal / Minimal", "colors_override": {"primary": "2B2B2B", "secondary": "5A5A5A", "accent": "B08A2E"}},
            ],
        }),
        TemplateSpec.from_dict({
            "id": "government_formal",
            "name": "Government / Official",
            "category": "government",
            "description": "Conservative formal layout for ministries and institutions.",
            "colors": {"primary": "0B3D2E", "secondary": "1B5E20", "accent": "8A6D3B"},
            "cover": {"style": "minimal_top"},
            "fonts": {"heading_en": "Times New Roman", "body_en": "Times New Roman",
                      "heading_ar": "Traditional Arabic", "body_ar": "Simplified Arabic"},
            "variants": [
                {"variant_id": "default", "label": "Formal Green"},
                {"variant_id": "navy", "label": "Formal Navy", "colors_override": {"primary": "0A2A4A", "secondary": "1D4E7A", "accent": "8A6D3B"}},
            ],
        }),
        TemplateSpec.from_dict({
            "id": "consulting_analytical",
            "name": "Consulting / Analytical",
            "category": "consulting",
            "description": "Structured, chart-and-callout-heavy layout for analytical reports.",
            "colors": {"primary": "111827", "secondary": "2563EB", "accent": "F59E0B"},
            "cover": {"style": "side_bar"},
            "variants": [
                {"variant_id": "default", "label": "Blue / Structured"},
                {"variant_id": "crimson", "label": "Crimson Accent", "colors_override": {"secondary": "9F1D35", "accent": "F2A900"}},
            ],
        }),
        TemplateSpec.from_dict({
            "id": "academic_research",
            "name": "Academic / Research",
            "category": "academic",
            "description": "TOC-heavy, citation-friendly layout for research and technical reports.",
            "colors": {"primary": "3B2E7E", "secondary": "5B4B9E", "accent": "A63A3A"},
            "cover": {"style": "full_bleed_footer"},
            "fonts": {"heading_en": "Georgia", "body_en": "Georgia",
                      "heading_ar": "Traditional Arabic", "body_ar": "Arial"},
            "variants": [
                {"variant_id": "default", "label": "Deep Purple"},
                {"variant_id": "slate", "label": "Slate", "colors_override": {"primary": "334155", "secondary": "475569", "accent": "0EA5E9"}},
            ],
        }),
    ]


class TemplateManager:
    def __init__(self, templates_dir: Optional[str] = None):
        self.templates_dir = templates_dir
        self._templates: List[TemplateSpec] = []
        self._load()

    def _load(self):
        loaded: List[TemplateSpec] = []
        if self.templates_dir and os.path.isdir(self.templates_dir):
            for entry in sorted(os.listdir(self.templates_dir)):
                meta_path = os.path.join(self.templates_dir, entry, "metadata.json")
                if os.path.isfile(meta_path):
                    try:
                        with open(meta_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        data.setdefault("id", entry)
                        loaded.append(TemplateSpec.from_dict(data))
                    except Exception as e:
                        print(f"[template_manager] failed to load {meta_path}: {e}")
        if not loaded:
            loaded = _builtin_templates()
        self._templates = loaded

    def list_templates(self, lang_mode: Optional[str] = None) -> List[TemplateSpec]:
        if not lang_mode:
            return list(self._templates)
        return [t for t in self._templates if lang_mode in t.supports_lang]

    def get(self, template_id: str) -> Optional[TemplateSpec]:
        for t in self._templates:
            if t.id == template_id:
                return t
        return None

    def reload(self):
        self._load()
