"""
studio_ui.py
-------------
Word Report Studio — the full workbench UI (offline, Tkinter).

Three-step workflow instead of a flat form:
  1 · Content   — paste rough text or import a .docx with structure intact
  2 · Preview   — page-by-page preview of every generated option, in-app
  3 · Fill My Template — slot grid for designer templates: see every text
      box, let the AI propose copy, edit any cell, apply & preview

Everything runs offline; long work happens on worker threads so the
window never freezes.
"""

from __future__ import annotations
import base64
import os
import sys
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.content_parser import ContentParser
from app.structure_model import DocumentMeta, LangMode
from app.template_manager import TemplateManager
from app.layout_engine import LayoutEngine
from app.docx_renderer import DocxRenderer
from app.offline_brain import OfflineDocumentBrain, format_brain_summary
from app import chart_engine, pdf_exporter, data_insights

TEMPLATES_DIR = os.path.join(PROJECT_ROOT, "templates")
USER_TEMPLATES_DIR = os.path.join(PROJECT_ROOT, "user_templates")
DEFAULT_OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")
SAMPLE_FILE = os.path.join(PROJECT_ROOT, "sample_input", "sample_bilingual.md")

# ---- palette ---------------------------------------------------------------
BG = "#F2F4F8"
PANEL = "#FFFFFF"
INK = "#16233B"
MUTED = "#6B7686"
ACCENT = "#1F4E79"
ACCENT_LIGHT = "#E8F0F8"
OK = "#2E8B57"
WARN = "#B3261E"


