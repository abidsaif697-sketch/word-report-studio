"""
auto_enrich.py
---------------
The "smart" in Smart Formatter: runs right after parsing and upgrades raw,
unstructured content into designer visuals — the user should NOT have to
know the %%visualize%% / ::: kpi markup to get a designed document.

What it does (deliberately conservative — it only ever *adds* or upgrades,
never deletes source text, and every transform here is also achievable by
hand with explicit markers):

1. Numeric tables        -> a themed chart inserted right below the table
2. KPI figures in prose  -> one KPI card strip at the top of the report
                            (percentages / big numbers found in bullets and
                            short sentences)
3. Year-led lists        -> timeline visual ("2023 | opened HQ" bullets)
4. Note/Warning lines    -> callout boxes ("Note: ...", "تحذير: ...")
5. Headings inferred     -> completely flat text (no # headings at all) is
                            split into sections so covers/TOC/dividers work

`enrich(report)` mutates the document in place and returns a counts dict of
what it added, so entry points can tell the user what the brain did.
"""

from __future__ import annotations
import re
from typing import Dict, List, Optional, Tuple

from . import data_insights
from .structure_model import (
    ReportDocument, Section, Block,
    Heading, Paragraph, ListBlock, TableBlock, Quote, Callout,
    KpiBlock, KpiItem, ChartBlock, TimelineBlock, ProcessBlock,
    Run, runs_from_text,
)

_YEAR_ITEM_RE = re.compile(r"^\s*((?:19|20)\d{2})\s*[:\-–—|]?\s+(.{3,})$")
_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")

# a number worth a KPI card: needs a % sign or a magnitude suffix, or be a
# standalone integer that is clearly a count (>= 3 digits, not a year).
# Word boundaries are load-bearing: without them "ISO 27001" yields "270"
# and "ISP0425V2A" yields "042" (seen on a real policy document).
_KPI_NUM_RE = re.compile(
    r"(?<![\w.\-/؀-ۿ])([+-]?\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*"
    r"(%|٪|percent|بالمئة|في المئة|[MKB]\b|mn\b|bn\b|million|billion|مليون|مليار|ألف)?"
    r"(?![\w\-/%٪])",
    re.IGNORECASE)

# strip date/version/code noise BEFORE harvesting: 01-04-2025, 2025/04/01,
# v2.0 / الإصدار 2.0, document codes like ISP0425V2A
_KPI_NOISE_RES = (
    re.compile(r"\b\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}\b"),          # dates
    re.compile(r"\b[A-Za-z]+\d[\w-]*\b"),                          # codes
    re.compile(r"(?:\bv|\bversion|الإصدار|النسخة|إصدار)\s*[:\s]?\s*\d+(?:\.\d+)?",
               re.IGNORECASE),                                       # versions
    re.compile(r"\d+(?:\.\d+)?\s*(?:الإصدار|النسخة|إصدار|\bversion\b)",
               re.IGNORECASE),                             # "2.0 الإصدار" order
)

# a bare count (no % / magnitude suffix) is only a KPI when its label talks
# about something countable — otherwise policy/legal numbers leak in
_COUNT_HINTS = ("employee", "staff", "client", "customer", "user", "project",
                "branch", "office", "member", "student", "ticket", "case",
                "موظف", "عميل", "عملاء", "مستخدم", "مشروع", "مشاريع", "فرع",
                "فروع", "مكتب", "مكاتب", "عضو", "طالب", "طلاب", "حالة", "تذكرة")

_UP_WORDS = ("grew", "growth", "increase", "increased", "improved", "improvement",
             "rose", "up ", "gain", "نمو", "نما", "ارتفع", "ارتفاع", "تحسن", "زيادة", "زاد")
_DOWN_WORDS = ("decline", "declined", "decrease", "decreased", "dropped", "fell",
               "down ", "reduction", "reduced", "انخفض", "انخفاض", "تراجع", "هبوط", "قل")
