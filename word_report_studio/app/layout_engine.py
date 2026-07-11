"""
layout_engine.py
------------------
Decides WHICH template + color/cover variant combinations to offer the user
as the "multiple design options" for a given document, and ranks them by
fit with the content (table-heavy -> favor consulting style, long TOC-heavy
docs -> favor academic style, etc). Does not touch python-docx directly;
docx_renderer.py does the actual rendering.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional, Dict

from .structure_model import (
    ReportDocument, Section, Heading, Paragraph, ListBlock, TableBlock,
    Callout, Quote, ImageBlock, ChartBlock, KpiBlock, TimelineBlock, ProcessBlock,
)
from .template_manager import TemplateManager, TemplateSpec, Variant
from .offline_brain import BrainAnalysis, OfflineDocumentBrain


@dataclass
class LayoutOption:
    option_id: str          # "<template_id>__<variant_id>", used as filename-safe key
    template: TemplateSpec
    variant: Variant
    score: float

    @property
    def label(self) -> str:
        return f"{self.template.name} — {self.variant.label}"


class LayoutEngine:
    def __init__(self, template_manager: TemplateManager):
        self.tm = template_manager

    def generate_options(
        self,
        doc: ReportDocument,
        max_options: int = 6,
        template_ids: Optional[List[str]] = None,
        brain_analysis: Optional[BrainAnalysis] = None,
    ) -> List[LayoutOption]:
        lang_mode = getattr(doc.meta.lang_mode, "value", doc.meta.lang_mode)
        templates = self.tm.list_templates(lang_mode=lang_mode)
        if template_ids:
            wanted = set(template_ids)
            templates = [t for t in templates if t.id in wanted]
        if not templates:
            templates = self.tm.list_templates()

        counts = self._content_counts(doc)
        brain_analysis = brain_analysis or OfflineDocumentBrain().analyze("", doc)
        options: List[LayoutOption] = []
        for t in templates:
            score = self._score_template(doc, t, counts, brain_analysis)
            for v in t.variants:
                options.append(LayoutOption(
                    option_id=f"{t.id}__{v.variant_id}",
                    template=t, variant=v, score=score,
                ))
        options.sort(key=lambda o: o.score, reverse=True)
        return options[:max_options] if max_options else options

    # ------------------------------------------------------------------

    def _score_template(
        self,
        doc: ReportDocument,
        template: TemplateSpec,
        counts: Dict[str, int],
        brain_analysis: BrainAnalysis,
    ) -> float:
        score = 1.0
        score += brain_analysis.template_biases.get(template.category, 0.0)
        score += brain_analysis.template_biases.get(template.id, 0.0)
        if template.category == "consulting" and (counts["tables"] + counts["callouts"]) >= 3:
            score += 1.6
        if template.category == "academic" and counts["headings"] >= 8:
            score += 1.2
        if template.category == "government" and doc.meta.confidentiality:
            score += 1.0
        if template.category == "corporate":
            score += 0.6  # safe, versatile default
        if counts["images"] >= 3 and template.category in ("consulting", "corporate"):
            score += 0.4
        # data-visual-heavy documents suit analytical/corporate layouts
        if counts["visuals"] >= 2 and template.category in ("consulting", "corporate"):
            score += 0.8
        return score

    def _content_counts(self, doc: ReportDocument) -> Dict[str, int]:
        counts = {"headings": 0, "paragraphs": 0, "tables": 0, "callouts": 0,
                  "lists": 0, "images": 0, "quotes": 0, "visuals": 0}

        def walk(sec: Section):
            counts["headings"] += 1
            for b in sec.blocks:
                if isinstance(b, Heading):
                    counts["headings"] += 1
                elif isinstance(b, Paragraph):
                    counts["paragraphs"] += 1
                elif isinstance(b, TableBlock):
                    counts["tables"] += 1
                elif isinstance(b, Callout):
                    counts["callouts"] += 1
                elif isinstance(b, ListBlock):
                    counts["lists"] += 1
                elif isinstance(b, ImageBlock):
                    counts["images"] += 1
                elif isinstance(b, Quote):
                    counts["quotes"] += 1
                elif isinstance(b, (ChartBlock, KpiBlock, TimelineBlock, ProcessBlock)):
                    counts["visuals"] += 1
            for sub in sec.subsections:
                walk(sub)

        for s in doc.all_sections():
            walk(s)
        return counts
