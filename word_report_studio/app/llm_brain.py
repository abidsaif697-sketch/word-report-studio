"""
llm_brain.py
-------------
Local-LLM understanding layer (fully offline — talks only to an Ollama
server on localhost). Its single job: read rough, unstructured text and
rewrite it in the engine's markup dialect, DECIDING what deserves to be a
table, KPI strip, chart, timeline, process diagram, or callout. The
deterministic renderer then guarantees designer-grade output.

Design rules:
- Never required: everything must degrade gracefully to the rule-based
  auto_enrich pass when no model is installed.
- Never trusted blindly: the model's output is validated (it must parse,
  and it must not lose the source's numbers or most of its words). On any
  doubt we fall back to the original text.
- Never online: only http://localhost:11434 (or WRS_OLLAMA_URL) is used.
"""

from __future__ import annotations
import json
import os
import re
import sys
import urllib.request
import urllib.error
from typing import List, Optional

DEFAULT_URL = os.environ.get("WRS_OLLAMA_URL", "http://localhost:11434")
FORCED_MODEL = os.environ.get("WRS_LLM_MODEL")
FORCED_FAST_MODEL = os.environ.get("WRS_LLM_FAST_MODEL")

# small-and-quick models, good enough for structured slot work — used to
# keep this CPU-only machine efficient (big model only where it matters)
_FAST_PREFERENCE = ["qwen2.5:3b", "qwen2.5:1.5b", "llama3.2:3b", "llama3.2:1b",
                    "phi3:mini", "gemma2:2b"]

# preference order when several models are installed (first hit wins;
# matched as substring of the installed model tag). Sized variants come
# first so the MAIN model is the most capable one installed — the small
# sibling is only ever picked via pick_fast_model.
_MODEL_PREFERENCE = ["qwen2.5:14b", "qwen2.5:7b", "qwen3:14b", "qwen3:8b",
                     "llama3.1:8b", "mistral:7b", "gemma2:9b",
                     "qwen2.5", "qwen3", "command-r", "aya", "llama3.1",
                     "llama3.2", "llama3", "mistral", "gemma2", "gemma",
                     "phi3", "phi"]

from .design_principles import (DESIGNER_PERSONA, COPY_RULES,
                                SLOT_CRITIQUE_RUBRIC, DOC_CRITIQUE_RUBRIC)

_SYSTEM_PROMPT = DESIGNER_PERSONA + """
You convert rough notes into a structured report source file for an offline
Word-report design engine. You decide the best visual for each piece of
content. Output ONLY the structured document — no explanations, no code
fences, no comments.

FORMAT REFERENCE (the only syntax you may use):
# Section Title            (## for subsections; every report needs sections)
Plain paragraphs for narrative text.

::: kpi                     (2-4 headline figures. Percentage values render
+12% | Revenue Growth | growth   as donut GAUGES; counts render as icon
94% | Retention | people         badges. The 3rd field picks the icon:
1,450 | User Accounts | lock     up, down, down-good, star, check, warn,
:::                              shield, person, people, money, growth,
                                 clock, target, gear, doc, lock, building,
                                 chart. Choose the icon that matches the
                                 MEANING of each figure. down-good = a
                                 decrease that is good news, e.g. complaints)

| Col A | Col B |           (pipe tables for any comparative/structured data)
|---|---|
| row | row |
%%visualize%%               (add this line right after a table that contains
                             numbers so a chart is also generated from it)

```chart bar Title Here     (chart types: bar, line, area, pie, donut,
series: Revenue, Profit      pictogram, progress, funnel, versus.
2021: 48.2, 6.1              line/area = trends over time; donut/pie =
2022: 55.6, 7.9              shares of a whole; bar = comparisons;
```                          pictogram = people-percentages as icon rows;
                             progress = completion bars per item;
                             funnel = pipeline/conversion stages, largest
                             value first; versus = EXACTLY two values
                             head-to-head, e.g. this year vs last year)

::: timeline Title          (events over years/quarters)
2023 | What happened
2024 | What happened next
:::

::: process Title           (sequential steps/phases)
Step one | Step two | Step three
:::

::: warning Title           (also: ::: info, ::: success — for notes,
Body text of the callout.    risks, achievements worth highlighting)
:::

DECISION GUIDELINES — think like an information designer, not a typist.
Take your time; a rich, visual document is worth far more than a fast one:
- Numbers describing the same entities across periods/categories -> a pipe
  table, followed by %%visualize%%.
- 2-4 headline achievements/metrics -> one ::: kpi block near the start,
  each with a meaningful icon (this becomes an infographic gauge strip).
- Dated events -> ::: timeline. Sequential how-we-work steps -> ::: process.
- Risks, cautions, notes -> ::: warning or ::: info callout with a title.
- Shares of a whole -> ```chart donut. Trends over time -> ```chart line.
- Section titles matter: security/finance/staffing/goals wording earns each
  chapter a matching icon badge automatically — name sections by topic.
- Hunt for hidden structure: a sentence listing values IS a table; a
  paragraph narrating years IS a timeline; scattered stats ARE a KPI strip.
- Everything else stays as clean paragraphs under sensible # sections.

HARD RULES:
- Copy every number EXACTLY as written in the input. Never invent, merge,
  swap, or repeat values. Before finishing, re-check each number in your
  output against the input — a wrong figure is worse than no figure.
- Preserve EVERY fact from the input.
- Keep the input's languages: if content is Arabic keep Arabic; if bilingual
  keep both languages paired like "English / العربية".
- Do not add opinions, closing summaries, commentary about the structure,
  or horizontal rules. The document ends with the last piece of content.

""" + COPY_RULES


