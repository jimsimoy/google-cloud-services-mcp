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
import mimetypes
import os
import sys
import re

from google.oauth2.service_account import Credentials
import google_auth_httplib2
import httplib2
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload, MediaIoBaseDownload

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


_ID = r"([a-zA-Z0-9_-]+)"


def extract_id_from_url(url_or_id):
    """Accept a full Google URL or a bare ID and return the bare ID.

    Handles Docs/Sheets/Slides/file/folder URLs (including `/u/0/` account
    prefixes, query strings and `#gid=` fragments) and `...?id=ID` style links.
    Anything that is not a recognisable Google URL is returned unchanged.
    """
    if not isinstance(url_or_id, str):
        return url_or_id
    value = url_or_id.strip()
    patterns = [
        r"/document/(?:u/\d+/)?d/" + _ID,
        r"/spreadsheets/(?:u/\d+/)?d/" + _ID,
        r"/presentation/(?:u/\d+/)?d/" + _ID,
        r"/file/(?:u/\d+/)?d/" + _ID,
        r"/folders/" + _ID,
    ]
    for p in patterns:
        m = re.search(p, value)
        if m:
            return m.group(1)
    if "google.com" in value:
        m = re.search(r"[?&]id=" + _ID, value)
        if m:
            return m.group(1)
    return value


def extract_gid(url):
    """Return the numeric tab id (`gid=`) from a Sheets URL, or None."""
    if not isinstance(url, str):
        return None
    m = re.search(r"[#?&]gid=(\d+)", url)
    return int(m.group(1)) if m else None


# ── Google Docs ───────────────────────────────────────────────────────────────

def doc_read(creds, document_id):
    """Return the full plain text of a Google Doc.

    Tries the Docs API first (richer output); falls back to Drive export
    (text/plain) when the Docs API returns 'not supported' (e.g. converted
    third-party files or restricted documents).
    """
    from googleapiclient.errors import HttpError as _HttpError

    document_id = extract_id_from_url(document_id)

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
    document_id = extract_id_from_url(document_id)
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


# ── Google Sheets: values ─────────────────────────────────────────────────────

INPUT_OPTIONS = ("RAW", "USER_ENTERED")
VALUE_RENDER_OPTIONS = {
    "formatted": "FORMATTED_VALUE",
    "unformatted": "UNFORMATTED_VALUE",
    "formula": "FORMULA",
}


def _sheets(creds):
    return build("sheets", "v4", credentials=creds)


def _check_input_option(value_input_option):
    option = str(value_input_option).upper()
    if option not in INPUT_OPTIONS:
        raise ValueError(f"value_input_option must be one of {INPUT_OPTIONS}, got {value_input_option!r}")
    return option


def _quote_sheet(title):
    """Quote a tab title for use in an A1 range, e.g. My Tab -> 'My Tab'."""
    return "'" + title.replace("'", "''") + "'"


def _col_to_index(letters):
    n = 0
    for ch in letters.upper():
        if not "A" <= ch <= "Z":
            raise ValueError(f"Not a column letter: {letters!r}")
        n = n * 26 + (ord(ch) - 64)
    if n == 0:
        raise ValueError("Empty column reference")
    return n - 1


def _index_to_col(index):
    n = index + 1
    out = ""
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


def sheet_read(creds, spreadsheet_id, range_name="A1:ZZ", value_render="formatted"):
    """Return values from a sheet range as a list of rows.

    value_render: "formatted" (as displayed, the default), "unformatted" (raw
    numbers, no number formatting) or "formula" (cell formulas instead of results).
    """
    render = VALUE_RENDER_OPTIONS.get(str(value_render).lower())
    if render is None:
        raise ValueError(f"value_render must be one of {sorted(VALUE_RENDER_OPTIONS)}, got {value_render!r}")
    spreadsheet_id = extract_id_from_url(spreadsheet_id)
    result = (
        _sheets(creds)
        .spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=range_name, valueRenderOption=render)
        .execute()
    )
    return result.get("values", [])


def sheet_write(creds, spreadsheet_id, range_name, values, value_input_option="USER_ENTERED"):
    """Overwrite a range with values (list of rows).

    value_input_option: "USER_ENTERED" (default; parsed like typing into the UI, so
    "=SUM(A1:A3)" becomes a formula and "2026-01-01" a date) or "RAW" (stored exactly
    as given, never parsed).
    """
    option = _check_input_option(value_input_option)
    spreadsheet_id = extract_id_from_url(spreadsheet_id)
    return (
        _sheets(creds)
        .spreadsheets()
        .values()
        .update(
            spreadsheetId=spreadsheet_id,
            range=range_name,
            valueInputOption=option,
            body={"values": values},
        )
        .execute()
    )


