#!/usr/bin/env python3
"""
run_regression.py — freeze the session's hard-won manual verifications into
fast automated checks. No LLM, no LibreOffice needed (docx-level checks);
runs in seconds. Exit code 0 = all green.

Usage:  python tests/run_regression.py
"""
from __future__ import annotations
import os
import sys
import shutil
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    ok = bool(cond)
    PASS += ok
    FAIL += (not ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""))


def docx_text(path):
    with zipfile.ZipFile(path) as z:
        return z.read("word/document.xml").decode("utf-8", "replace")


def section(title):
    print(f"\n== {title} ==")


def main():
    tmp = tempfile.mkdtemp(prefix="wrs_regress_")
    try:
        # ------------------------------------------------ KPI extractor unit cases
        section("KPI extractor")
        from app.auto_enrich import _kpi_from_text, _kpi_segments

        def kpi_values(line):
            return [k.value for k in
                    (_kpi_from_text(s) for s in _kpi_segments(line)) if k]

        check("percent extracted", kpi_values("Revenue grew 18% overall.") == ["18%"])
        check("sentence-final % extracted",
              kpi_values("Customer satisfaction reached 91%.") == ["91%"])
        check("count with hint extracted",
              kpi_values("Team headcount is now 265 employees.") == ["265"])
        check("ISO code not a KPI", kpi_values("ISO 27001 معيار دولي") == [])
        check("doc code not a KPI", kpi_values("ISP0425V2A سياسة أمن المعلومات") == [])
        check("version not a KPI", kpi_values("الجمهور 2.0 الإصدار معتمدة") == [])
        check("date not a KPI", kpi_values("التاريخ 14-03-2024 مسودة 0.1") == [])

        # ------------------------------------------------ full generation pipeline
        section("Report generation (rough prose, rules tier)")
        from app.content_parser import ContentParser
        from app.structure_model import DocumentMeta
        from app.template_manager import TemplateManager
        from app.layout_engine import LayoutEngine
        from app.docx_renderer import DocxRenderer

        content = open("sample_input/rough_prose.txt", encoding="utf-8").read()
        parser = ContentParser()
        report = parser.parse_auto(content, meta=DocumentMeta(title="Regression"))
        enr = report.auto_enrichment
        check("prose table built", enr.get("tables", 0) >= 1, str(enr))
        check("chart from table", enr.get("charts", 0) >= 1, str(enr))
        check("KPI cards found", enr.get("kpi_cards", 0) >= 3, str(enr))
        check("process from prose", enr.get("processes", 0) >= 1, str(enr))
        check("timeline from prose", enr.get("timelines", 0) >= 1, str(enr))

        tm = TemplateManager(os.path.join(ROOT, "templates"))
        engine = LayoutEngine(tm)
        options = engine.generate_options(report, max_options=1,
                                          template_ids=["executive_premium"])
        out = DocxRenderer().render(report, options[0].template,
                                    options[0].variant,
                                    os.path.join(tmp, "regress.docx"))
        xml = docx_text(out)
        check("no consecutive page-break paragraphs",
              xml.count('w:type="page"') <= 3, f"{xml.count('page-brk')}")
        with zipfile.ZipFile(out) as z:
            media = [n for n in z.namelist() if "media" in n]
        check("visuals embedded as images", len(media) >= 4, str(len(media)))
        check("cantSplit on rows", "cantSplit" in xml)

        # numbers preserved end-to-end
        for num in ("12.4", "15.1", "8.2", "9.6", "18", "91", "22"):
            if num not in xml:
                check(f"number {num} preserved", False)
                break
        else:
            check("all source numbers preserved", True)

        # ------------------------------------------------ docx ingestion
        section("Word-file ingestion")
        from app.docx_ingest import docx_to_markdown, source_title
        md = docx_to_markdown("sample_input/source_policy.docx")
        check("headings preserved", "# Introduction" in md and "# Scope" in md)
        check("tables preserved", md.count("|---|") >= 2)
        check("lists preserved", "- All employees" in md)
        check("title read", "Information Security Policy"
              in (source_title("sample_input/source_policy.docx") or ""))

        # ------------------------------------------------ template slot filling
        section("Designer template filling (rules tier)")
        tpl = "user_templates/1 - Capability Statement Brochure (docx).docx"
        if os.path.isfile(tpl):
            from app.template_filler import (extract_slots, rules_mapping,
                                             sanitize_mapping, fill_slots,
                                             unfillable_pages, _LOREM_HINTS)
            slots = extract_slots(tpl)
            check("slots found", len(slots) >= 150, str(len(slots)))
            check("all slots twin-paired",
                  all(len(s.copies) >= 2 for s in slots))
            check("placeholders flagged",
                  sum(1 for s in slots if s.is_placeholder) >= 40)
            company = open("sample_input/company_rough.txt", encoding="utf-8").read()
            mapping = sanitize_mapping(slots, rules_mapping(company, slots, org="TestCo"))
            trim = unfillable_pages(slots, mapping)
            for s in slots:
                if (s.kind == "body" and s.page not in trim
                        and s.slot_id not in mapping
                        and any(h in s.text.lower() for h in _LOREM_HINTS)):
                    mapping[s.slot_id] = ""
            out2 = fill_slots(tpl, mapping, os.path.join(tmp, "filled.docx"),
                              slots=slots, trim_pages=trim,
                              images=[os.path.join(ROOT, "sample_input/test_photos/photo_1.png")])
            xml2 = docx_text(out2)
            check("no lorem left in filled doc",
                  "doluptatint" not in xml2 and "utamsa" not in xml2)
            check("photo placed",
                  zipfile.ZipFile(out2).namelist() != zipfile.ZipFile(tpl).namelist())
        else:
            print("  SKIP  user template not present")

        # ------------------------------------------------ chart engine fonts
        section("Chart engine")
        from app.chart_engine import _font_covers_arabic
        check("Segoe UI covers Arabic presentation forms",
              _font_covers_arabic("Segoe UI"))
        check("Lusail correctly rejected",
              not _font_covers_arabic("Lusail"))

        # ------------------------------------------------ advanced visuals
        section("Advanced visuals")
        from app import chart_engine, cover_art
        colors = tm.get("executive_premium").resolved_colors(
            tm.get("executive_premium").variants[0])
        for ctype in ("pictogram", "progress", "funnel", "versus"):
            vals = [80, 60] if ctype == "versus" else [80, 60, 40]
            cats = ["A / أ", "B / ب"] if ctype == "versus" else ["A / أ", "B / ب", "C / ج"]
            buf = chart_engine.render_chart(ctype, cats, [("", vals)],
                                            "t", colors)
            check(f"chart type {ctype} renders", len(buf.getvalue()) > 5000)
        meta = {"title": "T / عنوان", "organization": "Org / وزارة الداخلية"}
        for style in ("geometric", "contours", "halftone", "diagonal"):
            buf = cover_art.render_cover_png(style, meta, colors)
            check(f"cover style {style} renders", len(buf.getvalue()) > 20000)
        # the RTL space fix: two Arabic words must stay two pieces with a space
        lines = cover_art._wrap_mixed("وزارة الداخلية", "modern", 40, 4000)
        flat = "".join(p for p, _f, *_ in lines[0])
        check("Arabic words keep their space on covers", " " in flat.strip())

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'=' * 40}\nRESULT: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