_GOOD_WHEN_DOWN = ("complaint", "شكاوى", "الشكاوى", "error", "أخطاء", "خطأ",
                   "incident", "حوادث", "cost", "تكاليف", "التكاليف", "churn",
                   "delay", "تأخير", "failure", "فشل", "الفشل", "risk",
                   "مخاطر", "المخاطر", "breach", "اختراق", "violation", "مخالفات")

_CALLOUT_PREFIX_RE = re.compile(
    r"^(Note|Important|Warning|Caution|Risk|ملاحظة|ملحوظة|تنبيه|تحذير|هام|مهم)\s*[:：\-–]\s*(.+)$",
    re.IGNORECASE | re.DOTALL)
_WARNING_PREFIXES = ("warning", "caution", "risk", "تنبيه", "تحذير")

_HEADING_MAX_CHARS = 64
_HEADING_MAX_WORDS = 9
_SENTENCE_END = (".", "!", "?", "؟", "…", ":", "،", ",", ";", "؛")


def _text(runs: List[Run]) -> str:
    return "".join(r.text for r in runs)


# ---------------------------------------------------------------------------
# 1. Numeric tables -> charts
# ---------------------------------------------------------------------------

def _auto_chart_tables(section: Section, counts: Dict[str, int]):
    blocks = section.blocks
    i = 0
    while i < len(blocks):
        b = blocks[i]
        nxt = blocks[i + 1] if i + 1 < len(blocks) else None
        if (isinstance(b, TableBlock) and not isinstance(nxt, ChartBlock)
                and data_insights.is_chartable(b)):
            title = b.caption
            if not title:
                # nearest heading above the table, else the section title
                for prev in reversed(blocks[:i]):
                    if isinstance(prev, Heading):
                        title = _text(prev.runs)
                        break
                title = title or section.title or None
            chart = data_insights.chart_from_table(b, title=title)
            if chart is not None:
                blocks.insert(i + 1, chart)
                counts["charts"] += 1
                i += 1
        i += 1
    for sub in section.subsections:
        _auto_chart_tables(sub, counts)


# ---------------------------------------------------------------------------
# 2. KPI figures in prose -> KPI card strip
# ---------------------------------------------------------------------------

def _kpi_icon(text: str, value: str) -> str:
    low = text.lower()
    down_good = any(w in low for w in _GOOD_WHEN_DOWN)
    if value.startswith("-") or any(w in low for w in _DOWN_WORDS):
        return "down-good" if down_good else "down"
    if value.startswith("+") or any(w in low for w in _UP_WORDS):
        return "up"
    return "star" if value.endswith(("%", "٪")) else "dot"


# the markdown parser joins consecutive lines into one paragraph, so "one
# fact per line" raw notes arrive as a single blob. Split it back into
# clauses: at sentence enders, and after a number/% where a new capitalized
# clause begins ("... grew 18% Customer satisfaction ..." -> split before
# "Customer"). Arabic halves stay attached to their English clause.
_CLAUSE_SPLIT_RE = re.compile(r"(?<=[%٪0-9])\s+(?=[A-Z])")
# split only at punctuation followed by whitespace — "12.4M" must not split
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?؟;؛])\s+|[\n•]+")


def _kpi_segments(text: str) -> List[str]:
    segs: List[str] = []
    for sentence in _SENTENCE_SPLIT_RE.split(text):
        segs.extend(_CLAUSE_SPLIT_RE.split(sentence))
    return [s.strip() for s in segs if s.strip()]


