"""
offline_brain.py
----------------
Deterministic, offline "AI-like" document analysis for confidential reports.

This module deliberately does not call a cloud model, local HTTP service, or
network API. It gives the app a practical brain for official documents by
scoring content signals, sensitivity, and template fit using auditable rules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from .structure_model import (
    ReportDocument, Section, Block, Run,
    Heading, Paragraph, ListBlock, TableBlock, ImageBlock, Quote, Callout,
    KpiBlock, ChartBlock, TimelineBlock, ProcessBlock,
)


REMOTE_REF_RE = re.compile(r"\b(?:https?|ftp)://|\bwww\.", re.IGNORECASE)


@dataclass
class BrainAnalysis:
    document_type: str
    audience: str
    sensitivity_level: str
    confidence: float
    template_biases: Dict[str, float] = field(default_factory=dict)
    recommended_template_ids: List[str] = field(default_factory=list)
    suggested_confidentiality: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    offline_assurances: List[str] = field(default_factory=list)
    signals: Dict[str, int] = field(default_factory=dict)


def runs_to_text(runs: Iterable[Run]) -> str:
    return "".join(r.text for r in runs)


class OfflineDocumentBrain:
    """Auditable local analyzer for official/confidential report workflows."""

    OFFICIAL_TERMS = (
        "official", "ministry", "government", "department", "authority",
        "cabinet", "regulation", "policy", "compliance", "committee",
        "memorandum", "circular", "قرار", "وزارة", "حكومة", "جهة",
        "هيئة", "إدارة", "لجنة", "سياسة", "امتثال", "تعميم",
    )
    CONFIDENTIAL_TERMS = (
        "confidential", "restricted", "secret", "internal use", "private",
        "sensitive", "not for distribution", "classified", "سري", "سرية",
        "مقيد", "حساس", "غير قابل للتداول", "للاستخدام الداخلي",
    )
    PERSONAL_TERMS = (
        "passport", "national id", "id number", "employee id", "salary",
        "personal data", "phone number", "email address", "رقم الهوية",
        "جواز", "راتب", "بيانات شخصية", "رقم الموظف", "رقم الجوال",
    )
    FINANCIAL_TERMS = (
        "revenue", "profit", "budget", "cost", "financial", "audit",
        "expenditure", "procurement", "tender", "contract", "إيرادات",
        "أرباح", "ميزانية", "تكلفة", "مالي", "تدقيق", "مناقصة", "عقد",
    )
    STRATEGY_TERMS = (
        "strategy", "roadmap", "initiative", "portfolio", "milestone",
        "vision", "objective", "risk", "استراتيجية", "خارطة", "مبادرة",
        "محفظة", "هدف", "مخاطر",
    )
    RESEARCH_TERMS = (
        "methodology", "research", "study", "analysis", "findings",
        "appendix", "references", "منهجية", "بحث", "دراسة", "تحليل",
        "نتائج", "مراجع", "ملحق",
    )
    EXECUTIVE_TERMS = (
        "executive", "board", "ceo", "chairman", "leadership",
        "summary", "مجلس", "الرئيس التنفيذي", "القيادة", "ملخص",
    )

    def analyze(self, raw_text: str, report: ReportDocument) -> BrainAnalysis:
        source_text = raw_text or self._report_text(report)
        text = "\n".join([self._meta_text(report), source_text]).lower()
        signals = {
            "official": self._count_terms(text, self.OFFICIAL_TERMS),
            "confidential": self._count_terms(text, self.CONFIDENTIAL_TERMS),
            "personal": self._count_terms(text, self.PERSONAL_TERMS),
            "financial": self._count_terms(text, self.FINANCIAL_TERMS),
            "strategy": self._count_terms(text, self.STRATEGY_TERMS),
            "research": self._count_terms(text, self.RESEARCH_TERMS),
            "executive": self._count_terms(text, self.EXECUTIVE_TERMS),
            "remote_refs": len(REMOTE_REF_RE.findall(raw_text or "")),
        }
        counts = self._content_counts(report)
        signals.update(counts)

        sensitivity = self._sensitivity_level(report, signals)
        document_type = self._document_type(report, signals)
        audience = self._audience(report, signals)
        template_biases = self._template_biases(document_type, sensitivity, signals)
        warnings = self._warnings(signals)
        recommendations = self._recommendations(document_type, sensitivity, signals)
        suggested_confidentiality = self._suggested_confidentiality(report, sensitivity)
        recommended_template_ids = self._recommended_templates(document_type, sensitivity)

        confidence = min(0.95, 0.45 + sum(1 for v in signals.values() if v > 0) * 0.04)
        return BrainAnalysis(
            document_type=document_type,
            audience=audience,
            sensitivity_level=sensitivity,
            confidence=round(confidence, 2),
            template_biases=template_biases,
            recommended_template_ids=recommended_template_ids,
            suggested_confidentiality=suggested_confidentiality,
            warnings=warnings,
            recommendations=recommendations,
            offline_assurances=[
                "No cloud AI or external API is used by the brain.",
                "Analysis is rule-based and runs inside this Python process.",
                "Remote links are reported so official documents can stay self-contained.",
            ],
            signals=signals,
        )

    def apply_to_report(self, report: ReportDocument, analysis: BrainAnalysis) -> None:
        if analysis.suggested_confidentiality and not report.meta.confidentiality:
            report.meta.confidentiality = analysis.suggested_confidentiality

    @staticmethod
    def _count_terms(text: str, terms: Iterable[str]) -> int:
        return sum(text.count(term.lower()) for term in terms)

    def _sensitivity_level(self, report: ReportDocument, signals: Dict[str, int]) -> str:
        if report.meta.confidentiality:
            return "confidential"
        if signals["confidential"] >= 2 or signals["personal"] >= 2:
            return "strict"
        if signals["confidential"] or signals["personal"] or signals["official"] >= 2:
            return "confidential"
        if signals["financial"] or signals["strategy"]:
            return "internal"
        return "normal"

    @staticmethod
    def _document_type(report: ReportDocument, signals: Dict[str, int]) -> str:
        if (report.meta.confidentiality or signals["official"] >= 2 or
                signals["confidential"] or signals["personal"]):
            return "official"
        if signals["financial"] >= max(signals["strategy"], signals["research"], 1):
            return "financial"
        if signals["strategy"] >= max(signals["research"], 1):
            return "strategy"
        if signals["research"]:
            return "research"
        if signals.get("tables", 0) + signals.get("visuals", 0) >= 3:
            return "analytical"
        return "general"

    @staticmethod
    def _audience(report: ReportDocument, signals: Dict[str, int]) -> str:
        if report.meta.confidentiality or signals["official"] >= 2:
            return "government/official stakeholders"
        if signals["executive"]:
            return "executive leadership"
        if signals["research"]:
            return "technical/research readers"
        return "business readers"

    @staticmethod
    def _template_biases(document_type: str, sensitivity: str,
                         signals: Dict[str, int]) -> Dict[str, float]:
        biases: Dict[str, float] = {"corporate": 0.3}
        if document_type == "official" or sensitivity in ("confidential", "strict"):
            biases.update({
                "government": 2.4,
                "government_formal": 0.8,
                "royal_formal": 0.7,
                "consulting": -0.2,
            })
        if document_type in ("financial", "strategy", "analytical"):
            biases["consulting"] = biases.get("consulting", 0.0) + 1.0
            biases["executive_premium"] = biases.get("executive_premium", 0.0) + 0.8
        if document_type == "research":
            biases["academic"] = 1.7
        if signals.get("visuals", 0) >= 2:
            biases["consulting"] = biases.get("consulting", 0.0) + 0.6
            biases["corporate"] = biases.get("corporate", 0.0) + 0.3
        return biases

    @staticmethod
    def _recommended_templates(document_type: str, sensitivity: str) -> List[str]:
        if document_type == "official" or sensitivity in ("confidential", "strict"):
            return ["government_formal", "royal_formal"]
        if document_type in ("financial", "strategy", "analytical"):
            return ["executive_premium", "consulting_analytical"]
        if document_type == "research":
            return ["academic_research"]
        return ["corporate_modern"]

    @staticmethod
    def _suggested_confidentiality(report: ReportDocument, sensitivity: str) -> Optional[str]:
        if report.meta.confidentiality:
            return None
        if sensitivity == "strict":
            return "Strictly Confidential / سري للغاية"
        if sensitivity == "confidential":
            return "Confidential / سري"
        if sensitivity == "internal":
            return "Internal Use Only / للاستخدام الداخلي"
        return None

    @staticmethod
    def _warnings(signals: Dict[str, int]) -> List[str]:
        warnings: List[str] = []
        if signals["remote_refs"]:
            warnings.append(
                "Remote URLs were found. Keep official documents self-contained and use local files only."
            )
        if signals["personal"]:
            warnings.append(
                "Personal-data terms were detected. Review distribution and access controls before sharing."
            )
        return warnings

    @staticmethod
    def _recommendations(document_type: str, sensitivity: str,
                         signals: Dict[str, int]) -> List[str]:
        recommendations: List[str] = []
        if sensitivity in ("confidential", "strict"):
            recommendations.append("Use an official template and keep the confidentiality label enabled.")
        if document_type == "official":
            recommendations.append("Prefer Government / Official or Royal Formal layouts for formal review.")
        if signals.get("tables", 0) and not signals.get("visuals", 0):
            recommendations.append("Add %%visualize%% after numeric tables when a chart helps reviewers scan quickly.")
        if signals.get("headings", 0) >= 8:
            recommendations.append("Keep the table of contents enabled for long reports.")
        return recommendations

    def _report_text(self, report: ReportDocument) -> str:
        pieces = [self._meta_text(report)]

        def scan_section(sec: Section):
            pieces.append(sec.title or "")
            for block in sec.blocks:
                pieces.append(self._block_text(block))
            for sub in sec.subsections:
                scan_section(sub)

        for section in report.all_sections():
            scan_section(section)
        return "\n".join(p for p in pieces if p)

    @staticmethod
    def _meta_text(report: ReportDocument) -> str:
        return "\n".join(
            p for p in (
                report.meta.title,
                report.meta.subtitle,
                report.meta.organization,
                report.meta.confidentiality,
            )
            if p
        )

    def _block_text(self, block: Block) -> str:
        if isinstance(block, (Heading, Paragraph, Quote, Callout)):
            return runs_to_text(block.runs)
        if isinstance(block, ListBlock):
            return "\n".join(runs_to_text(item) for item in block.items)
        if isinstance(block, TableBlock):
            return "\n".join(
                " | ".join(runs_to_text(cell) for cell in row)
                for row in block.rows
            )
        if isinstance(block, ImageBlock):
            return " ".join(p for p in (block.caption, block.path) if p)
        if isinstance(block, KpiBlock):
            return "\n".join(f"{item.value} {item.label}" for item in block.items)
        if isinstance(block, ChartBlock):
            labels = " ".join(block.categories)
            series = " ".join(name for name, _values in block.series)
            return " ".join(p for p in (block.title, block.caption, labels, series) if p)
        if isinstance(block, TimelineBlock):
            return " ".join([block.title or ""] + [f"{m} {t}" for m, t in block.items])
        if isinstance(block, ProcessBlock):
            return " ".join([block.title or ""] + list(block.steps))
        return ""

    @staticmethod
    def _content_counts(report: ReportDocument) -> Dict[str, int]:
        counts = {"headings": 0, "tables": 0, "visuals": 0, "images": 0}

        def scan(sec: Section):
            counts["headings"] += 1 if sec.title else 0
            for block in sec.blocks:
                if isinstance(block, Heading):
                    counts["headings"] += 1
                elif isinstance(block, TableBlock):
                    counts["tables"] += 1
                elif isinstance(block, ImageBlock):
                    counts["images"] += 1
                elif isinstance(block, (KpiBlock, ChartBlock, TimelineBlock, ProcessBlock)):
                    counts["visuals"] += 1
            for sub in sec.subsections:
                scan(sub)

        for section in report.all_sections():
            scan(section)
        return counts


def format_brain_summary(analysis: BrainAnalysis) -> str:
    lines = [
        "Offline brain:",
        f"  Type: {analysis.document_type}   Audience: {analysis.audience}",
        f"  Sensitivity: {analysis.sensitivity_level}   Confidence: {analysis.confidence:.2f}",
    ]
    if analysis.suggested_confidentiality:
        lines.append(f"  Applied label: {analysis.suggested_confidentiality}")
    if analysis.recommended_template_ids:
        lines.append("  Recommended templates: " + ", ".join(analysis.recommended_template_ids))
    for warning in analysis.warnings:
        lines.append(f"  Warning: {warning}")
    for recommendation in analysis.recommendations:
        lines.append(f"  Recommendation: {recommendation}")
    lines.append("  Privacy: " + analysis.offline_assurances[0])
    return "\n".join(lines)
