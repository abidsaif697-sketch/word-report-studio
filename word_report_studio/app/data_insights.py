"""
data_insights.py
-----------------
Data Recognition + Visual Recommendation engine (fully offline).

Two jobs:

1. `chart_from_table(...)` — turn a numeric TableBlock into a ChartBlock.
   Used by the `%%visualize%%` token in content: place it on the line right
   after a table and the system reads the table's numbers and adds a chart
   below it in every generated layout. Categories come from the first
   column, one series per numeric column, series names from the header row.
   Year-like categories get a line chart, otherwise bars.

2. `analyze(report)` — document analysis summary: counts of sections,
   words, tables, visuals, plus recommendations (e.g. "this table is a
   time series — add %%visualize%% to chart it"). Shown by the CLI and on
   the HTML comparison sheet.
"""

from __future__ import annotations
import re
from typing import List, Optional

from .structure_model import (
    ReportDocument, Section, Block, Run,
    Heading, Paragraph, ListBlock, TableBlock, ImageBlock, Quote, Callout,
    KpiBlock, ChartBlock, TimelineBlock, ProcessBlock,
)

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_NUM_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?$")
_YEAR_RE = re.compile(r"^(19|20)\d{2}$")


def runs_to_text(runs: List[Run]) -> str:
    return "".join(r.text for r in runs)


def parse_number(text: str) -> Optional[float]:
    """Parse '3.1', '1,204', '24%', '5.2M', '3.4K', Arabic-Indic digits.
    Returns the bare magnitude (24% -> 24.0, 5.2M -> 5.2) since charts
    compare values within one column, not across units."""
    t = text.strip().translate(_ARABIC_DIGITS)
    t = t.replace(",", "").replace("٪", "%")
    t = re.sub(r"[%$€£]|SAR|AED|USD", "", t, flags=re.IGNORECASE).strip()
    m = re.match(r"^([+-]?\d+(?:\.\d+)?)\s*[MKBmkb]?$", t)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def chart_from_table(table: TableBlock, chart_type: Optional[str] = None,
                     title: Optional[str] = None) -> Optional[ChartBlock]:
    """Build a ChartBlock from a numeric table, or None if the table has
    no usable numeric columns."""
    if not table.rows or len(table.rows) < 2:
        return None
    header = [runs_to_text(c).strip() for c in table.rows[0]]
    body = table.rows[1:] if table.header_row else table.rows
    if not table.header_row:
        header = ["" for _ in table.rows[0]]

    n_cols = max(len(r) for r in table.rows)
    categories: List[str] = []
    columns: List[List[Optional[float]]] = [[] for _ in range(n_cols - 1)]
    for row in body:
        cells = [runs_to_text(c).strip() for c in row] + [""] * (n_cols - len(row))
        categories.append(cells[0])
        for ci in range(1, n_cols):
            columns[ci - 1].append(parse_number(cells[ci]))

    # keep only columns where every body row parsed as a number
    series = []
    for ci, col in enumerate(columns):
        if col and all(v is not None for v in col):
            name = header[ci + 1] if ci + 1 < len(header) else ""
            series.append((name, [float(v) for v in col]))
    if not series:
        return None

    if not chart_type:
        year_like = sum(1 for c in categories
                        if _YEAR_RE.match(c.translate(_ARABIC_DIGITS).strip()))
        chart_type = "line" if year_like >= max(2, len(categories) - 1) else "bar"

    return ChartBlock(
        chart_type=chart_type,
        title=title or (table.caption or None),
        categories=categories,
        series=series,
    )


def is_chartable(table: TableBlock) -> bool:
    return chart_from_table(table) is not None


# ---------------------------------------------------------------------------
# Document analysis / recommendations
# ---------------------------------------------------------------------------

def analyze(report: ReportDocument) -> dict:
    stats = {
        "sections": 0, "subsections": 0, "words": 0, "paragraphs": 0,
        "tables": 0, "images": 0, "charts": 0, "kpi_blocks": 0,
        "timelines": 0, "processes": 0, "callouts": 0, "quotes": 0, "lists": 0,
    }
    suggestions: List[str] = []

    def scan_blocks(blocks: List[Block], where: str):
        prev: Optional[Block] = None
        for b in blocks:
            if isinstance(b, Paragraph):
                stats["paragraphs"] += 1
                stats["words"] += sum(len(r.text.split()) for r in b.runs)
            elif isinstance(b, TableBlock):
                stats["tables"] += 1
            elif isinstance(b, ImageBlock):
                stats["images"] += 1
            elif isinstance(b, ChartBlock):
                stats["charts"] += 1
            elif isinstance(b, KpiBlock):
                stats["kpi_blocks"] += 1
            elif isinstance(b, TimelineBlock):
                stats["timelines"] += 1
            elif isinstance(b, ProcessBlock):
                stats["processes"] += 1
            elif isinstance(b, Callout):
                stats["callouts"] += 1
            elif isinstance(b, Quote):
                stats["quotes"] += 1
            elif isinstance(b, ListBlock):
                stats["lists"] += 1
            # recommendation: numeric table with no chart right after it
            if isinstance(prev, TableBlock) and not isinstance(b, ChartBlock):
                if is_chartable(prev):
                    suggestions.append(
                        f'Table in "{where}" contains numeric series — add '
                        f"%%visualize%% on the line after it to also chart it."
                    )
            prev = b
        if isinstance(prev, TableBlock) and is_chartable(prev):
            suggestions.append(
                f'Table in "{where}" contains numeric series — add '
                f"%%visualize%% on the line after it to also chart it."
            )

    def scan_section(sec: Section, top: bool):
        stats["sections" if top else "subsections"] += 1
        scan_blocks(sec.blocks, sec.title or "(untitled)")
        for sub in sec.subsections:
            scan_section(sub, top=False)

    for s in report.all_sections():
        scan_section(s, top=True)

    visuals = stats["charts"] + stats["kpi_blocks"] + stats["timelines"] + stats["processes"]
    if stats["words"] > 1500 and visuals == 0 and stats["images"] == 0:
        suggestions.append(
            "The document is text-heavy with no visuals — consider a ::: kpi "
            "block in the executive summary or a ```chart block for key numbers."
        )
    stats["visuals"] = visuals
    return {"stats": stats, "suggestions": suggestions}


def format_analysis(analysis: dict) -> str:
    s = analysis["stats"]
    lines = [
        "Document analysis:",
        f"  Sections: {s['sections']} (+{s['subsections']} subsections)   "
        f"Words: {s['words']}   Paragraphs: {s['paragraphs']}",
        f"  Tables: {s['tables']}   Images: {s['images']}   "
        f"Charts: {s['charts']}   KPI cards: {s['kpi_blocks']}   "
        f"Timelines: {s['timelines']}   Processes: {s['processes']}",
    ]
    for sug in analysis["suggestions"]:
        lines.append(f"  Tip: {sug}")
    return "\n".join(lines)
