"""
content_parser.py
------------------
Turns raw input (Markdown-ish text, plain text, or JSON) into a
structure_model.ReportDocument. Fully offline, no external services.

Supported Markdown subset (works for mixed Arabic/English content):

    # Title                -> new top-level Section
    ## Subtitle             -> new subsection under current top-level Section
    ### / ####               -> Heading block (level 3/4) inside current container
    plain text lines         -> paragraph (blank line ends the paragraph)
    - item / * item           -> unordered list
    1. item                   -> ordered list
    > quoted text              -> quote block
    | a | b |                   -> table (2nd row must be a |---|---| separator)
    ![caption](path/to/img.png) -> image block
    ::: info Title               callout block (tone: info|warning|success)
    text...
    :::
    %%pagebreak%%                 -> explicit page break
    Sections named "Appendix ..." or "ملحق ..." are routed to doc.appendices.

Designer-grade visual blocks:

    ```chart bar Revenue by Year     chart block (bar|column|line|area|pie|donut)
    series: Revenue, Profit           (optional series names, for multi-series)
    2021: 2.4, 1.1                    label: value[, value2...]
    2022: 3.1, 1.4
    ```

    ::: kpi                          row of KPI stat cards
    24% | Revenue Growth | up         value | label | icon
    91% | رضا العملاء | star           icons: up, down, down-good, up-bad,
    :::                                       star, dot, flat, check, warn

    ::: timeline Roadmap             milestone timeline diagram
    2021 | Company founded            marker | description
    2022 | التوسع الإقليمي
    :::

    ::: process Delivery Phases      workflow diagram (steps left-to-right)
    Plan | Design | Build | Launch    (pipes on one line, or one step per line)
    :::

    %%visualize%%                    placed on the line right after a table:
                                     reads the table's numbers and adds a chart
                                     (%%visualize pie%% forces a chart type)
"""

from __future__ import annotations
import json
import re
from typing import List, Optional

from .structure_model import (
    ReportDocument, DocumentMeta, Section, LangMode,
    Block, Heading, Paragraph, ListBlock, TableBlock, ImageBlock,
    Quote, Callout, PageBreak, Run, runs_from_text, detect_lang,
    KpiBlock, KpiItem, KPI_ICONS, ChartBlock, TimelineBlock, ProcessBlock,
)
from . import data_insights

APPENDIX_PREFIXES = ("appendix", "ملحق")

HEADING_RE = re.compile(r"^(#{1,4})\s+(.*)$")
UL_RE = re.compile(r"^\s*[-*]\s+(.*)$")
OL_RE = re.compile(r"^\s*\d+[.)]\s+(.*)$")
QUOTE_RE = re.compile(r"^\s*>\s?(.*)$")
IMAGE_RE = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)$")
TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")
TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:-]+\|[\s:|-]*$")
CALLOUT_OPEN_RE = re.compile(r"^:::\s*(info|warning|success)?\s*(.*)$", re.IGNORECASE)
CALLOUT_CLOSE = ":::"
PAGEBREAK_TOKEN = "%%pagebreak%%"
VISUAL_OPEN_RE = re.compile(r"^:::\s*(kpi|timeline|process)\s*(.*)$", re.IGNORECASE)
CHART_OPEN_RE = re.compile(r"^```\s*chart\s+(\w+)\s*(.*)$", re.IGNORECASE)
FENCE_CLOSE = "```"
VISUALIZE_RE = re.compile(r"^%%visualize(?:\s+(\w+))?%%$", re.IGNORECASE)
CHART_SERIES_LINE_RE = re.compile(r"^series\s*:\s*(.+)$", re.IGNORECASE)


def _split_table_row(line: str) -> List[str]:
    inner = line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    return [c.strip() for c in inner.split("|")]


def _is_appendix(title: str) -> bool:
    t = title.strip().lower()
    return any(t.startswith(p) for p in APPENDIX_PREFIXES)