def _kpi_from_text(text: str) -> Optional[KpiItem]:
    text = text.strip()
    if not text or len(text) > 220:
        return None
    scrubbed = text
    for noise in _KPI_NOISE_RES:
        scrubbed = noise.sub(" ", scrubbed)
    best: Optional[Tuple[str, str]] = None  # (value_display, matched_span_text)
    for m in _KPI_NUM_RE.finditer(scrubbed):
        num, suffix = m.group(1), (m.group(2) or "")
        if _YEAR_RE.match(num.replace(",", "")):
            continue  # bare years are not KPIs
        sfx = suffix.strip()
        if sfx in ("٪", "percent", "بالمئة", "في المئة"):
            sfx = "%"
        if sfx:
            display = f"{num}{sfx if sfx == '%' else ' ' + sfx if len(sfx) > 1 else sfx}"
            # prefer a suffixed number over a bare one
            best = (display, m.group(0))
            break
        if best is None and len(re.sub(r"\D", "", num)) >= 3:
            low = scrubbed.lower()
            if any(h in low for h in _COUNT_HINTS):
                best = (num, m.group(0))
    if best is None:
        return None
    text = scrubbed
    value, span = best
    # sign context: "+12%" written as "grew 12%" keeps the bare number
    prefix_pos = text.find(span)
    if prefix_pos > 0 and text[prefix_pos - 1] in "+-":
        value = text[prefix_pos - 1] + value
        span = text[prefix_pos - 1] + span
    # a segment can hold several comma-joined clauses, each about a different
    # fact ("...averaged 4.2 hours, a 12.4M SAR budget was allocated, and...").
    # Using the whole segment as the label source bleeds a neighboring clause's
    # facts into this card; keep only the clause the matched number lives in.
    # ",\s+" (comma + space) is safe against thousands separators like "1,204",
    # which never have a space after the comma.
    clauses = re.split(r"[,،]\s+", text)
    label_source = next((c for c in clauses if span in c), text)
    label = label_source.replace(span, " ").strip(" \t-–—:،,.;؛/\\")
    label = re.sub(r"\s{2,}", " ", label)
    if len(label) < 3:
        return None
    if len(label) > 60:
        label = label[:57].rsplit(" ", 1)[0] + "…"
    return KpiItem(value=value, label=label, icon=_kpi_icon(text, value))


def _auto_kpis(report: ReportDocument, counts: Dict[str, int]):
    def has_kpi(sec: Section) -> bool:
        return (any(isinstance(b, KpiBlock) for b in sec.blocks)
                or any(has_kpi(s) for s in sec.subsections))

    if any(has_kpi(s) for s in report.sections):
        return  # author already made a KPI strip — don't compete with it

    items: List[KpiItem] = []
    seen_labels = set()

    def harvest(sec: Section):
        for bi, b in enumerate(sec.blocks):
            sources: List[str] = []
            if isinstance(b, ListBlock):
                for it in b.items:
                    sources.extend(_kpi_segments(_text(it)))
            elif isinstance(b, Paragraph):
                nxt = sec.blocks[bi + 1] if bi + 1 < len(sec.blocks) else None
                if getattr(nxt, "_from_prose", False) or isinstance(nxt, TimelineBlock):
                    continue  # this paragraph's numbers are already visualized
                sources = _kpi_segments(_text(b.runs))
            for s in sources:
                if len(items) >= 4:
                    return
                kpi = _kpi_from_text(s)
                if kpi and kpi.label.lower() not in seen_labels:
                    seen_labels.add(kpi.label.lower())
                    items.append(kpi)
        for sub in sec.subsections:
            harvest(sub)

    for sec in report.sections:
        harvest(sec)
        if len(items) >= 4:
            break

    if len(items) >= 2 and report.sections:
        report.sections[0].blocks.insert(0, KpiBlock(items=items))
        counts["kpi_cards"] += len(items)


# ---------------------------------------------------------------------------
# 3. Year-led lists -> timelines
# ---------------------------------------------------------------------------

