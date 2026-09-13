#!/usr/bin/env python3
"""
Google Docs / Sheets / Drive client library.

Pure functions over the Google Docs v1, Sheets v4, and Drive v3 REST APIs via
a service account — no CLI, no MCP protocol code. `server.py` is the actual
entry point; it imports this module and exposes these functions as MCP tools.

Config: one JSON file per client/service-account, kept under configs/ (gitignored).
See config.example.json for the schema — copy it into configs/<name>.json to
set up a new client/service account.
"""

import io
import json
import os
import sys
import re

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG_PATH = os.path.join(SCRIPT_DIR, "configs", "default.json")


def load_config(path=None):
    path = path or DEFAULT_CONFIG_PATH
    if not os.path.isabs(path):
        path = os.path.join(SCRIPT_DIR, path)
    if not os.path.exists(path):
        sys.exit(
            f"Config not found: {path}\n"
            f"Copy config.example.json to configs/<name>.json and fill in your "
            f"service account key path, e.g.:\n"
            f"  cp config.example.json configs/default.json"
        )
    with open(path) as f:
        return json.load(f)


def get_credentials(config):
    return Credentials.from_service_account_file(
        config["service_account_key"],
        scopes=config["scopes"],
    )


def extract_id_from_url(url_or_id):
    """Accept a full Google URL or a bare ID."""
    patterns = [
        r"/document/d/([a-zA-Z0-9_-]+)",
        r"/spreadsheets/d/([a-zA-Z0-9_-]+)",
        r"/file/d/([a-zA-Z0-9_-]+)",
        r"/folders/([a-zA-Z0-9_-]+)",
    ]
    for p in patterns:
        m = re.search(p, url_or_id)
        if m:
            return m.group(1)
    return url_or_id  # assume it's already a bare ID


# ── Google Docs ───────────────────────────────────────────────────────────────

def doc_read(creds, document_id):
    """Return the full plain text of a Google Doc.

    Tries the Docs API first (richer output); falls back to Drive export
    (text/plain) when the Docs API returns 'not supported' (e.g. converted
    third-party files or restricted documents).
    """
    from googleapiclient.errors import HttpError as _HttpError

    # ── Try Docs API ──────────────────────────────────────────────────────
    try:
        service = build("docs", "v1", credentials=creds)
        doc = service.documents().get(documentId=document_id).execute()

        title = doc.get("title", "")
        content = doc.get("body", {}).get("content", [])

        def paragraph_text(para):
            text = ""
            for run in para.get("elements", []):
                tr = run.get("textRun")
                if tr:
                    text += tr.get("content", "")
            return text.rstrip("\n")

        lines = []
        for element in content:
            if "paragraph" in element:
                lines.append(paragraph_text(element["paragraph"]))
            elif "table" in element:
                for row in element["table"]["tableRows"]:
                    cell_texts = []
                    for cell in row["tableCells"]:
                        cell_parts = [
                            paragraph_text(c["paragraph"])
                            for c in cell.get("content", [])
                            if "paragraph" in c
                        ]
                        cell_texts.append(" ".join(p for p in cell_parts if p))
                    lines.append(" | ".join(cell_texts))

        return {"title": title, "text": "\n".join(lines)}

    except _HttpError as e:
        # Fall back to Drive export for unsupported / restricted docs
        if e.status_code not in (400, 403):
            raise

    # ── Fallback: Drive export as plain text ──────────────────────────────
    drive = build("drive", "v3", credentials=creds)
    meta = drive.files().get(
        fileId=document_id, fields="name", supportsAllDrives=True
    ).execute()
    title = meta.get("name", "")
    content = (
        drive.files()
        .export(fileId=document_id, mimeType="text/plain")
        .execute()
    )
    text = content.decode("utf-8") if isinstance(content, bytes) else content
    return {"title": title, "text": text}


def _parse_markdown_segments(md_text):
    """Split markdown into ordered segments: contiguous non-table lines
    become {"kind": "text", "lines": [...]}; a markdown table (a "|...|"
    row immediately followed by a "|---|---|" separator, then more "|...|"
    rows) becomes {"kind": "table", "rows": [[cell, ...], ...]} (separator
    row dropped, header is rows[0]).
    """
    lines = md_text.split("\n")
    segments = []
    current = []

    def flush():
        if current:
            segments.append({"kind": "text", "lines": list(current)})
            current.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("|") and i + 1 < len(lines) and re.match(
            r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]
        ):
            flush()
            table_lines = [line]
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                # A row immediately followed by its own separator marks the
                # start of a NEW table (two tables with no blank line between
                # them) — stop here so the outer loop picks it up fresh,
                # rather than swallowing it (and its separator) as more data
                # rows of this table.
                if j + 1 < len(lines) and re.match(
                    r"^\s*\|[\s:|-]+\|\s*$", lines[j + 1]
                ):
                    break
                table_lines.append(lines[j])
                j += 1
            rows = [
                [c.strip() for c in row.strip().strip("|").split("|")]
                for row in table_lines
            ]
            segments.append({"kind": "table", "rows": rows})
            i = j
        else:
            current.append(line)
            i += 1
    flush()
    return segments


