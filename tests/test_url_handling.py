"""Every library function and every MCP tool accepts a full Google URL as well as a
bare ID. Module level: the API only ever sees the bare ID. Server level: every tool's
URL argument reaches the library intact (and the library strips it)."""

import asyncio
from unittest.mock import MagicMock

import pytest

import google_cloud_services as gcs
import server
from test_google_apis import fake_build

SHEET_URL = "https://docs.google.com/spreadsheets/d/SHEET_ID_1/edit?gid=0#gid=0"
SHEET_URL_AUTHUSER = "https://docs.google.com/spreadsheets/u/0/d/SHEET_ID_1/edit?usp=sharing"
DOC_URL = "https://docs.google.com/document/d/DOC_ID_1/edit?tab=t.0"
FILE_URL = "https://drive.google.com/file/d/FILE_ID_1/view?usp=sharing"
FOLDER_URL = "https://drive.google.com/drive/u/0/folders/FOLDER_ID_1?usp=sharing"


@pytest.mark.parametrize(
    "value,expected",
    [
        (SHEET_URL, "SHEET_ID_1"),
        (SHEET_URL_AUTHUSER, "SHEET_ID_1"),
        ("https://docs.google.com/spreadsheets/d/SHEET_ID_1/edit#gid=123", "SHEET_ID_1"),
        ("https://docs.google.com/spreadsheets/d/SHEET_ID_1", "SHEET_ID_1"),
        (DOC_URL, "DOC_ID_1"),
        ("https://docs.google.com/document/u/1/d/DOC_ID_1/edit", "DOC_ID_1"),
        ("https://docs.google.com/presentation/d/SLIDES_ID_1/edit", "SLIDES_ID_1"),
        (FILE_URL, "FILE_ID_1"),
        (FOLDER_URL, "FOLDER_ID_1"),
        ("https://drive.google.com/open?id=OPEN_ID_1", "OPEN_ID_1"),
        ("  SHEET_ID_1  ", "SHEET_ID_1"),
        ("SHEET_ID_1", "SHEET_ID_1"),
    ],
)
def test_extract_id_from_url_shapes(value, expected):
    assert gcs.extract_id_from_url(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [(SHEET_URL, 0), ("https://x/spreadsheets/d/A/edit#gid=42", 42), ("...?foo=1&gid=7", 7),
     ("https://docs.google.com/spreadsheets/d/A/edit", None), ("BARE_ID", None), (None, None)],
)
def test_extract_gid(value, expected):
    assert gcs.extract_gid(value) == expected


def test_extract_id_ignores_non_strings():
    assert gcs.extract_id_from_url(None) is None


def all_strings(mock_calls):
    """Every string found in the args/kwargs of the recorded API calls."""
    found = []

    def walk(v):
        if isinstance(v, str):
            found.append(v)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)

    for _, args, kwargs in mock_calls:
        walk(args)
        walk(kwargs)
    return found