def sheet_append(creds, spreadsheet_id, range_name, values, value_input_option="USER_ENTERED"):
    """Append rows after the last row with data in the range.

    value_input_option: see sheet_write.
    """
    option = _check_input_option(value_input_option)
    spreadsheet_id = extract_id_from_url(spreadsheet_id)
    return (
        _sheets(creds)
        .spreadsheets()
        .values()
        .append(
            spreadsheetId=spreadsheet_id,
            range=range_name,
            valueInputOption=option,
            insertDataOption="INSERT_ROWS",
            body={"values": values},
        )
        .execute()
    )


def sheet_clear(creds, spreadsheet_id, range_name):
    """Clear all values in a range."""
    spreadsheet_id = extract_id_from_url(spreadsheet_id)
    return (
        _sheets(creds)
        .spreadsheets()
        .values()
        .clear(spreadsheetId=spreadsheet_id, range=range_name)
        .execute()
    )


def _column_range(sheet, column):
    col = column.strip().upper()
    if not re.fullmatch(r"[A-Z]{1,3}", col):
        raise ValueError(f"column must be a column letter such as 'H', got {column!r}")
    prefix = f"{_quote_sheet(sheet)}!" if sheet else ""
    return col, f"{prefix}{col}:{col}"


def sheet_find(creds, spreadsheet_id, column, value, sheet=None, match="exact", case_sensitive=True):
    """Search one column for a value and return the matching 1-based row numbers.

    column: a column letter such as "H". sheet: tab title (default: the first tab).
    match: "exact" (whole cell equals value) or "contains". Cells are compared as
    displayed text. Returns {"column", "sheet", "count", "matches": [{"row", "value"}]}.
    """
    if match not in ("exact", "contains"):
        raise ValueError("match must be 'exact' or 'contains'")
    spreadsheet_id = extract_id_from_url(spreadsheet_id)
    col, rng = _column_range(sheet, column)
    rows = sheet_read(creds, spreadsheet_id, rng)
    needle = str(value)
    if not case_sensitive:
        needle = needle.lower()
    matches = []
    for i, row in enumerate(rows, start=1):
        cell = str(row[0]) if row else ""
        hay = cell if case_sensitive else cell.lower()
        if (hay == needle) if match == "exact" else (needle in hay):
            matches.append({"row": i, "value": cell})
    return {"column": col, "sheet": sheet, "count": len(matches), "matches": matches}


def sheet_update_row_by_key(creds, spreadsheet_id, key_column, key_value, updates, sheet=None,
                            value_input_option="USER_ENTERED", on_multiple="error",
                            append_if_missing=False, match="exact"):
    """Update cells in the row whose `key_column` equals `key_value`.

    updates: {column letter: new value}, e.g. {"M": "done", "N": 3}. Only those cells
    are written; the rest of the row is untouched. match: "exact" (default) or "contains"
    (for keys stored inside a longer cell, such as an ID within a URL).
    on_multiple: what to do when several rows match - "error" (default), "first" or "all".
    append_if_missing: when no row matches, append a new row holding the key and the
    updates instead of raising (default False).
    Returns {"action": "updated"|"appended", "rows": [...], "updated_cells": n}.
    """
    if not updates:
        raise ValueError("updates must name at least one column")
    if on_multiple not in ("error", "first", "all"):
        raise ValueError("on_multiple must be 'error', 'first' or 'all'")
    option = _check_input_option(value_input_option)
    spreadsheet_id = extract_id_from_url(spreadsheet_id)
    key_col, _ = _column_range(sheet, key_column)
    cells = {}
    for col, v in updates.items():
        c, _ = _column_range(sheet, col)
        cells[c] = v

    rows = [m["row"] for m in sheet_find(creds, spreadsheet_id, key_col, key_value, sheet=sheet, match=match)["matches"]]

    if not rows:
        if not append_if_missing:
            raise ValueError(f"No row has {key_value!r} in column {key_col}")
        cells.setdefault(key_col, key_value)
        width = max(_col_to_index(c) for c in cells) + 1
        new_row = [""] * width
        for c, v in cells.items():
            new_row[_col_to_index(c)] = v
        prefix = f"{_quote_sheet(sheet)}!" if sheet else ""
        res = sheet_append(creds, spreadsheet_id, f"{prefix}A1", [new_row], option)
        return {"action": "appended", "rows": [],
                "updated_cells": res.get("updates", {}).get("updatedCells", len(cells))}

    if len(rows) > 1 and on_multiple == "error":
        raise ValueError(
            f"{len(rows)} rows have {key_value!r} in column {key_col} (rows {rows}); "
            f"pass on_multiple='first' or 'all' to choose"
        )
    targets = rows[:1] if on_multiple == "first" else rows

    prefix = f"{_quote_sheet(sheet)}!" if sheet else ""
    data = [
        {"range": f"{prefix}{c}{r}", "values": [[v]]}
        for r in targets
        for c, v in cells.items()
    ]
    res = (
        _sheets(creds)
        .spreadsheets()
        .values()
        .batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"valueInputOption": option, "data": data},
        )
        .execute()
    )
    return {"action": "updated", "rows": targets,
            "updated_cells": res.get("totalUpdatedCells", len(data))}