class LocalLLMBrain:
    def __init__(self, base_url: str = DEFAULT_URL, model: Optional[str] = None,
                 timeout: int = 900):  # let the model think — quality over speed
        self.base_url = base_url.rstrip("/")
        self.model = model or FORCED_MODEL
        self.timeout = timeout
        self.last_error: Optional[str] = None

    # -- plumbing ----------------------------------------------------------

    def _get_json(self, path: str, timeout: int = 4):
        req = urllib.request.Request(self.base_url + path)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def _post_json(self, path: str, payload: dict):
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.base_url + path, data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    # -- capability discovery ---------------------------------------------

    def installed_models(self) -> List[str]:
        try:
            tags = self._get_json("/api/tags")
            return [m.get("name", "") for m in tags.get("models", [])]
        except Exception as e:
            self.last_error = f"Ollama not reachable at {self.base_url} ({e})"
            return []

    def is_available(self) -> bool:
        return bool(self.pick_model())

    def free_other_models(self, keep: Optional[str] = None):
        """Unload every loaded model except `keep`. This machine has
        15.5 GB RAM: two resident 5 GB models push Windows into swap and
        everything crawls — models must evict each other, not coexist."""
        try:
            loaded = self._get_json("/api/ps").get("models", [])
        except Exception:
            return
        for m in loaded:
            name = m.get("name", "")
            if not name or (keep and name == keep):
                continue
            try:
                self._post_json("/api/generate",
                                {"model": name, "keep_alive": 0})
            except Exception:
                pass

    def pick_model(self) -> Optional[str]:
        if self.model:
            return self.model
        installed = self.installed_models()
        if not installed:
            return None
        low = [(m, m.lower()) for m in installed]
        for pref in _MODEL_PREFERENCE:
            for name, l in low:
                if pref in l:
                    self.model = name
                    return name
        self.model = installed[0]
        return self.model

    def pick_fast_model(self) -> Optional[str]:
        """Smallest capable model for structured slot work — on a CPU-only
        machine the big model is reserved for the hard thinking."""
        if FORCED_FAST_MODEL:
            return FORCED_FAST_MODEL
        installed = [m.lower() for m in self.installed_models()]
        for pref in _FAST_PREFERENCE:
            for m in installed:
                if pref in m:
                    return m
        return self.pick_model()

    # -- the actual work ----------------------------------------------------

    def restructure(self, raw_text: str) -> Optional[str]:
        """Rough text -> engine markup, or None when unavailable/unsafe.
        The model gets one corrective retry naming exactly which numbers it
        lost; if it still can't reproduce the facts we refuse its output —
        a plainer report beats a wrong one."""
        model = self.pick_model()
        if not model:
            return None
        self.free_other_models(keep=model)
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": raw_text},
        ]
        good = None
        for attempt in range(2):
            try:
                resp = self._post_json("/api/chat", {
                    "model": model,
                    "stream": False,
                    "options": {"temperature": 0.0, "num_ctx": 8192,
                                "keep_alive": "15m"},
                    "messages": messages,
                })
                out = (resp.get("message") or {}).get("content", "")
            except Exception as e:
                self.last_error = f"Ollama request failed: {e}"
                return None
            out = _strip_wrapping(out)
            missing = _missing_numbers(raw_text, out)
            if not missing and _output_is_safe(raw_text, out):
                good = out
                break
            if attempt == 0:
                messages.append({"role": "assistant", "content": out})
                messages.append({"role": "user", "content":
                    "Your output lost or changed these numbers from the "
                    f"input: {', '.join(sorted(missing)) or 'none'} — and/or "
                    "dropped content. Regenerate the COMPLETE document again, "
                    "copying every number exactly as written in the original "
                    "input. Output only the document."})
        if good is None:
            self.last_error = ("model output failed validation "
                               "(lost or changed numbers) — using rules instead")
            return None

        # the designer's second look: critique against the award rubric and
        # revise. Only adopt the revision if it survives the same fact checks.
        try:
            resp = self._post_json("/api/chat", {
                "model": model,
                "stream": False,
                "options": {"temperature": 0.0, "num_ctx": 8192},
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": raw_text},
                    {"role": "assistant", "content": good},
                    {"role": "user", "content": DOC_CRITIQUE_RUBRIC},
                ],
            })
            polished = _strip_wrapping(
                (resp.get("message") or {}).get("content", ""))
            if (polished and not _missing_numbers(raw_text, polished)
                    and _output_is_safe(raw_text, polished)):
                return polished
        except Exception:
            pass
        return good


