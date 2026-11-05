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

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="doc_read",
            description=(
                "Read the full text of a Google Doc. Tries the Docs API first, falls back to a "
                "Drive plain-text export for restricted or third-party-converted docs. Table "
                "content is included, one row per line as 'cell | cell | cell'."
            ),
            inputSchema={
                "type": "object",
                "required": ["document_id"],
                "properties": {
                    "document_id": {"type": "string", "description": "Google Doc URL or bare document ID"},
                },
            },
        ),
        Tool(
            name="doc_write",
            description=(
                "Replace a Google Doc's entire body from markdown text. "
                "#/##/### headings, - / * bullets, and | a | b | tables become real Docs "
                "formatting (native bordered tables with a bolded header row). **bold** markers "
                "elsewhere are stripped, not rendered. Deletes the existing body first — no undo "
                "beyond the Doc's own version history."
            ),
            inputSchema={
                "type": "object",
                "required": ["document_id", "markdown"],
                "properties": {
                    "document_id": {"type": "string", "description": "Google Doc URL or bare document ID"},
                    "markdown":    {"type": "string", "description": "Markdown content to write"},
                },
            },
        ),
        Tool(
            name="doc_append",
            description=(
                "Append markdown text to the end of a Google Doc, using the same heading/bullet/"
                "table rendering as doc_write. Does not touch existing content."
            ),
            inputSchema={
                "type": "object",
                "required": ["document_id", "markdown"],
                "properties": {
                    "document_id": {"type": "string", "description": "Google Doc URL or bare document ID"},
                    "markdown":    {"type": "string", "description": "Markdown content to append"},
                },
            },
        ),
        Tool(
            name="sheet_read",
            description="Read a range of values from a Google Sheet as rows of cells.",
            inputSchema={
                "type": "object",
                "required": ["spreadsheet_id"],
                "properties": {
                    "spreadsheet_id": {"type": "string", "description": "Google Sheet URL or bare spreadsheet ID"},
                    "range":          {"type": "string", "default": "A1:ZZ",
                                       "description": "A1 notation range, e.g. 'Sheet1!A1:D50'"},
                },
            },
        ),
        Tool(
            name="sheet_write",
            description="Overwrite a range in a Google Sheet with values.",
            inputSchema={
                "type": "object",
                "required": ["spreadsheet_id", "range", "values"],
                "properties": {
                    "spreadsheet_id": {"type": "string", "description": "Google Sheet URL or bare spreadsheet ID"},
                    "range":          {"type": "string", "description": "A1 notation range, e.g. 'Sheet1!A1'"},
                    "values": {
                        "type": "array",
                        "items": {"type": "array"},
                        "description": "Rows of values, e.g. [[\"Col1\",\"Col2\"],[\"v1\",\"v2\"]]",
                    },
                },
            },
        ),
        Tool(
            name="sheet_append",
            description="Append rows after the last row with data in a Google Sheet range.",
            inputSchema={
                "type": "object",
                "required": ["spreadsheet_id", "range", "values"],
                "properties": {
                    "spreadsheet_id": {"type": "string", "description": "Google Sheet URL or bare spreadsheet ID"},
                    "range":          {"type": "string", "description": "A1 notation range, e.g. 'Sheet1!A1'"},
                    "values": {
                        "type": "array",
                        "items": {"type": "array"},
                        "description": "Rows of values to append",
                    },
                },
            },
        ),
        Tool(
            name="sheet_clear",
            description="Clear all values in a range of a Google Sheet.",
            inputSchema={
                "type": "object",
                "required": ["spreadsheet_id", "range"],
                "properties": {
                    "spreadsheet_id": {"type": "string", "description": "Google Sheet URL or bare spreadsheet ID"},
                    "range":          {"type": "string", "description": "A1 notation range, e.g. 'Sheet1!A1:Z100'"},
                },
            },
        ),
        Tool(
            name="drive_list",
            description="List files in a Google Drive folder (or Drive root/shared drives if no folder given).",
            inputSchema={
                "type": "object",
                "properties": {
                    "folder_id": {"type": "string", "description": "Drive folder URL or bare folder ID (optional)"},
                },
            },
        ),
        Tool(
            name="drive_read",
            description=(
                "Read/export a Drive file. Native Google Docs/Sheets are exported as plain text, "
                ".docx files are parsed with python-docx, anything else is downloaded and decoded "
                "as text (best-effort)."
            ),
            inputSchema={
                "type": "object",
                "required": ["file_id"],
                "properties": {
                    "file_id": {"type": "string", "description": "Drive file URL or bare file ID"},
                },
            },
        ),
        Tool(
            name="drive_upload",
            description=(
                "Upload a local file path OR inline text into a Drive folder (shared drives "
                "supported). Give exactly one of local_path / content. Creates a new file by "
                "default; replace=true updates a same-named file in that folder instead. Does "
                "not change sharing. Returns id, name, mimeType and webViewLink."
            ),
            inputSchema={
                "type": "object",
                "required": ["folder_id"],
                "properties": {
                    "folder_id":  {"type": "string", "description": "Destination Drive folder URL or bare folder ID"},
                    "local_path": {"type": "string", "description": "Absolute path of a local file to upload"},
                    "content":    {"type": "string", "description": "Inline text to upload (needs file_name)"},
                    "file_name":  {"type": "string", "description": "File name in Drive (default: basename of local_path)"},
                    "mime_type":  {"type": "string", "description": "MIME type (default: guessed from the file name)"},
                    "replace":    {"type": "boolean", "default": False,
                                   "description": "Update a same-named file in the folder instead of creating a new one"},
                },
            },
        ),
        Tool(
            name="drive_mkdir",
            description="Create a folder inside a Drive folder (shared drives supported). Returns id, name and webViewLink.",
            inputSchema={
                "type": "object",
                "required": ["name", "parent_id"],
                "properties": {
                    "name":      {"type": "string", "description": "New folder name"},
                    "parent_id": {"type": "string", "description": "Parent Drive folder URL or bare folder ID"},
                },
            },
        ),
        Tool(
            name="drive_share",
            description=(
                "Turn 'anyone with the link can view' ON (public=true) or OFF (public=false) for a "
                "Drive file or folder. This makes content readable by anyone holding the link, so "
                "use public=true only when the owner wants that. public=false removes every "
                "'anyone' permission and leaves named users/groups alone. Shared drives may block "
                "external sharing, in which case the call errors."
            ),
            inputSchema={
                "type": "object",
                "required": ["file_id", "public"],
                "properties": {
                    "file_id": {"type": "string", "description": "Drive file or folder URL or bare ID"},
                    "public":  {"type": "boolean", "description": "true = anyone with the link can view; false = remove link sharing"},
                },
            },
        ),
    ]


