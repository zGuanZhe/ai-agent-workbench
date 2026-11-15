---
name: work-office
description: Use the local work_office MCP for general DOCX, XLSX, and PPTX work, including inspect structure or ranges, create or reformat documents, batch-edit, import CSV, consolidate data, merge Word files, compare versions, convert to PDF, or control live Office/WPS. Also covers advanced offline Excel/Word tools when the standard path is insufficient. Do not use for WeChat/WeCom or memory-only requests.
---

# Work Office

Use the `work_office` MCP directly. Do not route Office work through `work.py ask`, and do not probe again after the MCP is available.

## Choose the shortest path

| Request | Tool |
| --- | --- |
| Read text, table range, outline, stats, issues, forms, comments, schema, screenshot, HTML/SVG | `work_office_inspect` |
| Create Word/PPT from Markdown, or reformat an existing DOCX | `work_office_compose` |
| Modify an existing DOCX/XLSX/PPTX, or create and write in one call | `work_office_batch` |
| Merge multiple spreadsheets/files into a reconciled workbook | `work_office_consolidate` |
| Fill one template repeatedly from JSON records | `work_office_merge` |
| Import CSV/TSV into Excel; create the target if missing | `work_office_import` |
| Compare two DOCX or XLSX versions | `work_office_diff` |
| Join several Word files | `work_office_join_docx` |
| Exact PDF, formula recalculation, Word field refresh, replace, or live app control | `work_office_to_pdf` / `work_office_com` |
| Pivot, chart, formula trace, footnote, comments, protection, XML, anchor editing | `work_office_advanced` |
| Diagnose which cloned Office engines really load | `work_office_engines` |

## Working rules

1. Inspect before rewriting an existing file. For an uncertain element or property, call `work_office_inspect(mode="schema", format=..., verb=..., element=...)`; do not guess property names.
2. For spreadsheet ranges, use `work_office_inspect(mode="text", cell_range="Sheet1!A1:G20")`. Use the real sheet name when known.
3. Prefer one `work_office_batch` call over many single edits. It automatically backs up an existing target and validates after writing.
4. Use `work_office_compose` for new polished documents and for preset reformatting of an existing DOCX. Add `toc=true` when a long document needs a table of contents.
5. Use `work_office_advanced` only when the normal inspect/batch/compose paths cannot do the job. Call `action="list"` or `"schema"` before `action="call"`. It is read-only by default; writes require `allow_write=true`.
6. OfficeCLI cannot export PDF. Use `work_office_to_pdf` or `work_office_com(action="export_pdf")`; do not claim PDF support from inspect or compose.
7. For visually important Word/PPT output, render a screenshot with `work_office_inspect(mode="screenshot")` and inspect it before delivery.
8. Report only observed results: actual output path, validation status, backup path, page count when known, and any warning. Never invent a file or successful conversion.

Detailed parameters, batch JSON, COM actions, and troubleshooting: read [references/office.md](references/office.md) only when the current task needs them.
