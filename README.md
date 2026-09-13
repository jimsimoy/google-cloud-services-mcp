# Google Cloud Services MCP — Google Docs, Sheets & Drive for AI Clients

<div align="center">

<img src="https://img.shields.io/badge/python-3.9%2B-blue.svg?style=flat-square" alt="Python 3.9+">
<a href="https://github.com/jimsimoy/google-cloud-services-mcp/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg?style=flat-square" alt="License: MIT"></a>
<a href="https://modelcontextprotocol.io"><img src="https://img.shields.io/badge/MCP-compatible-green.svg?style=flat-square" alt="MCP Compatible"></a>
<img src="https://img.shields.io/badge/tools-9-brightgreen.svg?style=flat-square" alt="9 Tools">
<img src="https://img.shields.io/badge/google-docs%20%7C%20sheets%20%7C%20drive-4285F4.svg?style=flat-square" alt="Google Docs, Sheets, Drive">

**9 tools for the Google Docs, Sheets, and Drive APIs — read, write, and manage files with a service account, no OAuth consent screen required — for Claude Desktop, Claude Code, and any MCP client.**

by [Jan Ivan Simoy](https://github.com/jimsimoy)

</div>

---

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

**Supported platform:** any MCP client on macOS, Linux, or Windows with Python 3.9+.

---

## Tools

| Category | Tools | What you can do |
|---|---|---|
| **Docs** | 3 | Read any Doc as plain text; replace or append its body from markdown — real headings, bullet lists, and native bordered tables, not just raw text |
| **Sheets** | 4 | Read a range, overwrite it, append rows, or clear it |
| **Drive** | 2 | List files in a folder; read/export any file (Docs/Sheets as text, `.docx` parsed, anything else downloaded raw) |

<details>
<summary>Full tool reference</summary>

| Tool | Description |
|---|---|
| `doc_read` | Read a Doc's full text. Tries the Docs API first, falls back to a Drive plain-text export for restricted or third-party-converted docs |
| `doc_write` | Replace a Doc's body from markdown — `#`/`##`/`###` headings, `- `/`* ` bullets, and `\| a \| b \|` tables become real Docs formatting, with an auto-bolded header row on tables |
| `doc_append` | Same markdown rendering as `doc_write`, appended to the end instead of replacing |
| `sheet_read` | Read a range (default `A1:ZZ`) as rows of values |
| `sheet_write` | Overwrite a range with values |
| `sheet_append` | Append rows after the last row with data in a range |
| `sheet_clear` | Clear all values in a range |
| `drive_list` | List files in a folder (or Drive root/shared drive if none given) |
| `drive_read` | Read/export any file — native Docs/Sheets as text, `.docx` parsed via `python-docx`, anything else downloaded raw |

</details>

Every tool accepts a full Google URL **or** a bare document/spreadsheet/file ID — URL extraction is
handled internally.

---

## Requirements

| Requirement | Version |
|---|---|
| Python | 3.9 or later |
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
   `doc_write`/`doc_append`.
4. Point `service_account_key` at the downloaded key file in your config (see
   [Configuration](#configuration)).

---

## Installation

```bash
git clone https://github.com/jimsimoy/google-cloud-services-mcp.git
cd google-cloud-services-mcp
pip install -r requirements.txt

cp config.example.json configs/default.json
# edit configs/default.json: set service_account_key to your downloaded key file's path
```

Run directly (mainly for debugging — an MCP client normally launches this for you):

```bash
python3 server.py --config configs/default.json
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

```json
{
  "mcpServers": {
    "google-cloud-services": {
      "command": "python3",
      "args": ["/path/to/google-cloud-services-mcp/server.py", "--config", "configs/default.json"]
    }
  }
}
```

Restart your MCP client after saving. The 9 tools will appear automatically.

---

## Usage Examples

### Read a doc

```
Read the Google Doc at https://docs.google.com/document/d/1AbC.../edit
```

### Publish a markdown report to a doc

```
Write this report to Google Doc 1AbC... : <markdown content>
```

### Log a row to a tracking sheet

```
Append a row to Sheet1!A1 in spreadsheet 1XyZ...: 2026-09-13, done
```

### List and export files from a Drive folder

```
List the files in Drive folder FOLDER_ID, then read the first PDF you find
```

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
```

The server communicates over stdio using JSON-RPC 2.0, the standard MCP transport.

---

## A note on testing

This was built directly against the official Google Docs v1, Sheets v4, and Drive v3 REST APIs via
`google-api-python-client`. The markdown-to-Docs writer (headings, bullet lists, and native bordered
tables with an auto-bolded header row) and the URL-or-bare-ID handling have both been exercised
against real Google accounts in production use, prior to this repo's creation.

The MCP layer itself was verified end-to-end using the official `mcp` Python client SDK against a
real server subprocess: the `initialize` handshake, `tools/list` (all 9 tools returned with correct
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
