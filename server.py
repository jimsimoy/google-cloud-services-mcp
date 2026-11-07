#!/usr/bin/env python3
"""
Google Cloud Services MCP Server

Exposes Google Docs, Sheets, and Drive operations via the Model Context Protocol
(stdio transport). One server instance = one client/service-account config.
Start with --config pointing at that config file.

Usage:
  python3 server.py --config configs/default.json
  python3 server.py --config /absolute/path/to/config.json

Config files: see config.example.json for the full schema.
"""

import argparse
import json
import sys

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    Tool,
    TextContent,
    CallToolResult,
)

import google_cloud_services as gcs

# ── Bootstrap ──────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(description="Google Cloud Services MCP Server")
    parser.add_argument(
        "--config", required=True,
        help="Path to the client/service-account config JSON file "
             "(absolute, or relative to this script's directory)",
    )
    return parser.parse_args()


# ── Helpers ────────────────────────────────────────────────────────────────────

def ok(data) -> CallToolResult:
    text = data if isinstance(data, str) else json.dumps(data, indent=2, ensure_ascii=False)
    return CallToolResult(content=[TextContent(type="text", text=text)])


def err(message: str) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=f"Error: {message}")],
        isError=True,
    )


# ── Server setup ───────────────────────────────────────────────────────────────

server = Server("google-cloud-services-mcp")
_creds = None


def get_creds():
    if _creds is None:
        raise RuntimeError("Google credentials not initialised")
    return _creds


# ── Tool definitions ───────────────────────────────────────────────────────────
#
# Every tool is one entry in TOOLS: its MCP definition plus the function that runs it.
# The ID/URL arguments are passed through untouched; the library functions call
# extract_id_from_url themselves, so a full URL works for every tool.

def _prop(type_, description, **extra):
    return {"type": type_, "description": description, **extra}


SPREADSHEET = _prop("string", "Google Sheet URL or bare spreadsheet ID")
DOCUMENT = _prop("string", "Google Doc URL or bare document ID")
FOLDER = _prop("string", "Drive folder URL or bare folder ID")
TAB = _prop("string", "Tab title (default: the tab named by gid= in the URL, else the first tab)")
INPUT_OPTION = _prop(
    "string",
    "USER_ENTERED (default): values are parsed like typing in the UI, so '=SUM(A1:A3)' becomes "
    "a formula and '2026-01-01' a date. RAW: stored exactly as given.",
    enum=["RAW", "USER_ENTERED"], default="USER_ENTERED",
)
VALUES = {"type": "array", "items": {"type": "array"}, "description": "Rows of values, e.g. [[\"Col1\",\"Col2\"],[\"v1\",\"v2\"]]"}
COLOR = "#RRGGBB hex colour, e.g. '#FFF2CC'"


def _tool(name, description, required, properties, run):
    definition = Tool(
        name=name,
        description=description,
        inputSchema={"type": "object", "required": required, "properties": properties},
    )
    return name, (definition, run)


