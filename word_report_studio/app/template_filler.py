"""
template_filler.py
-------------------
Fill professionally designed, canvas-style Word templates (brochures,
annual-report showpieces) with the user's content — the way a human
designer would, but automatically.

These templates keep essentially ALL of their content in floating text
boxes layered over photos and artwork; the body is just empty anchor
paragraphs. Flowing content into them is meaningless — the design IS the
fixed page composition. What works is slot filling: enumerate every text
box ("slot"), understand its role from its placeholder (a heading, a lorem
body block, a stat, a contact line), then rewrite the text INSIDE each box
while leaving every bit of formatting, artwork, and layout untouched.

Notes learned from real template dissection:
- Each visible box exists twice in the XML (mc:AlternateContent Choice +
  Fallback copies with identical text). Both copies must receive identical
  replacement text or Word and LibreOffice render different content.
- Placeholder body text is pseudo-Latin lorem ("Dus doluptatint aliquid…"),
  which makes body slots easy to recognize; short slots are labels,
  headings, and micro-facts.
"""

from __future__ import annotations
import copy
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from docx import Document
from docx.oxml.ns import qn

_MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"


def _mc(tag: str) -> str:
    return "{%s}%s" % (_MC_NS, tag)


_LOREM_HINTS = ("dus dolupt", "lorem ipsum", "doluptatint", "utamsa", "utaemsa",
                "volorion", "pernatusdam", "reratem", "idesan")
# designed placeholder wording that MUST be replaced before the document is
# usable — "Your Specific Service Name", "Insert full name here", sample
# contact lines, etc.
_MUSTFILL_HINTS = ("your company", "your spesific", "your specific",
                   "insert full name", "insert name", "name here",
                   "your text", "your tagline", "your title", "your name",
                   "put_email", "put email", "your_webiste", "your website",
                   "company name", "12 345 6789", "your address",
                   "اسم الشركة", "أدخل الاسم", "اسمك هنا")
_PAGE_NUM_RE = re.compile(r"^page\s*\d+$", re.IGNORECASE)


@dataclass
class Slot:
    slot_id: int
    page: int
    text: str                 # current placeholder text ("|" joins lines)
    capacity: int             # characters in the placeholder
    kind: str                 # heading | body | micro | page-number
    copies: list = field(default_factory=list, repr=False)  # txbxContent els
    is_placeholder: bool = False   # designed text that MUST be replaced


def _box_text(el) -> str:
    """Text of an element's DIRECT paragraph children (tables excluded)."""
    lines = []
    for p in el.findall(qn("w:p")):
        t = "".join(x.text or "" for x in p.iter(qn("w:t")))
        if t.strip():
            lines.append(t.strip())
    return "\n".join(lines)


def _full_text(el) -> str:
    """All text under an element, including nested tables — used as the
    identity key for pairing a box with its Choice/Fallback twin."""
    parts = []
    for t in el.iter(qn("w:t")):
        if t.text and t.text.strip():
            parts.append(t.text.strip())
    return " ".join(parts)


def _classify(text: str) -> str:
    flat = text.replace("\n", " ").strip()
    low = flat.lower()
    if _PAGE_NUM_RE.match(low):
        return "page-number"
    if any(h in low for h in _LOREM_HINTS):
        return "body"
    if len(flat) <= 45:
        return "micro" if len(flat) <= 25 else "heading"
    return "body" if len(flat) > 120 else "heading"


def extract_slots(path: str) -> List[Slot]:
    """Enumerate fillable slots in document order.
    - duplicate Choice/Fallback box copies merge into one slot
    - a table nested inside a box yields one slot PER CELL (service-card
      grids and stat tables live this way in designer templates)"""
    doc = Document(path)
    body = doc.element.body
    page = 1
    groups: List[dict] = []   # {"page": int, "els": [...]}
    visited = set()

    def boxes_of(el):
        return [] if el is None else el.findall(".//" + qn("w:txbxContent"))

    for el in body.iter():
        if el.tag == qn("w:br") and el.get(qn("w:type")) == "page":
            page += 1
        elif el.tag == _mc("AlternateContent"):
            # a drawing exists twice: mc:Choice (modern) + mc:Fallback
            # (legacy pict). Pair the copies positionally so BOTH always
            # receive the same replacement — Word renders one, LibreOffice
            # sometimes the other.
            choice_boxes = boxes_of(el.find(_mc("Choice")))
            fallback_boxes = boxes_of(el.find(_mc("Fallback")))
            for i, cb in enumerate(choice_boxes):
                visited.add(id(cb))
                copies = [cb]
                if i < len(fallback_boxes):
                    copies.append(fallback_boxes[i])
                if _full_text(cb):
                    groups.append({"page": page, "els": copies})
            for fb in fallback_boxes:
                visited.add(id(fb))
        elif el.tag == qn("w:txbxContent") and id(el) not in visited:
            visited.add(id(el))
            if _full_text(el):
                groups.append({"page": page, "els": [el]})

    slots: List[Slot] = []

    def add_slot(page: int, text: str, copies: list):
        low = text.lower()
        slots.append(Slot(slot_id=len(slots) + 1, page=page, text=text,
                          capacity=len(text.replace("\n", " ")),
                          kind=_classify(text), copies=copies,
                          is_placeholder=(any(h in low for h in _MUSTFILL_HINTS)
                                          or any(h in low for h in _LOREM_HINTS))))

    for g in groups:
        first = g["els"][0]
        para_text = _box_text(first)
        if para_text:
            add_slot(g["page"], para_text, g["els"])
        tables_per_copy = [c.findall(qn("w:tbl")) for c in g["els"]]
        for ti, tbl in enumerate(tables_per_copy[0]):
            cells0 = tbl.findall(".//" + qn("w:tc"))
            for ci, cell in enumerate(cells0):
                ctext = _box_text(cell)
                if not ctext.strip():
                    continue
                copies = []
                for copy_tables in tables_per_copy:
                    if ti < len(copy_tables):
                        twin_cells = copy_tables[ti].findall(".//" + qn("w:tc"))
                        if ci < len(twin_cells):
                            copies.append(twin_cells[ci])
                add_slot(g["page"], ctext, copies)

    # keep a handle to the Document so fill() can save it
    for s in slots:
        s._doc = doc  # type: ignore[attr-defined]
    return slots


