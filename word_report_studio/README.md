# Word Report Studio

Offline, bilingual (Arabic + English) long-report design engine for Microsoft Word.
Paste or load content once, and it generates several designer-grade `.docx` layout
options at once — cover page, table of contents, headers/footers with page numbers,
styled tables, callouts, charts, KPI cards, timelines, process diagrams, and correct
Arabic RTL / English LTR shaping in the same document. No internet connection is
used at any point.

Every visual (chart colors, KPI card tints, timeline markers) is derived from the
active template's palette, so each generated layout option looks like one designer
made the whole document.

## How it works

Content (Markdown-ish text or JSON) → **content_parser.py** builds a language-agnostic
document model (**structure_model.py**) → **offline_brain.py** performs local,
auditable document/sensitivity analysis → **layout_engine.py** picks and ranks which
templates fit the content → **docx_renderer.py** renders each template + color variant
into a real `.docx` with python-docx → **pdf_exporter.py** (optional) converts each to
PDF via a local LibreOffice install → **preview_gallery.py** builds thumbnails and an
offline HTML comparison sheet so you can see every option side by side before opening
Word.

The offline brain is rule-based Python, not a cloud LLM. It does not call OpenAI,
Ollama, a browser, or any network API. It detects official/confidential signals,
flags remote links, recommends safer templates, and applies a confidentiality label
when the content looks sensitive and no label was supplied.

Templates are **metadata**, not hand-authored binary files — each one is a small
`metadata.json` describing colors, fonts, page setup, and cover style
(see `templates/*/metadata.json`). This is deliberate: the renderer builds every
document from scratch to spec, which is what makes the "designer grade, consistent,
multiple options" combination possible offline. Adding a new template means adding a
new metadata file, not fighting with a Word template binary.

## Install (once, then fully offline)