# ---------------------------------------------------------------------------
# Output validation — the model must not lose the user's facts
# ---------------------------------------------------------------------------

_NUM_TOKEN_RE = re.compile(r"\d+(?:\.\d+)?")
_WORD_RE = re.compile(r"[\w؀-ۿ]{3,}")
_FENCE_WRAP_RE = re.compile(r"^```[a-zA-Z]*\s*\n(.*)\n```\s*$", re.DOTALL)


def _strip_wrapping(text: str) -> str:
    t = text.strip()
    m = _FENCE_WRAP_RE.match(t)
    if m and "```chart" not in t[:20]:
        t = m.group(1).strip()
    # models sometimes prefix chatter before the first heading
    first_hash = t.find("#")
    if 0 < first_hash < 200 and "\n" in t[:first_hash]:
        pre = t[:first_hash]
        if not any(tok in pre for tok in ("|", ":::", "```")):
            t = t[first_hash:]
    return t


def _missing_numbers(source: str, output: str) -> set:
    """Multi-digit source numbers absent from the output. Numbers ARE the
    facts — all of them must survive restructuring."""
    src = {n for n in _NUM_TOKEN_RE.findall(source) if len(n) >= 2}
    out = set(_NUM_TOKEN_RE.findall(output))
    return src - out


def _output_is_safe(source: str, output: str) -> bool:
    if not output or len(output) < 40:
        return False
    src_words = set(w.lower() for w in _WORD_RE.findall(source))
    out_words = set(w.lower() for w in _WORD_RE.findall(output))
    if src_words:
        kept_w = len(src_words & out_words) / len(src_words)
        if kept_w < 0.5:
            return False
    return True


_SLOT_SYSTEM_PROMPT = DESIGNER_PERSONA + COPY_RULES + """
You are filling a professionally designed Word brochure template. You get:
1. an inventory of text SLOTS (id | page | kind | max chars | current text)
2. the user's rough content.

Decide what goes in which slot, like a designer laying out a brochure.
Return ONLY a JSON object mapping slot id (string) to replacement text.

RULES:
- Respect every slot's max chars — text that does not fit is cut off
  invisibly in the design. Write tight, polished copy that fits.
- Slots marked PLACEHOLDER-MUST-REPLACE contain designer dummy wording
  ("Your Specific Service Name", "Insert full name here", sample phone
  numbers). You MUST write real replacements for these from the user's
  content — e.g. actual service names as card titles, the real CEO name,
  the real contact line. Leaving dummy wording is a failure.
- heading slots: keep or adapt the designed heading. If the template
  heading already suits the content (e.g. "Executive Summary"), keep it.
- When SEVERAL slots share the same placeholder text, they are a CARD GRID
  (e.g. six service cards). Distribute DIFFERENT items across them — one
  service name + its own description per card. Writing the same text into
  sibling cards is a failure.
- body slots (placeholder lorem text): fill with the user's actual content,
  summarized to fit the space. Never leave lorem text for a topic the user
  provided content about.
- micro slots (company name, year, tagline, contact): fill only when the
  user's content provides the fact; otherwise omit the slot from your JSON
  so the design placeholder stays.
- NEVER invent numbers, names, or facts not present in the user's content.
- Keep the user's language(s): Arabic content stays Arabic.
- Use \\n inside a value for line breaks in multi-line slots.
- Omit page-number slots and any slot you don't want to change.
Output: one JSON object, nothing else.
"""


