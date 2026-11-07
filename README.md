# Google Cloud Services MCP — Google Docs, Sheets & Drive for AI Clients

<div align="center">

<img src="https://img.shields.io/badge/python-3.10%2B-blue.svg?style=flat-square" alt="Python 3.10+">
<a href="https://github.com/jimsimoy/google-cloud-services-mcp/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg?style=flat-square" alt="License: MIT"></a>
<a href="https://modelcontextprotocol.io"><img src="https://img.shields.io/badge/MCP-compatible-green.svg?style=flat-square" alt="MCP Compatible"></a>
<img src="https://img.shields.io/badge/tools-27-brightgreen.svg?style=flat-square" alt="27 Tools">
<img src="https://img.shields.io/badge/google-docs%20%7C%20sheets%20%7C%20drive-4285F4.svg?style=flat-square" alt="Google Docs, Sheets, Drive">

**27 tools for the Google Docs, Sheets, and Drive APIs — read, write, format, search, export, and manage files with a service account, no OAuth consent screen required — for Claude Desktop, Claude Code, and any MCP client.**

by [Jan Ivan Simoy](https://github.com/jimsimoy)

</div>

---

> **Unofficial.** This is an independent, community-built project — not affiliated with, endorsed by, or sponsored by Google.

## What is this?

Google Cloud Services MCP is a [Model Context Protocol](https://modelcontextprotocol.io) server that
gives AI assistants structured read/write access to
[Google Docs](https://developers.google.com/docs/api),
[Sheets](https://developers.google.com/sheets/api), and
[Drive](https://developers.google.com/drive/api) — backed by a Google Cloud **service account**, not
a 3-legged OAuth user flow. There's no browser consent screen and no refresh-token dance: share a
file with the service account's email address and it's accessible immediately.

It supports multiple clients/service accounts side by side — each gets its own config file, and one
server instance serves one config — so a single checkout can back several Google Cloud projects
without editing code.

**Supported platform:** any MCP client on macOS, Linux, or Windows with Python 3.10+.

---

## Tools

| Category | Tools | What you can do |
|---|---|---|
| **Docs** | 4 | Read any Doc as plain text; replace or append its body from markdown — real headings, bullet lists, and native bordered tables; create a Doc in a folder if it does not exist yet |
| **Sheets: values** | 6 | Read (displayed text, raw values, or formulas), overwrite, append, clear; search a column for a value; update a row in place by key instead of appending duplicates |
| **Sheets: structure & formatting** | 11 | Spreadsheet/tab info; header styling; freeze rows/columns; column widths and auto-fit; text wrap, alignment, number/date formats; create a spreadsheet; add, rename, delete tabs; export to PDF/xlsx/csv |
| **Drive** | 6 | List files; read any file as text; upload a file or text; create folders; turn link sharing on or off; export native Docs/Sheets/Slides to PDF, docx, xlsx and more |

<details>
<summary>Full tool reference</summary>

| Tool | Description |
|---|---|
| `doc_read` | Read a Doc's full text. Tries the Docs API first, falls back to a Drive plain-text export for restricted or third-party-converted docs |
| `doc_write` | Replace a Doc's body from markdown — `#`/`##`/`###` headings, `- `/`* ` bullets, and `\| a \| b \|` tables become real Docs formatting, with an auto-bolded header row on tables |
| `doc_append` | Same markdown rendering as `doc_write`, appended to the end instead of replacing |
| `doc_create` | Create a Doc in a Drive folder (shared drives supported). Idempotent by default: an existing same-named Doc is returned with `created=false` and left untouched. Optional initial `markdown`, written only into a newly created Doc |
| `sheet_read` | Read a range (default `A1:ZZ`) as rows. `value_render`: `formatted` (default), `unformatted` (raw numbers), or `formula` |
| `sheet_write` | Overwrite a range. `value_input_option`: `USER_ENTERED` (default, parsed like typing) or `RAW` (stored literally) |
| `sheet_append` | Append rows after the last row with data. Same `value_input_option` |
| `sheet_clear` | Clear all values in a range |
| `sheet_find` | Search one column for a value (`exact` or `contains`, optionally case-insensitive) and return the matching 1-based row numbers |
| `sheet_update_row_by_key` | Find the row whose key column matches and write only the listed cells (`{"M": "done"}`). Errors on a missing or ambiguous key unless you opt in with `append_if_missing` / `on_multiple` (`first`, `all`) |
| `sheet_info` | Spreadsheet title and URL plus every tab: `sheetId`, title, row/column counts, frozen rows/columns, hidden flag |
| `sheet_format_header` | Bold (default) and optional background/text colour on a header row |
| `sheet_format_cells` | Text wrap (`WRAP`/`CLIP`/`OVERFLOW_CELL`), vertical and horizontal alignment, number/date format and pattern, bold, colours — only the options you pass change |
| `sheet_freeze` | Freeze the first N rows and/or columns of a tab (0 unfreezes) |
| `sheet_set_column_width` | Set column widths in pixels, by letter or range: `{"A": 120, "C:E": 300}` |
| `sheet_auto_resize_columns` | Auto-fit column widths to their content |
| `sheet_create` | Create a Sheet in a Drive folder (shared drives supported). Idempotent by default, like `doc_create` |
| `sheet_add_tab` | Add a tab, optionally with a grid size |
| `sheet_rename_tab` | Rename a tab |
| `sheet_delete_tab` | Delete a tab. The exact title is required — there is no "first tab" default for deletion |
| `sheet_export` | Export a whole workbook or one tab to `pdf`, `xlsx`, `ods`, `csv` or `tsv`, saved locally and/or uploaded to a Drive folder. PDF supports landscape and fit-to-width |
| `drive_list` | List files in a folder (or Drive root/shared drive if none given) |
| `drive_read` | Read/export any file — native Docs/Sheets as text, `.docx` parsed via `python-docx`, anything else downloaded raw |
| `drive_export` | Export a native Doc/Sheet/Slides file to `pdf`, `docx`, `xlsx`, `pptx`, `odt`, `ods`, `odp`, `rtf`, `txt`, `html`, `md`, `csv`, `tsv` or `epub` (set depends on file type), saved locally and/or uploaded to a folder. Refuses to overwrite a local file unless `overwrite=true` |
| `drive_upload` | Upload a local file path **or** inline text into a folder (shared drives supported). Creates a new file by default; `replace=true` updates a same-named file in that folder instead. Does not change sharing |
| `drive_mkdir` | Create a folder inside a parent folder |
| `drive_share` | `public=true` turns on "anyone with the link can view"; `public=false` removes every link-sharing permission and leaves named users/groups alone. Always an explicit argument, never a side effect of another tool |

</details>

Every tool accepts a full Google URL **or** a bare document/spreadsheet/file/folder ID — including
URLs with query strings and fragments such as `.../edit?gid=0#gid=0`. The library functions in
`google_cloud_services.py` do the extraction themselves, so calling them directly behaves the same
way. When a Sheets URL carries a `gid=`, the tab-aware tools (`sheet_format_*`, `sheet_freeze`,
`sheet_export`, ...) use that tab unless you name one.

---

## Requirements

| Requirement | Version |
|---|---|
| Python | 3.10 or later |
| Google Cloud project | with the Docs, Sheets, and Drive APIs enabled |
| Service account | with a downloaded JSON key |

---

## Authentication

This server uses a **service account** — no interactive OAuth consent screen, no refresh tokens to
manage. Setup:

1. In [Google Cloud Console](https://console.cloud.google.com), create/select a project and enable
   the **Google Docs API**, **Google Sheets API**, and **Google Drive API** (APIs & Services →
   Library).
2. Create a **service account** (IAM & Admin → Service Accounts) and generate/download its JSON key.
3. Share each target Doc, Sheet, or Drive folder with the service account's email address (the
   `client_email` field in the key file) — **Viewer** access for reading, **Editor** access for
   `doc_write`/`doc_append`, the Sheets write/format tools and the Drive write tools
   (`drive_upload`, `drive_mkdir`, `drive_share`, `*_create`). Service accounts have no storage quota
   of their own, so create and upload files in a **shared drive** (or a folder inside one), not a
   plain My Drive folder.
4. Point `service_account_key` at the downloaded key file in your config (see
   [Configuration](#configuration)).

---

## Installation

```bash
git clone https://github.com/jimsimoy/google-cloud-services-mcp.git
cd google-cloud-services-mcp
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt

cp config.example.json configs/default.json
# edit configs/default.json: set service_account_key to your downloaded key file's path
```

Run directly (mainly for debugging — an MCP client normally launches this for you; it speaks
JSON-RPC on stdin/stdout and logs to stderr):

```bash
./.venv/bin/python server.py --config configs/default.json
```

---

## Configuration

Configs live under `configs/` — one JSON file per client or service account. One server instance
serves one config, chosen with `--config` at launch. Everything in `configs/` is gitignored except
the folder itself; only `config.example.json` at the repo root is tracked.

`config.example.json` documents the schema:

```json
{
  "service_account_key": "/absolute/path/to/service-account-key.json",
  "scopes": [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
  ],
  "defaults": {
    "spreadsheet_range": "A1",
    "doc_export_format": "text"
  }
}
```

To serve a second client/service account, copy it again as `configs/acme.json` and point a second
MCP client entry at `--config configs/acme.json`.

---

## Client Setup

### Claude Code

Use the virtualenv's Python so the server runs with its own dependencies, and absolute paths so it
starts from any working directory:

```bash
claude mcp add google-cloud-services --scope user -- \
  /path/to/google-cloud-services-mcp/.venv/bin/python \
  /path/to/google-cloud-services-mcp/server.py \
  --config /path/to/google-cloud-services-mcp/configs/default.json

claude mcp list    # google-cloud-services should show "Connected"
```

`--scope user` makes it available in every project; drop it for the current project only. One server
instance serves one config, so to use a second service account register a second entry with a
different name and `--config`.

### Claude Desktop and other MCP clients

```json
{
  "mcpServers": {
    "google-cloud-services": {
      "command": "/path/to/google-cloud-services-mcp/.venv/bin/python",
      "args": [
        "/path/to/google-cloud-services-mcp/server.py",
        "--config", "/path/to/google-cloud-services-mcp/configs/default.json"
      ]
    }
  }
}
```

Restart your MCP client after saving. The 27 tools will appear automatically.

---

## Usage Examples

These are prompts you can give an MCP client; the IDs below are placeholders.

### Read a doc

```
Read the Google Doc at https://docs.google.com/document/d/DOCUMENT_ID/edit
```

### Publish a markdown report to a doc

```
Write this report to Google Doc DOCUMENT_ID : <markdown content>
```

### Log a row, or update it in place

```
Append a row to Sheet1!A1 in spreadsheet SPREADSHEET_ID: 2026-09-13, done
```

```
In spreadsheet SPREADSHEET_ID, find the row whose column H contains "VIDEO_ID" and set
column M to "published". Do not add a new row.
```

(`sheet_update_row_by_key` with `key_column: "H"`, `match: "contains"`, `updates: {"M": "published"}`.)

### Set up a new tracking sheet

```
Create a spreadsheet "Example Tracker" in Drive folder FOLDER_ID, write the headers
Name, Status, Amount in row 1, make the header bold with a light-yellow background, freeze
it, format column C as 0.00, and auto-fit the columns
```

### Export a sheet tab as a PDF

```
Export the "Summary" tab of spreadsheet SPREADSHEET_ID as a landscape PDF into Drive folder
FOLDER_ID (or to /path/to/summary.pdf)
```

(`sheet_export` with `format: "pdf"`, `sheet: "Summary"`, `landscape: true`, `folder_id: "FOLDER_ID"`.)

### List and export files from a Drive folder

```
List the files in Drive folder FOLDER_ID, then export the first Doc as PDF to /path/to/doc.pdf
```

### Upload a file and share it by link

```
Upload /path/to/notes.txt into Drive folder FOLDER_ID, then turn on link sharing for it
and give me the link
```

### Make a subfolder

```
Create a folder named "exports" inside Drive folder FOLDER_ID
```

---

## Tips

- **Share the file with the service account first.** A "not found" or 403 error almost always means
  the Doc, Sheet or folder was not shared with the service account's `client_email` (Viewer to read,
  Editor to write). Sharing a folder covers everything inside it.
- **Shared drives.** Every Drive call in this server passes `supportsAllDrives=True`, so shared-drive
  folders and files work the same as My Drive. Create and upload inside a shared drive: service
  accounts have no storage quota of their own, which is why `doc_create`, `sheet_create` and
  `drive_upload` take a `folder_id`.
- **`USER_ENTERED` vs `RAW`.** The default `USER_ENTERED` parses values like typing in the UI
  (`=SUM(...)` becomes a formula, dates and numbers are converted). Use `RAW` to store text exactly
  as given, e.g. IDs or text that merely starts with `=` or `+`.
- **Update in place with a key.** `sheet_update_row_by_key` keeps a tracking sheet free of duplicates.
  If the key lives inside a longer cell (an ID inside a URL), use `match: "contains"`. Keys are
  matched on displayed text.
- **Read the formulas, not just the results.** `sheet_read` with `value_render: "formula"` returns the
  formulas and the raw typed values, which is the right thing to read back before writing a cell
  unchanged.
- **Quotas.** The Sheets API limits requests per minute, per project and per user (on the order of
  60 per user per minute at the time of writing — see the quota page in your Cloud Console).
  Prefer one `sheet_write` of a whole range over many single-cell calls. A tab-formatting tool is one
  API call for the metadata plus one batched write.
- **Export limits.** `drive_export` uses the Drive export API, which caps output at 10 MB;
  `sheet_export` uses the spreadsheet export endpoint and handles larger sheets. xlsx/ods are always
  the whole workbook; pdf/csv/tsv can be one tab.
- **Destructive tools are explicit.** `doc_write` replaces a whole Doc, `sheet_delete_tab` needs an
  exact title, `drive_share` needs an explicit `public`, and exports refuse to overwrite a local file
  unless `overwrite=true`.

---

## Security

- Credentials live only in `configs/*.json`, which are entirely gitignored — only the placeholder
  `config.example.json` is tracked.
- The service account key file itself should be stored outside the repo. Treat it like a password:
  anyone holding it can act as that account against every file it's been shared on.
- `doc_write` deletes and replaces the entire body of the target Doc — there's no undo beyond
  Google Docs' own built-in version history.
- Output this server downloads or exports belongs in `artifacts/` (gitignored) so working files
  never end up in a commit.

---

## Project Structure

```
server.py                  # MCP server entry point and tool definitions
google_cloud_services.py   # Docs/Sheets/Drive client library (pure functions, no protocol code)
config.example.json        # config template — copy into configs/
configs/                   # per-client/service-account configs (gitignored)
artifacts/                 # scratch space for downloaded/exported files (gitignored)
tests/                     # offline pytest suite — no credentials or network needed
```

The server communicates over stdio using JSON-RPC 2.0, the standard MCP transport.

---

## Development

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt pytest
./.venv/bin/python -m pytest -q
```

223 tests, all offline — every Google Docs/Sheets/Drive API call is faked via a mocked `build()`
(`unittest.mock.MagicMock`), so nothing here touches the network or needs a real service account.
Coverage: URL/ID extraction (every URL shape, `gid=` fragments) and a per-function check that the
API only ever receives the bare ID; the markdown-to-Docs conversion including multi-table and
malformed-table edge cases; `doc_read`'s Docs-API-then-Drive-fallback; every Sheets value operation
(render modes, input options, `sheet_find`, `sheet_update_row_by_key`) and every structure/formatting
request body (header, cell formats, A1-to-grid-range conversion, freeze, widths, auto-resize, tabs);
creation and export (`doc_create`, `sheet_create`, `drive_export`, `sheet_export`); the three
`drive_read` branches including a real in-memory `.docx`; and the server layer (every tool has a
handler and a valid schema, URL arguments are forwarded, errors surface as tool errors).

---

## A note on testing

Beyond the automated suite above: this was built directly against the official Google Docs v1,
Sheets v4, and Drive v3 REST APIs via `google-api-python-client`. The markdown-to-Docs writer
(headings, bullet lists, and native bordered tables with an auto-bolded header row) and the
URL-or-bare-ID handling have both been exercised against real Google accounts in production use,
prior to this repo's creation.

The MCP layer itself was verified end-to-end using the official `mcp` Python client SDK against a
real server subprocess: the `initialize` handshake, `tools/list` (all 27 tools returned with correct
schemas), a live authenticated tool call (`drive_list` returning real Drive data), a live API error
surfaced correctly as `isError: true` (`sheet_read` on a nonexistent spreadsheet), SDK-level input
validation rejecting a call missing a required argument, and an unknown tool name returning a clean
error instead of crashing the server.

---

## License

[MIT](./LICENSE) — free to use, modify, and distribute.

---

<div align="center">

[Report a Bug](https://github.com/jimsimoy/google-cloud-services-mcp/issues) · [Request a Feature](https://github.com/jimsimoy/google-cloud-services-mcp/issues)

</div>