def _auto_timelines(section: Section, counts: Dict[str, int]):
    for idx, b in enumerate(section.blocks):
        if not isinstance(b, ListBlock) or len(b.items) < 3:
            continue
        parsed = [_YEAR_ITEM_RE.match(_text(it)) for it in b.items]
        hits = [m for m in parsed if m]
        if len(hits) >= max(3, (2 * len(b.items)) // 3) and len(hits) == len(b.items):
            section.blocks[idx] = TimelineBlock(
                items=[(m.group(1), m.group(2).strip()) for m in hits])
            counts["timelines"] += 1
    for sub in section.subsections:
        _auto_timelines(sub, counts)


# ---------------------------------------------------------------------------
# 4. Note/Warning prefixed paragraphs -> callouts
# ---------------------------------------------------------------------------

def _auto_callouts(section: Section, counts: Dict[str, int]):
    for idx, b in enumerate(section.blocks):
        if not isinstance(b, Paragraph):
            continue
        m = _CALLOUT_PREFIX_RE.match(_text(b.runs).strip())
        if not m:
            continue
        prefix, body = m.group(1), m.group(2).strip()
        tone = "warning" if prefix.lower() in _WARNING_PREFIXES else "info"
        section.blocks[idx] = Callout(runs=runs_from_text(body),
                                      title=prefix, tone=tone)
        counts["callouts"] += 1
    for sub in section.subsections:
        _auto_callouts(sub, counts)


# ---------------------------------------------------------------------------
# 5. Flat text (no headings anywhere) -> inferred sections
# ---------------------------------------------------------------------------

def _looks_like_heading(text: str) -> bool:
    t = text.strip()
    if not t or len(t) > _HEADING_MAX_CHARS or t.endswith(_SENTENCE_END):
        return False
    if len(t.split()) > _HEADING_MAX_WORDS:
        return False
    if t[0].isdigit():  # "45 new hires" is data, not a title
        return False
    return True


def _auto_headings(report: ReportDocument, counts: Dict[str, int]):
    if len(report.sections) != 1 or report.sections[0].title:
        return  # author gave structure — trust it
    sec = report.sections[0]
    if sec.subsections or len(sec.blocks) < 6:
        return
    paragraphs = [b for b in sec.blocks if isinstance(b, Paragraph)]
    candidates = [b for b in paragraphs
                  if _looks_like_heading(_text(b.runs))
                  and b is not sec.blocks[-1]]
    # need real structure, not a doc where everything is short lines —
    # except a lone short opening line, which is almost always the title
    if len(candidates) == 1 and candidates[0] is not sec.blocks[0]:
        return
    if not candidates or len(candidates) > max(2, len(paragraphs) // 2):
        return

    cand_ids = {id(b) for b in candidates}
    new_sections: List[Section] = []
    current = Section(title="")
    for b in sec.blocks:
        if id(b) in cand_ids:
            if current.blocks or current.title:
                new_sections.append(current)
            current = Section(title=_text(b.runs).strip())
            counts["sections"] += 1
        else:
            current.blocks.append(b)
    new_sections.append(current)
    report.sections = [s for s in new_sections if s.blocks or s.title]


# ---------------------------------------------------------------------------
# 6. Structure FROM prose — sentences that hide a table, process, or timeline
# ---------------------------------------------------------------------------

_SENTENCE_ENDERS_RE = re.compile(r"(?<=[.!?؟;؛])\s+")

_SEQ_MARKERS = ("first", "second", "third", "then", "next", "after that",
                "afterwards", "finally", "lastly",
                "أولاً", "أولا", "ثانياً", "ثانيا", "ثالثاً", "ثالثا",
                "ثم", "بعد ذلك", "وأخيراً", "وأخيرا", "أخيراً", "أخيرا")

# a data value inside prose: needs decimals, a magnitude suffix, or a % —
# bare small integers ("5 people said") are too noisy to tabulate
_PROSE_VALUE_RE = re.compile(
    r"([+-]?\d+(?:\.\d+)?)\s*(%|٪|[MKB]\b|mn\b|bn\b|million|billion|مليون|مليار|ألف)?",
    re.IGNORECASE)

_PERIOD_LABEL_RE = re.compile(
    r"\b(Q[1-4]|H[12]|FY\s?\d{2,4}|(?:19|20)\d{2})\b|"
    r"(الربع الأول|الربع الثاني|الربع الثالث|الربع الرابع)")

_AR_QUARTER_MAP = {"الربع الأول": "Q1", "الربع الثاني": "Q2",
                   "الربع الثالث": "Q3", "الربع الرابع": "Q4"}

_ENTITY_VERB_STOP = {"sold", "made", "recorded", "achieved", "reached",
                     "generated", "reported", "delivered", "posted", "did",
                     "grew", "had", "hit", "closed", "at", "with", "of",
                     "حققت", "حقق", "سجلت", "سجل", "باعت", "باع", "بلغت",
                     "بلغ", "وصلت", "وصل", "أنجزت", "انجزت"}


def _split_sentences(text: str) -> List[str]:
    return [s.strip() for s in _SENTENCE_ENDERS_RE.split(text.strip()) if s.strip()]


def _prose_process(section: Section, counts: Dict[str, int]):
    """'First we discover. Then we design. Finally we launch.' -> process bar."""
    for idx, b in enumerate(list(section.blocks)):
        if not isinstance(b, Paragraph):
            continue
        sentences = _split_sentences(_text(b.runs))
        if len(sentences) < 3:
            continue
        steps = []
        for s in sentences:
            low = s.lower().lstrip("و")
            marker = next((m for m in _SEQ_MARKERS if low.startswith(m)), None)
            if marker is None:
                continue  # intro/outro sentences are fine around the steps
            step = re.sub(r"^[\s,،:–—-]+|[\s.!؟?]+$", "",
                          s.lstrip("و")[len(marker):])
            if step:
                steps.append(step)
        if len(steps) >= 3 and 2 * len(steps) >= len(sentences):
            block = ProcessBlock(steps=steps[:8])
            if len(steps) == len(sentences) and all(len(st) <= 60 for st in steps):
                section.blocks[section.blocks.index(b)] = block
            else:
                section.blocks.insert(section.blocks.index(b) + 1, block)
            counts["processes"] += 1
    for sub in section.subsections:
        _prose_process(sub, counts)


_PROSE_YEAR_RE = re.compile(
    r"\b((?:19|20)\d{2})\b[\s:–—-]*([^.;؛!?؟,،0-9][^.;؛!?؟,،]{5,90})")


def _prose_timeline(section: Section, counts: Dict[str, int]):
    """'In 2023 we launched. In 2024 we expanded...' -> timeline visual."""
    for idx, b in enumerate(list(section.blocks)):
        if not isinstance(b, Paragraph):
            continue
        nxt = section.blocks[idx + 1] if idx + 1 < len(section.blocks) else None
        if isinstance(nxt, TimelineBlock):
            continue
        text = _text(b.runs)
        hits = [(m.group(1), m.group(2).strip(" ,،"))
                for m in _PROSE_YEAR_RE.finditer(text)]
        years = {y for y, _ in hits}
        if len(hits) >= 3 and len(years) >= 3:
            tl = TimelineBlock(items=sorted(hits, key=lambda t: t[0]))
            section.blocks.insert(section.blocks.index(b) + 1, tl)
            counts["timelines"] += 1
    for sub in section.subsections:
        _prose_timeline(sub, counts)


def _clause_values(clause: str) -> List[str]:
    """Data-like values in a clause (decimal, suffixed, or percentage)."""
    vals = []
    for m in _PROSE_VALUE_RE.finditer(clause):
        num, suffix = m.group(1), (m.group(2) or "").strip()
        if not suffix and "." not in num:
            continue  # bare integer without magnitude: too noisy
        if _YEAR_RE.match(num):
            continue
        vals.append((num + suffix).replace("٪", "%"))
    return vals


def _clause_entity(clause: str) -> str:
    head = re.split(r"\d", clause, maxsplit=1)[0]
    tokens = [t for t in re.findall(r"[\w؀-ۿ/]+", head)]
    while tokens and tokens[-1].lower() in _ENTITY_VERB_STOP:
        tokens.pop()
    entity = " ".join(tokens[-4:]).strip()
    return entity[:40]


def _prose_table(section: Section, counts: Dict[str, int]):
    """'Riyadh sold 12.4M in Q1 and 15.1M in Q2, while Jeddah did 8.2M and
    9.6M.' -> comparison table (which the chart pass then also charts)."""
    for b in list(section.blocks):
        if not isinstance(b, Paragraph):
            continue
        idx = section.blocks.index(b)
        nxt = section.blocks[idx + 1] if idx + 1 < len(section.blocks) else None
        if isinstance(nxt, (TableBlock, ChartBlock, TimelineBlock)):
            continue
        text = _text(b.runs)
        clauses = [c.strip(" ,،") for c in re.split(
            r"[.;؛](?=\s|$)|\bwhile\b|\bwhereas\b|,\s+and\s+|،\s*و(?=\S)"
            r"|،\s*بينما|\bبينما\b",
            text) if c.strip(" ,،")]
        rows: List[Tuple[str, List[str]]] = []
        for c in clauses:
            vals = _clause_values(c)
            ent = _clause_entity(c)
            if vals and ent and len(ent) >= 2:
                rows.append((ent, vals))
        if len(rows) < 2:
            continue
        n_vals = len(rows[0][1])
        if not n_vals or any(len(v) != n_vals for _e, v in rows):
            continue
        if n_vals == 1 and all(v[0].endswith("%") for _e, v in rows):
            continue  # single percentages read better as KPI cards
        entities = [e for e, _v in rows]
        if len(set(e.lower() for e in entities)) != len(entities):
            continue
        # column headers: period tokens (Q1/2024/الربع الأول) from the first clause
        labels = []
        for m in _PERIOD_LABEL_RE.finditer(clauses[0]):
            token = m.group(1) or _AR_QUARTER_MAP.get(m.group(2), m.group(2))
            labels.append(token)
        if len(labels) != n_vals:
            labels = [f"Value {i+1}" for i in range(n_vals)] if n_vals > 1 else ["Value"]
        header = [runs_from_text("Item")] + [runs_from_text(l) for l in labels]
        table_rows = [header] + [
            [runs_from_text(e)] + [runs_from_text(v) for v in vals]
            for e, vals in rows]
        generated = TableBlock(rows=table_rows, header_row=True)
        generated._from_prose = True  # KPI harvest must not double-count it
        section.blocks.insert(idx + 1, generated)
        counts["tables"] += 1
    for sub in section.subsections:
        _prose_table(sub, counts)


# ---------------------------------------------------------------------------

def enrich(report: ReportDocument) -> Dict[str, int]:
    """Upgrade a parsed document in place. Returns counts of added visuals."""
    counts = {"charts": 0, "kpi_cards": 0, "timelines": 0,
              "callouts": 0, "sections": 0, "tables": 0, "processes": 0}
    _auto_headings(report, counts)
    for sec in report.sections + report.appendices:
        _auto_timelines(sec, counts)
        _auto_callouts(sec, counts)
        _prose_process(sec, counts)
        _prose_timeline(sec, counts)
        _prose_table(sec, counts)
        _auto_chart_tables(sec, counts)  # last: also charts prose-built tables
    _auto_kpis(report, counts)
    return counts


def format_enrichment(counts: Dict[str, int]) -> str:
    parts = []
    names = {"tables": "table(s) built from prose",
             "charts": "chart(s) from tables", "kpi_cards": "KPI card(s)",
             "timelines": "timeline(s)", "processes": "process diagram(s)",
             "callouts": "callout box(es)",
             "sections": "inferred section(s)"}
    for key, label in names.items():
        if counts.get(key):
            parts.append(f"{counts[key]} {label}")
    if not parts:
        return "Auto-design: content already fully structured — nothing to add."
    return "Auto-design: added " + ", ".join(parts) + "."