CASES = [
    ("doc_read", lambda: gcs.doc_read(None, DOC_URL), "DOC_ID_1"),
    ("doc_write", lambda: gcs.doc_write(None, DOC_URL, "text"), "DOC_ID_1"),
    ("sheet_read", lambda: gcs.sheet_read(None, SHEET_URL, "A1"), "SHEET_ID_1"),
    ("sheet_write", lambda: gcs.sheet_write(None, SHEET_URL, "A1", [["x"]]), "SHEET_ID_1"),
    ("sheet_append", lambda: gcs.sheet_append(None, SHEET_URL, "A1", [["x"]]), "SHEET_ID_1"),
    ("sheet_clear", lambda: gcs.sheet_clear(None, SHEET_URL, "A1"), "SHEET_ID_1"),
    ("sheet_find", lambda: gcs.sheet_find(None, SHEET_URL, "A", "key1"), "SHEET_ID_1"),
    ("sheet_update_row_by_key", lambda: gcs.sheet_update_row_by_key(None, SHEET_URL, "A", "key1", {"B": 1}), "SHEET_ID_1"),
    ("sheet_info", lambda: gcs.sheet_info(None, SHEET_URL), "SHEET_ID_1"),
    ("sheet_format_header", lambda: gcs.sheet_format_header(None, SHEET_URL), "SHEET_ID_1"),
    ("sheet_format_cells", lambda: gcs.sheet_format_cells(None, SHEET_URL, "A1", bold=True), "SHEET_ID_1"),
    ("sheet_freeze", lambda: gcs.sheet_freeze(None, SHEET_URL, rows=1), "SHEET_ID_1"),
    ("sheet_set_column_width", lambda: gcs.sheet_set_column_width(None, SHEET_URL, {"A": 100}), "SHEET_ID_1"),
    ("sheet_auto_resize_columns", lambda: gcs.sheet_auto_resize_columns(None, SHEET_URL), "SHEET_ID_1"),
    ("sheet_add_tab", lambda: gcs.sheet_add_tab(None, SHEET_URL, "T"), "SHEET_ID_1"),
    ("sheet_rename_tab", lambda: gcs.sheet_rename_tab(None, SHEET_URL, "Main", "T"), "SHEET_ID_1"),
    ("sheet_delete_tab", lambda: gcs.sheet_delete_tab(None, SHEET_URL, "Main"), "SHEET_ID_1"),
    ("sheet_export", lambda: gcs.sheet_export(None, SHEET_URL, folder_id=FOLDER_URL), "SHEET_ID_1"),
    ("sheet_create", lambda: gcs.sheet_create(None, "N", FOLDER_URL), "FOLDER_ID_1"),
    ("doc_create", lambda: gcs.doc_create(None, "N", FOLDER_URL), "FOLDER_ID_1"),
    ("drive_list", lambda: gcs.drive_list(None, FOLDER_URL), "FOLDER_ID_1"),
    ("drive_read", lambda: gcs.drive_read(None, FILE_URL), "FILE_ID_1"),
    ("drive_export", lambda: gcs.drive_export(None, FILE_URL, "pdf", folder_id=FOLDER_URL), "FILE_ID_1"),
    ("drive_upload", lambda: gcs.drive_upload(None, FOLDER_URL, file_name="a.txt", content="x"), "FOLDER_ID_1"),
    ("drive_mkdir", lambda: gcs.drive_mkdir(None, "N", FOLDER_URL), "FOLDER_ID_1"),
    ("drive_share", lambda: gcs.drive_share(None, FILE_URL, True), "FILE_ID_1"),
]


@pytest.mark.parametrize("name,call,bare_id", CASES, ids=[c[0] for c in CASES])
def test_library_function_sends_only_the_bare_id_to_google(monkeypatch, name, call, bare_id):
    services = {k: MagicMock() for k in ("docs", "sheets", "drive")}
    meta = {
        "properties": {"title": "Example"}, "name": "n", "mimeType": gcs.DOC_MIME,
        "sheets": [{"properties": {"sheetId": 0, "title": "Main", "gridProperties": {"columnCount": 4}}}],
        "files": [], "permissions": [], "id": "new", "webViewLink": "https://example.com/v",
        "title": "t", "body": {"content": [{"endIndex": 2}]}, "values": [["Header"], ["key1"]],
        "replies": [{"addSheet": {"properties": {"sheetId": 5, "title": "T"}}}],
        "totalUpdatedCells": 1,
    }

    def wire(node):
        for method in ("get", "update", "append", "clear", "batchUpdate", "list", "create",
                       "export", "delete", "get_media"):
            m = getattr(node, method)
            m.return_value.execute.return_value = dict(meta)
    for svc in services.values():
        wire(svc.spreadsheets.return_value)
        wire(svc.spreadsheets.return_value.values.return_value)
        wire(svc.files.return_value)
        wire(svc.permissions.return_value)
        wire(svc.documents.return_value)
    services["drive"].files.return_value.export.return_value.execute.return_value = b"data"
    monkeypatch.setattr(gcs, "build", fake_build(services))
    sent_urls = []
    monkeypatch.setattr(gcs, "_authorized_get", lambda creds, url: (sent_urls.append(url), (200, b"data"))[1])

    call()

    strings = sent_urls[:]
    for svc in services.values():
        strings.extend(all_strings(svc.mock_calls))
    assert strings, "expected the API to be called"
    assert any(bare_id in s for s in strings)
    for s in strings:
        assert "drive.google.com" not in s and "docs.google.com/document" not in s
        assert "/edit" not in s and "usp=sharing" not in s and "#gid" not in s and "?gid" not in s


