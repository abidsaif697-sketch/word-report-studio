"""
branding.py
------------
The user's brand, applied once and everywhere — organization name, logo,
colors, and fonts stored in a local `branding.json` next to the app.
Fully offline: reads local files only, no network.

When branding is enabled, every generated design option (any template, any
variant) is recolored to the brand palette, headings/body use the brand
fonts, the logo appears in the page header, and the organization name
pre-fills document metadata. Charts, KPI gauges, icons, and dividers all
inherit automatically because they draw from the template ColorSpec.
"""

from __future__ import annotations
import dataclasses
import json
import os
import re
from dataclasses import dataclass
from typing import Optional

from .template_manager import TemplateSpec

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRAND_FILE = os.path.join(PROJECT_ROOT, "branding.json")

_HEX_RE = re.compile(r"^[0-9A-Fa-f]{6}$")


@dataclass
class Brand:
    enabled: bool = False
    organization: str = ""
    logo_path: str = ""            # local image file; empty = no logo
    primary: str = ""              # 6-digit hex without '#'; empty = keep template
    secondary: str = ""
    accent: str = ""
    heading_font: str = ""         # empty = keep template fonts
    body_font: str = ""
    heading_font_ar: str = ""
    body_font_ar: str = ""


def _clean_hex(value: str) -> str:
    v = (value or "").strip().lstrip("#")
    return v.upper() if _HEX_RE.match(v) else ""


def load_brand(path: str = BRAND_FILE) -> Optional[Brand]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    brand = Brand(
        enabled=bool(data.get("enabled", False)),
        organization=str(data.get("organization", "")),
        logo_path=str(data.get("logo_path", "")),
        primary=_clean_hex(data.get("primary", "")),
        secondary=_clean_hex(data.get("secondary", "")),
        accent=_clean_hex(data.get("accent", "")),
        heading_font=str(data.get("heading_font", "")),
        body_font=str(data.get("body_font", "")),
        heading_font_ar=str(data.get("heading_font_ar", "")),
        body_font_ar=str(data.get("body_font_ar", "")),
    )
    return brand


def save_brand(brand: Brand, path: str = BRAND_FILE):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dataclasses.asdict(brand), f, indent=2, ensure_ascii=False)


def apply_to_template(brand: Brand, template: TemplateSpec) -> TemplateSpec:
    """A copy of the template wearing the brand. Only fields the brand
    actually sets are overridden; template design (layout, spacing, cover
    style) is untouched. Variant color overrides are dropped for branded
    color keys so every option carries the same brand identity."""
    if not brand or not brand.enabled:
        return template

    colors = dataclasses.replace(
        template.colors,
        **{k: v for k, v in (("primary", brand.primary),
                             ("secondary", brand.secondary),
                             ("accent", brand.accent)) if v})
    fonts = dataclasses.replace(
        template.fonts,
        **{k: v for k, v in (("heading_en", brand.heading_font),
                             ("body_en", brand.body_font),
                             ("heading_ar", brand.heading_font_ar),
                             ("body_ar", brand.body_font_ar)) if v})

    branded_keys = {k for k, v in (("primary", brand.primary),
                                   ("secondary", brand.secondary),
                                   ("accent", brand.accent)) if v}
    variants = [
        dataclasses.replace(
            v, colors_override={k: c for k, c in (v.colors_override or {}).items()
                                if k not in branded_keys})
        for v in template.variants
    ]
    return dataclasses.replace(template, colors=colors, fonts=fonts,
                               variants=variants)


def logo_exists(brand: Optional[Brand]) -> bool:
    return bool(brand and brand.enabled and brand.logo_path
                and os.path.isfile(brand.logo_path))
