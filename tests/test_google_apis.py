"""doc_read/doc_write, sheet_*, drive_* — every call to the Docs/Sheets/Drive
APIs is faked via a mocked `build()`, so nothing here touches the network or a
real service account. Each service (docs/sheets/drive) gets its own MagicMock,
so a fallback path (e.g. doc_read's Docs->Drive fallback) can be wired
independently per service.
"""

import io
from unittest.mock import MagicMock

import docx
import httplib2
import pytest
from googleapiclient.errors import HttpError

import google_cloud_services as gcs


def http_error(status: int) -> HttpError:
    return HttpError(httplib2.Response({"status": str(status)}), b'{"error": {"message": "x"}}')


def fake_build(services: dict):
    """services: {"docs": MagicMock(), "drive": MagicMock(), ...} -> patchable `build`."""

    def _build(service_name, version, credentials=None):
        return services[service_name]

    return _build


# =========================================================================== #
# doc_read
# =========================================================================== #


def test_doc_read_via_docs_api_joins_paragraphs_and_renders_a_table(monkeypatch):
    docs_service = MagicMock()
    docs_service.documents.return_value.get.return_value.execute.return_value = {
        "title": "My Doc",
        "body": {
            "content": [
                {"paragraph": {"elements": [{"textRun": {"content": "Hello world\n"}}]}},
                {
                    "table": {
                        "tableRows": [
                            {
                                "tableCells": [
                                    {"content": [{"paragraph": {"elements": [{"textRun": {"content": "a"}}]}}]},
                                    {"content": [{"paragraph": {"elements": [{"textRun": {"content": "b"}}]}}]},
                                ]
                            }
                        ]
                    }
                },
            ]
        },
    }
    monkeypatch.setattr(gcs, "build", fake_build({"docs": docs_service}))

    result = gcs.doc_read(object(), "doc123")

    assert result["title"] == "My Doc"
    assert result["text"] == "Hello world\na | b"
    docs_service.documents.return_value.get.assert_called_once_with(documentId="doc123")


def test_doc_read_falls_back_to_drive_export_on_403(monkeypatch):
    docs_service = MagicMock()
    docs_service.documents.return_value.get.return_value.execute.side_effect = http_error(403)

    drive_service = MagicMock()
    drive_service.files.return_value.get.return_value.execute.return_value = {"name": "Restricted Doc"}
    drive_service.files.return_value.export.return_value.execute.return_value = b"plain text body"

    monkeypatch.setattr(gcs, "build", fake_build({"docs": docs_service, "drive": drive_service}))

    result = gcs.doc_read(object(), "doc123")

    assert result == {"title": "Restricted Doc", "text": "plain text body"}
    drive_service.files.return_value.export.assert_called_once_with(fileId="doc123", mimeType="text/plain")


def test_doc_read_falls_back_on_400_too(monkeypatch):
    docs_service = MagicMock()
    docs_service.documents.return_value.get.return_value.execute.side_effect = http_error(400)
    drive_service = MagicMock()
    drive_service.files.return_value.get.return_value.execute.return_value = {"name": "X"}
    drive_service.files.return_value.export.return_value.execute.return_value = "already-str-not-bytes"
    monkeypatch.setattr(gcs, "build", fake_build({"docs": docs_service, "drive": drive_service}))

    result = gcs.doc_read(object(), "doc123")
    assert result["text"] == "already-str-not-bytes"


def test_doc_read_reraises_non_fallback_status(monkeypatch):
    docs_service = MagicMock()
    docs_service.documents.return_value.get.return_value.execute.side_effect = http_error(500)
    monkeypatch.setattr(gcs, "build", fake_build({"docs": docs_service}))

    with pytest.raises(HttpError):
        gcs.doc_read(object(), "doc123")


# =========================================================================== #
# _insert_text_blocks_at
# =========================================================================== #


def test_insert_text_blocks_builds_insert_and_style_requests_with_correct_ranges():
    service = MagicMock()
    blocks = [
        {"type": "heading1", "text": "Title"},
        {"type": "bullet", "text": "item one"},
        {"type": "paragraph", "text": "plain"},
    ]
    end = gcs._insert_text_blocks_at(service, "doc1", index=5, blocks=blocks)

    full_text = "Title\nitem one\nplain\n"
    assert end == 5 + len(full_text)

    call = service.documents.return_value.batchUpdate.call_args
    assert call.kwargs["documentId"] == "doc1"
    requests = call.kwargs["body"]["requests"]

    assert requests[0] == {"insertText": {"location": {"index": 5}, "text": full_text}}

    # "Title\n" occupies [0,6) -> doc indices [5,11)
    heading_req = requests[1]["updateParagraphStyle"]
    assert heading_req["range"] == {"startIndex": 5, "endIndex": 11}
    assert heading_req["paragraphStyle"]["namedStyleType"] == "HEADING_1"

    # "item one\n" occupies [6,15) -> doc indices [11,20)
    bullet_req = requests[2]["createParagraphBullets"]
    assert bullet_req["range"] == {"startIndex": 11, "endIndex": 20}

    # plain paragraph gets no style request at all
    assert len(requests) == 3