class ContentParser:
    """Stateful line-by-line Markdown-ish parser producing a ReportDocument."""

    def parse_markdown(self, text: str, meta: Optional[DocumentMeta] = None) -> ReportDocument:
        doc = ReportDocument(meta=meta or DocumentMeta())

        top_section: Optional[Section] = None
        sub_section: Optional[Section] = None
        in_appendix = False

        def container_blocks() -> List[Block]:
            nonlocal top_section
            if sub_section is not None:
                return sub_section.blocks
            if top_section is not None:
                return top_section.blocks
            # No section declared yet: create an implicit one
            top_section = Section(title="")
            doc.sections.append(top_section)
            return top_section.blocks

        lines = text.splitlines()
        i = 0
        n = len(lines)
        paragraph_buffer: List[str] = []

        def flush_paragraph():
            if paragraph_buffer:
                joined = " ".join(l.strip() for l in paragraph_buffer if l.strip())
                if joined:
                    container_blocks().append(Paragraph(runs=runs_from_text(joined)))
                paragraph_buffer.clear()

        while i < n:
            raw = lines[i]
            line = raw.rstrip("\n")
            stripped = line.strip()

            if not stripped:
                flush_paragraph()
                i += 1
                continue

            if stripped == PAGEBREAK_TOKEN:
                flush_paragraph()
                container_blocks().append(PageBreak())
                i += 1
                continue

            m = VISUALIZE_RE.match(stripped)
            if m:
                flush_paragraph()
                blocks = container_blocks()
                last = blocks[-1] if blocks else None
                if isinstance(last, TableBlock):
                    chart = data_insights.chart_from_table(last, chart_type=m.group(1))
                    if chart is not None:
                        blocks.append(chart)
                # a %%visualize%% with no numeric table before it is silently
                # dropped rather than failing the whole document
                i += 1
                continue

            m = CHART_OPEN_RE.match(stripped)
            if m:
                flush_paragraph()
                chart_type = m.group(1).lower()
                title = m.group(2).strip() or None
                body_lines: List[str] = []
                i += 1
                while i < n and lines[i].strip() != FENCE_CLOSE:
                    body_lines.append(lines[i])
                    i += 1
                i += 1  # skip closing ```
                chart = self._parse_chart_body(chart_type, title, body_lines)
                if chart is not None:
                    container_blocks().append(chart)
                continue

            m = VISUAL_OPEN_RE.match(stripped)
            if m:
                flush_paragraph()
                kind = m.group(1).lower()
                title = m.group(2).strip() or None
                body_lines = []
                i += 1
                while i < n and lines[i].strip() != CALLOUT_CLOSE:
                    body_lines.append(lines[i].strip())
                    i += 1
                i += 1  # skip closing :::
                block = self._parse_visual_block(kind, title, body_lines)
                if block is not None:
                    container_blocks().append(block)
                continue

            m = HEADING_RE.match(line)
            if m:
                flush_paragraph()
                level = len(m.group(1))
                title_text = m.group(2).strip()
                if level == 1:
                    in_appendix = _is_appendix(title_text)
                    top_section = Section(title=title_text)
                    sub_section = None
                    if in_appendix:
                        doc.appendices.append(top_section)
                    else:
                        doc.sections.append(top_section)
                elif level == 2:
                    sub_section = Section(title=title_text)
                    if top_section is None:
                        top_section = Section(title="")
                        doc.sections.append(top_section)
                    top_section.subsections.append(sub_section)
                else:
                    container_blocks().append(
                        Heading(level=level, runs=runs_from_text(title_text))
                    )
                i += 1
                continue

            m = IMAGE_RE.match(stripped)
            if m:
                flush_paragraph()
                caption, path = m.group(1), m.group(2)
                container_blocks().append(
                    ImageBlock(path=path, caption=caption or None)
                )
                i += 1
                continue

            m = CALLOUT_OPEN_RE.match(stripped)
            if m:
                flush_paragraph()
                tone = (m.group(1) or "info").lower()
                title = m.group(2).strip() or None
                body_lines: List[str] = []
                i += 1
                while i < n and lines[i].strip() != CALLOUT_CLOSE:
                    body_lines.append(lines[i])
                    i += 1
                i += 1  # skip closing :::
                joined = " ".join(l.strip() for l in body_lines if l.strip())
                container_blocks().append(
                    Callout(runs=runs_from_text(joined), title=title, tone=tone)
                )
                continue

            if QUOTE_RE.match(line):
                flush_paragraph()
                q_lines = []
                while i < n and QUOTE_RE.match(lines[i]):
                    q_lines.append(QUOTE_RE.match(lines[i]).group(1))
                    i += 1
                joined = " ".join(l.strip() for l in q_lines if l.strip())
                container_blocks().append(Quote(runs=runs_from_text(joined)))
                continue

            if UL_RE.match(line) or OL_RE.match(line):
                flush_paragraph()
                ordered = bool(OL_RE.match(line))
                items: List[List[Run]] = []
                pattern = OL_RE if ordered else UL_RE
                # allow mixed marker styles to break the list, but keep same-type runs together
                while i < n:
                    mm = pattern.match(lines[i])
                    if not mm:
                        break
                    items.append(runs_from_text(mm.group(1).strip()))
                    i += 1
                container_blocks().append(ListBlock(items=items, ordered=ordered))
                continue

            if TABLE_ROW_RE.match(line):
                flush_paragraph()
                header_cells = _split_table_row(line)
                rows: List[List[str]] = [header_cells]
                i += 1
                has_header = False
                if i < n and TABLE_SEP_RE.match(lines[i]):
                    has_header = True
                    i += 1
                while i < n and TABLE_ROW_RE.match(lines[i]):
                    rows.append(_split_table_row(lines[i]))
                    i += 1
                table_rows = [[runs_from_text(cell) for cell in row] for row in rows]
                container_blocks().append(TableBlock(rows=table_rows, header_row=has_header))
                continue

            # default: accumulate into paragraph
            paragraph_buffer.append(line)
            i += 1

        flush_paragraph()

        # infer lang_mode if not explicitly set
        if meta is None or meta.lang_mode is None:
            doc.meta.lang_mode = self._infer_lang_mode(doc)

        return doc

    # ------------------------------------------------------------------
    # Visual block helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_chart_body(chart_type: str, title: Optional[str],
                          body_lines: List[str]) -> Optional[ChartBlock]:
        """Body lines: optional 'series: A, B' plus 'label: v1[, v2...]' rows."""
        series_names: List[str] = []
        categories: List[str] = []
        value_rows: List[List[float]] = []
        for raw in body_lines:
            line = raw.strip()
            if not line:
                continue
            m = CHART_SERIES_LINE_RE.match(line)
            if m:
                series_names = [s.strip() for s in m.group(1).split(",")]
                continue
            if ":" not in line:
                continue
            label, _, values_part = line.partition(":")
            values = []
            for tok in re.split(r"[,|]", values_part):
                v = data_insights.parse_number(tok)
                if v is not None:
                    values.append(v)
            if values:
                categories.append(label.strip())
                value_rows.append(values)
        if not categories:
            return None
        n_series = max(len(r) for r in value_rows)
        series = []
        for si in range(n_series):
            name = series_names[si] if si < len(series_names) else ""
            series.append((name, [row[si] if si < len(row) else 0.0
                                  for row in value_rows]))
        return ChartBlock(chart_type=chart_type, title=title,
                          categories=categories, series=series)

    @staticmethod
    def _parse_visual_block(kind: str, title: Optional[str],
                            body_lines: List[str]) -> Optional[Block]:
        rows = [l for l in body_lines if l]
        if kind == "kpi":
            items = []
            for row in rows:
                parts = [p.strip() for p in row.split("|")]
                if not parts or not parts[0]:
                    continue
                value = parts[0]
                label = parts[1] if len(parts) > 1 else ""
                icon = parts[2].lower() if len(parts) > 2 else "none"
                if icon not in KPI_ICONS:
                    icon = "none"
                items.append(KpiItem(value=value, label=label, icon=icon))
            return KpiBlock(items=items) if items else None
        if kind == "timeline":
            items = []
            for row in rows:
                marker, _, text = row.partition("|")
                items.append((marker.strip(), text.strip()))
            return TimelineBlock(items=items, title=title) if items else None
        if kind == "process":
            steps: List[str] = []
            for row in rows:
                if "|" in row:
                    steps.extend(p.strip() for p in row.split("|") if p.strip())
                else:
                    steps.append(row)
            return ProcessBlock(steps=steps, title=title) if steps else None
        return None

    def parse_json(self, data: dict) -> ReportDocument:
        """Load a document from a JSON structure (e.g. produced by an LLM).
        Schema mirrors structure_model closely; see sample_input/schema_example.json.
        """
        meta_d = data.get("meta", {})
        meta = DocumentMeta(
            title=meta_d.get("title", "Untitled Report"),
            subtitle=meta_d.get("subtitle"),
            author=meta_d.get("author"),
            organization=meta_d.get("organization"),
            date=meta_d.get("date"),
            confidentiality=meta_d.get("confidentiality"),
            lang_mode=LangMode(meta_d.get("lang_mode", "bilingual")),
        )
        doc = ReportDocument(
            meta=meta,
            include_toc=data.get("include_toc", True),
            include_cover=data.get("include_cover", True),
        )
        doc.sections = [self._section_from_json(s) for s in data.get("sections", [])]
        doc.appendices = [self._section_from_json(s) for s in data.get("appendices", [])]
        return doc

    def _section_from_json(self, s: dict) -> Section:
        section = Section(title=s.get("title", ""), lang=s.get("lang"))
        section.blocks = [self._block_from_json(b) for b in s.get("blocks", [])]
        section.subsections = [self._section_from_json(sub) for sub in s.get("subsections", [])]
        return section

    def _block_from_json(self, b: dict) -> Block:
        btype = b.get("type")
        if btype == "heading":
            return Heading(level=b.get("level", 2), runs=runs_from_text(b.get("text", "")))
        if btype == "paragraph":
            return Paragraph(runs=runs_from_text(b.get("text", "")))
        if btype == "list":
            return ListBlock(
                items=[runs_from_text(it) for it in b.get("items", [])],
                ordered=b.get("ordered", False),
            )
        if btype == "table":
            rows = b.get("rows", [])
            return TableBlock(
                rows=[[runs_from_text(cell) for cell in row] for row in rows],
                header_row=b.get("header_row", True),
                caption=b.get("caption"),
            )
        if btype == "image":
            return ImageBlock(
                path=b.get("path", ""),
                caption=b.get("caption"),
                width_inches=b.get("width_inches"),
            )
        if btype == "quote":
            return Quote(runs=runs_from_text(b.get("text", "")), attribution=b.get("attribution"))
        if btype == "callout":
            return Callout(
                runs=runs_from_text(b.get("text", "")),
                title=b.get("title"),
                tone=b.get("tone", "info"),
            )
        if btype == "page_break":
            return PageBreak()
        if btype == "kpi":
            items = [
                KpiItem(value=str(it.get("value", "")), label=it.get("label", ""),
                        icon=it.get("icon", "none") if it.get("icon", "none") in KPI_ICONS else "none")
                for it in b.get("items", [])
            ]
            return KpiBlock(items=items)
        if btype == "chart":
            return ChartBlock(
                chart_type=b.get("chart_type", "bar"),
                title=b.get("title"),
                categories=[str(c) for c in b.get("categories", [])],
                series=[(s.get("name", ""), [float(v) for v in s.get("values", [])])
                        for s in b.get("series", [])],
                caption=b.get("caption"),
            )
        if btype == "timeline":
            return TimelineBlock(
                items=[(str(it.get("marker", "")), it.get("text", ""))
                       for it in b.get("items", [])],
                title=b.get("title"),
            )
        if btype == "process":
            return ProcessBlock(steps=[str(s) for s in b.get("steps", [])],
                                title=b.get("title"))
        # fallback: treat unknown types as paragraph text if possible
        return Paragraph(runs=runs_from_text(str(b.get("text", ""))))

    def parse_auto(self, text: str, meta: Optional[DocumentMeta] = None,
                   auto_visuals: bool = True) -> ReportDocument:
        """Best-effort: try JSON first, fall back to Markdown. Unless
        auto_visuals is disabled, raw content is then upgraded by
        auto_enrich (tables -> charts, KPI extraction, timelines, inferred
        headings) so unstructured text still yields a designed report."""
        stripped = text.strip()
        doc = None
        if stripped.startswith("{"):
            try:
                data = json.loads(stripped)
                doc = self.parse_json(data)
            except json.JSONDecodeError:
                pass
        if doc is None:
            doc = self.parse_markdown(text, meta=meta)
        if auto_visuals:
            from . import auto_enrich
            doc.auto_enrichment = auto_enrich.enrich(doc)
        return doc

    @staticmethod
    def _infer_lang_mode(doc: ReportDocument) -> LangMode:
        has_ar = False
        has_en = False

        def scan_runs(runs: List[Run]):
            nonlocal has_ar, has_en
            for r in runs:
                if r.lang == "ar":
                    has_ar = True
                else:
                    has_en = True

        def scan_section(sec: Section):
            nonlocal has_ar, has_en
            if sec.title:
                if detect_lang(sec.title) == "ar":
                    has_ar = True
                else:
                    has_en = True
            for b in sec.blocks:
                if isinstance(b, (Heading, Paragraph, Quote, Callout)):
                    scan_runs(b.runs)
                elif isinstance(b, ListBlock):
                    for item in b.items:
                        scan_runs(item)
                elif isinstance(b, TableBlock):
                    for row in b.rows:
                        for cell in row:
                            scan_runs(cell)
            for sub in sec.subsections:
                scan_section(sub)

        for s in doc.all_sections():
            scan_section(s)

        if has_ar and has_en:
            return LangMode.BILINGUAL
        if has_ar:
            return LangMode.ARABIC
        return LangMode.ENGLISH
