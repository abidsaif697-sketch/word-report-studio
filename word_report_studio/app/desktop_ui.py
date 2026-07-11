"""
desktop_ui.py
---------------
Offline Tkinter front end for Word Report Studio. Paste/load bilingual
(Arabic + English) content, fill in report metadata, pick which template
families to consider, and generate several designer-grade .docx layout
options at once -- plus optional PDF previews and an HTML comparison sheet.

Nothing here calls the network. Uses only the Python standard library for
the UI (tkinter) so there is nothing extra to install for the GUI itself.
"""

from __future__ import annotations
import os
import sys
import platform
import subprocess
import threading
import webbrowser
from datetime import date

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(APP_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.structure_model import DocumentMeta, LangMode
from app.content_parser import ContentParser
from app.template_manager import TemplateManager
from app.layout_engine import LayoutEngine
from app.docx_renderer import DocxRenderer
from app import pdf_exporter
from app import preview_gallery
from app import chart_engine
from app import data_insights
from app.offline_brain import OfflineDocumentBrain, format_brain_summary

TEMPLATES_DIR = os.path.join(PROJECT_ROOT, "templates")
SAMPLE_FILE = os.path.join(PROJECT_ROOT, "sample_input", "sample_bilingual.md")
DEFAULT_OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output")


def _open_path(path: str):
    try:
        system = platform.system()
        if system == "Windows":
            os.startfile(path)  # noqa
        elif system == "Darwin":
            subprocess.run(["open", path])
        else:
            subprocess.run(["xdg-open", path])
    except Exception as e:
        messagebox.showerror("Could not open", f"{path}\n\n{e}")


class WordReportStudioApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Word Report Studio — Offline Bilingual Report Designer")
        self.geometry("1180x780")
        self.minsize(980, 640)

        self.template_manager = TemplateManager(TEMPLATES_DIR)
        self.layout_engine = LayoutEngine(self.template_manager)
        self.renderer = DocxRenderer()

        self.template_vars: dict[str, tk.BooleanVar] = {}
        self.last_manifest: list[dict] = []
        self.last_contact_sheet: str | None = None

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=3)
        root.columnconfigure(1, weight=2)
        root.rowconfigure(0, weight=1)

        # ---- Left: content input ----
        left = ttk.Frame(root)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)

        toolbar = ttk.Frame(left)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(toolbar, text="Report content (Markdown-style, Arabic + English mixed OK)",
                  font=("Segoe UI", 10, "bold")).pack(side="left")
        ttk.Button(toolbar, text="Load File...", command=self._load_file).pack(side="right", padx=2)
        ttk.Button(toolbar, text="Load Sample", command=self._load_sample).pack(side="right", padx=2)
        ttk.Button(toolbar, text="Clear", command=self._clear_content).pack(side="right", padx=2)
        ttk.Button(toolbar, text="📋 Paste / لصق", command=self._paste_clipboard).pack(side="right", padx=2)

        self.content_text = tk.Text(left, wrap="word", undo=True, font=("Consolas", 11))
        self.content_text.grid(row=1, column=0, sticky="nsew")
        yscroll = ttk.Scrollbar(left, orient="vertical", command=self.content_text.yview)
        yscroll.grid(row=1, column=1, sticky="ns")
        self.content_text.configure(yscrollcommand=yscroll.set)
        self._attach_edit_menu(self.content_text)

        # Ctrl+V/C/X/A/Z by Windows keycode so shortcuts keep working even
        # when the active keyboard layout is Arabic (Tk's built-in bindings
        # only fire on the Latin letter keysyms).
        self.bind_all("<Control-KeyPress>", self._on_ctrl_key)

        log_label = ttk.Label(left, text="Log", font=("Segoe UI", 9, "bold"))
        log_label.grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.log_text = tk.Text(left, height=8, wrap="word", state="disabled",
                                 font=("Consolas", 9), background="#f4f4f4")
        self.log_text.grid(row=3, column=0, columnspan=2, sticky="nsew", pady=(2, 0))

        # ---- Right: settings + results ----
        right = ttk.Frame(root)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)

        meta_box = ttk.LabelFrame(right, text="Report details", padding=8)
        meta_box.grid(row=0, column=0, sticky="ew")
        meta_box.columnconfigure(1, weight=1)

        self.title_var = tk.StringVar(value="Annual Report / التقرير السنوي")
        self.subtitle_var = tk.StringVar(value="")
        self.org_var = tk.StringVar(value="")
        self.author_var = tk.StringVar(value="")
        self.date_var = tk.StringVar(value=date.today().isoformat())
        self.confidential_var = tk.StringVar(value="")
        self.lang_override_var = tk.StringVar(value="Auto-detect")

        self._labeled_entry(meta_box, 0, "Title", self.title_var)
        self._labeled_entry(meta_box, 1, "Subtitle", self.subtitle_var)
        self._labeled_entry(meta_box, 2, "Organization", self.org_var)
        self._labeled_entry(meta_box, 3, "Author", self.author_var)
        self._labeled_entry(meta_box, 4, "Date", self.date_var)
        self._labeled_entry(meta_box, 5, "Confidentiality (optional)", self.confidential_var)

        ttk.Label(meta_box, text="Language mode").grid(row=6, column=0, sticky="w", pady=3)
        lang_combo = ttk.Combobox(meta_box, textvariable=self.lang_override_var, state="readonly",
                                   values=["Auto-detect", "English", "Arabic", "Bilingual"])
        lang_combo.grid(row=6, column=1, sticky="ew", pady=3)

        self.include_cover_var = tk.BooleanVar(value=True)
        self.include_toc_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(meta_box, text="Include cover page", variable=self.include_cover_var
                         ).grid(row=7, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(meta_box, text="Include table of contents", variable=self.include_toc_var
                         ).grid(row=8, column=0, columnspan=2, sticky="w")

        # ---- Templates ----
        tmpl_box = ttk.LabelFrame(right, text="Templates to consider", padding=8)
        tmpl_box.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        self.tmpl_box = tmpl_box
        self._render_template_checkboxes(tmpl_box)
        ttk.Button(tmpl_box, text="Reload template library", command=self._reload_templates
                   ).pack(anchor="w", pady=(6, 0))

        # ---- Output options ----
        out_box = ttk.LabelFrame(right, text="Generate", padding=8)
        out_box.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        out_box.columnconfigure(1, weight=1)

        ttk.Label(out_box, text="Number of options").grid(row=0, column=0, sticky="w")
        self.num_options_var = tk.IntVar(value=6)
        ttk.Spinbox(out_box, from_=1, to=12, textvariable=self.num_options_var, width=6
                    ).grid(row=0, column=1, sticky="w")

        self.make_pdf_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(out_box, text="Also export PDF previews (needs LibreOffice, offline)",
                         variable=self.make_pdf_var).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

        ttk.Label(out_box, text="Output folder").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.output_dir_var = tk.StringVar(value=DEFAULT_OUTPUT_DIR)
        out_row = ttk.Frame(out_box)
        out_row.grid(row=3, column=0, columnspan=2, sticky="ew")
        out_row.columnconfigure(0, weight=1)
        ttk.Entry(out_row, textvariable=self.output_dir_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(out_row, text="Browse...", command=self._browse_output).grid(row=0, column=1, padx=(4, 0))

        self.generate_btn = ttk.Button(out_box, text="Generate Layout Options", command=self._on_generate)
        self.generate_btn.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(10, 0))

        self.progress = ttk.Progressbar(out_box, mode="indeterminate")
        self.progress.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(6, 0))

        # ---- Results ----
        results_box = ttk.LabelFrame(right, text="Generated options", padding=8)
        results_box.grid(row=3, column=0, sticky="nsew", pady=(8, 0))
        right.rowconfigure(3, weight=1)

        columns = ("label", "category", "status")
        self.results_tree = ttk.Treeview(results_box, columns=columns, show="headings", height=8)
        self.results_tree.heading("label", text="Option")
        self.results_tree.heading("category", text="Category")
        self.results_tree.heading("status", text="File")
        self.results_tree.column("label", width=170)
        self.results_tree.column("category", width=90)
        self.results_tree.column("status", width=160)
        self.results_tree.pack(fill="both", expand=True)
        self.results_tree.bind("<Double-1>", lambda e: self._open_selected())

        btn_row = ttk.Frame(results_box)
        btn_row.pack(fill="x", pady=(6, 0))
        ttk.Button(btn_row, text="Open File", command=self._open_selected).pack(side="left")
        ttk.Button(btn_row, text="Open Output Folder", command=self._open_output_folder).pack(side="left", padx=4)
        ttk.Button(btn_row, text="Open Comparison Sheet", command=self._open_contact_sheet).pack(side="left")

    def _labeled_entry(self, parent, row, label, var):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        entry = ttk.Entry(parent, textvariable=var)
        entry.grid(row=row, column=1, sticky="ew", pady=3)
        self._attach_edit_menu(entry)

    # ------------------------------------------------------------------
    # Clipboard / editing helpers (right-click menu + Arabic-layout keys)
    # ------------------------------------------------------------------

    def _attach_edit_menu(self, widget):
        """Right-click Cut/Copy/Paste menu on a Text or Entry widget."""
        menu = tk.Menu(widget, tearoff=0)
        menu.add_command(label="Paste / لصق",
                         command=lambda: widget.event_generate("<<Paste>>"))
        menu.add_command(label="Copy / نسخ",
                         command=lambda: widget.event_generate("<<Copy>>"))
        menu.add_command(label="Cut / قص",
                         command=lambda: widget.event_generate("<<Cut>>"))
        menu.add_separator()
        menu.add_command(label="Select All / تحديد الكل",
                         command=lambda: self._select_all(widget))

        def popup(event):
            widget.focus_set()
            menu.tk_popup(event.x_root, event.y_root)
        widget.bind("<Button-3>", popup)
        # Windows-standard select-all (Tk's Text default binds Ctrl+A to
        # "go to line start" instead)
        widget.bind("<Control-a>", lambda e: (self._select_all(widget), "break")[1])

    @staticmethod
    def _select_all(widget):
        if isinstance(widget, tk.Text):
            widget.tag_add("sel", "1.0", "end-1c")
        else:
            try:
                widget.select_range(0, "end")
            except Exception:
                pass

    _CTRL_KEYCODE_ACTIONS = {86: "<<Paste>>", 67: "<<Copy>>", 88: "<<Cut>>", 90: "<<Undo>>"}

    def _on_ctrl_key(self, event):
        """Make Ctrl+V/C/X/A/Z work under non-Latin keyboard layouts.
        With e.g. an Arabic layout active, Tk never fires its class bindings
        because the keysym isn't the Latin letter — but Windows keycodes are
        layout-independent, so dispatch on those instead."""
        if event.keysym.lower() in ("v", "c", "x", "a", "z"):
            return None  # Latin layout active: Tk's own bindings handle it
        widget = event.widget
        if event.keycode == 65:  # A
            self._select_all(widget)
            return "break"
        action = self._CTRL_KEYCODE_ACTIONS.get(event.keycode)
        if action:
            try:
                widget.event_generate(action)
            except Exception:
                pass
            return "break"
        return None

    def _paste_clipboard(self):
        try:
            text = self.clipboard_get()
        except tk.TclError:
            messagebox.showinfo(
                "Clipboard empty / الحافظة فارغة",
                "Copy some text first (from Word, a browser, anywhere), then click Paste.\n"
                "انسخ نصاً أولاً ثم اضغط لصق.")
            return
        self.content_text.insert("insert", text)
        self.content_text.see("insert")
        self._log_ui(f"Pasted {len(text)} characters from clipboard.")

    def _clear_content(self):
        self.content_text.delete("1.0", "end")

    def _render_template_checkboxes(self, parent):
        for widget in parent.winfo_children():
            if isinstance(widget, ttk.Checkbutton):
                widget.destroy()
        self.template_vars.clear()
        for t in self.template_manager.list_templates():
            var = tk.BooleanVar(value=True)
            self.template_vars[t.id] = var
            ttk.Checkbutton(parent, text=f"{t.name}  ({t.category})", variable=var).pack(anchor="w")

    def _reload_templates(self):
        self.template_manager.reload()
        self._render_template_checkboxes(self.tmpl_box)
        self._log_ui(f"Reloaded template library — {len(self.template_manager.list_templates())} templates available.")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _load_file(self):
        path = filedialog.askopenfilename(
            title="Load content file",
            filetypes=[("Word document", "*.docx"),
                       ("Text/Markdown/JSON", "*.md *.txt *.json"),
                       ("All files", "*.*")],
        )
        if not path:
            return
        try:
            if path.lower().endswith(".docx"):
                from app.docx_ingest import docx_to_markdown, source_title
                content = docx_to_markdown(path)
                title = source_title(path)
                if title and (not self.title_var.get()
                              or self.title_var.get() == "Untitled Report"):
                    self.title_var.set(title)
                self._log_ui(f"Imported Word document with structure intact "
                             f"(headings, tables, lists, images): {path}")
                self._log_ui("Review the extracted content on the left, then Generate.")
            else:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                self._log_ui(f"Loaded {path}")
            self.content_text.delete("1.0", "end")
            self.content_text.insert("1.0", content)
        except Exception as e:
            messagebox.showerror("Could not load file", str(e))

    def _load_sample(self):
        if not os.path.isfile(SAMPLE_FILE):
            messagebox.showwarning("Sample not found", f"Expected sample at:\n{SAMPLE_FILE}")
            return
        with open(SAMPLE_FILE, "r", encoding="utf-8") as f:
            content = f.read()
        self.content_text.delete("1.0", "end")
        self.content_text.insert("1.0", content)

    def _browse_output(self):
        path = filedialog.askdirectory(title="Choose output folder")
        if path:
            self.output_dir_var.set(path)

    def _log_ui(self, msg: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _log(self, msg: str):
        self.after(0, lambda: self._log_ui(msg))

    def _on_generate(self):
        content = self.content_text.get("1.0", "end").strip()
        if not content:
            messagebox.showwarning("No content", "Paste or load report content first.")
            return
        selected_ids = [tid for tid, var in self.template_vars.items() if var.get()]
        if not selected_ids:
            messagebox.showwarning("No templates selected", "Select at least one template family.")
            return

        self.generate_btn.configure(state="disabled")
        self.progress.start(12)
        self.results_tree.delete(*self.results_tree.get_children())

        thread = threading.Thread(target=self._generate_worker, args=(content, selected_ids), daemon=True)
        thread.start()

    def _generate_worker(self, content: str, selected_ids: list[str]):
        try:
            lang_map = {"Auto-detect": None, "English": LangMode.ENGLISH,
                        "Arabic": LangMode.ARABIC, "Bilingual": LangMode.BILINGUAL}
            forced_lang = lang_map.get(self.lang_override_var.get())

            meta = DocumentMeta(
                title=self.title_var.get() or "Untitled Report",
                subtitle=self.subtitle_var.get() or None,
                author=self.author_var.get() or None,
                organization=self.org_var.get() or None,
                date=self.date_var.get() or None,
                confidentiality=self.confidential_var.get() or None,
                lang_mode=forced_lang,  # None triggers content-based auto-detection
            )

            from app.llm_brain import LocalLLMBrain, describe_status
            llm = LocalLLMBrain()
            self._log(describe_status(llm))
            if llm.is_available():
                self._log("Understanding content with the local model...")
                structured = llm.restructure(content)
                if structured:
                    content = structured
                    self._log("Local AI restructured the content into designed elements.")
                else:
                    self._log(f"Local AI skipped: {llm.last_error}")

            self._log("Parsing content...")
            parser = ContentParser()
            report = parser.parse_auto(content, meta=meta)
            if forced_lang is not None:
                report.meta.lang_mode = forced_lang
            report.include_cover = self.include_cover_var.get()
            report.include_toc = self.include_toc_var.get()
            self._log(f"Language mode: {report.meta.lang_mode.value}")
            if getattr(report, "auto_enrichment", None) is not None:
                from app.auto_enrich import format_enrichment
                self._log(format_enrichment(report.auto_enrichment))
            brain = OfflineDocumentBrain()
            brain_analysis = brain.analyze(content, report)
            brain.apply_to_report(report, brain_analysis)
            for line in format_brain_summary(brain_analysis).splitlines():
                self._log(line)
            analysis = data_insights.analyze(report)
            stats = analysis["stats"]
            chart_visuals = stats["charts"] + stats["timelines"] + stats["processes"]
            if chart_visuals and not chart_engine.is_available():
                self._log(
                    "Chart renderer not available: install requirements.txt to render "
                    "charts, timelines, and process diagrams as PNG images. This run "
                    "will use editable Word-native visual tables instead."
                )

            n_options = self.num_options_var.get()
            options = self.layout_engine.generate_options(
                report,
                max_options=n_options,
                template_ids=selected_ids,
                brain_analysis=brain_analysis,
            )
            self._log(f"Selected {len(options)} layout option(s) to render.")

            out_dir = self.output_dir_var.get() or DEFAULT_OUTPUT_DIR
            os.makedirs(out_dir, exist_ok=True)

            results = self.renderer.render_batch(report, options, output_dir=out_dir)
            docx_paths = {opt.option_id: path for opt, path in results}
            for opt, path in results:
                self._log(f"  ✓ {opt.label}  ->  {os.path.basename(path)}")

            pdf_paths, thumb_paths = {}, {}
            if self.make_pdf_var.get():
                if pdf_exporter.is_available():
                    self._log("Exporting PDF previews via LibreOffice (offline)...")
                    pdf_map = pdf_exporter.convert_batch(list(docx_paths.values()), output_dir=out_dir)
                    for opt, docx_path in results:
                        pdf_paths[opt.option_id] = pdf_map.get(docx_path, "")
                    thumb_paths = preview_gallery.render_thumbnails(
                        pdf_paths, os.path.join(out_dir, "thumbnails")
                    )
                else:
                    self._log("LibreOffice not found — skipping PDF export (.docx files are still ready).")

            manifest = preview_gallery.build_manifest(options, docx_paths, pdf_paths, thumb_paths)
            contact_sheet = preview_gallery.write_html_contact_sheet(
                manifest, os.path.join(out_dir, "layout_options.html"), doc_title=meta.title
            )
            self._log(f"Wrote comparison sheet: {contact_sheet}")
            self._log("Done.")
            self.after(0, lambda: self._populate_results(manifest, contact_sheet))
        except Exception as e:
            self._log(f"ERROR: {e}")
            self.after(0, lambda: messagebox.showerror("Generation failed", str(e)))
        finally:
            self.after(0, self._generation_finished)

    def _generation_finished(self):
        self.progress.stop()
        self.generate_btn.configure(state="normal")

    def _populate_results(self, manifest: list[dict], contact_sheet: str):
        self.last_manifest = manifest
        self.last_contact_sheet = contact_sheet
        for m in manifest:
            path = m.get("docx_path") or ""
            self.results_tree.insert(
                "", "end", iid=m["option_id"],
                values=(m["label"], m["category"], os.path.basename(path)),
            )

    def _open_selected(self):
        sel = self.results_tree.selection()
        if not sel:
            messagebox.showinfo("Nothing selected", "Select a generated option first.")
            return
        option_id = sel[0]
        match = next((m for m in self.last_manifest if m["option_id"] == option_id), None)
        if match and match.get("docx_path"):
            _open_path(match["docx_path"])

    def _open_output_folder(self):
        out_dir = self.output_dir_var.get() or DEFAULT_OUTPUT_DIR
        if os.path.isdir(out_dir):
            _open_path(out_dir)
        else:
            messagebox.showinfo("Not found", f"{out_dir} does not exist yet — generate something first.")

    def _open_contact_sheet(self):
        if self.last_contact_sheet and os.path.isfile(self.last_contact_sheet):
            webbrowser.open(f"file://{self.last_contact_sheet}")
        else:
            messagebox.showinfo("Not available", "Generate options first to create the comparison sheet.")


def main():
    app = WordReportStudioApp()
    app.mainloop()


if __name__ == "__main__":
    main()