def _parse_simple_markdown(lines):
    """Parse a small markdown subset (a list of lines, no tables) into blocks.

    Supported: #/##/### headings, "- "/"* " bullets, blank-line paragraph
    breaks, plain paragraphs. Bold (**x**) markers are stripped (kept as
    plain text) since rich inline styling isn't implemented.
    """
    blocks = []
    for raw_line in lines:
        stripped = raw_line.strip()

        if not stripped:
            blocks.append({"type": "paragraph", "text": ""})
        elif stripped.startswith("### "):
            blocks.append({"type": "heading3", "text": stripped[4:]})
        elif stripped.startswith("## "):
            blocks.append({"type": "heading2", "text": stripped[3:]})
        elif stripped.startswith("# "):
            blocks.append({"type": "heading1", "text": stripped[2:]})
        elif stripped.startswith("- ") or stripped.startswith("* "):
            blocks.append({"type": "bullet", "text": stripped[2:]})
        else:
            blocks.append({"type": "paragraph", "text": stripped})

    for b in blocks:
        b["text"] = b["text"].replace("**", "").replace("__", "")

    return blocks


def _insert_text_blocks_at(service, document_id, index, blocks):
    """Insert parsed heading/bullet/paragraph blocks starting at `index`.
    Returns the cursor index immediately after the inserted text.
    """
    full_text = ""
    spans = []
    for b in blocks:
        start = len(full_text)
        full_text += b["text"] + "\n"
        spans.append((start, len(full_text), b))

    if not full_text:
        return index

    requests = [{"insertText": {"location": {"index": index}, "text": full_text}}]

    for start, end, b in spans:
        para_start = index + start
        para_end = index + end

        if b["type"] in ("heading1", "heading2", "heading3"):
            named_style = {
                "heading1": "HEADING_1",
                "heading2": "HEADING_2",
                "heading3": "HEADING_3",
            }[b["type"]]
            requests.append(
                {
                    "updateParagraphStyle": {
                        "range": {"startIndex": para_start, "endIndex": para_end},
                        "paragraphStyle": {"namedStyleType": named_style},
                        "fields": "namedStyleType",
                    }
                }
            )
        elif b["type"] == "bullet":
            requests.append(
                {
                    "createParagraphBullets": {
                        "range": {"startIndex": para_start, "endIndex": para_end},
                        "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
                    }
                }
            )

    service.documents().batchUpdate(
        documentId=document_id, body={"requests": requests}
    ).execute()

    return index + len(full_text)


def _find_table_element(doc, min_start_index):
    for el in doc["body"]["content"]:
        if "table" in el and el["startIndex"] >= min_start_index:
            return el
    raise RuntimeError("Could not locate inserted table in document")


def _insert_table_at(service, document_id, index, rows):
    """Insert a native (bordered, grid) Docs table with a bold header row
    at `index`. Returns the cursor index immediately after the table.
    """
    n_rows = len(rows)
    n_cols = max(len(r) for r in rows)

    service.documents().batchUpdate(
        documentId=document_id,
        body={
            "requests": [
                {
                    "insertTable": {
                        "rows": n_rows,
                        "columns": n_cols,
                        "location": {"index": index},
                    }
                }
            ]
        },
    ).execute()

    # Re-fetch to find the empty cells we just created and their indices.
    doc = service.documents().get(documentId=document_id).execute()
    table = _find_table_element(doc, index)["table"]

    cell_positions = []  # (row_idx, col_idx, cell_start_index)
    for r_idx, row in enumerate(table["tableRows"]):
        for c_idx, cell in enumerate(row["tableCells"]):
            cell_positions.append((r_idx, c_idx, cell["content"][0]["startIndex"]))

    # Fill from the highest index down so each insert leaves earlier
    # (lower-index) cell positions — including the header row — untouched.
    cell_positions.sort(key=lambda t: t[2], reverse=True)

    fill_requests = []
    header_bold_ranges = []
    for r_idx, c_idx, cell_start in cell_positions:
        text = rows[r_idx][c_idx] if c_idx < len(rows[r_idx]) else ""
        if not text:
            continue
        fill_requests.append(
            {"insertText": {"location": {"index": cell_start}, "text": text}}
        )
        if r_idx == 0:
            header_bold_ranges.append((cell_start, cell_start + len(text)))

    if fill_requests:
        service.documents().batchUpdate(
            documentId=document_id, body={"requests": fill_requests}
        ).execute()

    if header_bold_ranges:
        bold_requests = [
            {
                "updateTextStyle": {
                    "range": {"startIndex": s, "endIndex": e},
                    "textStyle": {"bold": True},
                    "fields": "bold",
                }
            }
            for s, e in header_bold_ranges
        ]
        service.documents().batchUpdate(
            documentId=document_id, body={"requests": bold_requests}
        ).execute()

    doc = service.documents().get(documentId=document_id).execute()
    return _find_table_element(doc, index)["endIndex"]