1. Python 3.9+ installed.
2. `pip install -r requirements.txt`
3. Optional but recommended: install [LibreOffice](https://www.libreoffice.org/download/)
   (free, offline installer) to enable PDF export and real page thumbnails. Without it,
   the app still generates full `.docx` files — it just skips the PDF step and shows
   color-swatch cards instead of page thumbnails in the comparison sheet.

If `matplotlib` is not installed yet, the app still starts and still generates
`.docx` files. Chart, timeline, and process blocks render as editable Word-native
visual tables until you install the requirements for PNG chart rendering.

Nothing else calls the network. No API keys, no cloud conversion.

## Confidential document posture

- No cloud AI, API keys, telemetry, or remote converters are used.
- Official/confidential content is biased toward formal government templates.
- Remote image URLs are blocked during `.docx` rendering; use local image files.
- Generated `.docx` files get explicit offline/confidential core metadata.
- Arabic-majority lists use manual RTL markers so bullets/numbers sit on the right.

## Run

**GUI (recommended):**
```
python run_gui.py
```
Paste/load your content, fill in title/subtitle/organization/date, pick which template
families to consider, choose how many options to generate, click **Generate Layout
Options**. Results appear in the list — double-click to open a `.docx`, or open the
generated `layout_options.html` comparison sheet.

**Command line:**
```
python run_cli.py --input sample_input/sample_bilingual.md \
    --title "Annual Report / التقرير السنوي" \
    --org "Acme Holdings" --date 2026-07-09 \
    --num-options 6 --pdf
```
Run `python run_cli.py --help` for all options.

## Content format

Plain Markdown-ish text, mixing Arabic and English freely (even within one sentence —
each run is shaped and directioned individually):

```
# Executive Summary
This report covers... / يغطي هذا التقرير ...

## Key Findings
- Revenue grew 12% year over year
- نمو الإيرادات بنسبة 12% على أساس سنوي

| Metric | 2024 | 2025 |
|---|---|---|
| Revenue | 10M | 11.2M |

::: warning ملاحظة هامة
Supply chain risk remains elevated in Q3.
:::

%%pagebreak%%

# Appendix A: Methodology
...
```

- `#` = new top-level section, `##` = subsection, `###`/`####` = headings inside a section
- `- item` / `1. item` = lists, `> text` = quote, `![caption](path.png)` = image
- `| a | b |` table rows (optional `|---|---|` header separator)
- Image paths are resolved relative to the folder you run `run_gui.py` / `run_cli.py`
  from — use absolute paths if your images live elsewhere. A missing image is shown
  as a placeholder note in the document instead of failing the whole generation.
- `::: info|warning|success Title ... :::` = a colored callout box
- `%%pagebreak%%` = explicit page break

### Designer-grade visual blocks

**Charts** (bar, column, line, area, pie, donut) from inline data — colored to
match each template variant automatically:

    ```chart line Revenue Trend (SAR M)
    series: Revenue, Profit
    2021: 48.2, 6.1
    2022: 55.6, 7.9
    ```

**KPI stat cards** (rendered as a real, editable Word table):

    ::: kpi
    +12% | Revenue Growth / نمو الإيرادات | up
    94% | Customer Retention | star
    -18% | Complaints | down-good
    :::

  Icons: `up`, `down` (red), `down-good` / `up-bad` (inverted meaning),
  `star`, `dot`, `flat`, `check`, `warn`, `none`.

**Timeline** and **process/workflow** diagrams:

    ::: timeline Expansion Roadmap
    2023 | Riyadh HQ opened
    2024 | Dubai office / مكتب دبي
    :::

    ::: process Delivery Methodology
    Discover | Design | Build | Test | Launch
    :::

**Auto-visualize a table** — put `%%visualize%%` on the line right after any
table with numeric columns and the system reads the numbers and adds a matching
chart below it (`%%visualize pie%%` forces a chart type). Year-like categories
get a line chart automatically, otherwise bars.

**Smart tables** — cells like `+21.9%` / `-8%` automatically get colored ▲/▼
trend arrows, a last row starting with "Total / الإجمالي / المجموع" is bolded
and shaded, and numeric cells are centered.

The CLI also prints a document analysis (word/table/visual counts) with
recommendations, e.g. flagging numeric tables that could be charted.
- Sections titled "Appendix ..." / "ملحق ..." are automatically routed to the appendix
  area at the end of the document
- JSON input is also supported (see `sample_input/schema_example.json`) if you want to
  feed the system structured content directly (e.g. generated by an LLM)

## Adding your own templates

Drop a new folder under `templates/<your_id>/metadata.json` following the schema used
by the four built-in templates (`corporate_modern`, `government_formal`,
`consulting_analytical`, `academic_research`). Key fields: `colors` (primary/secondary/
accent), `fonts` (separate Latin and Arabic typefaces for headings/body), `cover.style`
(`centered_band` | `minimal_top` | `side_bar` | `full_bleed_footer`), and `variants`
(color palette swaps that get offered as separate options automatically). Click
**Reload template library** in the GUI, or just restart, to pick up new templates.

## Known limitations

- The Table of Contents and page-number fields are live Word fields — open the
  document and press **F9** (or right-click → *Update Field*) once so Word paginates
  and fills them in. This is standard Word behavior for any generated document; it's
  not something an offline script can pre-compute without literally running Word.
- Arabic-majority list items use manual RTL bullets/numbers so official Arabic lists
  read naturally on the right. Mixed English-majority lists continue to use Word's
  built-in list styles.
- PDF export and real page thumbnails require a local LibreOffice install. Everything
  else (all `.docx` generation) works without it.

## Project layout

```
word_report_studio/
  app/
    structure_model.py    # language-agnostic document model
    content_parser.py      # Markdown/JSON -> structure_model
    offline_brain.py       # local rule-based analysis for official/confidential docs
    template_manager.py    # loads templates/*/metadata.json
    layout_engine.py       # ranks templates, produces N options
    docx_renderer.py        # structure_model + template -> .docx (python-docx)
    docx_xml_helpers.py     # RTL/bidi, shading, TOC/page-number fields
    chart_engine.py          # theme-aware charts/timelines/process diagrams (matplotlib)
    data_insights.py          # numeric-table detection, %%visualize%%, doc analysis
    pdf_exporter.py            # .docx -> .pdf via local LibreOffice
    preview_gallery.py          # thumbnails + offline HTML comparison sheet
    desktop_ui.py                # Tkinter GUI
  templates/<id>/metadata.json
  sample_input/
  run_gui.py
  run_cli.py
  requirements.txt
```