# ── Google Sheets: structure and formatting ───────────────────────────────────

WRAP_STRATEGIES = ("WRAP", "CLIP", "OVERFLOW_CELL")
VERTICAL_ALIGNS = ("TOP", "MIDDLE", "BOTTOM")
HORIZONTAL_ALIGNS = ("LEFT", "CENTER", "RIGHT")
NUMBER_FORMAT_TYPES = ("NUMBER", "PERCENT", "CURRENCY", "DATE", "TIME", "DATE_TIME", "SCIENTIFIC", "TEXT")


def _choice(value, allowed, name):
    v = str(value).upper()
    if v not in allowed:
        raise ValueError(f"{name} must be one of {allowed}, got {value!r}")
    return v


def _hex_to_rgb(color):
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", str(color).strip())
    if not m:
        raise ValueError(f"Colour must be a 6-digit hex such as '#FFF2CC', got {color!r}")
    h = m.group(1)
    return {k: int(h[i:i + 2], 16) / 255 for k, i in (("red", 0), ("green", 2), ("blue", 4))}


def _spreadsheet_meta(service, spreadsheet_id):
    return service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="spreadsheetUrl,properties.title,sheets.properties",
    ).execute()


def _resolve_tab(meta, sheet=None, gid=None):
    """Pick a tab's properties by title or numeric id; default gid, then the first tab."""
    tabs = [s["properties"] for s in meta.get("sheets", [])]
    if sheet is not None:
        for t in tabs:
            if t["title"] == sheet or (isinstance(sheet, int) and t["sheetId"] == sheet):
                return t
        raise ValueError(f"No tab {sheet!r}; tabs are {[t['title'] for t in tabs]}")
    if gid is not None:
        for t in tabs:
            if t["sheetId"] == gid:
                return t
    return tabs[0]


def _tab_context(creds, spreadsheet_ref, sheet):
    """Return (service, spreadsheet_id, tab properties) for a URL/ID and optional tab."""
    spreadsheet_id = extract_id_from_url(spreadsheet_ref)
    service = _sheets(creds)
    meta = _spreadsheet_meta(service, spreadsheet_id)
    return service, spreadsheet_id, _resolve_tab(meta, sheet, extract_gid(spreadsheet_ref))


def _batch_update(service, spreadsheet_id, requests):
    return service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id, body={"requests": requests}
    ).execute()


def _split_sheet_prefix(a1):
    """Split "Tab!A1:B2" / "'My Tab'!A1" into (tab title or None, range part)."""
    if "!" not in a1:
        return None, a1
    m = re.match(r"^'((?:[^']|'')*)'!(.*)$", a1)
    if m:
        return m.group(1).replace("''", "'"), m.group(2)
    title, rest = a1.rsplit("!", 1)
    return title, rest


def _parse_cell_ref(ref):
    m = re.fullmatch(r"([A-Za-z]{0,3})(\d*)", ref.strip())
    if not m or not (m.group(1) or m.group(2)):
        raise ValueError(f"Not a valid A1 reference: {ref!r}")
    col = _col_to_index(m.group(1)) if m.group(1) else None
    row = int(m.group(2)) - 1 if m.group(2) else None
    if row is not None and row < 0:
        raise ValueError(f"Row numbers start at 1: {ref!r}")
    return col, row


def _a1_to_grid_range(a1, sheet_id):
    """Convert "A1:D10", "A:C", "2:3", "A2:C" or "B4" into a GridRange (no sheet prefix)."""
    parts = a1.split(":")
    if len(parts) > 2:
        raise ValueError(f"Not a valid A1 range: {a1!r}")
    c1, r1 = _parse_cell_ref(parts[0])
    if len(parts) == 1:
        c2, r2 = c1, r1
    else:
        c2, r2 = _parse_cell_ref(parts[1])
    grid = {"sheetId": sheet_id}
    if r1 is not None:
        grid["startRowIndex"] = r1
    if r2 is not None:
        grid["endRowIndex"] = r2 + 1
    if c1 is not None:
        grid["startColumnIndex"] = c1
    if c2 is not None:
        grid["endColumnIndex"] = c2 + 1
    return grid


def _repeat_cell_request(grid, fmt, fields):
    return {
        "repeatCell": {
            "range": grid,
            "cell": {"userEnteredFormat": fmt},
            "fields": ",".join(f"userEnteredFormat.{f}" for f in fields),
        }
    }


