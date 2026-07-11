"""
vision_critic.py
-----------------
The design reviewer that actually SEES the pages. Renders the produced
PDF's pages to images and asks a local vision model (minicpm-v via Ollama,
fully offline) to critique each one like a design-award jury: overflowing
text, leftover placeholders, imbalanced layouts, broken alignment.

This closes the loop that text-only intelligence cannot: the text pipeline
believes the document is fine — the vision critic looks at what a reader
will look at.
"""

from __future__ import annotations
import base64
import json
import os
import urllib.request
from typing import List, Optional

from .llm_brain import DEFAULT_URL

_VISION_PREFERENCE = ["minicpm-v", "llama3.2-vision", "llava", "moondream",
                      "bakllava", "qwen2-vl", "qwen2.5vl"]

_CRITIC_PROMPT = """\
You are reviewing ONE page image of a business document before it is sent
to a client. Describe only problems you can actually SEE on this specific
page. For every problem you MUST quote the exact visible text involved or
name the precise location (e.g. "bottom-left card"). If you cannot quote
or locate it, it does not exist — do not report it.

Problems worth reporting: placeholder wording still visible (quote it),
text cut off mid-word by a box edge (quote the cut text), a heading with
an empty area under it (name the heading), overlapping text (quote both).

Respond as JSON: {"issues": ["<quoted evidence>: <what is wrong>", ...]}
If the page looks finished, respond exactly: {"issues": []}
Never restate these instructions. JSON only."""

# fragments of the instructions — a lazy model echoes them back as findings
_ECHO_MARKERS = ("overflowing or clipped", "placeholder leftovers",
                 "lorem-like latin", "empty regions that look broken",
                 "jarring imbalance", "unreadable contrast",
                 "problems worth reporting", "quoted evidence")


class VisionCritic:
    def __init__(self, base_url: str = DEFAULT_URL, model: Optional[str] = None,
                 timeout: int = 300):
        self.base_url = base_url.rstrip("/")
        self.model = model or os.environ.get("WRS_VISION_MODEL")
        self.timeout = timeout
        self.last_error: Optional[str] = None

    def _get_json(self, path, timeout=4):
        with urllib.request.urlopen(self.base_url + path, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def pick_model(self) -> Optional[str]:
        if self.model:
            return self.model
        try:
            installed = [m.get("name", "") for m in
                         self._get_json("/api/tags").get("models", [])]
        except Exception as e:
            self.last_error = f"Ollama not reachable ({e})"
            return None
        low = [(m, m.lower()) for m in installed]
        for pref in _VISION_PREFERENCE:
            for name, l in low:
                if pref in l:
                    self.model = name
                    return name
        self.last_error = ("no vision model installed — `ollama pull "
                           "minicpm-v` to enable design review")
        return None

    def is_available(self) -> bool:
        return bool(self.pick_model())

    def critique_page(self, png_bytes: bytes) -> Optional[List[str]]:
        model = self.pick_model()
        if not model:
            return None
        payload = json.dumps({
            "model": model,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.0, "keep_alive": "10m"},
            "messages": [{
                "role": "user",
                "content": _CRITIC_PROMPT,
                "images": [base64.b64encode(png_bytes).decode("ascii")],
            }],
        }).encode("utf-8")
        req = urllib.request.Request(self.base_url + "/api/chat", data=payload,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                resp = json.loads(r.read().decode("utf-8"))
            out = (resp.get("message") or {}).get("content", "")
            data = json.loads(out)
            issues = data.get("issues", [])
            clean = []
            for i in issues:
                if isinstance(i, dict):  # some models answer {"description": ...}
                    i = ": ".join(str(v) for v in i.values() if v)
                text = str(i).strip()
                if not text:
                    continue
                low = text.lower()
                if any(m in low for m in _ECHO_MARKERS):
                    continue  # instruction echo, not an observation
                clean.append(text)
            return clean
        except Exception as e:
            self.last_error = f"vision critique failed: {e}"
            return None


def review_pdf(pdf_path: str, log=None, max_pages: int = 20,
               dpi: int = 100) -> dict:
    """Run the vision critic over every page of a PDF.
    Returns {page_number: [issues]} for pages with findings."""
    log = log or (lambda *_: None)
    critic = VisionCritic()
    if not critic.is_available():
        log(f"Design review unavailable: {critic.last_error}")
        return {}
    # the vision model needs the RAM the chat models are holding
    from .llm_brain import LocalLLMBrain
    LocalLLMBrain().free_other_models(keep=critic.model)
    import fitz
    doc = fitz.open(pdf_path)
    findings: dict = {}
    n = min(len(doc), max_pages)
    log(f"AI design review ({critic.model}) — looking at {n} page(s)...")
    for i in range(n):
        png = doc[i].get_pixmap(dpi=dpi).tobytes("png")
        issues = critic.critique_page(png)
        if issues is None:
            log(f"  page {i + 1}: review failed ({critic.last_error})")
            continue
        if issues:
            findings[i + 1] = issues
            for issue in issues:
                log(f"  page {i + 1}: ⚠ {issue}")
        else:
            log(f"  page {i + 1}: ✓ clean")
    if not findings:
        log("Design review: all pages look professionally finished.")
    # release the 5.5 GB vision model as soon as the review ends
    try:
        critic_payload = json.dumps({"model": critic.model,
                                     "keep_alive": 0}).encode("utf-8")
        urllib.request.urlopen(urllib.request.Request(
            critic.base_url + "/api/generate", data=critic_payload,
            headers={"Content-Type": "application/json"}), timeout=10).read()
    except Exception:
        pass
    return findings