def _set_box_text(txbx, new_text: str):
    """Rewrite a text box's content, reusing its first paragraph/run
    formatting for every line so the designer's typography is kept."""
    paras = txbx.findall(qn("w:p"))
    if not paras:
        return
    lines = [l for l in new_text.split("\n")] or [""]
    template_p = paras[0]

    def write_line(p, line: str):
        runs = p.findall(qn("w:r"))
        first_with_t = None
        for r in runs:
            if r.find(qn("w:t")) is not None:
                first_with_t = r
                break
        if first_with_t is None:
            return
        t = first_with_t.find(qn("w:t"))
        t.text = line
        t.set(qn("xml:space"), "preserve")
        # drop text from any further runs, keep non-text runs (tabs, breaks
        # inside the box are part of the design? no — remove leftovers)
        seen = False
        for r in runs:
            if r is first_with_t:
                seen = True
                continue
            if seen and r.find(qn("w:t")) is not None:
                r.getparent().remove(r)

    write_line(template_p, lines[0])
    # remove the other placeholder paragraphs
    for p in paras[1:]:
        p.getparent().remove(p)
    # add cloned paragraphs for additional lines
    anchor = template_p
    for line in lines[1:]:
        clone = copy.deepcopy(template_p)
        write_line(clone, line)
        anchor.addnext(clone)
        anchor = clone


def fill_slots(path: str, replacements: Dict[int, str], out_path: str,
               slots: Optional[List[Slot]] = None,
               trim_pages: Optional[set] = None,
               images: Optional[List[str]] = None,
               log=None) -> str:
    """Write `replacements` (slot_id -> new text) into a copy of the
    template. Slots not mentioned keep their designed placeholder.
    Pages listed in `trim_pages` are removed entirely (their anchor
    paragraphs carry their floating artwork with them) — used to drop
    designed pages the user's content could not fill."""
    slots = slots if slots is not None else extract_slots(path)
    if not slots:
        raise ValueError("no fillable text boxes found in template")
    doc = slots[0]._doc  # type: ignore[attr-defined]
    by_id = {s.slot_id: s for s in slots}
    for sid, text in replacements.items():
        slot = by_id.get(int(sid))
        if slot is None or not isinstance(text, str):
            continue
        for copy_el in slot.copies:
            _set_box_text(copy_el, text)

    if trim_pages:
        body = doc.element.body
        page = 1
        for child in list(body):
            if child.tag == qn("w:sectPr"):
                continue
            has_break = any(br.get(qn("w:type")) == "page"
                            for br in child.iter(qn("w:br")))
            if page in trim_pages:
                body.remove(child)
            if has_break:
                page += 1

    if images:
        from .image_filler import fill_images
        work_dir = os.path.join(os.path.dirname(os.path.abspath(out_path)),
                                "_photo_crops")
        fill_images(doc, images, work_dir, log=log)

    doc.save(out_path)
    return out_path


def unfillable_pages(slots: List[Slot], mapping: Dict[int, str]) -> set:
    """Pages whose designed body slots would all keep lorem placeholder
    text — candidates for trimming. Page 1 (the cover) is never trimmed."""
    pages: Dict[int, dict] = {}
    for s in slots:
        if s.kind != "body" or not any(h in s.text.lower() for h in _LOREM_HINTS):
            continue
        info = pages.setdefault(s.page, {"filled": 0, "lorem": 0})
        if s.slot_id in mapping and mapping[s.slot_id].strip():
            info["filled"] += 1
        else:
            info["lorem"] += 1
    return {p for p, info in pages.items()
            if p > 1 and info["filled"] == 0 and info["lorem"] > 0}