# ── Tool dispatch ────────────────────────────────────────────────────────────────

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> CallToolResult:
    creds = get_creds()

    try:
        if name == "doc_read":
            doc_id = gcs.extract_id_from_url(arguments["document_id"])
            return ok(gcs.doc_read(creds, doc_id))

        elif name == "doc_write":
            doc_id = gcs.extract_id_from_url(arguments["document_id"])
            return ok(gcs.doc_write(creds, doc_id, arguments["markdown"], mode="replace"))

        elif name == "doc_append":
            doc_id = gcs.extract_id_from_url(arguments["document_id"])
            return ok(gcs.doc_write(creds, doc_id, arguments["markdown"], mode="append"))

        elif name == "sheet_read":
            sheet_id = gcs.extract_id_from_url(arguments["spreadsheet_id"])
            range_name = arguments.get("range", "A1:ZZ")
            return ok(gcs.sheet_read(creds, sheet_id, range_name))

        elif name == "sheet_write":
            sheet_id = gcs.extract_id_from_url(arguments["spreadsheet_id"])
            return ok(gcs.sheet_write(creds, sheet_id, arguments["range"], arguments["values"]))

        elif name == "sheet_append":
            sheet_id = gcs.extract_id_from_url(arguments["spreadsheet_id"])
            return ok(gcs.sheet_append(creds, sheet_id, arguments["range"], arguments["values"]))

        elif name == "sheet_clear":
            sheet_id = gcs.extract_id_from_url(arguments["spreadsheet_id"])
            return ok(gcs.sheet_clear(creds, sheet_id, arguments["range"]))

        elif name == "drive_list":
            folder_id = arguments.get("folder_id")
            if folder_id:
                folder_id = gcs.extract_id_from_url(folder_id)
            return ok(gcs.drive_list(creds, folder_id))

        elif name == "drive_read":
            file_id = gcs.extract_id_from_url(arguments["file_id"])
            return ok(gcs.drive_read(creds, file_id))

        elif name == "drive_upload":
            return ok(gcs.drive_upload(
                creds,
                gcs.extract_id_from_url(arguments["folder_id"]),
                file_name=arguments.get("file_name"),
                local_path=arguments.get("local_path"),
                content=arguments.get("content"),
                mime_type=arguments.get("mime_type"),
                replace=bool(arguments.get("replace", False)),
            ))

        elif name == "drive_mkdir":
            return ok(gcs.drive_mkdir(
                creds, arguments["name"], gcs.extract_id_from_url(arguments["parent_id"])
            ))

        elif name == "drive_share":
            return ok(gcs.drive_share(
                creds, gcs.extract_id_from_url(arguments["file_id"]), bool(arguments["public"])
            ))

        else:
            return err(f"Unknown tool: {name}")

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