class StudioApp(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("Word Report Studio")
        self.geometry("1380x860")
        self.minsize(1150, 720)
        self.configure(bg=BG)
        self._style()

        self.template_manager = TemplateManager(TEMPLATES_DIR)
        self.layout_engine = LayoutEngine(self.template_manager)
        self.renderer = DocxRenderer()

        # preview state: list of {"label","docx","pdf"} + current indexes
        self.preview_options: list[dict] = []
        self.preview_idx = 0
        self.preview_page = 0
        self._preview_photo = None       # keep a reference or Tk drops it
        self._fitz_doc = None
        self._resize_job = None

        self.fill_slots_cache = None     # (template_path, slots)

        self._build_header()
        self._build_main()
        self._build_log()
        self._report_capabilities()

    # ------------------------------------------------------------------ style
    def _style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=INK, font=("Segoe UI", 10))
        s.configure("Panel.TFrame", background=PANEL)
        s.configure("BG.TFrame", background=BG)
        s.configure("Header.TFrame", background=ACCENT)
        s.configure("Header.TLabel", background=ACCENT, foreground="white",
                    font=("Segoe UI Semibold", 15))
        s.configure("HeaderSub.TLabel", background=ACCENT, foreground="#BFD3E6",
                    font=("Segoe UI", 9))
        s.configure("Side.TLabelframe", background=PANEL)
        s.configure("Side.TLabelframe.Label", background=PANEL,
                    foreground=MUTED, font=("Segoe UI Semibold", 9))
        s.configure("TLabelframe", background=PANEL)
        s.configure("TLabelframe.Label", background=PANEL, foreground=MUTED)
        s.configure("TNotebook", background=BG, borderwidth=0)
        s.configure("TNotebook.Tab", padding=(18, 8),
                    font=("Segoe UI Semibold", 10))
        s.map("TNotebook.Tab",
              background=[("selected", PANEL), ("!selected", BG)],
              foreground=[("selected", ACCENT), ("!selected", MUTED)])
        s.configure("Accent.TButton", background=ACCENT, foreground="white",
                    font=("Segoe UI Semibold", 11), padding=(16, 8))
        s.map("Accent.TButton", background=[("active", "#2A639A"),
                                            ("disabled", "#9FB3C8")])
        s.configure("Tool.TButton", padding=(10, 4))
        s.configure("Muted.TLabel", foreground=MUTED, background=PANEL,
                    font=("Segoe UI", 9))
        s.configure("PanelLbl.TLabel", background=PANEL)
        s.configure("Badge.TLabel", background=ACCENT_LIGHT, foreground=ACCENT,
                    font=("Segoe UI", 9), padding=(8, 2))
        s.configure("Treeview", rowheight=26, font=("Segoe UI", 9))
        s.configure("Treeview.Heading", font=("Segoe UI Semibold", 9))

    # ----------------------------------------------------------------- header
    def _build_header(self):
        h = ttk.Frame(self, style="Header.TFrame", padding=(18, 10))
        h.pack(fill="x")
        left = ttk.Frame(h, style="Header.TFrame")
        left.pack(side="left")
        ttk.Label(left, text="Word Report Studio", style="Header.TLabel").pack(anchor="w")
        ttk.Label(left, text="Rough content in — designer documents out. 100% offline.",
                  style="HeaderSub.TLabel").pack(anchor="w")
        self.badges = ttk.Frame(h, style="Header.TFrame")
        self.badges.pack(side="right")

    def _badge(self, text, good=True):
        lbl = tk.Label(self.badges, text=("● " if good else "○ ") + text,
                       bg=ACCENT, fg="#9FDDA8" if good else "#E8B4B0",
                       font=("Segoe UI", 9))
        lbl.pack(side="right", padx=6)

    def _report_capabilities(self):
        self._badge("Charts", chart_engine.is_available())
        self._badge("PDF preview", pdf_exporter.is_available())
        def check_ai():
            try:
                from app.llm_brain import LocalLLMBrain
                brain = LocalLLMBrain()
                ok = brain.is_available()
                model = brain.model or ""
                self.after(0, lambda: self._badge(f"Local AI {model}".strip(), ok))
                self.after(0, lambda: self.log(
                    f"Local AI: {'ready — ' + model if ok else 'not detected (install Ollama to enable)'}"))
            except Exception:
                self.after(0, lambda: self._badge("Local AI", False))
        threading.Thread(target=check_ai, daemon=True).start()

    # ------------------------------------------------------------------- main
    def _build_main(self):
        main = ttk.Frame(self, style="BG.TFrame")
        main.pack(fill="both", expand=True, padx=12, pady=(10, 4))

        self._build_sidebar(main)

        self.nb = ttk.Notebook(main)
        self.nb.pack(side="left", fill="both", expand=True, padx=(12, 0))
        self._build_content_tab()
        self._build_preview_tab()
        self._build_fill_tab()

    # ---------------------------------------------------------------- sidebar
    def _build_sidebar(self, parent):
        side = ttk.Frame(parent, style="Panel.TFrame", padding=12)
        side.pack(side="left", fill="y")

        doc = ttk.Labelframe(side, text="DOCUMENT", style="Side.TLabelframe", padding=8)
        doc.pack(fill="x")
        self.title_var = tk.StringVar(value="Untitled Report")
        self.org_var = tk.StringVar()
        self.author_var = tk.StringVar()
        self.date_var = tk.StringVar()
        self.subtitle_var = tk.StringVar()
        self.confidential_var = tk.StringVar()
        for lbl, var in (("Title", self.title_var), ("Subtitle", self.subtitle_var),
                         ("Organization", self.org_var), ("Author", self.author_var),
                         ("Date", self.date_var), ("Confidentiality", self.confidential_var)):
            row = ttk.Frame(doc, style="Panel.TFrame")
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=lbl, width=12, style="PanelLbl.TLabel").pack(side="left")
            ttk.Entry(row, textvariable=var, width=22).pack(side="left", fill="x", expand=True)
        row = ttk.Frame(doc, style="Panel.TFrame")
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="Language", width=12, style="PanelLbl.TLabel").pack(side="left")
        self.lang_var = tk.StringVar(value="Auto-detect")
        ttk.Combobox(row, textvariable=self.lang_var, state="readonly", width=20,
                     values=["Auto-detect", "English", "Arabic", "Bilingual"]).pack(side="left")

        des = ttk.Labelframe(side, text="BUILT-IN DESIGNS", style="Side.TLabelframe", padding=8)
        des.pack(fill="x", pady=(10, 0))
        self.template_vars = {}
        for t in self.template_manager.list_templates():
            var = tk.BooleanVar(value=t.id in ("executive_premium", "strategy_premium"))
            self.template_vars[t.id] = var
            ttk.Checkbutton(des, text=t.name, variable=var).pack(anchor="w")

        opt = ttk.Labelframe(side, text="OPTIONS", style="Side.TLabelframe", padding=8)
        opt.pack(fill="x", pady=(10, 0))
        self.include_cover_var = tk.BooleanVar(value=True)
        self.include_toc_var = tk.BooleanVar(value=True)
        self.use_ai_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt, text="Cover page", variable=self.include_cover_var).pack(anchor="w")
        ttk.Checkbutton(opt, text="Table of contents", variable=self.include_toc_var).pack(anchor="w")
        ttk.Checkbutton(opt, text="Use local AI (slower, smarter)",
                        variable=self.use_ai_var).pack(anchor="w")
        row = ttk.Frame(opt, style="Panel.TFrame")
        row.pack(fill="x", pady=(4, 0))
        ttk.Label(row, text="Max options", style="PanelLbl.TLabel").pack(side="left")
        self.num_options_var = tk.IntVar(value=4)
        ttk.Spinbox(row, from_=1, to=8, textvariable=self.num_options_var,
                    width=4).pack(side="left", padx=6)

        br = ttk.Labelframe(side, text="MY BRAND", style="Side.TLabelframe", padding=8)
        br.pack(fill="x", pady=(10, 0))
        from app.branding import load_brand
        existing = load_brand()
        self.brand_enabled_var = tk.BooleanVar(value=bool(existing and existing.enabled))
        self.brand_logo_var = tk.StringVar(value=(existing.logo_path if existing else ""))
        self.brand_primary_var = tk.StringVar(value=(existing.primary if existing else ""))
        self.brand_secondary_var = tk.StringVar(value=(existing.secondary if existing else ""))
        self.brand_accent_var = tk.StringVar(value=(existing.accent if existing else ""))
        ttk.Checkbutton(br, text="Apply my brand to all designs",
                        variable=self.brand_enabled_var,
                        command=self._save_branding).pack(anchor="w")
        row = ttk.Frame(br, style="Panel.TFrame")
        row.pack(fill="x", pady=(4, 0))
        self.brand_logo_btn = ttk.Button(
            row, style="Tool.TButton", command=self._pick_logo,
            text=("Logo ✓" if (existing and existing.logo_path) else "Logo…"))
        self.brand_logo_btn.pack(side="left")
        for lbl, var in (("P", self.brand_primary_var),
                         ("S", self.brand_secondary_var),
                         ("A", self.brand_accent_var)):
            ttk.Label(row, text=" " + lbl, style="PanelLbl.TLabel").pack(side="left")
            e = ttk.Entry(row, textvariable=var, width=7)
            e.pack(side="left")
            e.bind("<FocusOut>", lambda _e: self._save_branding())
        ttk.Label(br, text="P/S/A = primary, secondary, accent hex colors",
                  style="Muted.TLabel").pack(anchor="w")

        out = ttk.Labelframe(side, text="OUTPUT FOLDER", style="Side.TLabelframe", padding=8)
        out.pack(fill="x", pady=(10, 0))
        self.output_dir_var = tk.StringVar(value=DEFAULT_OUTPUT_DIR)
        row = ttk.Frame(out, style="Panel.TFrame")
        row.pack(fill="x")
        ttk.Entry(row, textvariable=self.output_dir_var).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="…", width=3, command=self._browse_output).pack(side="left", padx=(4, 0))

        self.generate_btn = ttk.Button(side, text="⚡  Generate & Preview",
                                       style="Accent.TButton", command=self._on_generate)
        self.generate_btn.pack(fill="x", pady=(16, 4))
        self.progress = ttk.Progressbar(side, mode="indeterminate")
        self.progress.pack(fill="x")

    # ------------------------------------------------------------ content tab
    def _build_content_tab(self):
        tab = ttk.Frame(self.nb, style="Panel.TFrame", padding=10)
        self.nb.add(tab, text="  1 · Content  ")
        bar = ttk.Frame(tab, style="Panel.TFrame")
        bar.pack(fill="x", pady=(0, 6))
        ttk.Button(bar, text="Paste", style="Tool.TButton", command=self._paste).pack(side="left", padx=2)
        ttk.Button(bar, text="Load File…  (.docx / .md / .txt)", style="Tool.TButton",
                   command=self._load_file).pack(side="left", padx=2)
        ttk.Button(bar, text="Load Sample", style="Tool.TButton",
                   command=self._load_sample).pack(side="left", padx=2)
        ttk.Button(bar, text="Clear", style="Tool.TButton",
                   command=lambda: self.editor.delete("1.0", "end")).pack(side="left", padx=2)
        ttk.Label(bar, text="Tip: rough, unformatted text is fine — the engine builds "
                            "tables, charts, KPIs and icons from it.",
                  style="Muted.TLabel").pack(side="left", padx=14)
        self.editor = tk.Text(tab, wrap="word", undo=True, font=("Consolas", 11),
                              relief="flat", background="#FBFCFE", foreground=INK,
                              insertbackground=INK, padx=10, pady=8)
        self.editor.pack(fill="both", expand=True)

    # ------------------------------------------------------------ preview tab
    def _build_preview_tab(self):
        tab = ttk.Frame(self.nb, style="Panel.TFrame", padding=10)
        self.nb.add(tab, text="  2 · Preview  ")
        bar = ttk.Frame(tab, style="Panel.TFrame")
        bar.pack(fill="x", pady=(0, 6))
        ttk.Label(bar, text="Design option:", style="PanelLbl.TLabel").pack(side="left")
        self.option_box = ttk.Combobox(bar, state="readonly", width=44)
        self.option_box.pack(side="left", padx=6)
        self.option_box.bind("<<ComboboxSelected>>", self._on_option_change)
        ttk.Button(bar, text="◀", width=3, style="Tool.TButton",
                   command=lambda: self._flip_page(-1)).pack(side="left", padx=(16, 2))
        self.page_lbl = ttk.Label(bar, text="– / –", style="PanelLbl.TLabel")
        self.page_lbl.pack(side="left")
        ttk.Button(bar, text="▶", width=3, style="Tool.TButton",
                   command=lambda: self._flip_page(1)).pack(side="left", padx=2)
        ttk.Button(bar, text="Open in Word", style="Tool.TButton",
                   command=self._open_in_word).pack(side="right", padx=2)
        ttk.Button(bar, text="Open folder", style="Tool.TButton",
                   command=self._open_folder).pack(side="right", padx=2)
        ttk.Button(bar, text="👁 AI Design Review", style="Tool.TButton",
                   command=self._design_review).pack(side="right", padx=2)
        ttk.Button(bar, text="↻ Regenerate", style="Tool.TButton",
                   command=self._on_generate).pack(side="right", padx=(2, 10))

        self.canvas = tk.Canvas(tab, bg="#DDE3EB", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.canvas.bind("<Button-1>", lambda e: self._flip_page(1))
        self.canvas.bind("<Button-3>", lambda e: self._flip_page(-1))
        self._canvas_hint("Generate a report (or fill a template) to preview pages here.\n"
                          "Left-click: next page · Right-click: previous page")

    def _canvas_hint(self, text):
        self.canvas.delete("all")
        self.canvas.create_text(
            self.canvas.winfo_width() // 2 or 480,
            self.canvas.winfo_height() // 2 or 300,
            text=text, fill=MUTED, font=("Segoe UI", 12), justify="center")

    # --------------------------------------------------------------- fill tab
    def _build_fill_tab(self):
        tab = ttk.Frame(self.nb, style="Panel.TFrame", padding=10)
        self.nb.add(tab, text="  3 · Fill My Template  ")

        bar = ttk.Frame(tab, style="Panel.TFrame")
        bar.pack(fill="x", pady=(0, 6))
        ttk.Label(bar, text="Designer template:", style="PanelLbl.TLabel").pack(side="left")
        self.user_tpl_box = ttk.Combobox(bar, state="readonly", width=48,
                                         values=self._user_templates())
        self.user_tpl_box.pack(side="left", padx=6)
        ttk.Button(bar, text="⟳", width=3, style="Tool.TButton",
                   command=lambda: self.user_tpl_box.configure(
                       values=self._user_templates())).pack(side="left")
        ttk.Button(bar, text="1 · Analyze slots", style="Tool.TButton",
                   command=self._analyze_slots).pack(side="left", padx=(14, 2))
        self.ai_suggest_btn = ttk.Button(bar, text="2 · AI Suggest content",
                                         style="Tool.TButton", command=self._ai_suggest)
        self.ai_suggest_btn.pack(side="left", padx=2)
        self.apply_btn = ttk.Button(bar, text="3 · Apply && Preview",
                                    style="Tool.TButton", command=self._apply_fill)
        self.apply_btn.pack(side="left", padx=2)
        self.trim_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Trim unfilled pages",
                        variable=self.trim_var).pack(side="right")
        self.images_dir_var = tk.StringVar()
        self.images_btn = ttk.Button(bar, text="Photos folder…",
                                     style="Tool.TButton", command=self._pick_images)
        self.images_btn.pack(side="right", padx=(2, 10))

        ttk.Label(tab, text="Double-click any row to edit its new text. Content comes "
                            "from the Content tab; the AI writes copy that fits each "
                            "designed box — you always get the final say here.",
                  style="Muted.TLabel").pack(anchor="w", pady=(0, 4))

        cols = ("page", "kind", "max", "current", "new")
        self.slot_tree = ttk.Treeview(tab, columns=cols, show="headings")
        widths = {"page": 46, "kind": 70, "max": 46, "current": 330, "new": 400}
        for c in cols:
            self.slot_tree.heading(c, text=c.title())
            self.slot_tree.column(c, width=widths[c],
                                  anchor="center" if c in ("page", "max") else "w")
        vs = ttk.Scrollbar(tab, orient="vertical", command=self.slot_tree.yview)
        self.slot_tree.configure(yscrollcommand=vs.set)
        self.slot_tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="left", fill="y")
        self.slot_tree.bind("<Double-1>", self._edit_slot_row)
        # red = designed placeholder wording that must be replaced
        self.slot_tree.tag_configure("ph", background="#FDECEA")

    def _user_templates(self):
        try:
            files = [f for f in sorted(os.listdir(USER_TEMPLATES_DIR))
                     if f.lower().endswith((".docx", ".dotx"))]
        except FileNotFoundError:
            files = []
        return files

    # ------------------------------------------------------------------- log
    def _build_log(self):
        frame = ttk.Frame(self, style="BG.TFrame")
        frame.pack(fill="x", padx=12, pady=(0, 8))
        self.log_text = tk.Text(frame, height=6, wrap="word", state="disabled",
                                relief="flat", background="#0E1B2C",
                                foreground="#B9CCE0", font=("Consolas", 9),
                                padx=8, pady=4)
        self.log_text.pack(fill="x")

    def log(self, msg: str):
        def _do():
            self.log_text.configure(state="normal")
            self.log_text.insert("end", msg + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        self.after(0, _do)

    # ----------------------------------------------------------- content ops
    def _paste(self):
        try:
            self.editor.insert("insert", self.clipboard_get())
        except tk.TclError:
            messagebox.showinfo("Clipboard empty",
                                "Copy some text first, then click Paste.")

    def _load_file(self):
        path = filedialog.askopenfilename(
            title="Load content file",
            filetypes=[("Word document", "*.docx"),
                       ("Text/Markdown/JSON", "*.md *.txt *.json"),
                       ("All files", "*.*")])
        if not path:
            return
        try:
            if path.lower().endswith(".docx"):
                from app.docx_ingest import docx_to_markdown, source_title
                content = docx_to_markdown(path)
                title = source_title(path)
                if title and self.title_var.get() in ("", "Untitled Report"):
                    self.title_var.set(title)
                self.log(f"Imported Word document with structure intact: {path}")
            else:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                self.log(f"Loaded {path}")
            self.editor.delete("1.0", "end")
            self.editor.insert("1.0", content)
        except Exception as e:
            messagebox.showerror("Could not load file", str(e))

    def _load_sample(self):
        if os.path.isfile(SAMPLE_FILE):
            with open(SAMPLE_FILE, "r", encoding="utf-8") as f:
                self.editor.delete("1.0", "end")
                self.editor.insert("1.0", f.read())

    def _browse_output(self):
        path = filedialog.askdirectory(title="Choose output folder")
        if path:
            self.output_dir_var.set(path)

    def _pick_logo(self):
        path = filedialog.askopenfilename(
            title="Choose your logo image",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp"), ("All files", "*.*")])
        if path:
            self.brand_logo_var.set(path)
            self.brand_logo_btn.configure(text="Logo ✓")
            self._save_branding()

    def _save_branding(self):
        from app.branding import Brand, save_brand, load_brand
        prev = load_brand() or Brand()
        brand = Brand(
            enabled=self.brand_enabled_var.get(),
            organization=self.org_var.get() or prev.organization,
            logo_path=self.brand_logo_var.get(),
            primary=self.brand_primary_var.get(),
            secondary=self.brand_secondary_var.get(),
            accent=self.brand_accent_var.get(),
            heading_font=prev.heading_font, body_font=prev.body_font,
            heading_font_ar=prev.heading_font_ar, body_font_ar=prev.body_font_ar)
        save_brand(brand)
        self.log("Brand saved" + (" — active on every design."
                                  if brand.enabled else " (disabled)."))

    # ------------------------------------------------------------- generation
    def _busy(self, on: bool):
        def _do():
            self.generate_btn.configure(state="disabled" if on else "normal")
            (self.progress.start(12) if on else self.progress.stop())
        self.after(0, _do)

    def _on_generate(self):
        content = self.editor.get("1.0", "end").strip()
        if not content:
            messagebox.showwarning("No content", "Paste or load content first (tab 1).")
            return
        selected = [tid for tid, v in self.template_vars.items() if v.get()]
        if not selected:
            messagebox.showwarning("No designs", "Select at least one built-in design.")
            return
        self._busy(True)
        threading.Thread(target=self._generate_worker,
                         args=(content, selected), daemon=True).start()

    def _generate_worker(self, content: str, selected_ids: list):
        try:
            lang_map = {"Auto-detect": None, "English": LangMode.ENGLISH,
                        "Arabic": LangMode.ARABIC, "Bilingual": LangMode.BILINGUAL}
            forced_lang = lang_map.get(self.lang_var.get())
            meta = DocumentMeta(
                title=self.title_var.get() or "Untitled Report",
                subtitle=self.subtitle_var.get() or None,
                author=self.author_var.get() or None,
                organization=self.org_var.get() or None,
                date=self.date_var.get() or None,
                confidentiality=self.confidential_var.get() or None,
                lang_mode=forced_lang)

            if self.use_ai_var.get():
                from app.llm_brain import LocalLLMBrain
                llm = LocalLLMBrain()
                if llm.is_available():
                    self.log("Local AI is reading and restructuring your content "
                             "(this is the slow, smart part)...")
                    structured = llm.restructure(content)
                    if structured:
                        content = structured
                        self.log("Local AI finished: content restructured into designed elements.")
                    else:
                        self.log(f"Local AI skipped: {llm.last_error}")

            from app.branding import load_brand, apply_to_template
            brand = load_brand()
            brand_active = bool(brand and brand.enabled)
            if brand_active:
                self.log(f"Branding active: {brand.organization or 'my brand'}")
                if brand.organization and not meta.organization:
                    meta.organization = brand.organization

            self.log("Parsing and auto-designing...")
            parser = ContentParser()
            report = parser.parse_auto(content, meta=meta)
            if brand_active:
                report.branding = brand
            if forced_lang is not None:
                report.meta.lang_mode = forced_lang
            report.include_cover = self.include_cover_var.get()
            report.include_toc = self.include_toc_var.get()
            if getattr(report, "auto_enrichment", None) is not None:
                from app.auto_enrich import format_enrichment
                self.log(format_enrichment(report.auto_enrichment))
            brain = OfflineDocumentBrain()
            analysis = brain.analyze(content, report)
            brain.apply_to_report(report, analysis)
            for line in format_brain_summary(analysis).splitlines():
                self.log(line)

            options = self.layout_engine.generate_options(
                report, max_options=self.num_options_var.get(),
                template_ids=selected_ids, brain_analysis=analysis)
            if brand_active:
                for opt in options:
                    opt.template = apply_to_template(brand, opt.template)
            out_dir = self.output_dir_var.get() or DEFAULT_OUTPUT_DIR
            os.makedirs(out_dir, exist_ok=True)
            results = self.renderer.render_batch(report, options, output_dir=out_dir)
            self.log(f"Rendered {len(results)} option(s).")

            previews = []
            if pdf_exporter.is_available():
                self.log("Building page previews (LibreOffice)...")
                pdf_map = pdf_exporter.convert_batch(
                    [p for _o, p in results], output_dir=out_dir)
                for opt, docx_path in results:
                    previews.append({"label": opt.label, "docx": docx_path,
                                     "pdf": pdf_map.get(docx_path)})
            else:
                self.log("LibreOffice not found — preview disabled, .docx files ready.")
                previews = [{"label": o.label, "docx": p, "pdf": None}
                            for o, p in results]
            self.after(0, lambda: self._set_preview_options(previews))
            self.log("Done — see the Preview tab.")
        except Exception as e:
            self.log(f"ERROR: {e!r}")
        finally:
            self._busy(False)

    # -------------------------------------------------------------- previewer
    def _set_preview_options(self, options: list):
        self.preview_options = [o for o in options if o.get("pdf")] or options
        self.option_box.configure(values=[o["label"] for o in self.preview_options])
        if self.preview_options:
            self.option_box.current(0)
            self.preview_idx = 0
            self.preview_page = 0
            self._load_pdf()
            self.nb.select(1)

    def _on_option_change(self, _e=None):
        self.preview_idx = self.option_box.current()
        self.preview_page = 0
        self._load_pdf()

    def _load_pdf(self):
        opt = self.preview_options[self.preview_idx] if self.preview_options else None
        self._fitz_doc = None
        if not opt or not opt.get("pdf") or not os.path.isfile(opt["pdf"]):
            self._canvas_hint("No PDF preview for this option.\n"
                              "Install LibreOffice to enable page previews.")
            self.page_lbl.configure(text="– / –")
            return
        try:
            import fitz
            self._fitz_doc = fitz.open(opt["pdf"])
        except Exception as e:
            self._canvas_hint(f"Preview failed: {e}")
            return
        self._show_page()

    def _show_page(self):
        if self._fitz_doc is None:
            return
        n = len(self._fitz_doc)
        self.preview_page = max(0, min(self.preview_page, n - 1))
        page = self._fitz_doc[self.preview_page]
        cw = max(self.canvas.winfo_width(), 200)
        ch = max(self.canvas.winfo_height(), 200)
        zoom = min((cw - 24) / page.rect.width, (ch - 24) / page.rect.height)
        import fitz
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        data = base64.b64encode(pix.tobytes("png"))
        self._preview_photo = tk.PhotoImage(data=data)
        self.canvas.delete("all")
        self.canvas.create_image(cw // 2, ch // 2, image=self._preview_photo)
        self.page_lbl.configure(text=f"{self.preview_page + 1} / {n}")

    def _flip_page(self, step: int):
        if self._fitz_doc is not None:
            self.preview_page += step
            self._show_page()

    def _on_canvas_resize(self, _e):
        if self._resize_job:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(150, self._show_page)

    def _design_review(self):
        if not self.preview_options:
            messagebox.showinfo("Nothing to review", "Generate or fill something first.")
            return
        opt = self.preview_options[self.preview_idx]
        if not opt.get("pdf"):
            messagebox.showinfo("No PDF", "This option has no PDF preview to review.")
            return
        self._busy(True)
        self.log("AI design review starting — the vision model looks at "
                 "every page (about a minute per page on this machine)...")
        def worker():
            try:
                from app.vision_critic import review_pdf
                review_pdf(opt["pdf"], log=self.log)
            except Exception as e:
                self.log(f"ERROR: {e!r}")
            finally:
                self._busy(False)
        threading.Thread(target=worker, daemon=True).start()

    def _open_in_word(self):
        if self.preview_options:
            os.startfile(self.preview_options[self.preview_idx]["docx"])

    def _open_folder(self):
        if self.preview_options:
            subprocess.Popen(["explorer", "/select,",
                              os.path.normpath(self.preview_options[self.preview_idx]["docx"])])

    # ---------------------------------------------------------- template fill
    def _current_template_path(self):
        name = self.user_tpl_box.get()
        if not name:
            messagebox.showinfo("Pick a template",
                                f"Put your .docx/.dotx templates in:\n{USER_TEMPLATES_DIR}\n"
                                "then choose one from the list.")
            return None
        return os.path.join(USER_TEMPLATES_DIR, name)

    def _analyze_slots(self):
        path = self._current_template_path()
        if not path:
            return
        from app.template_filler import extract_slots
        self.log(f"Analyzing template slots: {os.path.basename(path)}")
        slots = extract_slots(path)
        self.fill_slots_cache = (path, slots)
        self.slot_tree.delete(*self.slot_tree.get_children())
        n_ph = 0
        for s in slots:
            if s.kind == "page-number":
                continue
            tags = ("ph",) if s.is_placeholder else ()
            n_ph += bool(s.is_placeholder)
            self.slot_tree.insert("", "end", iid=str(s.slot_id), tags=tags,
                                  values=(s.page, s.kind, s.capacity,
                                          s.text.replace("\n", " ")[:90], ""))
        from app.image_filler import count_photo_frames
        frames = count_photo_frames(slots[0]._doc)
        self.log(f"Found {len(slots)} text slots across "
                 f"{max(s.page for s in slots)} designed pages "
                 f"({n_ph} highlighted red = placeholder wording that must be "
                 f"replaced) and {frames} photo frame(s). "
                 "Use AI Suggest, pick a Photos folder, or edit rows directly.")

    def _ai_suggest(self):
        if not self.fill_slots_cache:
            messagebox.showinfo("Analyze first", "Click '1 · Analyze slots' first.")
            return
        content = self.editor.get("1.0", "end").strip()
        if not content:
            messagebox.showwarning("No content", "Put your rough content in tab 1 first.")
            return
        self._busy(True)
        self.log("AI is assigning your content to the designed slots "
                 "(takes a few minutes — quality over speed)...")
        threading.Thread(target=self._ai_suggest_worker, args=(content,),
                         daemon=True).start()

    def _ai_suggest_worker(self, content):
        try:
            path, slots = self.fill_slots_cache
            from app.llm_brain import LocalLLMBrain, map_content_to_slots_chunked
            from app.template_filler import sanitize_mapping, rules_mapping
            brain = LocalLLMBrain()
            mapping = None
            if brain.is_available():
                mapping = map_content_to_slots_chunked(brain, content, slots,
                                                       log=self.log)
                if mapping is None:
                    self.log(f"AI mapping failed ({brain.last_error}) — using rules.")
            if mapping is None:
                mapping = rules_mapping(content, slots, org=self.org_var.get() or None)
                mapping = sanitize_mapping(slots, mapping)
            else:
                mapping = sanitize_mapping(slots, mapping)
                extra = rules_mapping(content, slots,
                                      org=self.org_var.get() or None, exclude=mapping)
                mapping.update(sanitize_mapping(slots, extra))

            def apply_to_grid():
                for sid, text in mapping.items():
                    iid = str(sid)
                    if self.slot_tree.exists(iid):
                        vals = list(self.slot_tree.item(iid, "values"))
                        vals[4] = text.replace("\n", " ⏎ ")
                        self.slot_tree.item(iid, values=vals)
                self.log(f"AI proposed text for {len(mapping)} slot(s). Review/edit, "
                         "then click '3 · Apply & Preview'.")
            self.after(0, apply_to_grid)
        except Exception as e:
            self.log(f"ERROR: {e!r}")
        finally:
            self._busy(False)

    def _pick_images(self):
        folder = filedialog.askdirectory(title="Folder with photos for the "
                                               "template's image frames")
        if folder:
            from app.image_filler import collect_images
            n = len(collect_images(folder))
            self.images_dir_var.set(folder)
            self.images_btn.configure(text=f"Photos: {n} ✓")
            self.log(f"Photos folder set ({n} usable image(s)): {folder}")

    def _edit_slot_row(self, _e):
        sel = self.slot_tree.selection()
        if not sel:
            return
        iid = sel[0]
        vals = list(self.slot_tree.item(iid, "values"))
        win = tk.Toplevel(self)
        win.title(f"Edit slot (page {vals[0]}, max {vals[2]} chars)")
        win.geometry("560x220")
        win.configure(bg=PANEL)
        ttk.Label(win, text=f"Designed placeholder:  {vals[3]}",
                  style="Muted.TLabel", wraplength=520).pack(anchor="w", padx=10, pady=(10, 4))
        box = tk.Text(win, height=6, wrap="word", font=("Segoe UI", 10))
        box.pack(fill="both", expand=True, padx=10)
        box.insert("1.0", vals[4].replace(" ⏎ ", "\n"))
        counter = ttk.Label(win, style="Muted.TLabel")
        counter.pack(anchor="e", padx=10)

        def update_count(_ev=None):
            n = len(box.get("1.0", "end").strip())
            over = n > int(vals[2]) * 1.35
            counter.configure(text=f"{n} / max ~{vals[2]} chars"
                                   + ("  — TOO LONG, will be cut" if over else ""))
        box.bind("<KeyRelease>", update_count)
        update_count()

        def save():
            vals[4] = box.get("1.0", "end").strip().replace("\n", " ⏎ ")
            self.slot_tree.item(iid, values=vals)
            win.destroy()
        btns = ttk.Frame(win, style="Panel.TFrame")
        btns.pack(fill="x", pady=8)
        ttk.Button(btns, text="Save", style="Accent.TButton", command=save).pack(side="right", padx=10)
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="right")

    def _apply_fill(self):
        if not self.fill_slots_cache:
            messagebox.showinfo("Analyze first", "Click '1 · Analyze slots' first.")
            return
        mapping = {}
        for iid in self.slot_tree.get_children():
            vals = self.slot_tree.item(iid, "values")
            if vals[4]:
                mapping[int(iid)] = str(vals[4]).replace(" ⏎ ", "\n")
        if not mapping:
            messagebox.showinfo("Nothing to apply",
                                "No new text in the grid — run AI Suggest or edit rows.")
            return
        self._busy(True)
        threading.Thread(target=self._apply_fill_worker, args=(mapping,),
                         daemon=True).start()

    def _apply_fill_worker(self, mapping):
        try:
            path, slots = self.fill_slots_cache
            from app.template_filler import (sanitize_mapping, unfillable_pages,
                                             fill_slots, _LOREM_HINTS)
            mapping = sanitize_mapping(slots, mapping)
            trim = unfillable_pages(slots, mapping) if self.trim_var.get() else set()
            for s in slots:
                if (s.kind == "body" and s.page not in trim
                        and s.slot_id not in mapping
                        and any(h in s.text.lower() for h in _LOREM_HINTS)):
                    mapping[s.slot_id] = ""
            out_dir = self.output_dir_var.get() or DEFAULT_OUTPUT_DIR
            os.makedirs(out_dir, exist_ok=True)
            base = os.path.splitext(os.path.basename(path))[0]
            out_path = os.path.join(out_dir, f"filled__{base}.docx")
            images = None
            if self.images_dir_var.get():
                from app.image_filler import collect_images
                images = collect_images(self.images_dir_var.get())
            fill_slots(path, mapping, out_path, slots=slots, trim_pages=trim,
                       images=images, log=self.log)
            if trim:
                self.log(f"Trimmed unfilled designed pages: {sorted(trim)}")
            self.log(f"Filled {len(mapping)} slot(s) -> {out_path}")
            from app.template_filler import sample_data_warnings
            warns = sample_data_warnings(slots, mapping)
            if warns:
                self.log("REVIEW BEFORE SENDING — template sample data kept:")
                for w in warns[:10]:
                    self.log(f"  ! {w}")
            pdf = None
            if pdf_exporter.is_available():
                pdf = pdf_exporter.convert_to_pdf(out_path, out_dir)
            entry = {"label": f"Filled: {base}", "docx": out_path, "pdf": pdf}
            def add_preview():
                labels = [o["label"] for o in self.preview_options]
                if entry["label"] in labels:
                    self.preview_options[labels.index(entry["label"])] = entry
                else:
                    self.preview_options.append(entry)
                self._set_preview_options(self.preview_options)
                self.option_box.current(len(self.preview_options) - 1)
                self._on_option_change()
            self.after(0, add_preview)
            # slots were consumed by fill; re-extract for another round
            self.fill_slots_cache = None
        except Exception as e:
            self.log(f"ERROR: {e!r}")
        finally:
            self._busy(False)


def main():
    app = StudioApp()
    app.mainloop()


if __name__ == "__main__":
    main()