def doc_write(creds, document_id, md_text, mode="replace"):
    """Write markdown-ish text into a Google Doc as real headings/bullets/
    native bordered tables.

    mode: "replace" clears the existing body first; "append" adds to the end.
    """
    service = build("docs", "v1", credentials=creds)
    doc = service.documents().get(documentId=document_id).execute()
    end_index = doc["body"]["content"][-1]["endIndex"]

    if mode == "replace" and end_index > 2:
        service.documents().batchUpdate(
            documentId=document_id,
            body={
                "requests": [
                    {
                        "deleteContentRange": {
                            "range": {"startIndex": 1, "endIndex": end_index - 1}
                        }
                    }
                ]
            },
        ).execute()
        cursor = 1
    else:
        doc = service.documents().get(documentId=document_id).execute()
        cursor = doc["body"]["content"][-1]["endIndex"] - 1

    segments = _parse_markdown_segments(md_text)
    n_blocks = 0
    n_tables = 0

    for seg in segments:
        if seg["kind"] == "table":
            cursor = _insert_table_at(service, document_id, cursor, seg["rows"])
            n_tables += 1
        else:
            blocks = _parse_simple_markdown(seg["lines"])
            cursor = _insert_text_blocks_at(service, document_id, cursor, blocks)
            n_blocks += len(blocks)

    return {"blocks_written": n_blocks, "tables_written": n_tables}


# ── Google Sheets ─────────────────────────────────────────────────────────────

def sheet_read(creds, spreadsheet_id, range_name="A1:ZZ"):
    """Return values from a sheet range as a list of rows."""
    service = build("sheets", "v4", credentials=creds)
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=range_name)
        .execute()
    )
    return result.get("values", [])


def sheet_write(creds, spreadsheet_id, range_name, values):
    """Overwrite a range with values (list of rows)."""
    service = build("sheets", "v4", credentials=creds)
    body = {"values": values}
    result = (
        service.spreadsheets()
        .values()
        .update(
            spreadsheetId=spreadsheet_id,
            range=range_name,
            valueInputOption="USER_ENTERED",
            body=body,
        )
        .execute()
    )
    return result


def sheet_append(creds, spreadsheet_id, range_name, values):
    """Append rows after the last row with data in the range."""
    service = build("sheets", "v4", credentials=creds)
    body = {"values": values}
    result = (
        service.spreadsheets()
        .values()
        .append(
            spreadsheetId=spreadsheet_id,
            range=range_name,
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body=body,
        )
        .execute()
    )
    return result


def sheet_clear(creds, spreadsheet_id, range_name):
    """Clear all values in a range."""
    service = build("sheets", "v4", credentials=creds)
    result = (
        service.spreadsheets()
        .values()
        .clear(spreadsheetId=spreadsheet_id, range=range_name)
        .execute()
    )
    return result


# ── Google Drive ──────────────────────────────────────────────────────────────

def drive_list(creds, folder_id=None):
    """List files in a Drive folder (or root/shared drive if no folder given)."""
    service = build("drive", "v3", credentials=creds)
    q = f"'{folder_id}' in parents and trashed=false" if folder_id else "trashed=false"
    result = (
        service.files()
        .list(
            q=q,
            fields="files(id, name, mimeType, modifiedTime)",
            pageSize=100,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
    )
    return result.get("files", [])


def drive_read(creds, file_id):
    """Read a Drive file — exports native Docs/Sheets as text, downloads .docx as plain text."""
    service = build("drive", "v3", credentials=creds)

    meta = service.files().get(
        fileId=file_id,
        fields="mimeType,name",
        supportsAllDrives=True,
    ).execute()
    mime = meta.get("mimeType", "")

    # Native Google Docs Editors files → export as plain text
    if mime.startswith("application/vnd.google-apps."):
        content = (
            service.files()
            .export(fileId=file_id, mimeType="text/plain")
            .execute()
        )
        return content.decode("utf-8") if isinstance(content, bytes) else content

    # Word .docx files → download binary, parse with python-docx
    if mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        import docx
        buf = io.BytesIO()
        request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        buf.seek(0)
        doc = docx.Document(buf)
        return "\n".join(p.text for p in doc.paragraphs)

    # Fallback: try plain download
    buf = io.BytesIO()
    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    buf.seek(0)
    return buf.read().decode("utf-8", errors="replace")