def sanitize_mapping(slots: List[Slot], mapping: Dict[int, str]) -> Dict[int, str]:
    """Drop unsafe assignments and fit overlong text.
    - never touch page-number slots
    - reject a value that equals ANOTHER slot's designed text (models
      sometimes shift headings by one slot — 'Page of Contents' written
      into the 'Executive Summary' heading)
    - fit text to the designed capacity (word-boundary cut; tiny label
      slots are cut without an ellipsis)"""
    by_id = {s.slot_id: s for s in slots}
    originals = {s.text.replace("\n", " ").strip().lower(): s.slot_id
                 for s in slots}
    clean: Dict[int, str] = {}
    for sid, value in mapping.items():
        slot = by_id.get(int(sid))
        if slot is None or slot.kind == "page-number" or not isinstance(value, str):
            continue
        flat = value.replace("\n", " ").strip()
        if not flat:
            continue
        if any(h in flat.lower() for h in _LOREM_HINTS):
            continue  # model echoed the placeholder back — not a fill
        twin = originals.get(flat.lower())
        if twin is not None and twin != slot.slot_id:
            continue  # heading copied from a different slot: a shift error
        if len(flat) > int(slot.capacity * 1.35):
            limit = int(slot.capacity * 1.15)
            cut = value[:limit].rsplit(" ", 1)[0].rstrip(" ,،.;؛-")
            value = cut if slot.kind == "micro" else cut + "…"
        clean[slot.slot_id] = value

    # card grids: sibling slots share identical designed text; the model
    # sometimes writes the SAME value into all of them. Keep the first
    # occurrence only — a repeated card is worse than a placeholder one,
    # which at least gets flagged for the user.
    seen_by_group: Dict[tuple, set] = {}
    for sid in sorted(clean):
        slot = by_id[sid]
        group = (slot.text.replace("\n", " ").strip().lower(), slot.kind)
        vals = seen_by_group.setdefault(group, set())
        norm = clean[sid].replace("\n", " ").strip().lower()
        if norm in vals:
            del clean[sid]
        else:
            vals.add(norm)
    return clean


def describe_slots(slots: List[Slot]) -> str:
    """Compact, model-readable slot inventory."""
    lines = []
    for s in slots:
        if s.kind == "page-number":
            continue  # never touched
        label = s.text.replace("\n", " / ")[:60]
        marker = " | PLACEHOLDER-MUST-REPLACE" if s.is_placeholder else ""
        lines.append(f"{s.slot_id} | page {s.page} | {s.kind} | "
                     f"max {s.capacity} chars{marker} | now: {label}")
    return "\n".join(lines)


def sample_data_warnings(slots: List[Slot], mapping: Dict[int, str]) -> List[str]:
    """Slots that will ship with the template's SAMPLE data (fake phone
    numbers, sample codes, 'Your Company' wording) — the user must review
    these before sending the document to anyone."""
    warnings = []
    for s in slots:
        filled = s.slot_id in mapping and str(mapping[s.slot_id]).strip()
        if filled or s.kind == "page-number":
            continue
        flat = s.text.replace("\n", " ")[:60]
        if s.is_placeholder and not any(h in s.text.lower() for h in _LOREM_HINTS):
            warnings.append(f"page {s.page}: '{flat}'")
        elif s.kind == "micro" and re.search(r"\d{4,}", s.text):
            warnings.append(f"page {s.page}: sample number kept — '{flat}'")
    return warnings


# ---------------------------------------------------------------------------
# Rules fallback mapping — used when no local AI is available. Keeps the
# template's own section headings (they are already well designed) and pours
# the user's paragraphs into the lorem body slots in reading order.
# ---------------------------------------------------------------------------

def _fit(text: str, capacity: int) -> str:
    limit = int(capacity * 1.15)
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,،.;؛")
    return cut + "…"


def rules_mapping(raw_text: str, slots: List[Slot],
                  org: Optional[str] = None,
                  title: Optional[str] = None,
                  exclude: Optional[Dict[int, str]] = None) -> Dict[int, str]:
    """Fill lorem body slots with the user's paragraphs in reading order.
    With `exclude` (an AI mapping), acts as the completion pass: only slots
    the AI skipped are filled, and only with content the AI did not already
    place somewhere — a designed page must never ship with placeholder
    latin, no matter how lazy the model felt."""
    exclude = exclude or {}
    used_text = " ".join(exclude.values())
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n|\n", raw_text)
                  if len(p.strip()) >= 40
                  and p.strip()[:40] not in used_text]
    mapping: Dict[int, str] = {}
    pi = 0
    for s in slots:
        if s.slot_id in exclude:
            continue
        if s.kind == "body" and any(h in s.text.lower() for h in _LOREM_HINTS):
            if pi < len(paragraphs):
                mapping[s.slot_id] = _fit(paragraphs[pi], s.capacity)
                pi += 1
        elif s.kind == "micro" and org and "your company" in s.text.lower():
            mapping[s.slot_id] = org
    return mapping