def test_insert_text_blocks_with_no_blocks_makes_no_api_call_and_returns_index():
    service = MagicMock()
    end = gcs._insert_text_blocks_at(service, "doc1", index=7, blocks=[])
    assert end == 7
    service.documents.return_value.batchUpdate.assert_not_called()


# =========================================================================== #
# _insert_table_at
# =========================================================================== #


def _table_doc(index, cell_starts, table_end_index):
    """A minimal Docs `content` tree with one table at `index`, one row of
    len(cell_starts) cells whose paragraph content starts at each given index.
    """
    return {
        "body": {
            "content": [
                {
                    "startIndex": index,
                    "endIndex": table_end_index,
                    "table": {
                        "tableRows": [
                            {
                                "tableCells": [
                                    {"content": [{"startIndex": s}]} for s in cell_starts
                                ]
                            }
                        ]
                    },
                }
            ]
        }
    }


def test_insert_table_at_fills_cells_highest_index_first_and_bolds_header():
    service = MagicMock()
    # After insertTable, a 1-row, 2-col table sits at index 10 with empty cells
    # starting at 11 and 13. Re-fetched twice: once to locate cells, once for
    # the final endIndex.
    service.documents.return_value.get.return_value.execute.side_effect = [
        _table_doc(index=10, cell_starts=[11, 13], table_end_index=20),
        _table_doc(index=10, cell_starts=[11, 13], table_end_index=20),
    ]

    end = gcs._insert_table_at(service, "doc1", index=10, rows=[["H1", "H2"]])

    assert end == 20
    calls = service.documents.return_value.batchUpdate.call_args_list
    assert len(calls) == 3  # insertTable, fill cells, bold header

    insert_table_req = calls[0].kwargs["body"]["requests"][0]["insertTable"]
    assert insert_table_req == {"rows": 1, "columns": 2, "location": {"index": 10}}

    fill_requests = calls[1].kwargs["body"]["requests"]
    # Highest cell_start (13) filled before the lower one (11), so the earlier
    # insert doesn't shift the position of cells not yet written.
    assert [r["insertText"]["location"]["index"] for r in fill_requests] == [13, 11]
    assert fill_requests[0]["insertText"]["text"] == "H2"
    assert fill_requests[1]["insertText"]["text"] == "H1"

    bold_requests = calls[2].kwargs["body"]["requests"]
    bold_ranges = {(r["updateTextStyle"]["range"]["startIndex"], r["updateTextStyle"]["range"]["endIndex"]) for r in bold_requests}
    assert bold_ranges == {(11, 13), (13, 15)}  # "H1"/"H2" are 2 chars each


def test_insert_table_at_skips_empty_cells_without_error():
    service = MagicMock()
    service.documents.return_value.get.return_value.execute.side_effect = [
        _table_doc(index=0, cell_starts=[1, 3], table_end_index=10),
        _table_doc(index=0, cell_starts=[1, 3], table_end_index=10),
    ]
    end = gcs._insert_table_at(service, "doc1", index=0, rows=[["only-one-cell"]])
    assert end == 10
    fill_calls = service.documents.return_value.batchUpdate.call_args_list[1]
    fill_requests = fill_calls.kwargs["body"]["requests"]
    # Second cell has no corresponding value (row has only 1 item) -> skipped
    assert len(fill_requests) == 1
    assert fill_requests[0]["insertText"]["text"] == "only-one-cell"


def test_insert_table_at_skips_bold_request_when_header_row_is_all_blank():
    service = MagicMock()
    service.documents.return_value.get.return_value.execute.side_effect = [
        _table_doc(index=0, cell_starts=[1], table_end_index=5),
        _table_doc(index=0, cell_starts=[1], table_end_index=5),
    ]
    gcs._insert_table_at(service, "doc1", index=0, rows=[[""]])
    # insertTable + (no fill, since the only cell is blank) -> at most 1 call
    calls = service.documents.return_value.batchUpdate.call_args_list
    assert len(calls) == 1