# ── server level ──────────────────────────────────────────────────────────────

URL_ARG_FOR = {
    "spreadsheet_id": SHEET_URL,
    "document_id": DOC_URL,
    "folder_id": FOLDER_URL,
    "parent_id": FOLDER_URL,
    "file_id": FILE_URL,
}


def minimal_args(tool):
    schema = tool.inputSchema
    args = {}
    for key in schema["required"]:
        prop = schema["properties"][key]
        if key in URL_ARG_FOR:
            args[key] = URL_ARG_FOR[key]
        elif prop["type"] == "boolean":
            args[key] = True
        elif prop["type"] == "array":
            args[key] = [["x"]]
        elif prop["type"] == "object":
            args[key] = {"A": 1}
        elif prop["type"] == "integer":
            args[key] = 1
        else:
            args[key] = "x"
    return args


def test_every_registered_tool_has_a_handler_and_a_valid_schema():
    tools = asyncio.run(server.list_tools())
    names = [t.name for t in tools]
    assert names == list(server.TOOLS)
    assert len(names) == len(set(names))
    for t in tools:
        assert t.inputSchema["type"] == "object"
        assert set(t.inputSchema["required"]) <= set(t.inputSchema["properties"])
        assert t.description


def test_every_id_argument_in_every_tool_schema_is_described_as_url_or_id():
    for name, (tool, _) in server.TOOLS.items():
        for key, prop in tool.inputSchema["properties"].items():
            if key in URL_ARG_FOR:
                assert "URL" in prop["description"], f"{name}.{key}"


@pytest.mark.parametrize("name", list(server.TOOLS))
def test_server_dispatch_hands_url_arguments_to_the_library_unchanged(monkeypatch, name):
    tool, _ = server.TOOLS[name]
    monkeypatch.setattr(server, "_creds", object())
    seen = []

    def spy(*args, **kwargs):
        seen.append((args, kwargs))
        return {"ok": True}

    for attr in dir(gcs):
        if attr.startswith(("doc_", "sheet_", "drive_")):
            monkeypatch.setattr(gcs, attr, spy)

    result = asyncio.run(server.call_tool(name, minimal_args(tool)))

    assert not result.isError, result.content[0].text
    assert len(seen) == 1
    flat = [a for a in seen[0][0][1:] if isinstance(a, str)] + [v for v in seen[0][1].values() if isinstance(v, str)]
    url_args = {v for k, v in URL_ARG_FOR.items() if k in tool.inputSchema["required"]}
    assert url_args <= set(flat), f"{name}: URL argument not forwarded"


def test_unknown_tool_is_a_clean_error(monkeypatch):
    monkeypatch.setattr(server, "_creds", object())
    result = asyncio.run(server.call_tool("nope", {}))
    assert result.isError and "Unknown tool" in result.content[0].text


def test_library_errors_become_tool_errors(monkeypatch):
    monkeypatch.setattr(server, "_creds", object())
    monkeypatch.setattr(gcs, "sheet_info", lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))
    result = asyncio.run(server.call_tool("sheet_info", {"spreadsheet_id": SHEET_URL}))
    assert result.isError and "boom" in result.content[0].text


def test_server_before_credentials_are_loaded_is_a_tool_error(monkeypatch):
    monkeypatch.setattr(server, "_creds", None)
    result = asyncio.run(server.call_tool("sheet_info", {"spreadsheet_id": "x"}))
    assert result.isError