def map_content_to_slots(brain: LocalLLMBrain, raw_text: str,
                         slot_inventory: str) -> Optional[dict]:
    """Ask the local model to assign the user's content to template slots.
    Returns {slot_id(str): text} or None (caller falls back to rules).
    Uses the FAST model — slot work is structured enough for a 3B, which
    roughly halves wall-clock time on this CPU-only machine."""
    import json as _json
    model = brain.pick_fast_model()
    if not model:
        return None
    brain.free_other_models(keep=model)
    user_msg = (f"SLOTS:\n{slot_inventory}\n\n"
                f"USER CONTENT:\n{raw_text}\n\nReturn the JSON mapping now.")
    messages = [
        {"role": "system", "content": _SLOT_SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    def _ask(msgs):
        resp = brain._post_json("/api/chat", {
            "model": model, "stream": False, "format": "json",
            # chunked inventories are small — 4k context prefills faster
            "options": {"temperature": 0.0, "num_ctx": 4096,
                        "keep_alive": "15m"},
            "messages": msgs,
        })
        return (resp.get("message") or {}).get("content", "")

    def _parse(out):
        try:
            mapping = _json.loads(out)
        except Exception:
            return None
        if not isinstance(mapping, dict) or not mapping:
            return None
        clean = {}
        for k, v in mapping.items():
            try:
                clean[int(k)] = str(v)
            except (ValueError, TypeError):
                continue
        return clean or None

    try:
        first_raw = _ask(messages)
    except Exception as e:
        brain.last_error = f"slot mapping failed: {e}"
        return None
    first = _parse(first_raw)
    if first is None:
        brain.last_error = "slot mapping: model returned no usable JSON"
        return None

    # designer's second look: critique the mapping against the award rubric
    try:
        polished = _parse(_ask(messages + [
            {"role": "assistant", "content": first_raw},
            {"role": "user", "content": SLOT_CRITIQUE_RUBRIC},
        ]))
        if polished:
            return polished
    except Exception:
        pass
    return first


def map_content_to_slots_chunked(brain: LocalLLMBrain, raw_text: str,
                                 slots, log=None,
                                 pages_per_chunk: int = 4) -> Optional[dict]:
    """Slot mapping a few pages at a time. One giant inventory makes small
    local models slow, lazy in the middle, and prone to timeouts; short
    focused prompts are dramatically more reliable — and give the user
    real progress feedback."""
    from .template_filler import describe_slots
    log = log or (lambda *_: None)
    pages = sorted({s.page for s in slots if s.kind != "page-number"})
    mapping: dict = {}
    for i in range(0, len(pages), pages_per_chunk):
        chunk_pages = set(pages[i:i + pages_per_chunk])
        chunk = [s for s in slots if s.page in chunk_pages]
        if not chunk:
            continue
        m = map_content_to_slots(brain, raw_text, describe_slots(chunk))
        got = len(m or {})
        log(f"  AI mapped pages {min(chunk_pages)}–{max(chunk_pages)}: "
            f"{got} slot(s) proposed")
        if m:
            mapping.update(m)
    return mapping or None


def describe_status(brain: LocalLLMBrain) -> str:
    """One-liner for logs/UI about whether the AI tier is active."""
    model = brain.pick_model()
    if model:
        return f"Local AI: Ollama model '{model}' will restructure the content."
    return ("Local AI: not detected (install Ollama and e.g. `ollama pull "
            "qwen2.5:7b` to enable) — using the rule-based smart formatter.")