# =========================================================================== #
# doc_write
# =========================================================================== #


def test_doc_write_replace_mode_deletes_then_writes_text_and_table(monkeypatch):
    service = MagicMock()
    # "Intro text\n" (one block, no blank line before the table) -> cursor
    # after the delete (1) + inserted text (11 chars) = 12, which is where
    # _insert_table_at will look for the freshly-inserted table.
    service.documents.return_value.get.return_value.execute.side_effect = [
        {"body": {"content": [{"endIndex": 50}]}},  # initial read -> triggers delete
        _table_doc(index=12, cell_starts=[13, 15], table_end_index=25),  # locate table cells
        _table_doc(index=12, cell_starts=[13, 15], table_end_index=25),  # final endIndex
    ]
    monkeypatch.setattr(gcs, "build", fake_build({"docs": service}))

    result = gcs.doc_write(
        object(), "doc1", "Intro text\n| H1 | H2 |\n|---|---|\n| v1 | v2 |", mode="replace"
    )
    batch_calls = service.documents.return_value.batchUpdate.call_args_list

    # First batchUpdate must be the delete of the old body.
    delete_req = batch_calls[0].kwargs["body"]["requests"][0]["deleteContentRange"]
    assert delete_req["range"] == {"startIndex": 1, "endIndex": 49}

    assert result == {"blocks_written": 1, "tables_written": 1}


def test_doc_write_replace_mode_on_an_empty_doc_skips_the_delete(monkeypatch):
    service = MagicMock()
    # end_index == 2 means the doc is already empty -> "replace" must not delete.
    service.documents.return_value.get.return_value.execute.return_value = {
        "body": {"content": [{"endIndex": 2}]}
    }
    monkeypatch.setattr(gcs, "build", fake_build({"docs": service}))

    gcs.doc_write(object(), "doc1", "just text", mode="replace")

    for call in service.documents.return_value.batchUpdate.call_args_list:
        assert "deleteContentRange" not in call.kwargs["body"]["requests"][0]


def test_doc_write_append_mode_never_deletes(monkeypatch):
    service = MagicMock()
    service.documents.return_value.get.return_value.execute.return_value = {
        "body": {"content": [{"endIndex": 40}]}
    }
    monkeypatch.setattr(gcs, "build", fake_build({"docs": service}))

    gcs.doc_write(object(), "doc1", "appended text", mode="append")

    for call in service.documents.return_value.batchUpdate.call_args_list:
        assert "deleteContentRange" not in call.kwargs["body"]["requests"][0]


# =========================================================================== #
# Google Sheets
# =========================================================================== #


def test_sheet_read_returns_values_or_empty_list(monkeypatch):
    service = MagicMock()
    service.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {
        "values": [["a", "b"], ["1", "2"]]
    }
    monkeypatch.setattr(gcs, "build", fake_build({"sheets": service}))

    result = gcs.sheet_read(object(), "sheet1", "A1:B2")
    assert result == [["a", "b"], ["1", "2"]]
    service.spreadsheets.return_value.values.return_value.get.assert_called_once_with(
        spreadsheetId="sheet1", range="A1:B2"
    )


def test_sheet_read_returns_empty_list_when_range_has_no_values(monkeypatch):
    service = MagicMock()
    service.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {}
    monkeypatch.setattr(gcs, "build", fake_build({"sheets": service}))
    assert gcs.sheet_read(object(), "sheet1") == []


def test_sheet_write_overwrites_with_user_entered_input_option(monkeypatch):
    service = MagicMock()
    service.spreadsheets.return_value.values.return_value.update.return_value.execute.return_value = {"updatedCells": 4}
    monkeypatch.setattr(gcs, "build", fake_build({"sheets": service}))

    result = gcs.sheet_write(object(), "sheet1", "A1:B2", [["a", "b"], ["1", "2"]])

    assert result == {"updatedCells": 4}
    service.spreadsheets.return_value.values.return_value.update.assert_called_once_with(
        spreadsheetId="sheet1",
        range="A1:B2",
        valueInputOption="USER_ENTERED",
        body={"values": [["a", "b"], ["1", "2"]]},
    )


def test_sheet_append_inserts_new_rows(monkeypatch):
    service = MagicMock()
    service.spreadsheets.return_value.values.return_value.append.return_value.execute.return_value = {"updates": {}}
    monkeypatch.setattr(gcs, "build", fake_build({"sheets": service}))

    gcs.sheet_append(object(), "sheet1", "A1", [["new", "row"]])

    service.spreadsheets.return_value.values.return_value.append.assert_called_once_with(
        spreadsheetId="sheet1",
        range="A1",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": [["new", "row"]]},
    )


