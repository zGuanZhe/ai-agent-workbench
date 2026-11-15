# work_office reference

The Office MCP exposes 12 public tools. `work_office_inspect`, `compose`, and `batch` cover most work; use the specialized tools only for their named task.

## Read and inspect

`work_office_inspect(file=..., mode=...)`

| mode | Result |
| --- | --- |
| `text` | Text; for XLSX use `cell_range="Sheet1!A1:G20"` to avoid pulling the whole sheet |
| `outline` | Headings and document structure |
| `stats` | Word/character/font/style statistics |
| `issues` | Layout, content, unevaluated formula, and field issues |
| `annotated` | DOM paths for precise edits |
| `forms` | Form controls |
| `comments` | Comments across DOCX/XLSX/PPTX |
| `screenshot` | PNG preview; set `output`, and `page` or `grid` when useful |
| `html` / `svg` | Standalone visual preview |
| `schema` | OfficeCLI element/property schema |
| `skills` | Built-in OfficeCLI construction guides |

Schema lookup:

```text
work_office_inspect(mode="schema", format="docx", verb="set", element="paragraph")
```

`mode="pdf"` is intentionally invalid. PDF conversion uses `work_office_to_pdf` or COM.

## Create or reformat

`work_office_compose(content_or_path=..., output=..., format="docx", preset="modern")`

- `content_or_path` may be inline Markdown/text, a `.md`/`.txt` file, or an existing `.docx` to reformat.
- Supports GFM tables, Mermaid blocks, and `$$...$$` equations.
- `format="docx"` or `"pptx"`.
- Presets: `modern`, `academic`, `memo`, `gbt9704`.
- Options include `toc`, `watermark`, `title`, `header_unit`, `doc_number`.
- The result includes an output path, size, and OfficeCLI validation.

## Edit safely

`work_office_batch(file=..., ops=..., create=False, backup=True, validate=True)`

`ops` is a JSON array encoded as a string. Example:

```json
[
  {"command":"add","parent":"/body","type":"paragraph","props":{"text":"正文"}},
  {"command":"set","path":"/body/p[1]","props":{"bold":"true"}}
]
```

- `create=true` creates a missing DOCX/XLSX/PPTX and can apply `ops` in the same call.
- Existing files are backed up by default.
- Validation runs by default after a successful write.
- Use `work_office_inspect(mode="annotated")` to obtain paths.
- Use `work_office_inspect(mode="schema")` before guessing a property name.
- For `.docx`, `work_office_com(action="replace")` is a fast find/replace alternative.

## Specialized tools

### Import CSV/TSV

```text
work_office_import(
  file="output.xlsx",
  sheet="Sheet1",
  source="input.csv",
  header=true,
  start_cell="A1"
)
```

The target workbook is created automatically if absent. An existing workbook is backed up first. `data=...` can provide inline CSV instead of `source`. The sheet name must exist when importing into an existing workbook.

### Fill a template

```text
work_office_merge(
  template="template.docx",
  output="out/result.docx",
  data="{\"单位\":\"甲局\",\"日期\":\"2026-11-15\"}"
)
```

`data` accepts one JSON object, a JSON array for batch generation, or a CSV/JSON/XLSX path. The template uses `{{key}}` placeholders.

### Consolidate and reconcile

```text
work_office_consolidate(source_dir="incoming", output_path="merged.xlsx")
```

This merges XLSX/CSV/DOCX sources and returns record counts, anomalies, validation, and consistency information. It writes files and has no dry-run. Report “本次未交” separately from “从未提交” rather than treating archived files as the current batch.

### Compare versions

```text
work_office_diff(file_a="old.xlsx", file_b="new.xlsx", sheet="Sheet1", key_column="编号")
```

DOCX mode compares paragraphs and table cells. XLSX mode compares cells, formulas, values, and numeric deltas. Optional `output` writes a Markdown report.

### Join Word documents

```text
work_office_join_docx(source_dir="docs", output="joined.docx", page_break=true, include_title=false)
```

Use `files="a.docx,b.docx"` for an explicit order. The output itself and `~$` lock files are excluded from a directory scan.

## PDF and live Office/WPS

Use `work_office_to_pdf(file=..., output=...)` for normal conversion through COM. `batch_dir` converts a directory. `engine="auto"` tries Microsoft Office then WPS. Excel page counts may be zero because COM does not report them.

`work_office_com` handles operations that require the installed application:

| action | Use |
| --- | --- |
| `apps` | See installed/running Office components |
| `active` | See files currently open; `saved=false` means unsaved changes |
| `info` | Page/word/table/slide counts |
| `export_pdf` | Exact application-rendered PDF |
| `recalc` | Recalculate and save Excel formulas |
| `refresh_fields` | Refresh Word fields and TOC |
| `replace` | Word find/replace |
| `read_cells` / `set_cells` | Direct cell access |
| `run_python` | Live control through the embedded Officer object |

`run_python` exposes `Officer`, `data`, and `output`. It can alter visible documents and execute arbitrary code. Explain the intended mutation first and save explicitly when persistence is required. Prefer a fixed COM action whenever one exists.

## Advanced Excel/Word engines

Use only when the primary OfficeCLI path cannot perform the operation.

Two-step discovery:

```text
work_office_advanced(engine="excel", action="list", keyword="pivot")
work_office_advanced(engine="excel", action="call", tool="create_pivot_table",
                     args="{\"path\":\"book.xlsx\",\"sheet\":\"Sheet1\"}")
```

- `engine="excel"` exposes 46 offline tools such as pivot tables, charts, conditional formatting, formula tracing, and goal seek.
- `engine="word"` exposes 58 tools such as comments, footnotes, protection, XML inspection, anchored insertion, and table styling.
- `action="schema"` returns the exact child-tool schema.
- `allow_write` defaults to false. When true, the target is backed up before a write.
- Use `keyword` to narrow discovery. Avoid listing every child tool.

## Verification checklist

1. Check `ok` and `validation`/`valid`; do not report a failed write as complete.
2. For XLSX, read back a bounded range and confirm the expected cells.
3. For DOCX/PPTX, inspect text/outline or render a screenshot when layout matters.
4. Include the actual output and backup paths.
5. State unsupported or degraded behavior directly; do not infer success from a created file alone.
