"""
docx_renderer.py
-------------------
Turns a (ReportDocument, TemplateSpec, Variant) triple into an actual .docx
file using python-docx, entirely offline. Handles mixed Arabic (RTL) /
English (LTR) content at the run level so a single paragraph can correctly
contain both scripts, plus cover pages, TOC field, headers/footers with page
numbers, styled tables, callout boxes, and quotes.

Known limitations (documented, not silently hidden):
- The Word TOC field and PAGE field are inserted as live fields; Word needs
  "Update Field" (F9 / right-click) once on open to populate real numbers,
  since we are not running Word itself to paginate.
"""

from __future__ import annotations
import os
import re
import sys
from typing import List, Optional, Tuple

from docx import Document
from docx.shared import Inches, Pt, Mm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn

from .structure_model import (
    ReportDocument, Section, Block,
    Heading, Paragraph, ListBlock, TableBlock, ImageBlock, Quote, Callout, PageBreak,
    KpiBlock, ChartBlock, TimelineBlock, ProcessBlock, KPI_ICONS,
    Run, runs_from_text,
)
from .template_manager import TemplateSpec, Variant, ColorSpec
from . import chart_engine
from .chart_engine import _tint as tint
from .docx_xml_helpers import (
    set_paragraph_bidi, set_run_rtl, set_run_font, set_paragraph_shading,
    set_cell_background, set_paragraph_border_bottom, set_paragraph_border_left,
    add_page_number_field, add_toc_field, safe_set_table_style,
    set_table_cell_margins, set_table_borders, set_cell_borders,
    set_row_height, set_vertical_alignment, set_table_width,
    set_table_fixed_layout, set_table_grid, set_cell_width,
    set_repeat_table_header, set_row_cant_split,
)


def slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip().lower()
    text = re.sub(r"[\s_-]+", "_", text, flags=re.UNICODE)
    return text or "report"


def _warn_visual_fallback(kind: str, label: str, exc: Exception):
    """A designer visual failed to render as an image and is degrading to the
    editable-table fallback. That must never be silent: the fallback looks
    plainer, and hiding the cause makes the degradation impossible to fix."""
    print(f"[docx_renderer] {kind} '{label}' could not be rendered as an image; "
          f"using the table fallback instead. Cause: {exc!r}", file=sys.stderr)


_REMOTE_PATH_RE = re.compile(r"^(?:https?|ftp)://", re.IGNORECASE)
_ARABIC_DIGIT_TRANS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
_CONTENT_WIDTH_DXA = 9360
_TABLE_INDENT_DXA = 120
_BLOCKS = "\u258f\u258e\u258d\u258c\u258b\u258a\u2589\u2588"
_CALLOUT_ICONS = {"info": "\u24d8", "warning": "!", "success": "\u2713"}


