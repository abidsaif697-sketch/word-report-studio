#!/usr/bin/env python3
"""
Command-line entry point for Word Report Studio.
Fully offline: reads a content file (Markdown-ish or JSON), generates
several designer-grade .docx layout options, and optionally exports PDFs
and an HTML comparison sheet.

Example:
    python run_cli.py --input sample_input/sample_bilingual.md \
        --title "Annual Report / التقرير السنوي" \
        --org "Acme Holdings" --date 2026-07-09 \
        --num-options 6 --pdf
"""
from __future__ import annotations
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Windows consoles default to a legacy codepage (e.g. cp1252) that can't
# encode Arabic text in titles/labels; force UTF-8 stdout/stderr so
# bilingual content prints correctly instead of crashing on encode.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

from app.structure_model import DocumentMeta, LangMode
from app.content_parser import ContentParser
from app.template_manager import TemplateManager
from app.layout_engine import LayoutEngine
from app.docx_renderer import DocxRenderer
from app import pdf_exporter
from app import preview_gallery
from app import data_insights
from app import chart_engine
from app.offline_brain import OfflineDocumentBrain, format_brain_summary

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")


def main():
    from cleanup import run_cleanup
    run_cleanup()
    ap = argparse.ArgumentParser(description="Generate offline bilingual Word report layouts.")
    ap.add_argument("--input", required=True, help="Path to Markdown-ish or JSON content file")
    ap.add_argument("--title", default="Untitled Report")
    ap.add_argument("--subtitle", default=None)
    ap.add_argument("--org", default=None, dest="organization")
    ap.add_argument("--author", default=None)
    ap.add_argument("--date", default=None)
    ap.add_argument("--confidential", default=None, dest="confidentiality")
    ap.add_argument("--lang", choices=["auto", "en", "ar", "bilingual"], default="auto")
    ap.add_argument("--templates", default=None,
                     help="Comma-separated template ids (default: all available)")
    ap.add_argument("--num-options", type=int, default=6)
    ap.add_argument("--output-dir", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "output"))
    ap.add_argument("--pdf", action="store_true", help="Also export PDF previews via LibreOffice (offline)")
    ap.add_argument("--no-cover", action="store_true")
    ap.add_argument("--no-toc", action="store_true")
    ap.add_argument("--no-auto-visuals", action="store_true",
                     help="Disable automatic visual upgrades (tables->charts, "
                          "KPI extraction, timelines, inferred headings)")
    ap.add_argument("--ai", choices=["auto", "on", "off"], default="auto",
                     help="Use a local Ollama model to understand and "
                          "restructure rough text (auto: use when installed)")
    ap.add_argument("--fill-template", default=None, metavar="TEMPLATE_DOCX",
                     help="Fill a designer Word template's text boxes with "
                          "the input content (keeps the design pixel-perfect)")
    ap.add_argument("--images", default=None, metavar="FOLDER",
                     help="Folder of photos to place into the template's "
                          "'IMAGE HERE' frames (used with --fill-template)")
    ap.add_argument("--design-review", action="store_true",
                     help="After PDF export, have the local vision model "
                          "LOOK at each page and report visual problems")
    ap.add_argument("--no-branding", action="store_true",
                     help="Ignore branding.json for this run")
    args = ap.parse_args()

    if args.input.lower().endswith(".docx"):
        from app.docx_ingest import docx_to_markdown, source_title
        content = docx_to_markdown(args.input)
        if args.title == "Untitled Report":
            args.title = source_title(args.input) or args.title
        print(f"Imported Word document with structure intact: {args.input}")
    else:
        with open(args.input, "r", encoding="utf-8") as f:
            content = f.read()

    lang_map = {"auto": None, "en": LangMode.ENGLISH, "ar": LangMode.ARABIC, "bilingual": LangMode.BILINGUAL}
    forced_lang = lang_map[args.lang]

    meta = DocumentMeta(
        title=args.title, subtitle=args.subtitle, author=args.author,
        organization=args.organization, date=args.date, confidentiality=args.confidentiality,
        lang_mode=forced_lang,  # None triggers content-based auto-detection
    )

    if args.fill_template:
        from app.template_filler import (extract_slots, describe_slots,
                                         fill_slots, rules_mapping)
        from app.llm_brain import (LocalLLMBrain, map_content_to_slots_chunked,
                                   describe_status)
        slots = extract_slots(args.fill_template)
        print(f"Template slots found: {len(slots)} across "
              f"{max(s.page for s in slots)} pages")
        mapping = None
        if args.ai != "off":
            brain = LocalLLMBrain()
            print(describe_status(brain))
            if brain.is_available():
                print("  Assigning your content to the designed slots, "
                      "a few pages at a time...")
                mapping = map_content_to_slots_chunked(brain, content, slots,
                                                       log=print)
                if mapping is None:
                    print(f"  Local AI skipped: {brain.last_error}")
        from app.template_filler import sanitize_mapping
        if mapping is None:
            mapping = rules_mapping(content, slots, org=args.organization,
                                    title=args.title)
            print("  Using rule-based slot mapping.")
            mapping = sanitize_mapping(slots, mapping)
        else:
            mapping = sanitize_mapping(slots, mapping)
            # completion pass: lorem must never survive into the output
            leftovers = rules_mapping(content, slots, org=args.organization,
                                      title=args.title, exclude=mapping)
            leftovers = sanitize_mapping(slots, leftovers)
            if leftovers:
                print(f"  Completion pass filled {len(leftovers)} slot(s) "
                      "the model skipped.")
                mapping.update(leftovers)
        from app.template_filler import unfillable_pages, _LOREM_HINTS
        trim = unfillable_pages(slots, mapping)
        # blank any lorem stragglers on pages we keep
        for s in slots:
            if (s.kind == "body" and s.page not in trim
                    and s.slot_id not in mapping
                    and any(h in s.text.lower() for h in _LOREM_HINTS)):
                mapping[s.slot_id] = ""
        os.makedirs(args.output_dir, exist_ok=True)
        base = os.path.splitext(os.path.basename(args.fill_template))[0]
        out_path = os.path.join(args.output_dir, f"filled__{base}.docx")
        images = None
        if args.images:
            from app.image_filler import collect_images
            images = collect_images(args.images)
            print(f"Photos provided: {len(images)}")
        fill_slots(args.fill_template, mapping, out_path, slots=slots,
                   trim_pages=trim, images=images, log=print)
        if trim:
            print(f"Trimmed {len(trim)} designed page(s) the content "
                  f"could not fill: {sorted(trim)}")
        print(f"Filled {len(mapping)} slot(s) -> {out_path}")
        from app.template_filler import sample_data_warnings
        warns = sample_data_warnings(slots, mapping)
        if warns:
            print("REVIEW BEFORE SENDING — template sample data kept:")
            for w in warns[:12]:
                print(f"  ! {w}")
        if args.pdf:
            pdf = pdf_exporter.convert_to_pdf(out_path, args.output_dir)
            print(f"PDF preview: {pdf}")
            if args.design_review:
                from app.vision_critic import review_pdf
                review_pdf(pdf, log=print)
        return

    if args.ai != "off":
        from app.llm_brain import LocalLLMBrain, describe_status
        llm = LocalLLMBrain()
        print(describe_status(llm))
        if llm.is_available():
            print("  Understanding content with the local model (this can take "
                  "a minute for long documents)...")
            structured = llm.restructure(content)
            if structured:
                content = structured
            else:
                print(f"  Local AI skipped: {llm.last_error}")
        elif args.ai == "on":
            print("  --ai on was requested but no local model is reachable; "
                  "continuing with the rule-based formatter.")

    from app.branding import load_brand, apply_to_template
    brand = None if args.no_branding else load_brand()
    if brand is not None and brand.enabled:
        print(f"Branding active: {brand.organization or 'unnamed brand'} "
              f"(colors {brand.primary or '-'}/{brand.secondary or '-'}/"
              f"{brand.accent or '-'})")
        if brand.organization and not meta.organization:
            meta.organization = brand.organization

    parser = ContentParser()
    report = parser.parse_auto(content, meta=meta,
                               auto_visuals=not args.no_auto_visuals)
    if brand is not None and brand.enabled:
        report.branding = brand
    if forced_lang is not None:
        report.meta.lang_mode = forced_lang
    report.include_cover = not args.no_cover
    report.include_toc = not args.no_toc

    brain = OfflineDocumentBrain()
    brain_analysis = brain.analyze(content, report)
    brain.apply_to_report(report, brain_analysis)

    print(f"[1/4] Parsed content. Language mode: {report.meta.lang_mode.value}")
    if getattr(report, "auto_enrichment", None) is not None:
        from app.auto_enrich import format_enrichment
        print(format_enrichment(report.auto_enrichment))
    print(format_brain_summary(brain_analysis))
    analysis = data_insights.analyze(report)
    print(data_insights.format_analysis(analysis))
    stats = analysis["stats"]
    chart_visuals = stats["charts"] + stats["timelines"] + stats["processes"]
    if chart_visuals and not chart_engine.is_available():
        print(
            "  Note: matplotlib is not installed, so charts/timelines/process "
            "diagrams will be rendered as editable Word-native visual tables. Run "
            "`pip install -r requirements.txt` to enable PNG chart rendering."
        )

    tm = TemplateManager(TEMPLATES_DIR)
    engine = LayoutEngine(tm)
    template_ids = [t.strip() for t in args.templates.split(",")] if args.templates else None
    options = engine.generate_options(
        report,
        max_options=args.num_options,
        template_ids=template_ids,
        brain_analysis=brain_analysis,
    )
    print(f"[2/4] Selected {len(options)} layout option(s): " + ", ".join(o.label for o in options))

    if brand is not None and brand.enabled:
        for opt in options:
            opt.template = apply_to_template(brand, opt.template)

    os.makedirs(args.output_dir, exist_ok=True)
    renderer = DocxRenderer()
    results = renderer.render_batch(report, options, output_dir=args.output_dir)
    docx_paths = {opt.option_id: path for opt, path in results}
    print(f"[3/4] Rendered {len(results)} .docx file(s) to {args.output_dir}")
    for opt, path in results:
        print(f"   - {opt.label}: {path}")

    pdf_paths, thumb_paths = {}, {}
    if args.pdf:
        if pdf_exporter.is_available():
            pdf_map = pdf_exporter.convert_batch(list(docx_paths.values()), output_dir=args.output_dir)
            failed = {k: v for k, v in pdf_map.items() if v.startswith("ERROR:")}
            for opt, docx_path in results:
                pdf_paths[opt.option_id] = pdf_map.get(docx_path, "")
            thumb_paths = preview_gallery.render_thumbnails(pdf_paths, os.path.join(args.output_dir, "thumbnails"))
            if failed:
                print(f"[4/4] PDF export failed for {len(failed)} of {len(pdf_map)} file(s):")
                for path, err in failed.items():
                    print(f"   - {path}: {err}")
            else:
                print("[4/4] Exported PDF previews.")
        else:
            print("[4/4] LibreOffice not found on this machine — skipped PDF export "
                  "(install it to enable --pdf; the .docx files are already complete).")
    else:
        print("[4/4] Skipped PDF export (pass --pdf to enable).")

    manifest = preview_gallery.build_manifest(options, docx_paths, pdf_paths, thumb_paths)
    contact_sheet = preview_gallery.write_html_contact_sheet(
        manifest, os.path.join(args.output_dir, "layout_options.html"), doc_title=meta.title
    )
    print(f"\nComparison sheet: {contact_sheet}")


if __name__ == "__main__":
    main()