def _build_format(bold=None, background_color=None, text_color=None, wrap=None,
                  vertical_align=None, horizontal_align=None,
                  number_format_type=None, number_format_pattern=None):
    """Turn friendly arguments into (userEnteredFormat dict, field paths)."""
    fmt, fields = {}, []
    text_format = {}
    if bold is not None:
        text_format["bold"] = bool(bold)
        fields.append("textFormat.bold")
    if text_color is not None:
        text_format["foregroundColor"] = _hex_to_rgb(text_color)
        fields.append("textFormat.foregroundColor")
    if text_format:
        fmt["textFormat"] = text_format
    if background_color is not None:
        fmt["backgroundColor"] = _hex_to_rgb(background_color)
        fields.append("backgroundColor")
    if wrap is not None:
        fmt["wrapStrategy"] = _choice(wrap, WRAP_STRATEGIES, "wrap")
        fields.append("wrapStrategy")
    if vertical_align is not None:
        fmt["verticalAlignment"] = _choice(vertical_align, VERTICAL_ALIGNS, "vertical_align")
        fields.append("verticalAlignment")
    if horizontal_align is not None:
        fmt["horizontalAlignment"] = _choice(horizontal_align, HORIZONTAL_ALIGNS, "horizontal_align")
        fields.append("horizontalAlignment")
    if number_format_pattern is not None and number_format_type is None:
        raise ValueError("number_format_pattern needs number_format_type (e.g. NUMBER, DATE)")
    if number_format_type is not None:
        nf = {"type": _choice(number_format_type, NUMBER_FORMAT_TYPES, "number_format_type")}
        if number_format_pattern is not None:
            nf["pattern"] = number_format_pattern
        fmt["numberFormat"] = nf
        fields.append("numberFormat")
    if not fields:
        raise ValueError("Nothing to format: give at least one formatting argument")
    return fmt, fields


def sheet_info(creds, spreadsheet_id):
    """Return the spreadsheet title and its tabs (sheetId, title, size, frozen rows/columns)."""
    spreadsheet_id = extract_id_from_url(spreadsheet_id)
    meta = _spreadsheet_meta(_sheets(creds), spreadsheet_id)
    tabs = []
    for s in meta.get("sheets", []):
        p = s["properties"]
        grid = p.get("gridProperties", {})
        tabs.append({
            "sheetId": p["sheetId"],
            "title": p["title"],
            "index": p.get("index"),
            "rowCount": grid.get("rowCount"),
            "columnCount": grid.get("columnCount"),
            "frozenRowCount": grid.get("frozenRowCount", 0),
            "frozenColumnCount": grid.get("frozenColumnCount", 0),
            "hidden": bool(p.get("hidden", False)),
        })
    return {
        "title": meta.get("properties", {}).get("title"),
        "url": meta.get("spreadsheetUrl"),
        "sheets": tabs,
    }


def sheet_format_header(creds, spreadsheet_id, sheet=None, row=1, bold=True,
                        background_color=None, text_color=None, last_column=None):
    """Style a header row: bold (default), optional background / text colour (#RRGGBB).

    sheet: tab title (default: the tab named by a `gid=` in the URL, else the first tab).
    row: 1-based header row. last_column: stop at this column letter (default: the whole row).
    """
    if row < 1:
        raise ValueError("row is 1-based")
    fmt, fields = _build_format(bold=bold, background_color=background_color, text_color=text_color)
    service, sid, tab = _tab_context(creds, spreadsheet_id, sheet)
    end_col = (_col_to_index(last_column) + 1) if last_column else tab["gridProperties"]["columnCount"]
    grid = {"sheetId": tab["sheetId"], "startRowIndex": row - 1, "endRowIndex": row,
            "startColumnIndex": 0, "endColumnIndex": end_col}
    _batch_update(service, sid, [_repeat_cell_request(grid, fmt, fields)])
    return {"sheet": tab["title"], "row": row, "formatted": [f.split(".")[-1] for f in fields]}


def sheet_format_cells(creds, spreadsheet_id, range_name, sheet=None, bold=None,
                       background_color=None, text_color=None, wrap=None, vertical_align=None,
                       horizontal_align=None, number_format_type=None, number_format_pattern=None):
    """Format a range. Only the options you pass are changed.

    range_name: A1 range such as "A2:D50", "C:C" or "Tab!B2:B" (a `Tab!` prefix overrides `sheet`).
    wrap: WRAP | CLIP | OVERFLOW_CELL. vertical_align: TOP | MIDDLE | BOTTOM.
    horizontal_align: LEFT | CENTER | RIGHT. Colours are #RRGGBB.
    number_format_type: NUMBER | PERCENT | CURRENCY | DATE | TIME | DATE_TIME | SCIENTIFIC | TEXT,
    with an optional number_format_pattern such as "0.00" or "yyyy-mm-dd".
    """
    fmt, fields = _build_format(bold, background_color, text_color, wrap, vertical_align,
                                horizontal_align, number_format_type, number_format_pattern)
    prefix_title, a1 = _split_sheet_prefix(range_name)
    service, sid, tab = _tab_context(creds, spreadsheet_id, prefix_title or sheet)
    grid = _a1_to_grid_range(a1, tab["sheetId"])
    _batch_update(service, sid, [_repeat_cell_request(grid, fmt, fields)])
    return {"sheet": tab["title"], "range": a1, "formatted": [f.split(".")[-1] for f in fields]}