class DocxRenderer:

    _last_break_el = None  # last pending page-break carrier paragraph (see _request_page_break)

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def render(self, report: ReportDocument, template: TemplateSpec, variant: Variant,
               output_path: str) -> str:
        colors = template.resolved_colors(variant)
        cover_style = template.resolved_cover_style(variant)
        cover_art_style = template.resolved_cover_art(variant)

        word = Document()
        self._last_break_el = None
        self._set_core_properties(word, report)
        self._apply_normal_defaults(word, template, colors)
        self._setup_page(word, template)

        if report.include_cover:
            self._render_cover(word, report, template, colors, cover_style, cover_art_style)

        self._setup_header_footer(word, report, template, colors)

        front_matter = report.include_cover
        if report.include_toc:
            if front_matter:
                self._request_page_break(word)
            self._render_toc_page(word, report, template, colors)
            front_matter = True

        for idx, section in enumerate(report.sections):
            if template.section_dividers and section.title:
                if idx > 0 or front_matter:
                    self._request_page_break(word)
                self._render_section_divider(word, section, template, colors,
                                              number=f"{idx + 1:02d}")
                self._render_section(word, section, template, colors, level=1,
                                      skip_title=True)
            else:
                if idx == 0 and front_matter:
                    self._request_page_break(word)
                self._render_section(word, section, template, colors, level=1)

        if report.appendices:
            self._request_page_break(word)
            appx_title = self._bilingual_label(report, "Appendices", "الملاحق")
            hp = word.add_paragraph(style="Heading 1")
            self._write_runs(hp, runs_from_text(appx_title), template.fonts.heading_en,
                              template.fonts.heading_ar, template.font_sizes.h2, colors.primary, bold=True)
            self._apply_paragraph_direction(hp, runs_from_text(appx_title))
            for section in report.appendices:
                self._render_section(word, section, template, colors, level=1)

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        word.save(output_path)
        return output_path

    def render_batch(self, report: ReportDocument, options: List, output_dir: str,
                      base_filename: Optional[str] = None) -> List[Tuple[object, str]]:
        """options: List[layout_engine.LayoutOption]. Returns [(option, filepath), ...]."""
        os.makedirs(output_dir, exist_ok=True)
        base = slugify(base_filename or report.meta.title)
        results = []
        for opt in options:
            filename = f"{base}__{opt.option_id}.docx"
            path = os.path.join(output_dir, filename)
            self.render(report, opt.template, opt.variant, path)
            results.append((opt, path))
        return results

    # ------------------------------------------------------------------
    # Page / style setup
    # ------------------------------------------------------------------

    def _set_core_properties(self, word: Document, report: ReportDocument):
        """Set explicit, non-personal core metadata for official documents."""
        props = word.core_properties
        props.title = report.meta.title or "Untitled Report"
        props.subject = report.meta.subtitle or "Offline generated report"
        props.author = report.meta.author or report.meta.organization or "Word Report Studio Offline"
        props.comments = report.meta.confidentiality or "Generated fully offline; no cloud service used."
        props.keywords = "offline, confidential, bilingual, report"
        try:
            props.last_modified_by = "Word Report Studio Offline"
        except Exception:
            pass

    def _apply_normal_defaults(self, word: Document, template: TemplateSpec,
                                colors: ColorSpec):
        """Restyle Word's built-in styles to the template spec so the whole
        document (including the TOC, which uses Heading styles) carries the
        design — proper line spacing and space-after are most of what makes
        text look typeset instead of typed."""
        try:
            normal = word.styles["Normal"]
            normal.font.name = template.fonts.body_en
            normal.font.size = Pt(template.font_sizes.body)
            normal.paragraph_format.line_spacing = template.line_spacing
            normal.paragraph_format.space_after = Pt(7)
        except Exception:
            pass
        heading_sizes = {1: template.font_sizes.h2, 2: template.font_sizes.h3,
                         3: template.font_sizes.h4, 4: max(template.font_sizes.h4 - 1, 10)}
        for level, size in heading_sizes.items():
            try:
                st = word.styles[f"Heading {level}"]
                st.font.name = template.fonts.heading_en
                st.font.size = Pt(size)
                st.font.bold = True
                st.font.color.rgb = RGBColor.from_string(colors.primary)
                pf = st.paragraph_format
                pf.space_before = Pt(20 if level == 1 else 14 if level == 2 else 10)
                pf.space_after = Pt(8 if level == 1 else 6)
                pf.keep_with_next = True
            except Exception:
                pass

    def _request_page_break(self, word: Document):
        """Start the *next* content on a fresh page without ever producing a
        fully blank page. An explicit break run (word.add_page_break) occupies
        a line of its own, so when the preceding content already fills the
        page — a full-height cover, a table ending at the margin, or a user
        %%pagebreak%% right before an automatic one — the break paragraph
        spills onto its own empty page. pageBreakBefore has "only break if
        not already at a page top" semantics, which is what we want.
        Consecutive requests with nothing rendered in between collapse into
        one, so an explicit %%pagebreak%% at a section boundary does not
        stack with the section divider's automatic break."""
        if self._last_break_el is not None:
            nxt = self._last_break_el.getnext()
            # still the last real block? (body always ends with w:sectPr)
            if nxt is None or nxt.tag == qn("w:sectPr"):
                return
        p = word.add_paragraph()
        pf = p.paragraph_format
        pf.page_break_before = True
        pf.space_before = Pt(0)
        pf.space_after = Pt(0)
        pf.line_spacing = Pt(2)  # exact 2pt line: an invisible carrier paragraph
        self._last_break_el = p._p

    def _setup_page(self, word: Document, template: TemplateSpec):
        section = word.sections[0]
        if template.page.size.upper() == "A4":
            section.page_width = Mm(210)
            section.page_height = Mm(297)
        else:
            section.page_width = Inches(8.5)
            section.page_height = Inches(11)
        section.top_margin = Inches(template.page.margin_top_in)
        section.bottom_margin = Inches(template.page.margin_bottom_in)
        section.left_margin = Inches(template.page.margin_left_in)
        section.right_margin = Inches(template.page.margin_right_in)
        # Note: "mirror margins" (binding gutter for printed/bound reports) is deliberately
        # not injected via raw XML here -- the sectPr child ordering for that flag is easy
        # to get subtly wrong without a live Word install to verify against, and a mangled
        # sectPr can trigger Word's "needs repair" dialog. template.page.mirror_margins is
        # kept in the schema for a future, verified implementation; set section.left_margin/
        # right_margin manually per-template in the meantime if you need asymmetric margins.

    def _content_width_in(self, template: TemplateSpec) -> float:
        page_w = 8.27 if template.page.size.upper() == "A4" else 8.5
        return max(5.5, page_w - template.page.margin_left_in - template.page.margin_right_in)

    def _content_width_dxa(self, template: TemplateSpec, indent_dxa: int = _TABLE_INDENT_DXA) -> int:
        return max(7200, int(round(self._content_width_in(template) * 1440)) - indent_dxa)

    def _apply_table_geometry(self, table, widths_dxa: List[int],
                              indent_dxa: int = _TABLE_INDENT_DXA):
        table.autofit = False
        set_table_fixed_layout(table)
        set_table_width(table, sum(widths_dxa), indent_dxa)
        set_table_grid(table, widths_dxa)
        for row in table.rows:
            # rows in this engine are always short (a data line, a KPI card,
            # a callout) — never let Word split one across pages
            set_row_cant_split(row)
            for ci, cell in enumerate(row.cells):
                set_cell_width(cell, widths_dxa[min(ci, len(widths_dxa) - 1)])

    def _setup_header_footer(self, word: Document, report: ReportDocument,
                              template: TemplateSpec, colors: ColorSpec):
        section = word.sections[0]
        section.header_distance = Inches(0.5)
        section.footer_distance = Inches(0.5)

        if report.include_cover:
            # Suppress the running header/footer on the cover page itself (page 1);
            # first_page_header/first_page_footer are left as their default blank
            # paragraphs, so the cover reads clean while page 2+ still get the
            # title/page-number footer set up below.
            section.different_first_page_header_footer = True

        header = section.header
        hp = header.paragraphs[0]
        hp.text = ""
        # brand logo leads the header when a branding pack is active
        brand = getattr(report, "branding", None)
        if brand is not None:
            from .branding import logo_exists
            if logo_exists(brand):
                try:
                    logo_run = hp.add_run()
                    logo_run.add_picture(brand.logo_path, height=Inches(0.28))
                    hp.add_run("   ")
                except Exception as exc:
                    _warn_visual_fallback("header logo", brand.logo_path, exc)
        title_runs = runs_from_text(report.meta.title or "")
        self._write_runs(hp, title_runs, template.fonts.body_en, template.fonts.body_ar,
                          template.font_sizes.caption, colors.muted)
        self._apply_paragraph_direction(hp, title_runs, align_override=WD_ALIGN_PARAGRAPH.CENTER)
        set_paragraph_border_bottom(hp, tint(colors.primary, 0.6).lstrip("#"), size=6)
        if report.meta.confidentiality:
            cp = header.add_paragraph()
            conf_runs = runs_from_text(report.meta.confidentiality)
            self._write_runs(cp, conf_runs, template.fonts.body_en, template.fonts.body_ar,
                              template.font_sizes.caption, colors.accent, bold=True)
            self._apply_paragraph_direction(
                cp, conf_runs, align_override=WD_ALIGN_PARAGRAPH.CENTER
            )
            cp.paragraph_format.space_after = Pt(0)

        if template.header_footer.show_page_numbers:
            footer = section.footer
            fp = footer.paragraphs[0]
            fp.text = ""
            # document title (+ confidentiality) on the left, page number on
            # the right via a right-aligned tab stop at the text edge
            is_a4 = template.page.size.upper() == "A4"
            page_w = 8.27 if is_a4 else 8.5
            content_w = page_w - template.page.margin_left_in - template.page.margin_right_in
            from docx.enum.text import WD_TAB_ALIGNMENT
            fp.paragraph_format.tab_stops.add_tab_stop(
                Inches(content_w), WD_TAB_ALIGNMENT.RIGHT)
            left_bits = [template.header_footer.footer_text or report.meta.title or ""]
            if report.meta.confidentiality:
                left_bits.append(report.meta.confidentiality)
            left_text = "   ·   ".join(b for b in left_bits if b)
            left_runs = runs_from_text(left_text)
            self._write_runs(fp, left_runs, template.fonts.body_en, template.fonts.body_ar,
                              template.font_sizes.caption, colors.muted)
            fp.add_run("\t")
            add_page_number_field(fp)
            for r in fp.runs[-2:]:
                set_run_font(r, ascii_font=template.fonts.body_en,
                             size_pt=template.font_sizes.caption, color_hex=colors.muted)

    # ------------------------------------------------------------------
    # Cover page styles
    # ------------------------------------------------------------------

    def _render_cover(self, word: Document, report: ReportDocument, template: TemplateSpec,
                       colors: ColorSpec, style: str, art_style: str = ""):
        if style == "image_hero":
            self._cover_image_hero(word, report, template, colors,
                                    art_style or template.cover.art)
            return
        method = {
            "centered_band": self._cover_centered_band,
            "minimal_top": self._cover_minimal_top,
            "side_bar": self._cover_side_bar,
            "full_bleed_footer": self._cover_full_bleed_footer,
        }.get(style, self._cover_centered_band)
        method(word, report, template, colors)

    def _cover_image_hero(self, word, report, template, colors, art_style: str):
        """Poster-quality painted cover (cover_art.py). Falls back to the
        centered band layout if image generation fails for any reason."""
        meta = {
            "title": report.meta.title or "",
            "subtitle": report.meta.subtitle,
            "organization": report.meta.organization,
            "author": report.meta.author,
            "date": report.meta.date,
            "confidentiality": report.meta.confidentiality,
        }
        is_a4 = template.page.size.upper() == "A4"
        page_w, page_h = (8.27, 11.69) if is_a4 else (8.5, 11.0)
        img_w = page_w - template.page.margin_left_in - template.page.margin_right_in
        img_h = page_h - template.page.margin_top_in - template.page.margin_bottom_in - 0.12
        try:
            from . import cover_art
            buf = cover_art.render_cover_png(art_style, meta, colors)
            word.add_picture(buf, width=Inches(img_w), height=Inches(img_h))
            word.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
            word.paragraphs[-1].paragraph_format.space_after = Pt(0)
        except Exception:
            self._cover_centered_band(word, report, template, colors)

    # Content area with 1in margins on A4 is ~9.7in tall; keep the cover
    # composition slightly shorter so it never spills to a second page.
    _COVER_HEIGHT_IN = 9.15
    _COVER_WIDTH_IN = 6.27
    _COVER_TITLE_PT = 38
    _COVER_SUB_PT = 15

    def _cover_meta_lines(self, report: ReportDocument) -> List[str]:
        lines = []
        if report.meta.organization:
            lines.append(report.meta.organization)
        if report.meta.author:
            lines.append(report.meta.author)
        if report.meta.date:
            lines.append(report.meta.date)
        return lines

    def _cover_canvas(self, word, rows: int, col_widths: List[float]):
        """Borderless full-page table used as the cover's layout grid."""
        table = word.add_table(rows=rows, cols=len(col_widths))
        table.autofit = False
        set_table_borders(table, {"top": None, "bottom": None, "start": None,
                                   "end": None, "insideH": None, "insideV": None})
        set_table_cell_margins(table, 0, 0, 0, 0)
        for r in table.rows:
            for ci, cell in enumerate(r.cells):
                cell.width = Inches(col_widths[ci])
        return table

    def _cell_text(self, cell, text: str, template, size_pt: float, color: str,
                    bold=False, align=WD_ALIGN_PARAGRAPH.LEFT, space_after=6,
                    new_par=False, line_spacing=None):
        p = cell.add_paragraph() if new_par or cell.paragraphs[0].runs else cell.paragraphs[0]
        runs = runs_from_text(text)
        self._write_runs(p, runs, template.fonts.heading_en if bold else template.fonts.body_en,
                          template.fonts.heading_ar if bold else template.fonts.body_ar,
                          size_pt, color, bold=bold)
        p.alignment = align
        p.paragraph_format.space_after = Pt(space_after)
        if line_spacing:
            p.paragraph_format.line_spacing = line_spacing
        return p

    def _cover_footer_meta(self, cell, report, template, color_text: str,
                            color_rule: str, align=WD_ALIGN_PARAGRAPH.LEFT):
        meta_line = "     ".join(self._cover_meta_lines(report))
        if meta_line:
            rule = cell.add_paragraph() if cell.paragraphs[0].runs else cell.paragraphs[0]
            rule.paragraph_format.space_after = Pt(10)
            set_paragraph_border_bottom(rule, color_rule, size=12)
            self._cell_text(cell, meta_line, template, template.font_sizes.body,
                             color_text, align=align, new_par=True)
        if report.meta.confidentiality:
            self._cell_text(cell, report.meta.confidentiality, template,
                             template.font_sizes.caption, color_rule, bold=True,
                             align=align, new_par=True)

    def _cover_centered_band(self, word, report, template, colors):
        """Full-width color band across the middle with large white title."""
        table = self._cover_canvas(word, rows=3, col_widths=[self._COVER_WIDTH_IN])
        top, band, bottom = (r.cells[0] for r in table.rows)
        set_row_height(table.rows[0], 2.6)
        set_row_height(table.rows[1], 3.1)
        set_row_height(table.rows[2], self._COVER_HEIGHT_IN - 5.7)

        if report.meta.organization:
            self._cell_text(top, report.meta.organization.upper(), template,
                             template.font_sizes.h4, colors.secondary, bold=True)
        set_cell_background(band, colors.primary)
        set_vertical_alignment(band, "center")
        self._cell_text(band, report.meta.title, template, self._COVER_TITLE_PT,
                         "FFFFFF", bold=True, align=WD_ALIGN_PARAGRAPH.CENTER,
                         space_after=10, line_spacing=1.05)
        if report.meta.subtitle:
            self._cell_text(band, report.meta.subtitle, template, self._COVER_SUB_PT,
                             tint(colors.primary, 0.75).lstrip("#"), align=WD_ALIGN_PARAGRAPH.CENTER,
                             new_par=True)
        set_vertical_alignment(bottom, "bottom")
        self._cover_footer_meta(bottom, report, template, colors.text, colors.accent,
                                 align=WD_ALIGN_PARAGRAPH.CENTER)

    def _cover_minimal_top(self, word, report, template, colors):
        """Formal, restrained: thick accent rule, big title upper-left, airy space."""
        table = self._cover_canvas(word, rows=3, col_widths=[self._COVER_WIDTH_IN])
        head, mid, bottom = (r.cells[0] for r in table.rows)
        set_row_height(table.rows[0], 0.9)
        set_row_height(table.rows[1], 5.4)
        set_row_height(table.rows[2], self._COVER_HEIGHT_IN - 6.3)

        if report.meta.organization:
            self._cell_text(head, report.meta.organization.upper(), template,
                             template.font_sizes.body, colors.muted, bold=True,
                             space_after=8)
        rule = head.add_paragraph()
        set_paragraph_border_bottom(rule, colors.accent, size=32)

        set_vertical_alignment(mid, "center")
        self._cell_text(mid, report.meta.title, template, self._COVER_TITLE_PT - 2,
                         colors.primary, bold=True, space_after=12, line_spacing=1.05)
        if report.meta.subtitle:
            self._cell_text(mid, report.meta.subtitle, template, self._COVER_SUB_PT,
                             colors.secondary, new_par=True)

        set_vertical_alignment(bottom, "bottom")
        self._cover_footer_meta(bottom, report, template, colors.text, colors.accent)

    def _cover_side_bar(self, word, report, template, colors):
        """Consulting look: full-height color band on the left, title block right."""
        table = self._cover_canvas(word, rows=1, col_widths=[1.9, 0.35, 4.02])
        set_row_height(table.rows[0], self._COVER_HEIGHT_IN)
        bar, gap, body = table.rows[0].cells

        set_cell_background(bar, colors.primary)
        set_vertical_alignment(bar, "bottom")
        if report.meta.organization:
            self._cell_text(bar, report.meta.organization, template,
                             template.font_sizes.body, "FFFFFF", bold=True,
                             align=WD_ALIGN_PARAGRAPH.CENTER, space_after=20)
        if report.meta.date:
            self._cell_text(bar, report.meta.date, template, template.font_sizes.caption,
                             tint(colors.primary, 0.7).lstrip("#"),
                             align=WD_ALIGN_PARAGRAPH.CENTER, space_after=24, new_par=True)

        set_vertical_alignment(body, "center")
        accent_rule = body.paragraphs[0]
        accent_rule.paragraph_format.space_after = Pt(16)
        set_paragraph_border_bottom(accent_rule, colors.accent, size=40)
        self._cell_text(body, report.meta.title, template, self._COVER_TITLE_PT - 2,
                         colors.primary, bold=True, space_after=12, new_par=True,
                         line_spacing=1.05)
        if report.meta.subtitle:
            self._cell_text(body, report.meta.subtitle, template, self._COVER_SUB_PT,
                             colors.secondary, new_par=True, space_after=18)
        if report.meta.author:
            self._cell_text(body, report.meta.author, template, template.font_sizes.body,
                             colors.muted, new_par=True)
        if report.meta.confidentiality:
            self._cell_text(body, report.meta.confidentiality, template,
                             template.font_sizes.caption, colors.accent, bold=True,
                             new_par=True)

    def _cover_full_bleed_footer(self, word, report, template, colors):
        """Academic: centered title over generous whitespace, solid color base."""
        table = self._cover_canvas(word, rows=3, col_widths=[self._COVER_WIDTH_IN])
        top, mid, base = (r.cells[0] for r in table.rows)
        set_row_height(table.rows[0], 1.4)
        set_row_height(table.rows[1], 5.4)
        set_row_height(table.rows[2], self._COVER_HEIGHT_IN - 6.8)

        if report.meta.organization:
            self._cell_text(top, report.meta.organization.upper(), template,
                             template.font_sizes.body, colors.muted, bold=True,
                             align=WD_ALIGN_PARAGRAPH.CENTER)

        set_vertical_alignment(mid, "center")
        self._cell_text(mid, report.meta.title, template, self._COVER_TITLE_PT - 2,
                         colors.primary, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER,
                         space_after=14, line_spacing=1.05)
        rule = mid.add_paragraph()
        rule.paragraph_format.space_after = Pt(14)
        # short centered accent rule under the title
        rule.paragraph_format.left_indent = Inches(2.3)
        rule.paragraph_format.right_indent = Inches(2.3)
        set_paragraph_border_bottom(rule, colors.accent, size=28)
        if report.meta.subtitle:
            self._cell_text(mid, report.meta.subtitle, template, self._COVER_SUB_PT,
                             colors.secondary, align=WD_ALIGN_PARAGRAPH.CENTER, new_par=True)

        set_cell_background(base, colors.primary)
        set_vertical_alignment(base, "center")
        meta_line = "     ".join(self._cover_meta_lines(report))
        if meta_line:
            self._cell_text(base, meta_line, template, template.font_sizes.body,
                             "FFFFFF", align=WD_ALIGN_PARAGRAPH.CENTER)
        if report.meta.confidentiality:
            self._cell_text(base, report.meta.confidentiality, template,
                             template.font_sizes.caption,
                             tint(colors.primary, 0.7).lstrip("#"), bold=True,
                             align=WD_ALIGN_PARAGRAPH.CENTER, new_par=True)

    # ------------------------------------------------------------------
    # TOC
    # ------------------------------------------------------------------

    def _render_toc_page(self, word: Document, report: ReportDocument,
                          template: TemplateSpec, colors: ColorSpec):
        lang_mode = getattr(report.meta.lang_mode, "value", report.meta.lang_mode)
        if lang_mode == "ar":
            toc_title = "فهرس المحتويات"
        elif lang_mode == "bilingual":
            toc_title = "Table of Contents   |   فهرس المحتويات"
        else:
            toc_title = "Table of Contents"

        # Deliberately NOT using a "Heading N" style here: the TOC field below collects
        # every Heading-styled paragraph in the document, and using a heading style for
        # this title would make the Table of Contents list itself as its own first entry.
        heading = word.add_paragraph()
        title_runs = runs_from_text(toc_title)
        self._write_runs(heading, title_runs, template.fonts.heading_en, template.fonts.heading_ar,
                          template.font_sizes.h2, colors.primary, bold=True)
        self._apply_paragraph_direction(heading, title_runs, align_override=WD_ALIGN_PARAGRAPH.CENTER)
        add_toc_field(word)

    def _bilingual_label(self, report: ReportDocument, en: str, ar: str) -> str:
        lang_mode = getattr(report.meta.lang_mode, "value", report.meta.lang_mode)
        if lang_mode == "ar":
            return ar
        if lang_mode == "bilingual":
            return f"{en}   |   {ar}"
        return en

    # ------------------------------------------------------------------
    # Section / block rendering
    # ------------------------------------------------------------------

    def _heading_size(self, level: int, template: TemplateSpec) -> float:
        return {
            1: template.font_sizes.h2,
            2: template.font_sizes.h3,
            3: template.font_sizes.h4,
            4: max(template.font_sizes.h4 - 1, 10),
        }.get(max(1, min(level, 4)), template.font_sizes.h4)

    def _render_section_divider(self, word: Document, section: Section,
                                 template: TemplateSpec, colors: ColorSpec, number: str):
        """Chapter opener: full-width color band with a large chapter number,
        the chapter title (kept on a real Heading 1 style so the TOC still
        picks it up), and a thin accent line."""
        table = word.add_table(rows=1, cols=2)
        table.autofit = False
        total = self._content_width_dxa(template, indent_dxa=0)
        self._apply_table_geometry(table, [int(total * 0.84), total - int(total * 0.84)],
                                   indent_dxa=0)
        set_table_borders(table, {"top": None, "bottom": None, "start": None,
                                   "end": None, "insideH": None,
                                   # paint the cell seam in the band color so
                                   # the divider reads as one solid block
                                   "insideV": ("single", 48, colors.primary)})
        set_table_cell_margins(table, 220, 220, 260, 260)
        cell = table.rows[0].cells[0]
        icon_cell = table.rows[0].cells[1]
        set_cell_background(cell, colors.primary)
        set_cell_background(icon_cell, colors.primary)
        set_row_height(table.rows[0], 1.7)
        set_row_cant_split(table.rows[0])
        set_vertical_alignment(cell, "center")
        set_vertical_alignment(icon_cell, "center")

        # topical icon badge on the band (security -> shield, staffing ->
        # people, ...) — quietly skipped when no topic matches
        if chart_engine.is_available():
            try:
                from .icon_library import topic_icon
                key = topic_icon(section.title)
                if key:
                    badge = chart_engine.render_icon_badge(key, colors.accent,
                                                           size_in=0.62)
                    ip = icon_cell.paragraphs[0]
                    ip.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    ip.add_run().add_picture(badge, width=Inches(0.62))
            except Exception as exc:
                _warn_visual_fallback("divider icon", section.title or "", exc)

        num_p = cell.paragraphs[0]
        num_run = num_p.add_run(number)
        set_run_font(num_run, ascii_font=template.fonts.heading_en,
                     size_pt=template.font_sizes.h2 + 10, bold=True,
                     color_hex=colors.accent)
        num_p.paragraph_format.space_after = Pt(2)

        try:
            title_p = cell.add_paragraph(style="Heading 1")
        except KeyError:
            title_p = cell.add_paragraph()
        title_runs = runs_from_text(section.title)
        self._write_runs(title_p, title_runs, template.fonts.heading_en,
                          template.fonts.heading_ar, template.font_sizes.h2,
                          "FFFFFF", bold=True)
        self._apply_paragraph_direction(title_p, title_runs)
        title_p.paragraph_format.space_before = Pt(0)
        title_p.paragraph_format.space_after = Pt(6)

        rule_p = cell.add_paragraph()
        rule_run = rule_p.add_run("━━━━")
        set_run_font(rule_run, ascii_font=template.fonts.body_en, size_pt=8,
                     color_hex=colors.accent)
        word.add_paragraph()

    def _render_section(self, word: Document, section: Section, template: TemplateSpec,
                         colors: ColorSpec, level: int, skip_title: bool = False):
        if section.title and not skip_title:
            style_name = f"Heading {max(1, min(level, 4))}"
            try:
                p = word.add_paragraph(style=style_name)
            except KeyError:
                p = word.add_paragraph()
            runs = runs_from_text(section.title)
            size = self._heading_size(level, template)
            self._write_runs(p, runs, template.fonts.heading_en, template.fonts.heading_ar,
                              size, colors.primary, bold=True)
            self._apply_paragraph_direction(p, runs)
            if level == 1:
                # accent rule under top-level section titles
                set_paragraph_border_bottom(p, colors.accent, size=16)

        for block in section.blocks:
            self._render_block(word, block, template, colors, level)

        for sub in section.subsections:
            self._render_section(word, sub, template, colors, level + 1)

    def _render_block(self, word: Document, block: Block, template: TemplateSpec,
                       colors: ColorSpec, level: int):
        if isinstance(block, Heading):
            style_name = f"Heading {max(1, min(block.level, 4))}"
            try:
                p = word.add_paragraph(style=style_name)
            except KeyError:
                p = word.add_paragraph()
            size = self._heading_size(block.level, template)
            self._write_runs(p, block.runs, template.fonts.heading_en, template.fonts.heading_ar,
                              size, colors.primary, bold=True)
            self._apply_paragraph_direction(p, block.runs)

        elif isinstance(block, Paragraph):
            p = word.add_paragraph()
            self._write_runs(p, block.runs, template.fonts.body_en, template.fonts.body_ar,
                              template.font_sizes.body, colors.text)
            self._apply_paragraph_direction(p, block.runs)

        elif isinstance(block, ListBlock):
            self._render_list(word, block, template, colors)

        elif isinstance(block, TableBlock):
            self._render_table(word, block, template, colors)

        elif isinstance(block, ImageBlock):
            self._render_image(word, block, template, colors)

        elif isinstance(block, Quote):
            self._render_quote(word, block, template, colors)

        elif isinstance(block, Callout):
            self._render_callout(word, block, template, colors)

        elif isinstance(block, KpiBlock):
            self._render_kpi(word, block, template, colors)

        elif isinstance(block, ChartBlock):
            self._render_chart_block(word, block, template, colors)

        elif isinstance(block, TimelineBlock):
            self._render_timeline_block(word, block, template, colors)

        elif isinstance(block, ProcessBlock):
            self._render_process_block(word, block, template, colors)

        elif isinstance(block, PageBreak):
            self._request_page_break(word)

    def _render_list(self, word: Document, block: ListBlock, template: TemplateSpec, colors: ColorSpec):
        style_name = "List Number" if block.ordered else "List Bullet"
        for idx, item_runs in enumerate(block.items, start=1):
            if self._runs_are_rtl(item_runs):
                p = word.add_paragraph()
                marker = f"{self._arabic_index(idx)}. " if block.ordered else "• "
                marker_run = p.add_run(marker)
                set_run_font(
                    marker_run,
                    ascii_font=template.fonts.body_ar,
                    cs_font=template.fonts.body_ar,
                    size_pt=template.font_sizes.body,
                    bold=True,
                    color_hex=colors.secondary,
                )
                set_run_rtl(marker_run, True)
                self._write_runs(p, item_runs, template.fonts.body_en, template.fonts.body_ar,
                                  template.font_sizes.body, colors.text)
                self._apply_paragraph_direction(p, item_runs)
                p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                p.paragraph_format.right_indent = Inches(0.18)
                p.paragraph_format.space_after = Pt(3)
                continue
            try:
                p = word.add_paragraph(style=style_name)
            except KeyError:
                p = word.add_paragraph()
            self._write_runs(p, item_runs, template.fonts.body_en, template.fonts.body_ar,
                              template.font_sizes.body, colors.text)
            self._apply_paragraph_direction(p, item_runs)

    @staticmethod
    def _runs_are_rtl(runs: List[Run]) -> bool:
        ar_chars = sum(len(r.text) for r in runs if r.lang == "ar")
        en_chars = sum(len(r.text) for r in runs if r.lang != "ar")
        return ar_chars > en_chars

    @staticmethod
    def _arabic_index(idx: int) -> str:
        return str(idx).translate(_ARABIC_DIGIT_TRANS)

    _TREND_RE = re.compile(r"^[+-]\s*\d+(?:\.\d+)?\s*[%٪]?$")
    _NUMERIC_RE = re.compile(r"^[+-]?[\d,.]+\s*[%٪MKBmkb]?$")
    _TOTAL_WORDS = ("total", "grand total", "الإجمالي", "الاجمالي", "المجموع")

    def _render_table(self, word: Document, block: TableBlock, template: TemplateSpec, colors: ColorSpec):
        if not block.rows:
            return
        n_rows = len(block.rows)
        n_cols = max(len(r) for r in block.rows)
        table = word.add_table(rows=n_rows, cols=n_cols)
        widths = self._table_widths(n_cols, self._content_width_dxa(template))
        self._apply_table_geometry(table, widths)
        # Designed table: no vertical lines, hairline row separators, strong
        # baseline, generous padding, zebra striping in the template palette.
        set_table_borders(table, {
            "top": ("single", 10, colors.primary),
            "start": ("single", 4, tint(colors.primary, 0.78).lstrip("#")),
            "end": ("single", 4, tint(colors.primary, 0.78).lstrip("#")),
            "insideV": ("single", 3, tint(colors.primary, 0.88).lstrip("#")),
            "insideH": ("single", 4, tint(colors.primary, 0.86).lstrip("#")),
            "bottom": ("single", 12, colors.primary),
        })
        set_table_cell_margins(table, 115, 115, 155, 155)
        if block.header_row:
            set_repeat_table_header(table.rows[0])
            set_row_height(table.rows[0], 0.34, rule="atLeast")
        zebra = tint(colors.primary, 0.965).lstrip("#")

        for ri, row in enumerate(block.rows):
            is_header = block.header_row and ri == 0
            first_cell_text = "".join(r.text for r in row[0]).strip().lower() if row else ""
            is_total = (not is_header and ri == n_rows - 1
                        and any(first_cell_text.startswith(w) for w in self._TOTAL_WORDS))
            for ci in range(n_cols):
                cell = table.cell(ri, ci)
                cell.text = ""
                cell_runs = row[ci] if ci < len(row) else []
                cell_text = "".join(r.text for r in cell_runs).strip()
                p = cell.paragraphs[0]
                color_hex = "FFFFFF" if is_header else colors.text
                bold = True if (is_header or is_total) else None
                set_vertical_alignment(cell, "center")

                # trend cells like "+12%" / "-8%" get a colored direction arrow
                if not is_header and self._TREND_RE.match(cell_text):
                    up = cell_text.startswith("+")
                    arrow = p.add_run(("\u25b2 " if up else "\u25bc "))
                    set_run_font(arrow, ascii_font=template.fonts.body_en,
                                 size_pt=template.font_sizes.body, bold=True,
                                 color_hex=colors.success if up else colors.error)

                self._write_runs(p, cell_runs, template.fonts.body_en, template.fonts.body_ar,
                                  template.font_sizes.body, color_hex, bold=bold)
                self._apply_paragraph_direction(p, cell_runs)
                p.paragraph_format.space_after = Pt(0)
                p.paragraph_format.line_spacing = 1.12
                # numbers read better centered than justified in a data table
                if is_header:
                    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                elif ci > 0 and self._NUMERIC_RE.match(cell_text):
                    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                if is_header:
                    set_cell_background(cell, colors.primary)
                elif is_total:
                    set_cell_background(cell, tint(colors.primary, 0.88).lstrip("#"))
                elif ri % 2 == 0:
                    set_cell_background(cell, zebra)

        if block.caption:
            cap = word.add_paragraph()
            cap_runs = runs_from_text(block.caption)
            self._write_runs(cap, cap_runs, template.fonts.body_en, template.fonts.body_ar,
                              template.font_sizes.caption, colors.muted, italic=True)
            self._apply_paragraph_direction(cap, cap_runs)

        word.add_paragraph()

    @staticmethod
    def _table_widths(n_cols: int, total_dxa: int) -> List[int]:
        if n_cols <= 1:
            return [total_dxa]
        presets = {
            2: [0.60, 0.40],
            3: [0.42, 0.29, 0.29],
            4: [0.38, 0.21, 0.21, 0.20],
            5: [0.32, 0.17, 0.17, 0.17, 0.17],
        }
        shares = presets.get(n_cols)
        if not shares:
            first = 0.30
            rest = (1.0 - first) / (n_cols - 1)
            shares = [first] + [rest] * (n_cols - 1)
        widths = [int(total_dxa * s) for s in shares]
        widths[-1] += total_dxa - sum(widths)
        return widths

    def _render_image(self, word: Document, block: ImageBlock, template: TemplateSpec, colors: ColorSpec):
        if _REMOTE_PATH_RE.match((block.path or "").strip()):
            p = word.add_paragraph()
            note = runs_from_text(
                f"[Remote image blocked for offline confidential mode: {block.path}]"
            )
            self._write_runs(p, note, template.fonts.body_en, template.fonts.body_ar,
                              template.font_sizes.caption, colors.muted, italic=True)
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            return
        try:
            width = Inches(block.width_inches) if block.width_inches else Inches(5.5)
            word.add_picture(block.path, width=width)
            word.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
        except Exception:
            p = word.add_paragraph()
            missing_runs = runs_from_text(f"[Image not found: {block.path}]")
            self._write_runs(p, missing_runs, template.fonts.body_en, template.fonts.body_ar,
                              template.font_sizes.caption, colors.muted, italic=True)
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER

        if block.caption:
            cap = word.add_paragraph()
            cap_runs = runs_from_text(block.caption)
            self._write_runs(cap, cap_runs, template.fonts.body_en, template.fonts.body_ar,
                              template.font_sizes.caption, colors.muted, italic=True)
            cap.alignment = WD_ALIGN_PARAGRAPH.CENTER

    def _render_quote(self, word: Document, block: Quote, template: TemplateSpec, colors: ColorSpec):
        p = word.add_paragraph()
        p.paragraph_format.left_indent = Inches(0.25)
        p.paragraph_format.space_before = Pt(10)
        p.paragraph_format.space_after = Pt(10)
        set_paragraph_border_left(p, colors.accent, size=28, space=12)
        self._write_runs(p, block.runs, template.fonts.body_en, template.fonts.body_ar,
                          template.font_sizes.body + 1, colors.secondary, italic=True)
        self._apply_paragraph_direction(p, block.runs)

        if block.attribution:
            attr_runs = runs_from_text(f"— {block.attribution}")
            ap = word.add_paragraph()
            self._write_runs(ap, attr_runs, template.fonts.body_en, template.fonts.body_ar,
                              template.font_sizes.caption, colors.muted)
            self._apply_paragraph_direction(ap, attr_runs)

    def _render_callout(self, word: Document, block: Callout, template: TemplateSpec, colors: ColorSpec):
        """Modern accent-bar callout: thin colored bar on the left, softly
        tinted body, dark readable text — instead of a saturated color slab."""
        tone_color = {
            "info": colors.info,
            "warning": colors.warning,
            "success": colors.success,
        }.get(block.tone, colors.info)

        table = word.add_table(rows=1, cols=3)
        total = self._content_width_dxa(template)
        widths = [120, 520, total - 640]
        self._apply_table_geometry(table, widths)
        set_table_borders(table, {"top": None, "bottom": None, "start": None,
                                   "end": None, "insideH": None, "insideV": None})
        set_table_cell_margins(table, 125, 125, 150, 150)
        bar, icon_cell, body = table.rows[0].cells
        set_cell_background(bar, tone_color)
        set_cell_background(icon_cell, tone_color)
        set_cell_background(body, tint(tone_color, 0.9).lstrip("#"))
        set_vertical_alignment(icon_cell, "center")
        ip = icon_cell.paragraphs[0]
        ip.alignment = WD_ALIGN_PARAGRAPH.CENTER
        ir = ip.add_run(_CALLOUT_ICONS.get(block.tone, _CALLOUT_ICONS["info"]))
        set_run_font(ir, ascii_font=template.fonts.heading_en,
                     size_pt=template.font_sizes.h3, bold=True, color_hex="FFFFFF")

        body.text = ""
        p = body.paragraphs[0]
        if block.title:
            title_runs = runs_from_text(block.title)
            self._write_runs(p, title_runs, template.fonts.heading_en, template.fonts.heading_ar,
                              template.font_sizes.body, tone_color, bold=True)
            self._apply_paragraph_direction(p, title_runs)
            p.paragraph_format.space_after = Pt(4)
            body_p = body.add_paragraph()
        else:
            body_p = p

        self._write_runs(body_p, block.runs, template.fonts.body_en, template.fonts.body_ar,
                          template.font_sizes.body, colors.text)
        self._apply_paragraph_direction(body_p, block.runs)
        word.add_paragraph()

    # ------------------------------------------------------------------
    # Designer-grade visuals (charts / timelines / process / KPI cards)
    # ------------------------------------------------------------------

    def _visual_heading(self, word: Document, title: Optional[str], icon: str,
                        template: TemplateSpec, colors: ColorSpec):
        if not title:
            return
        p = word.add_paragraph()
        p.paragraph_format.space_before = Pt(8)
        p.paragraph_format.space_after = Pt(6)
        icon_run = p.add_run(f"{icon} ")
        set_run_font(icon_run, ascii_font=template.fonts.heading_en,
                     size_pt=template.font_sizes.h4, bold=True,
                     color_hex=colors.accent)
        self._write_runs(p, runs_from_text(title), template.fonts.heading_en,
                          template.fonts.heading_ar, template.font_sizes.h4,
                          colors.primary, bold=True)
        self._apply_paragraph_direction(p, runs_from_text(title))

    def _caption(self, word: Document, text: str, template: TemplateSpec,
                 colors: ColorSpec):
        cap = word.add_paragraph()
        cap.paragraph_format.space_before = Pt(3)
        cap.paragraph_format.space_after = Pt(8)
        cap_runs = runs_from_text(text)
        self._write_runs(cap, cap_runs, template.fonts.body_en,
                          template.fonts.body_ar, template.font_sizes.caption,
                          colors.muted, italic=True)
        self._apply_paragraph_direction(cap, cap_runs)

    def _render_chart_block(self, word: Document, block: ChartBlock,
                            template: TemplateSpec, colors: ColorSpec):
        if not chart_engine.is_available():
            _warn_visual_fallback("chart", block.title or block.chart_type,
                                  RuntimeError("matplotlib is not installed"))
        else:
            try:
                buf = chart_engine.render_chart(
                    block.chart_type, block.categories, block.series,
                    block.title, colors, font_family=template.fonts.body_en)
                word.add_picture(buf, width=Inches(min(5.8, self._content_width_in(template))))
                word.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
                if block.caption:
                    self._caption(word, block.caption, template, colors)
                word.add_paragraph()
                return
            except Exception as exc:
                _warn_visual_fallback("chart", block.title or block.chart_type, exc)
        self._render_chart_fallback(word, block, template, colors)

    def _render_chart_fallback(self, word: Document, block: ChartBlock,
                               template: TemplateSpec, colors: ColorSpec):
        self._visual_heading(word, block.title or block.caption or "Table Visualization",
                             "\u25a6", template, colors)
        categories = block.categories or [""]
        series = block.series or [("", [])]
        n_cols = 1 + len(series)
        table = word.add_table(rows=1 + len(categories), cols=n_cols)
        total = self._content_width_dxa(template)
        first = int(total * 0.34)
        rest = int((total - first) / max(len(series), 1))
        widths = [first] + [rest] * len(series)
        widths[-1] += total - sum(widths)
        self._apply_table_geometry(table, widths)
        set_table_cell_margins(table, 95, 95, 125, 125)
        set_table_borders(table, {
            "top": ("single", 10, colors.primary),
            "bottom": ("single", 10, colors.primary),
            "start": None, "end": None,
            "insideH": ("single", 4, tint(colors.primary, 0.88).lstrip("#")),
            "insideV": ("single", 4, tint(colors.primary, 0.90).lstrip("#")),
        })
        set_repeat_table_header(table.rows[0])

        header = table.rows[0]
        # an unnamed single series is a plain value column, not "Series 1"
        headers = ["Category"] + [
            (name or ("Value" if len(series) == 1 else f"Series {i + 1}"))
            for i, (name, _v) in enumerate(series)]
        for ci, label in enumerate(headers):
            cell = header.cells[ci]
            set_cell_background(cell, colors.primary)
            p = cell.paragraphs[0]
            self._write_runs(p, runs_from_text(label), template.fonts.body_en,
                              template.fonts.body_ar, template.font_sizes.caption,
                              "FFFFFF", bold=True)
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.space_after = Pt(0)

        all_values = [abs(float(v)) for _name, values in series for v in values]
        max_value = max(all_values) if all_values else 1.0
        palette = chart_engine.build_palette(colors)
        for ri, cat in enumerate(categories, start=1):
            row = table.rows[ri]
            set_vertical_alignment(row.cells[0], "center")
            cp = row.cells[0].paragraphs[0]
            self._write_runs(cp, runs_from_text(cat), template.fonts.body_en,
                              template.fonts.body_ar, template.font_sizes.caption,
                              colors.text, bold=True)
            self._apply_paragraph_direction(cp, runs_from_text(cat))
            if ri % 2 == 0:
                set_cell_background(row.cells[0], tint(colors.primary, 0.965).lstrip("#"))
            for si, (_name, values) in enumerate(series):
                cell = row.cells[si + 1]
                set_vertical_alignment(cell, "center")
                if ri % 2 == 0:
                    set_cell_background(cell, tint(colors.primary, 0.965).lstrip("#"))
                value = float(values[ri - 1]) if ri - 1 < len(values) else 0.0
                p = cell.paragraphs[0]
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                val_run = p.add_run(f"{value:g}  ")
                set_run_font(val_run, ascii_font=template.fonts.body_en,
                             size_pt=template.font_sizes.caption, bold=True,
                             color_hex=colors.text)
                bar_len = max(1, int(round(abs(value) / max_value * 12))) if max_value else 1
                bar = p.add_run(_BLOCKS[-1] * bar_len)
                set_run_font(bar, ascii_font=template.fonts.body_en,
                             size_pt=template.font_sizes.caption + 1,
                             bold=True, color_hex=palette[si % len(palette)].lstrip("#"))
                p.paragraph_format.space_after = Pt(0)
        if block.caption:
            self._caption(word, block.caption, template, colors)
        word.add_paragraph()

    def _render_timeline_block(self, word: Document, block: TimelineBlock,
                               template: TemplateSpec, colors: ColorSpec):
        if chart_engine.is_available():
            try:
                buf = chart_engine.render_timeline(block.items, block.title, colors)
                word.add_picture(buf, width=Inches(min(6.3, self._content_width_in(template))))
                word.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
                word.add_paragraph()
                return
            except Exception as exc:
                _warn_visual_fallback("timeline", block.title or "Timeline", exc)
        self._visual_heading(word, block.title or "Timeline", "\u25c6", template, colors)
        items = block.items or []
        if not items:
            return
        table = word.add_table(rows=len(items), cols=2)
        total = self._content_width_dxa(template)
        widths = [int(total * 0.22), total - int(total * 0.22)]
        self._apply_table_geometry(table, widths)
        set_table_cell_margins(table, 100, 100, 140, 140)
        set_table_borders(table, {
            "top": None, "bottom": None, "start": None, "end": None,
            "insideH": ("single", 8, "FFFFFF"), "insideV": ("single", 8, "FFFFFF"),
        })
        palette = chart_engine.build_palette(colors)
        for i, (marker, text) in enumerate(items):
            marker_cell, text_cell = table.rows[i].cells
            set_cell_background(marker_cell, palette[i % len(palette)].lstrip("#"))
            set_cell_background(text_cell, tint(palette[i % len(palette)], 0.92).lstrip("#"))
            set_vertical_alignment(marker_cell, "center")
            set_vertical_alignment(text_cell, "center")
            mp = marker_cell.paragraphs[0]
            self._write_runs(mp, runs_from_text(str(marker)), template.fonts.heading_en,
                              template.fonts.heading_ar, template.font_sizes.body,
                              "FFFFFF", bold=True)
            mp.alignment = WD_ALIGN_PARAGRAPH.CENTER
            tp = text_cell.paragraphs[0]
            self._write_runs(tp, runs_from_text(str(text)), template.fonts.body_en,
                              template.fonts.body_ar, template.font_sizes.body,
                              colors.text)
            self._apply_paragraph_direction(tp, runs_from_text(str(text)))
        word.add_paragraph()

    def _render_process_block(self, word: Document, block: ProcessBlock,
                              template: TemplateSpec, colors: ColorSpec):
        if chart_engine.is_available():
            try:
                buf = chart_engine.render_process(block.steps, block.title, colors)
                word.add_picture(buf, width=Inches(min(6.3, self._content_width_in(template))))
                word.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
                word.add_paragraph()
                return
            except Exception as exc:
                _warn_visual_fallback("process", block.title or "Process", exc)
        self._visual_heading(word, block.title or "Process", "\u25b8", template, colors)
        steps = [s for s in block.steps if s]
        if not steps:
            return
        cols = min(len(steps), 5)
        rows = (len(steps) + cols - 1) // cols
        table = word.add_table(rows=rows, cols=cols)
        total = self._content_width_dxa(template)
        widths = [int(total / cols)] * cols
        widths[-1] += total - sum(widths)
        self._apply_table_geometry(table, widths)
        set_table_cell_margins(table, 120, 120, 120, 120)
        set_table_borders(table, {
            "top": None, "bottom": None, "start": None, "end": None,
            "insideH": ("single", 28, "FFFFFF"), "insideV": ("single", 28, "FFFFFF"),
        })
        palette = chart_engine.build_palette(colors)
        for idx in range(rows * cols):
            cell = table.cell(idx // cols, idx % cols)
            cell.text = ""
            if idx >= len(steps):
                set_cell_background(cell, "FFFFFF")
                continue
            tone = palette[idx % len(palette)].lstrip("#")
            set_cell_background(cell, tone)
            set_cell_borders(cell, {"top": ("single", 4, tone), "bottom": ("single", 4, tone),
                                     "start": ("single", 4, tone), "end": ("single", 4, tone)})
            set_vertical_alignment(cell, "center")
            p = cell.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            n = p.add_run(f"{idx + 1:02d}\n")
            set_run_font(n, ascii_font=template.fonts.heading_en,
                         size_pt=template.font_sizes.caption, bold=True,
                         color_hex=tint(tone, 0.72).lstrip("#"))
            self._write_runs(p, runs_from_text(steps[idx]), template.fonts.heading_en,
                              template.fonts.heading_ar, template.font_sizes.caption,
                              "FFFFFF", bold=True)
            p.paragraph_format.space_after = Pt(0)
        word.add_paragraph()

    def _render_visual(self, word: Document, template: TemplateSpec, colors: ColorSpec,
                        width_in: float, caption: Optional[str], make, describe: str):
        """Embed a chart_engine-generated PNG (from an in-memory stream).
        A failed visual degrades to a note instead of failing the document."""
        try:
            buf = make()
            word.add_picture(buf, width=Inches(width_in))
            word.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
        except Exception as e:
            p = word.add_paragraph()
            note = runs_from_text(f"[Could not render {describe}: {e}]")
            self._write_runs(p, note, template.fonts.body_en, template.fonts.body_ar,
                              template.font_sizes.caption, colors.muted, italic=True)
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            return
        if caption:
            cap = word.add_paragraph()
            cap_runs = runs_from_text(caption)
            self._write_runs(cap, cap_runs, template.fonts.body_en, template.fonts.body_ar,
                              template.font_sizes.caption, colors.muted, italic=True)
            cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
        word.add_paragraph()

    _KPI_CARDS_PER_ROW = 4

    def _render_kpi(self, word: Document, block: KpiBlock, template: TemplateSpec,
                     colors: ColorSpec):
        """KPI figures as a designed infographic strip — donut gauges for
        percentages, themed icon badges for counts. Falls back to the fully
        editable native card table when image rendering is unavailable."""
        items = block.items
        if not items:
            return
        if chart_engine.is_available():
            try:
                buf = chart_engine.render_kpi_strip(
                    [(it.value, it.label, it.icon) for it in items],
                    colors, font_family=template.fonts.body_en)
                word.add_picture(buf, width=Inches(
                    min(0.4 + 1.55 * min(len(items), 4),
                        self._content_width_in(template))))
                word.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
                word.add_paragraph()
                return
            except Exception as exc:
                _warn_visual_fallback("kpi", items[0].label if items else "KPI", exc)
        per_row = min(len(items), self._KPI_CARDS_PER_ROW)
        n_rows = (len(items) + per_row - 1) // per_row
        table = word.add_table(rows=n_rows, cols=per_row)
        total = self._content_width_dxa(template)
        widths = [int(total / per_row)] * per_row
        widths[-1] += total - sum(widths)
        self._apply_table_geometry(table, widths)
        # Card look: no grid lines — thick white separators create gaps between
        # cards, and each card gets a colored top accent matching its trend.
        set_table_borders(table, {"top": None, "bottom": None, "start": None,
                                   "end": None, "insideH": ("single", 32, "FFFFFF"),
                                   "insideV": ("single", 32, "FFFFFF")})
        set_table_cell_margins(table, 100, 100, 100, 100)

        for idx, item in enumerate(items):
            cell = table.cell(idx // per_row, idx % per_row)
            cell.text = ""
            glyph, tone = KPI_ICONS.get(item.icon, ("", "neutral"))
            tone_hex = {"good": colors.success, "bad": colors.error,
                        "neutral": colors.secondary}[tone]
            # white card on thin borders per the design system (soft shadows
            # and corner radii don't exist in Word tables — border + accent
            # top is the faithful equivalent)
            set_cell_background(cell, tint(tone_hex, 0.93).lstrip("#"))
            set_cell_borders(cell, {"top": ("single", 40, tone_hex),
                                     "bottom": ("single", 4, colors.border),
                                     "start": ("single", 4, colors.border),
                                     "end": ("single", 4, colors.border)})
            set_vertical_alignment(cell, "center")

            vp = cell.paragraphs[0]
            vp.alignment = WD_ALIGN_PARAGRAPH.CENTER
            vp.paragraph_format.space_before = Pt(6)
            vp.paragraph_format.space_after = Pt(1)
            if glyph:
                icon_run = vp.add_run(glyph + "\n")
                set_run_font(icon_run, ascii_font=template.fonts.body_en,
                             size_pt=template.font_sizes.h3 + 1, bold=True,
                             color_hex=tone_hex)
            value_run = vp.add_run(item.value)
            set_run_font(value_run, ascii_font=template.fonts.heading_en,
                         size_pt=template.font_sizes.h2 + 2, bold=True,
                         color_hex=colors.primary)

            lp = cell.add_paragraph()
            label_runs = runs_from_text(item.label)
            self._write_runs(lp, label_runs, template.fonts.body_en, template.fonts.body_ar,
                              template.font_sizes.caption, colors.muted)
            lp.alignment = WD_ALIGN_PARAGRAPH.CENTER
            lp.paragraph_format.space_after = Pt(6)
            lp.paragraph_format.line_spacing = 1.08

        # pad any unused trailing cells in the last row so they read as blank
        for idx in range(len(items), n_rows * per_row):
            cell = table.cell(idx // per_row, idx % per_row)
            cell.text = ""
        word.add_paragraph()

    # ------------------------------------------------------------------
    # Run-level writer (handles per-run Arabic/English shaping)
    # ------------------------------------------------------------------

    def _write_runs(self, paragraph, runs: List[Run], ascii_en_font: str, ascii_ar_font: str,
                     size_pt: float, color_hex: Optional[str], bold: Optional[bool] = None,
                     italic: Optional[bool] = None):
        for r in runs:
            if r.text == "":
                continue
            run = paragraph.add_run(r.text)
            is_ar = r.lang == "ar"
            font_name = ascii_ar_font if is_ar else ascii_en_font
            set_run_font(
                run,
                ascii_font=font_name,
                cs_font=ascii_ar_font if is_ar else None,
                size_pt=size_pt,
                bold=bold if bold is not None else r.bold,
                italic=italic if italic is not None else r.italic,
                color_hex=color_hex,
            )
            if is_ar:
                set_run_rtl(run, True)

    def _apply_paragraph_direction(self, paragraph, runs: List[Run],
                                    align_override=None):
        ar_chars = sum(len(r.text) for r in runs if r.lang == "ar")
        en_chars = sum(len(r.text) for r in runs if r.lang != "ar")
        is_rtl = ar_chars > en_chars
        set_paragraph_bidi(paragraph, is_rtl)
        if align_override is not None:
            paragraph.alignment = align_override
        else:
            paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT if is_rtl else WD_ALIGN_PARAGRAPH.JUSTIFY