TOOLS = dict([
    # ── Docs ──
    _tool(
        "doc_read",
        "Read the full text of a Google Doc. Tries the Docs API first, falls back to a Drive "
        "plain-text export for restricted or third-party-converted docs. Table content is "
        "included, one row per line as 'cell | cell | cell'.",
        ["document_id"], {"document_id": DOCUMENT},
        lambda c, a: gcs.doc_read(c, a["document_id"]),
    ),
    _tool(
        "doc_write",
        "Replace a Google Doc's entire body from markdown text. #/##/### headings, - / * bullets, "
        "and | a | b | tables become real Docs formatting (native bordered tables with a bolded "
        "header row). **bold** markers elsewhere are stripped, not rendered. Deletes the existing "
        "body first - no undo beyond the Doc's own version history.",
        ["document_id", "markdown"],
        {"document_id": DOCUMENT, "markdown": _prop("string", "Markdown content to write")},
        lambda c, a: gcs.doc_write(c, a["document_id"], a["markdown"], mode="replace"),
    ),
    _tool(
        "doc_append",
        "Append markdown text to the end of a Google Doc, using the same heading/bullet/table "
        "rendering as doc_write. Does not touch existing content.",
        ["document_id", "markdown"],
        {"document_id": DOCUMENT, "markdown": _prop("string", "Markdown content to append")},
        lambda c, a: gcs.doc_write(c, a["document_id"], a["markdown"], mode="append"),
    ),
    _tool(
        "doc_create",
        "Create a Google Doc in a Drive folder (shared drives supported). By default it is "
        "idempotent: if a Doc with that name already exists in the folder it is returned with "
        "created=false and left untouched. Optional markdown is written only into a newly "
        "created Doc.",
        ["name", "folder_id"],
        {
            "name": _prop("string", "Doc name"),
            "folder_id": FOLDER,
            "markdown": _prop("string", "Initial content, same rendering as doc_write (new Docs only)"),
            "reuse_existing": _prop("boolean", "Return an existing same-named Doc instead of creating another",
                                    default=True),
        },
        lambda c, a: gcs.doc_create(c, a["name"], a["folder_id"], markdown=a.get("markdown"),
                                    reuse_existing=bool(a.get("reuse_existing", True))),
    ),
    # ── Sheets: values ──
    _tool(
        "sheet_read",
        "Read a range of values from a Google Sheet as rows of cells. value_render chooses "
        "displayed text (default), raw unformatted values, or cell formulas.",
        ["spreadsheet_id"],
        {
            "spreadsheet_id": SPREADSHEET,
            "range": _prop("string", "A1 notation range, e.g. 'Sheet1!A1:D50'", default="A1:ZZ"),
            "value_render": _prop("string", "formatted = as displayed; unformatted = raw numbers; "
                                  "formula = formulas instead of results",
                                  enum=["formatted", "unformatted", "formula"], default="formatted"),
        },
        lambda c, a: gcs.sheet_read(c, a["spreadsheet_id"], a.get("range", "A1:ZZ"),
                                    value_render=a.get("value_render", "formatted")),
    ),
    _tool(
        "sheet_write",
        "Overwrite a range in a Google Sheet with values.",
        ["spreadsheet_id", "range", "values"],
        {
            "spreadsheet_id": SPREADSHEET,
            "range": _prop("string", "A1 notation range, e.g. 'Sheet1!A1'"),
            "values": VALUES,
            "value_input_option": INPUT_OPTION,
        },
        lambda c, a: gcs.sheet_write(c, a["spreadsheet_id"], a["range"], a["values"],
                                     value_input_option=a.get("value_input_option", "USER_ENTERED")),
    ),
    _tool(
        "sheet_append",
        "Append rows after the last row with data in a Google Sheet range.",
        ["spreadsheet_id", "range", "values"],
        {
            "spreadsheet_id": SPREADSHEET,
            "range": _prop("string", "A1 notation range, e.g. 'Sheet1!A1'"),
            "values": VALUES,
            "value_input_option": INPUT_OPTION,
        },
        lambda c, a: gcs.sheet_append(c, a["spreadsheet_id"], a["range"], a["values"],
                                      value_input_option=a.get("value_input_option", "USER_ENTERED")),
    ),
    _tool(
        "sheet_clear",
        "Clear all values in a range of a Google Sheet.",
        ["spreadsheet_id", "range"],
        {"spreadsheet_id": SPREADSHEET, "range": _prop("string", "A1 notation range, e.g. 'Sheet1!A1:Z100'")},
        lambda c, a: gcs.sheet_clear(c, a["spreadsheet_id"], a["range"]),
    ),
    _tool(
        "sheet_find",
        "Search one column of a Google Sheet for a value and return the matching 1-based row "
        "numbers. Use it to locate a row by key (e.g. a video id) before updating it in place.",
        ["spreadsheet_id", "column", "value"],
        {
            "spreadsheet_id": SPREADSHEET,
            "column": _prop("string", "Column letter to search, e.g. 'H'"),
            "value": _prop(["string", "number"], "Value to look for"),
            "sheet": _prop("string", "Tab title (default: first tab)"),
            "match": _prop("string", "exact = whole cell equals value; contains = substring",
                           enum=["exact", "contains"], default="exact"),
            "case_sensitive": _prop("boolean", "Case-sensitive comparison", default=True),
        },
        lambda c, a: gcs.sheet_find(c, a["spreadsheet_id"], a["column"], a["value"], sheet=a.get("sheet"),
                                    match=a.get("match", "exact"),
                                    case_sensitive=bool(a.get("case_sensitive", True))),
    ),
    _tool(
        "sheet_update_row_by_key",
        "Update cells in the row whose key column equals a key value, instead of appending a "
        "duplicate row. Only the listed columns are written. Errors if the key is missing or "
        "matches several rows, unless you opt in with append_if_missing / on_multiple.",
        ["spreadsheet_id", "key_column", "key_value", "updates"],
        {
            "spreadsheet_id": SPREADSHEET,
            "key_column": _prop("string", "Column letter holding the key, e.g. 'H'"),
            "key_value": _prop(["string", "number"], "Key to look for"),
            "updates": _prop("object", "Cells to set: {column letter: value}, e.g. {\"M\": \"done\"}"),
            "sheet": _prop("string", "Tab title (default: first tab)"),
            "value_input_option": INPUT_OPTION,
            "match": _prop("string", "exact = whole cell equals key; contains = key appears inside the cell "
                           "(e.g. an ID inside a URL)", enum=["exact", "contains"], default="exact"),
            "on_multiple": _prop("string", "When several rows match: error (default), first, or all",
                                 enum=["error", "first", "all"], default="error"),
            "append_if_missing": _prop("boolean", "Append a new row (key + updates) when no row matches",
                                       default=False),
        },
        lambda c, a: gcs.sheet_update_row_by_key(
            c, a["spreadsheet_id"], a["key_column"], a["key_value"], a["updates"],
            sheet=a.get("sheet"),
            value_input_option=a.get("value_input_option", "USER_ENTERED"),
            on_multiple=a.get("on_multiple", "error"),
            match=a.get("match", "exact"),
            append_if_missing=bool(a.get("append_if_missing", False)),
        ),
    ),
    # ── Sheets: structure and formatting ──
    _tool(
        "sheet_info",
        "Return a spreadsheet's title and URL and its tabs: sheetId, title, row/column counts, "
        "frozen rows/columns and hidden flag.",
        ["spreadsheet_id"], {"spreadsheet_id": SPREADSHEET},
        lambda c, a: gcs.sheet_info(c, a["spreadsheet_id"]),
    ),
    _tool(
        "sheet_format_header",
        "Style a header row: bold (default) and optional background / text colour. Changes "
        "formatting only, never values.",
        ["spreadsheet_id"],
        {
            "spreadsheet_id": SPREADSHEET,
            "sheet": TAB,
            "row": _prop("integer", "1-based header row", default=1, minimum=1),
            "bold": _prop("boolean", "Make the header bold", default=True),
            "background_color": _prop("string", COLOR),
            "text_color": _prop("string", COLOR),
            "last_column": _prop("string", "Last column letter to style (default: the whole row)"),
        },
        lambda c, a: gcs.sheet_format_header(
            c, a["spreadsheet_id"], sheet=a.get("sheet"), row=int(a.get("row", 1)),
            bold=a.get("bold", True), background_color=a.get("background_color"),
            text_color=a.get("text_color"), last_column=a.get("last_column")),
    ),
    _tool(
        "sheet_format_cells",
        "Format a range: text wrap, vertical/horizontal alignment, number/date format, bold and "
        "colours. Only the options you pass are changed; values are never touched.",
        ["spreadsheet_id", "range"],
        {
            "spreadsheet_id": SPREADSHEET,
            "range": _prop("string", "A1 range, e.g. 'A2:D50', 'C:C' or 'Tab!B2:B'"),
            "sheet": TAB,
            "wrap": _prop("string", "Text wrapping", enum=["WRAP", "CLIP", "OVERFLOW_CELL"]),
            "vertical_align": _prop("string", "Vertical alignment", enum=["TOP", "MIDDLE", "BOTTOM"]),
            "horizontal_align": _prop("string", "Horizontal alignment", enum=["LEFT", "CENTER", "RIGHT"]),
            "number_format_type": _prop("string", "Number format family",
                                        enum=["NUMBER", "PERCENT", "CURRENCY", "DATE", "TIME",
                                              "DATE_TIME", "SCIENTIFIC", "TEXT"]),
            "number_format_pattern": _prop("string", "Pattern for the type, e.g. '0.00' or 'yyyy-mm-dd' "
                                           "(needs number_format_type)"),
            "bold": _prop("boolean", "Set bold on/off"),
            "background_color": _prop("string", COLOR),
            "text_color": _prop("string", COLOR),
        },
        lambda c, a: gcs.sheet_format_cells(
            c, a["spreadsheet_id"], a["range"], sheet=a.get("sheet"), bold=a.get("bold"),
            background_color=a.get("background_color"), text_color=a.get("text_color"),
            wrap=a.get("wrap"), vertical_align=a.get("vertical_align"),
            horizontal_align=a.get("horizontal_align"),
            number_format_type=a.get("number_format_type"),
            number_format_pattern=a.get("number_format_pattern")),
    ),
    _tool(
        "sheet_freeze",
        "Freeze the first N rows and/or columns of a tab (0 unfreezes).",
        ["spreadsheet_id"],
        {
            "spreadsheet_id": SPREADSHEET,
            "sheet": TAB,
            "rows": _prop("integer", "Number of rows to freeze", minimum=0),
            "columns": _prop("integer", "Number of columns to freeze", minimum=0),
        },
        lambda c, a: gcs.sheet_freeze(c, a["spreadsheet_id"], sheet=a.get("sheet"),
                                      rows=a.get("rows"), columns=a.get("columns")),
    ),
    _tool(
        "sheet_set_column_width",
        "Set column widths in pixels.",
        ["spreadsheet_id", "widths"],
        {
            "spreadsheet_id": SPREADSHEET,
            "widths": _prop("object", "{column letter or range: pixels}, e.g. {\"A\": 120, \"C:E\": 300}"),
            "sheet": TAB,
        },
        lambda c, a: gcs.sheet_set_column_width(c, a["spreadsheet_id"], a["widths"], sheet=a.get("sheet")),
    ),
    _tool(
        "sheet_auto_resize_columns",
        "Auto-fit column widths to their content (default: every column of the tab).",
        ["spreadsheet_id"],
        {
            "spreadsheet_id": SPREADSHEET,
            "sheet": TAB,
            "first_column": _prop("string", "First column letter", default="A"),
            "last_column": _prop("string", "Last column letter (default: the tab's last column)"),
        },
        lambda c, a: gcs.sheet_auto_resize_columns(
            c, a["spreadsheet_id"], sheet=a.get("sheet"),
            first_column=a.get("first_column", "A"), last_column=a.get("last_column")),
    ),
    _tool(
        "sheet_create",
        "Create a Google Sheet in a Drive folder (shared drives supported). By default it is "
        "idempotent: if a Sheet with that name already exists in the folder it is returned with "
        "created=false.",
        ["name", "folder_id"],
        {
            "name": _prop("string", "Spreadsheet name"),
            "folder_id": FOLDER,
            "reuse_existing": _prop("boolean", "Return an existing same-named Sheet instead of creating another",
                                    default=True),
        },
        lambda c, a: gcs.sheet_create(c, a["name"], a["folder_id"],
                                      reuse_existing=bool(a.get("reuse_existing", True))),
    ),
    _tool(
        "sheet_add_tab",
        "Add a tab to a spreadsheet. Optional rows/columns set its grid size.",
        ["spreadsheet_id", "title"],
        {
            "spreadsheet_id": SPREADSHEET,
            "title": _prop("string", "New tab title"),
            "rows": _prop("integer", "Row count", minimum=1),
            "columns": _prop("integer", "Column count", minimum=1),
        },
        lambda c, a: gcs.sheet_add_tab(c, a["spreadsheet_id"], a["title"],
                                       rows=a.get("rows"), columns=a.get("columns")),
    ),
    _tool(
        "sheet_rename_tab",
        "Rename a tab.",
        ["spreadsheet_id", "sheet", "new_title"],
        {
            "spreadsheet_id": SPREADSHEET,
            "sheet": _prop("string", "Current tab title"),
            "new_title": _prop("string", "New tab title"),
        },
        lambda c, a: gcs.sheet_rename_tab(c, a["spreadsheet_id"], a["sheet"], a["new_title"]),
    ),
    _tool(
        "sheet_delete_tab",
        "Delete a tab and everything on it. The exact tab title is required (no default). "
        "Cannot be undone through this API; Sheets' version history is the only way back.",
        ["spreadsheet_id", "sheet"],
        {"spreadsheet_id": SPREADSHEET, "sheet": _prop("string", "Exact title of the tab to delete")},
        lambda c, a: gcs.sheet_delete_tab(c, a["spreadsheet_id"], a["sheet"]),
    ),
    _tool(
        "sheet_export",
        "Export a Google Sheet (whole workbook or one tab) to pdf, xlsx, ods, csv or tsv, saved "
        "to a local path and/or uploaded to a Drive folder. PDF supports landscape and fit-to-width. "
        "Refuses to overwrite an existing local file unless overwrite=true.",
        ["spreadsheet_id"],
        {
            "spreadsheet_id": SPREADSHEET,
            "format": _prop("string", "Output format", enum=["pdf", "xlsx", "ods", "csv", "tsv"], default="pdf"),
            "sheet": _prop("string", "Export only this tab (pdf/csv/tsv). Default: all tabs for pdf, "
                           "first tab for csv/tsv"),
            "local_path": _prop("string", "Absolute local file path, or an existing directory"),
            "folder_id": FOLDER,
            "file_name": _prop("string", "Output file name (default: '<title> - <tab>.<ext>')"),
            "landscape": _prop("boolean", "PDF landscape orientation", default=False),
            "fit_to_width": _prop("boolean", "PDF: scale columns to fit the page width", default=True),
            "replace": _prop("boolean", "Drive: update a same-named file in the folder", default=False),
            "overwrite": _prop("boolean", "Local: replace an existing file", default=False),
        },
        lambda c, a: gcs.sheet_export(
            c, a["spreadsheet_id"], format=a.get("format", "pdf"), sheet=a.get("sheet"),
            local_path=a.get("local_path"), folder_id=a.get("folder_id"),
            file_name=a.get("file_name"), landscape=bool(a.get("landscape", False)),
            fit_to_width=bool(a.get("fit_to_width", True)),
            replace=bool(a.get("replace", False)), overwrite=bool(a.get("overwrite", False))),
    ),
    # ── Drive ──
    _tool(
        "drive_list",
        "List files in a Google Drive folder (or Drive root/shared drives if no folder given).",
        [], {"folder_id": FOLDER},
        lambda c, a: gcs.drive_list(c, a.get("folder_id")),
    ),
    _tool(
        "drive_read",
        "Read/export a Drive file. Native Google Docs/Sheets are exported as plain text, .docx "
        "files are parsed with python-docx, anything else is downloaded and decoded as text "
        "(best-effort).",
        ["file_id"], {"file_id": _prop("string", "Drive file URL or bare file ID")},
        lambda c, a: gcs.drive_read(c, a["file_id"]),
    ),
    _tool(
        "drive_export",
        "Export a native Google Doc, Sheet or Slides file to pdf/docx/xlsx/pptx/odt/ods/odp/rtf/"
        "txt/html/md/csv/tsv/epub (the allowed set depends on the file type), saved to a local "
        "path and/or uploaded to a Drive folder. 10 MB Drive export limit; for per-tab Sheets "
        "PDFs use sheet_export.",
        ["file_id"],
        {
            "file_id": _prop("string", "Drive file URL or bare file ID"),
            "format": _prop("string", "Output format, e.g. pdf, docx, txt, md", default="pdf"),
            "local_path": _prop("string", "Absolute local file path, or an existing directory"),
            "folder_id": FOLDER,
            "file_name": _prop("string", "Output file name (default: '<title>.<ext>')"),
            "replace": _prop("boolean", "Drive: update a same-named file in the folder", default=False),
            "overwrite": _prop("boolean", "Local: replace an existing file", default=False),
        },
        lambda c, a: gcs.drive_export(
            c, a["file_id"], format=a.get("format", "pdf"), local_path=a.get("local_path"),
            folder_id=a.get("folder_id"), file_name=a.get("file_name"),
            replace=bool(a.get("replace", False)), overwrite=bool(a.get("overwrite", False))),
    ),
    _tool(
        "drive_upload",
        "Upload a local file path OR inline text into a Drive folder (shared drives supported). "
        "Give exactly one of local_path / content. Creates a new file by default; replace=true "
        "updates a same-named file in that folder instead. Does not change sharing. Returns id, "
        "name, mimeType and webViewLink.",
        ["folder_id"],
        {
            "folder_id": _prop("string", "Destination Drive folder URL or bare folder ID"),
            "local_path": _prop("string", "Absolute path of a local file to upload"),
            "content": _prop("string", "Inline text to upload (needs file_name)"),
            "file_name": _prop("string", "File name in Drive (default: basename of local_path)"),
            "mime_type": _prop("string", "MIME type (default: guessed from the file name)"),
            "replace": _prop("boolean", "Update a same-named file in the folder instead of creating a new one",
                             default=False),
        },
        lambda c, a: gcs.drive_upload(
            c, a["folder_id"], file_name=a.get("file_name"), local_path=a.get("local_path"),
            content=a.get("content"), mime_type=a.get("mime_type"),
            replace=bool(a.get("replace", False))),
    ),
    _tool(
        "drive_mkdir",
        "Create a folder inside a Drive folder (shared drives supported). Returns id, name and webViewLink.",
        ["name", "parent_id"],
        {"name": _prop("string", "New folder name"), "parent_id": _prop("string", "Parent Drive folder URL or bare folder ID")},
        lambda c, a: gcs.drive_mkdir(c, a["name"], a["parent_id"]),
    ),
    _tool(
        "drive_share",
        "Turn 'anyone with the link can view' ON (public=true) or OFF (public=false) for a Drive "
        "file or folder. This makes content readable by anyone holding the link, so use "
        "public=true only when the owner wants that. public=false removes every 'anyone' "
        "permission and leaves named users/groups alone. Shared drives may block external "
        "sharing, in which case the call errors.",
        ["file_id", "public"],
        {
            "file_id": _prop("string", "Drive file or folder URL or bare ID"),
            "public": _prop("boolean", "true = anyone with the link can view; false = remove link sharing"),
        },
        lambda c, a: gcs.drive_share(c, a["file_id"], bool(a["public"])),
    ),
])


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [definition for definition, _ in TOOLS.values()]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> CallToolResult:
    entry = TOOLS.get(name)
    if entry is None:
        return err(f"Unknown tool: {name}")
    try:
        return ok(entry[1](get_creds(), arguments or {}))
    except Exception as e:
        return err(str(e))


# ── Entry point ────────────────────────────────────────────────────────────────

async def main():
    global _creds
    args = parse_args()
    config = gcs.load_config(args.config)

    sys.stderr.write("[google-cloud-services-mcp] Loading credentials...\n")
    _creds = gcs.get_credentials(config)
    sys.stderr.write("[google-cloud-services-mcp] Ready.\n")

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