def sheet_freeze(creds, spreadsheet_id, sheet=None, rows=None, columns=None):
    """Freeze the first `rows` rows and/or `columns` columns of a tab (0 unfreezes)."""
    if rows is None and columns is None:
        raise ValueError("Give rows and/or columns")
    grid, fields = {}, []
    if rows is not None:
        if rows < 0:
            raise ValueError("rows must be >= 0")
        grid["frozenRowCount"] = int(rows)
        fields.append("gridProperties.frozenRowCount")
    if columns is not None:
        if columns < 0:
            raise ValueError("columns must be >= 0")
        grid["frozenColumnCount"] = int(columns)
        fields.append("gridProperties.frozenColumnCount")
    service, sid, tab = _tab_context(creds, spreadsheet_id, sheet)
    _batch_update(service, sid, [{
        "updateSheetProperties": {
            "properties": {"sheetId": tab["sheetId"], "gridProperties": grid},
            "fields": ",".join(fields),
        }
    }])
    return {"sheet": tab["title"], **grid}


def _column_span(spec):
    """"B" -> (1, 2); "A:C" -> (0, 3). Half-open 0-based indices."""
    parts = str(spec).split(":")
    if len(parts) > 2:
        raise ValueError(f"Not a column or column range: {spec!r}")
    first = _col_to_index(parts[0])
    last = _col_to_index(parts[-1])
    if last < first:
        raise ValueError(f"Column range is backwards: {spec!r}")
    return first, last + 1


def sheet_set_column_width(creds, spreadsheet_id, widths, sheet=None):
    """Set column widths in pixels. widths: {"A": 120, "C:E": 300} (letter or letter range)."""
    if not widths:
        raise ValueError("widths must name at least one column")
    service, sid, tab = _tab_context(creds, spreadsheet_id, sheet)
    requests = []
    for spec, px in widths.items():
        if int(px) <= 0:
            raise ValueError(f"Width for {spec!r} must be a positive number of pixels")
        start, end = _column_span(spec)
        requests.append({
            "updateDimensionProperties": {
                "range": {"sheetId": tab["sheetId"], "dimension": "COLUMNS",
                          "startIndex": start, "endIndex": end},
                "properties": {"pixelSize": int(px)},
                "fields": "pixelSize",
            }
        })
    _batch_update(service, sid, requests)
    return {"sheet": tab["title"], "widths": {k: int(v) for k, v in widths.items()}}


def sheet_auto_resize_columns(creds, spreadsheet_id, sheet=None, first_column="A", last_column=None):
    """Auto-fit column widths to their content (default: every column of the tab)."""
    service, sid, tab = _tab_context(creds, spreadsheet_id, sheet)
    start = _col_to_index(first_column)
    end = (_col_to_index(last_column) + 1) if last_column else tab["gridProperties"]["columnCount"]
    if end <= start:
        raise ValueError("last_column comes before first_column")
    _batch_update(service, sid, [{
        "autoResizeDimensions": {
            "dimensions": {"sheetId": tab["sheetId"], "dimension": "COLUMNS",
                           "startIndex": start, "endIndex": end}
        }
    }])
    return {"sheet": tab["title"], "columns": f"{_index_to_col(start)}:{_index_to_col(end - 1)}"}


def sheet_add_tab(creds, spreadsheet_id, title, rows=None, columns=None):
    """Add a tab. Optional rows/columns set its grid size (Sheets default: 1000 x 26)."""
    spreadsheet_id = extract_id_from_url(spreadsheet_id)
    props = {"title": title}
    grid = {}
    if rows is not None:
        grid["rowCount"] = int(rows)
    if columns is not None:
        grid["columnCount"] = int(columns)
    if grid:
        props["gridProperties"] = grid
    reply = _batch_update(_sheets(creds), spreadsheet_id, [{"addSheet": {"properties": props}}])
    p = reply["replies"][0]["addSheet"]["properties"]
    return {"sheetId": p["sheetId"], "title": p["title"]}


def sheet_rename_tab(creds, spreadsheet_id, sheet, new_title):
    """Rename the tab titled `sheet` (a tab name or numeric sheetId) to `new_title`."""
    service, sid, tab = _tab_context_strict(creds, spreadsheet_id, sheet)
    _batch_update(service, sid, [{
        "updateSheetProperties": {
            "properties": {"sheetId": tab["sheetId"], "title": new_title},
            "fields": "title",
        }
    }])
    return {"sheetId": tab["sheetId"], "old_title": tab["title"], "title": new_title}