def test_sheet_clear_wipes_the_given_range(monkeypatch):
    service = MagicMock()
    service.spreadsheets.return_value.values.return_value.clear.return_value.execute.return_value = {"clearedRange": "A1:Z100"}
    monkeypatch.setattr(gcs, "build", fake_build({"sheets": service}))

    result = gcs.sheet_clear(object(), "sheet1", "A1:Z100")

    assert result == {"clearedRange": "A1:Z100"}
    service.spreadsheets.return_value.values.return_value.clear.assert_called_once_with(
        spreadsheetId="sheet1", range="A1:Z100"
    )


# =========================================================================== #
# Google Drive
# =========================================================================== #


def test_drive_list_without_folder_id_lists_everything_not_trashed(monkeypatch):
    service = MagicMock()
    service.files.return_value.list.return_value.execute.return_value = {
        "files": [{"id": "1", "name": "a.txt"}]
    }
    monkeypatch.setattr(gcs, "build", fake_build({"drive": service}))

    result = gcs.drive_list(object())

    assert result == [{"id": "1", "name": "a.txt"}]
    call_kwargs = service.files.return_value.list.call_args.kwargs
    assert call_kwargs["q"] == "trashed=false"
    assert call_kwargs["supportsAllDrives"] is True
    assert call_kwargs["includeItemsFromAllDrives"] is True


def test_drive_list_with_folder_id_scopes_the_query(monkeypatch):
    service = MagicMock()
    service.files.return_value.list.return_value.execute.return_value = {"files": []}
    monkeypatch.setattr(gcs, "build", fake_build({"drive": service}))

    gcs.drive_list(object(), folder_id="folder123")

    call_kwargs = service.files.return_value.list.call_args.kwargs
    assert call_kwargs["q"] == "'folder123' in parents and trashed=false"


def test_drive_read_exports_native_google_docs_as_plain_text(monkeypatch):
    service = MagicMock()
    service.files.return_value.get.return_value.execute.return_value = {
        "mimeType": "application/vnd.google-apps.document",
        "name": "A Doc",
    }
    service.files.return_value.export.return_value.execute.return_value = b"exported text"
    monkeypatch.setattr(gcs, "build", fake_build({"drive": service}))

    result = gcs.drive_read(object(), "file1")

    assert result == "exported text"
    service.files.return_value.export.assert_called_once_with(fileId="file1", mimeType="text/plain")


class _FakeDownloader:
    """Stands in for MediaIoBaseDownload without touching real HTTP transport.

    `request` here is abused to carry the raw bytes to write, set up by each
    test's fake get_media()/get(..., alt="media") return value.
    """

    def __init__(self, buf, request):
        self._buf = buf
        self._payload = request

    def next_chunk(self):
        self._buf.write(self._payload)
        return None, True


def test_drive_read_parses_a_real_docx_file(monkeypatch):
    # Build an actual, valid .docx in memory so docx.Document() does real parsing.
    doc = docx.Document()
    doc.add_paragraph("First paragraph")
    doc.add_paragraph("Second paragraph")
    buf = io.BytesIO()
    doc.save(buf)
    docx_bytes = buf.getvalue()

    service = MagicMock()
    service.files.return_value.get.return_value.execute.return_value = {
        "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "name": "report.docx",
    }
    service.files.return_value.get_media.return_value = docx_bytes  # fake "request" object
    monkeypatch.setattr(gcs, "build", fake_build({"drive": service}))
    monkeypatch.setattr(gcs, "MediaIoBaseDownload", _FakeDownloader)

    result = gcs.drive_read(object(), "file1")

    assert result == "First paragraph\nSecond paragraph"


def test_drive_read_falls_back_to_raw_text_decode_for_unknown_mime_types(monkeypatch):
    service = MagicMock()
    service.files.return_value.get.return_value.execute.return_value = {
        "mimeType": "text/csv",
        "name": "data.csv",
    }
    service.files.return_value.get_media.return_value = b"a,b,c\n1,2,3\n"
    monkeypatch.setattr(gcs, "build", fake_build({"drive": service}))
    monkeypatch.setattr(gcs, "MediaIoBaseDownload", _FakeDownloader)

    result = gcs.drive_read(object(), "file1")

    assert result == "a,b,c\n1,2,3\n"