def sheet_delete_tab(creds, spreadsheet_id, sheet):
    """Delete the tab titled `sheet` (a tab name or numeric sheetId). Irreversible via the API.

    `sheet` is required and must match exactly - there is no "first tab" default here.
    """
    service, sid, tab = _tab_context_strict(creds, spreadsheet_id, sheet)
    _batch_update(service, sid, [{"deleteSheet": {"sheetId": tab["sheetId"]}}])
    return {"deleted": tab["title"], "sheetId": tab["sheetId"]}


def _tab_context_strict(creds, spreadsheet_ref, sheet):
    if sheet is None or sheet == "":
        raise ValueError("sheet is required")
    spreadsheet_id = extract_id_from_url(spreadsheet_ref)
    service = _sheets(creds)
    meta = _spreadsheet_meta(service, spreadsheet_id)
    return service, spreadsheet_id, _resolve_tab(meta, sheet)

# ── Google Drive ──────────────────────────────────────────────────────────────

def drive_list(creds, folder_id=None):
    """List files in a Drive folder (or root/shared drive if no folder given)."""
    folder_id = extract_id_from_url(folder_id) if folder_id else None
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
    file_id = extract_id_from_url(file_id)
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


# ── Google Drive: write tools ──────────────────────────────────────────────────

FOLDER_MIME = "application/vnd.google-apps.folder"
_FILE_FIELDS = "id, name, mimeType, webViewLink"


def _escape_query_value(value):
    """Escape a value for use inside single quotes in a Drive `q` expression."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def drive_upload(creds, folder_id, file_name=None, local_path=None, content=None,
                 mime_type=None, replace=False):
    """Upload a local file OR inline text into a Drive folder.

    Exactly one of `local_path` / `content` must be given. `file_name` defaults to
    the basename of `local_path` and is required with `content`. With `replace=True`,
    an existing non-trashed file of the same name in the folder is updated in place
    instead of creating a duplicate. Returns id, name, mimeType and webViewLink.
    """
    if (local_path is None) == (content is None):
        raise ValueError("Give exactly one of local_path or content")
    if local_path is not None:
        if not os.path.isfile(local_path):
            raise ValueError(f"Not a file: {local_path}")
        with open(local_path, "rb") as f:
            data = f.read()
        file_name = file_name or os.path.basename(local_path)
    else:
        if not file_name:
            raise ValueError("file_name is required when uploading inline content")
        data = content.encode("utf-8")

    mime_type = mime_type or mimetypes.guess_type(file_name)[0] or "application/octet-stream"
    return _upload_bytes(creds, folder_id, file_name, data, mime_type, replace)


def drive_mkdir(creds, name, parent_id):
    """Create a folder inside `parent_id`. Returns id, name, mimeType and webViewLink."""
    parent_id = extract_id_from_url(parent_id)
    service = build("drive", "v3", credentials=creds)
    return (
        service.files()
        .create(body={"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]},
                fields=_FILE_FIELDS, supportsAllDrives=True)
        .execute()
    )


def drive_share(creds, file_id, public):
    """Turn 'anyone with the link can view' on (public=True) or off (public=False).

    Turning it off removes every 'anyone' permission on the file/folder and leaves
    named users/groups untouched. Returns the file's id, webViewLink and the list
    of 'anyone' permission ids that were added or removed.
    """
    file_id = extract_id_from_url(file_id)
    service = build("drive", "v3", credentials=creds)
    if public:
        perm = (
            service.permissions()
            .create(fileId=file_id, body={"type": "anyone", "role": "reader"},
                    fields="id", supportsAllDrives=True)
            .execute()
        )
        changed = [perm["id"]]
    else:
        perms = (
            service.permissions()
            .list(fileId=file_id, fields="permissions(id, type)", supportsAllDrives=True)
            .execute()
            .get("permissions", [])
        )
        changed = [p["id"] for p in perms if p.get("type") == "anyone"]
        for pid in changed:
            service.permissions().delete(
                fileId=file_id, permissionId=pid, supportsAllDrives=True
            ).execute()

    meta = service.files().get(
        fileId=file_id, fields="id, webViewLink", supportsAllDrives=True
    ).execute()
    return {"id": meta["id"], "webViewLink": meta.get("webViewLink"),
            "public": public, "permission_ids": changed}


# ── Create native Docs / Sheets files ──────────────────────────────────────────

DOC_MIME = "application/vnd.google-apps.document"
SHEET_MIME = "application/vnd.google-apps.spreadsheet"
SLIDES_MIME = "application/vnd.google-apps.presentation"


def _create_native(creds, name, folder_id, mime_type, reuse_existing):
    """Create an empty Docs/Sheets file inside a Drive folder (shared drives supported).

    Created through Drive (not the Docs/Sheets create endpoints) so the file lands in
    the given folder - and therefore in a shared drive's storage - rather than in the
    service account's own, quota-less Drive.
    """
    if not name:
        raise ValueError("name is required")
    folder_id = extract_id_from_url(folder_id)
    service = build("drive", "v3", credentials=creds)
    if reuse_existing:
        q = (
            f"'{folder_id}' in parents and name='{_escape_query_value(name)}' "
            f"and mimeType='{mime_type}' and trashed=false"
        )
        existing = (
            service.files()
            .list(q=q, fields="files(" + _FILE_FIELDS + ")", pageSize=1,
                  supportsAllDrives=True, includeItemsFromAllDrives=True)
            .execute()
            .get("files", [])
        )
        if existing:
            return {**existing[0], "created": False}
    created = (
        service.files()
        .create(body={"name": name, "mimeType": mime_type, "parents": [folder_id]},
                fields=_FILE_FIELDS, supportsAllDrives=True)
        .execute()
    )
    return {**created, "created": True}


def doc_create(creds, name, folder_id, markdown=None, reuse_existing=True):
    """Create a Google Doc named `name` in a Drive folder, unless one already exists there.

    reuse_existing=True (default) returns the existing same-named Doc with created=False;
    set it False to always create a new one. `markdown`, if given, is written (same
    rendering as doc_write) only into a Doc that was newly created - an existing Doc is
    never touched. Returns id, name, mimeType, webViewLink and created.
    """
    result = _create_native(creds, name, folder_id, DOC_MIME, reuse_existing)
    if markdown and result["created"]:
        doc_write(creds, result["id"], markdown, mode="replace")
    return result


def sheet_create(creds, name, folder_id, reuse_existing=True):
    """Create a Google Sheet named `name` in a Drive folder, unless one already exists there.

    reuse_existing=True (default) returns the existing same-named Sheet with created=False;
    set it False to always create a new one. Returns id, name, mimeType, webViewLink and created.
    """
    return _create_native(creds, name, folder_id, SHEET_MIME, reuse_existing)


# ── Export / download ──────────────────────────────────────────────────────────

_OOXML = "application/vnd.openxmlformats-officedocument."
EXPORT_MIME = {
    "pdf": "application/pdf",
    "docx": _OOXML + "wordprocessingml.document",
    "xlsx": _OOXML + "spreadsheetml.sheet",
    "pptx": _OOXML + "presentationml.presentation",
    "odt": "application/vnd.oasis.opendocument.text",
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
    "odp": "application/vnd.oasis.opendocument.presentation",
    "rtf": "application/rtf",
    "txt": "text/plain",
    "html": "text/html",
    "md": "text/markdown",
    "csv": "text/csv",
    "tsv": "text/tab-separated-values",
    "epub": "application/epub+zip",
}
EXPORT_FORMATS_BY_TYPE = {
    DOC_MIME: ("pdf", "docx", "odt", "rtf", "txt", "html", "md", "epub"),
    SHEET_MIME: ("pdf", "xlsx", "ods", "csv", "tsv"),
    SLIDES_MIME: ("pdf", "pptx", "odp", "txt"),
}
SHEET_EXPORT_FORMATS = ("pdf", "xlsx", "ods", "csv", "tsv")


def _upload_bytes(creds, folder_id, file_name, data, mime_type, replace=False):
    media = MediaInMemoryUpload(data, mimetype=mime_type, resumable=False)
    folder_id = extract_id_from_url(folder_id)
    service = build("drive", "v3", credentials=creds)

    if replace:
        q = (
            f"'{folder_id}' in parents and name='{_escape_query_value(file_name)}' "
            f"and trashed=false and mimeType!='{FOLDER_MIME}'"
        )
        existing = (
            service.files()
            .list(q=q, fields="files(id)", pageSize=1,
                  supportsAllDrives=True, includeItemsFromAllDrives=True)
            .execute()
            .get("files", [])
        )
        if existing:
            return (
                service.files()
                .update(fileId=existing[0]["id"], media_body=media,
                        fields=_FILE_FIELDS, supportsAllDrives=True)
                .execute()
            )

    return (
        service.files()
        .create(body={"name": file_name, "parents": [folder_id]}, media_body=media,
                fields=_FILE_FIELDS, supportsAllDrives=True)
        .execute()
    )


def _deliver(creds, data, file_name, mime_type, local_path, folder_id, replace, overwrite):
    """Write exported bytes to a local path and/or a Drive folder."""
    out = {"bytes": len(data), "file_name": file_name}
    if local_path:
        path = os.path.abspath(os.path.expanduser(local_path))
        if os.path.isdir(path):
            path = os.path.join(path, file_name)
        if os.path.exists(path) and not overwrite:
            raise ValueError(f"{path} already exists; pass overwrite=true to replace it")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        out["local_path"] = path
    if folder_id:
        out["drive_file"] = _upload_bytes(creds, folder_id, file_name, data, mime_type, replace)
    return out


def _require_destination(local_path, folder_id):
    if not local_path and not folder_id:
        raise ValueError("Give local_path and/or folder_id to say where the export should go")


def drive_export(creds, file_id, format="pdf", local_path=None, folder_id=None, file_name=None,
                 replace=False, overwrite=False):
    """Export a native Google Doc / Sheet / Slides file to another format.

    format: Docs pdf|docx|odt|rtf|txt|html|md|epub, Sheets pdf|xlsx|ods|csv|tsv (csv/tsv =
    first tab), Slides pdf|pptx|odp|txt. Goes through the Drive export API (10 MB limit);
    for per-tab Sheets PDFs with layout options use sheet_export instead.
    Destination: local_path (file or existing directory; refuses to overwrite unless
    overwrite=true) and/or folder_id (uploaded there; replace=true updates a same-named
    file). At least one is required. Returns byte count and where the file went.
    """
    _require_destination(local_path, folder_id)
    fmt = str(format).lower().lstrip(".")
    file_id = extract_id_from_url(file_id)
    service = build("drive", "v3", credentials=creds)
    meta = service.files().get(fileId=file_id, fields="name,mimeType", supportsAllDrives=True).execute()
    allowed = EXPORT_FORMATS_BY_TYPE.get(meta.get("mimeType"))
    if allowed is None:
        raise ValueError(
            f"{meta.get('name')!r} is not a native Google Doc, Sheet or Slides file "
            f"({meta.get('mimeType')}); use drive_read to download other files"
        )
    if fmt not in allowed:
        raise ValueError(f"format {format!r} not available for this file; choose one of {allowed}")
    data = service.files().export(fileId=file_id, mimeType=EXPORT_MIME[fmt]).execute()
    if isinstance(data, str):
        data = data.encode("utf-8")
    name = file_name or f"{meta.get('name')}.{fmt}"
    return {"format": fmt, **_deliver(creds, data, name, EXPORT_MIME[fmt], local_path, folder_id,
                                      replace, overwrite)}


def _authorized_get(creds, url):
    """GET a URL with the service account's bearer token. Returns (status, body bytes)."""
    http = google_auth_httplib2.AuthorizedHttp(creds, http=httplib2.Http(timeout=120))
    resp, content = http.request(url)
    return resp.status, content


def sheet_export(creds, spreadsheet_id, format="pdf", sheet=None, local_path=None, folder_id=None,
                 file_name=None, landscape=False, fit_to_width=True, replace=False, overwrite=False):
    """Export a Google Sheet (whole workbook or one tab) to pdf, xlsx, ods, csv or tsv.

    Uses the spreadsheet export endpoint, so a single tab can be exported (pdf/csv/tsv)
    with page layout options and without the 10 MB Drive export limit. With no `sheet`
    (and no `gid=` in the URL) pdf exports every tab and csv/tsv the first tab; xlsx/ods
    are always the whole workbook. landscape / fit_to_width apply to pdf only.
    Destination: local_path and/or folder_id, as in drive_export. At least one is required.
    """
    _require_destination(local_path, folder_id)
    fmt = str(format).lower().lstrip(".")
    if fmt not in SHEET_EXPORT_FORMATS:
        raise ValueError(f"format must be one of {SHEET_EXPORT_FORMATS}, got {format!r}")
    if sheet is not None and fmt in ("xlsx", "ods"):
        raise ValueError(f"{fmt} always exports the whole workbook; omit sheet")
    sid = extract_id_from_url(spreadsheet_id)
    gid = extract_gid(spreadsheet_id) if fmt not in ("xlsx", "ods") else None

    meta = _spreadsheet_meta(_sheets(creds), sid)
    tab = _resolve_tab(meta, sheet, gid) if (sheet is not None or gid is not None) else None

    query = {"format": fmt}
    if tab is not None:
        query["gid"] = tab["sheetId"]
    if fmt == "pdf":
        query["portrait"] = "false" if landscape else "true"
        query["fitw"] = "true" if fit_to_width else "false"
        if tab is not None:
            query["single"] = "true"
    url = f"https://docs.google.com/spreadsheets/d/{sid}/export?" + "&".join(
        f"{k}={v}" for k, v in query.items()
    )
    status, data = _authorized_get(creds, url)
    if status != 200:
        raise RuntimeError(f"Export failed with HTTP {status}; is the spreadsheet shared with the service account?")

    title = meta.get("properties", {}).get("title") or "spreadsheet"
    name = file_name or (f"{title} - {tab['title']}.{fmt}" if tab is not None else f"{title}.{fmt}")
    return {"format": fmt, "sheet": tab["title"] if tab is not None else None,
            **_deliver(creds, data, name, EXPORT_MIME[fmt], local_path, folder_id, replace, overwrite)}
