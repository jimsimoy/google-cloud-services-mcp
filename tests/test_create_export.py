"""doc_create / sheet_create / drive_export / sheet_export - all API calls mocked."""

from unittest.mock import MagicMock

import pytest

import google_cloud_services as gcs
from test_google_apis import fake_build

CREATED = {"id": "new1", "name": "Example", "mimeType": "x", "webViewLink": "https://example.com/v"}


def drive_with(monkeypatch, **extra):
    drive = MagicMock()
    services = {"drive": drive, **extra}
    monkeypatch.setattr(gcs, "build", fake_build(services))
    return drive


# ── create ────────────────────────────────────────────────────────────────────


def test_sheet_create_makes_a_native_sheet_in_the_folder(monkeypatch):
    drive = drive_with(monkeypatch)
    drive.files.return_value.list.return_value.execute.return_value = {"files": []}
    drive.files.return_value.create.return_value.execute.return_value = CREATED

    result = gcs.sheet_create(object(), "Example", "https://drive.google.com/drive/folders/FOLDER_ID?usp=sharing")

    assert result == {**CREATED, "created": True}
    kwargs = drive.files.return_value.create.call_args.kwargs
    assert kwargs["body"] == {"name": "Example", "mimeType": gcs.SHEET_MIME, "parents": ["FOLDER_ID"]}
    assert kwargs["supportsAllDrives"] is True


def test_sheet_create_returns_existing_instead_of_duplicating(monkeypatch):
    drive = drive_with(monkeypatch)
    drive.files.return_value.list.return_value.execute.return_value = {"files": [CREATED]}

    result = gcs.sheet_create(object(), "O'Example", "FOLDER_ID")

    assert result == {**CREATED, "created": False}
    drive.files.return_value.create.assert_not_called()
    q = drive.files.return_value.list.call_args.kwargs["q"]
    assert "name='O\\'Example'" in q and "'FOLDER_ID' in parents" in q and "trashed=false" in q


def test_sheet_create_reuse_existing_false_skips_the_lookup(monkeypatch):
    drive = drive_with(monkeypatch)
    drive.files.return_value.create.return_value.execute.return_value = CREATED
    gcs.sheet_create(object(), "Example", "FOLDER_ID", reuse_existing=False)
    drive.files.return_value.list.assert_not_called()
    drive.files.return_value.create.assert_called_once()


def test_create_requires_a_name(monkeypatch):
    drive_with(monkeypatch)
    with pytest.raises(ValueError):
        gcs.sheet_create(object(), "", "FOLDER_ID")


def test_doc_create_writes_markdown_only_into_a_new_doc(monkeypatch):
    drive = drive_with(monkeypatch)
    drive.files.return_value.list.return_value.execute.return_value = {"files": []}
    drive.files.return_value.create.return_value.execute.return_value = CREATED
    written = []
    monkeypatch.setattr(gcs, "doc_write", lambda creds, doc_id, md, mode: written.append((doc_id, md, mode)))

    result = gcs.doc_create(object(), "Example", "FOLDER_ID", markdown="# Hi")

    assert result["created"] is True
    assert written == [("new1", "# Hi", "replace")]
    assert drive.files.return_value.create.call_args.kwargs["body"]["mimeType"] == gcs.DOC_MIME


def test_doc_create_never_touches_an_existing_doc(monkeypatch):
    drive = drive_with(monkeypatch)
    drive.files.return_value.list.return_value.execute.return_value = {"files": [CREATED]}
    written = []
    monkeypatch.setattr(gcs, "doc_write", lambda *a, **k: written.append(a))

    result = gcs.doc_create(object(), "Example", "FOLDER_ID", markdown="# Hi")

    assert result["created"] is False
    assert written == []


# ── drive_export ──────────────────────────────────────────────────────────────


def export_drive(monkeypatch, mime, data=b"%PDF"):
    drive = drive_with(monkeypatch)
    drive.files.return_value.get.return_value.execute.return_value = {"name": "Report", "mimeType": mime}
    drive.files.return_value.export.return_value.execute.return_value = data
    return drive


def test_drive_export_to_local_file(monkeypatch, tmp_path):
    drive = export_drive(monkeypatch, gcs.DOC_MIME)
    out = tmp_path / "out.pdf"

    result = gcs.drive_export(object(), "https://docs.google.com/document/d/DOC_ID/edit", "pdf",
                              local_path=str(out))

    assert out.read_bytes() == b"%PDF"
    assert result == {"format": "pdf", "bytes": 4, "file_name": "Report.pdf", "local_path": str(out)}
    drive.files.return_value.export.assert_called_once_with(fileId="DOC_ID", mimeType="application/pdf")


def test_drive_export_into_directory_uses_default_name_and_refuses_overwrite(monkeypatch, tmp_path):
    export_drive(monkeypatch, gcs.SHEET_MIME, data="a,b\n")
    gcs.drive_export(object(), "SHEET_ID", "csv", local_path=str(tmp_path))
    assert (tmp_path / "Report.csv").read_text() == "a,b\n"
    with pytest.raises(ValueError, match="overwrite"):
        gcs.drive_export(object(), "SHEET_ID", "csv", local_path=str(tmp_path))
    gcs.drive_export(object(), "SHEET_ID", "csv", local_path=str(tmp_path), overwrite=True)


def test_drive_export_uploads_to_a_shared_drive_folder(monkeypatch):
    drive = export_drive(monkeypatch, gcs.DOC_MIME)
    drive.files.return_value.create.return_value.execute.return_value = CREATED

    result = gcs.drive_export(object(), "DOC_ID", "pdf", folder_id="FOLDER_ID", file_name="r.pdf")

    assert result["drive_file"] == CREATED and "local_path" not in result
    kwargs = drive.files.return_value.create.call_args.kwargs
    assert kwargs["body"] == {"name": "r.pdf", "parents": ["FOLDER_ID"]}
    assert kwargs["supportsAllDrives"] is True
    assert kwargs["media_body"].mimetype() == "application/pdf"


def test_drive_export_replace_updates_same_named_file(monkeypatch):
    drive = export_drive(monkeypatch, gcs.DOC_MIME)
    drive.files.return_value.list.return_value.execute.return_value = {"files": [{"id": "old1"}]}
    drive.files.return_value.update.return_value.execute.return_value = CREATED
    gcs.drive_export(object(), "DOC_ID", "pdf", folder_id="FOLDER_ID", replace=True)
    assert drive.files.return_value.update.call_args.kwargs["fileId"] == "old1"
    drive.files.return_value.create.assert_not_called()


def test_drive_export_needs_a_destination_and_a_valid_format(monkeypatch):
    drive = export_drive(monkeypatch, gcs.DOC_MIME)
    with pytest.raises(ValueError, match="local_path"):
        gcs.drive_export(object(), "DOC_ID", "pdf")
    with pytest.raises(ValueError, match="not available"):
        gcs.drive_export(object(), "DOC_ID", "xlsx", folder_id="FOLDER_ID")
    drive.files.return_value.export.assert_not_called()


def test_drive_export_rejects_non_native_files(monkeypatch):
    drive = export_drive(monkeypatch, "application/pdf")
    with pytest.raises(ValueError, match="not a native"):
        gcs.drive_export(object(), "FILE_ID", "pdf", folder_id="FOLDER_ID")
    drive.files.return_value.export.assert_not_called()


# ── sheet_export ──────────────────────────────────────────────────────────────

META = {
    "properties": {"title": "Example Sheet"},
    "sheets": [
        {"properties": {"sheetId": 0, "title": "Main", "gridProperties": {"columnCount": 5}}},
        {"properties": {"sheetId": 42, "title": "Second", "gridProperties": {"columnCount": 5}}},
    ],
}


def sheet_export_env(monkeypatch, status=200, body=b"%PDF-data"):
    sheets = MagicMock()
    sheets.spreadsheets.return_value.get.return_value.execute.return_value = META
    drive = MagicMock()
    monkeypatch.setattr(gcs, "build", fake_build({"sheets": sheets, "drive": drive}))
    urls = []
    monkeypatch.setattr(gcs, "_authorized_get", lambda creds, url: (urls.append(url), (status, body))[1])
    return drive, urls


def test_sheet_export_whole_workbook_pdf_defaults(monkeypatch, tmp_path):
    _, urls = sheet_export_env(monkeypatch)
    out = tmp_path / "x.pdf"
    result = gcs.sheet_export(object(), "https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit",
                              local_path=str(out))
    assert urls == ["https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/export?format=pdf&portrait=true&fitw=true"]
    assert out.read_bytes() == b"%PDF-data"
    assert result["sheet"] is None and result["file_name"] == "Example Sheet.pdf"


def test_sheet_export_one_tab_landscape(monkeypatch, tmp_path):
    _, urls = sheet_export_env(monkeypatch)
    result = gcs.sheet_export(object(), "SPREADSHEET_ID", "pdf", sheet="Second", landscape=True,
                              fit_to_width=False, local_path=str(tmp_path))
    assert urls[0].endswith("export?format=pdf&gid=42&portrait=false&fitw=false&single=true")
    assert result["file_name"] == "Example Sheet - Second.pdf"
    assert (tmp_path / "Example Sheet - Second.pdf").exists()


def test_sheet_export_takes_the_tab_from_a_gid_url(monkeypatch, tmp_path):
    _, urls = sheet_export_env(monkeypatch)
    gcs.sheet_export(object(), "https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit?gid=42#gid=42",
                     "csv", local_path=str(tmp_path / "t.csv"))
    assert urls == ["https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/export?format=csv&gid=42"]


def test_sheet_export_xlsx_is_whole_workbook_and_rejects_sheet(monkeypatch, tmp_path):
    _, urls = sheet_export_env(monkeypatch)
    gcs.sheet_export(object(), "https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit#gid=42", "xlsx",
                     local_path=str(tmp_path / "w.xlsx"))
    assert urls == ["https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/export?format=xlsx"]
    with pytest.raises(ValueError, match="whole workbook"):
        gcs.sheet_export(object(), "SPREADSHEET_ID", "xlsx", sheet="Main", local_path=str(tmp_path / "x.xlsx"))


def test_sheet_export_uploads_to_drive(monkeypatch):
    drive, _ = sheet_export_env(monkeypatch)
    drive.files.return_value.create.return_value.execute.return_value = CREATED
    result = gcs.sheet_export(object(), "SPREADSHEET_ID", folder_id="FOLDER_ID")
    assert result["drive_file"] == CREATED
    kwargs = drive.files.return_value.create.call_args.kwargs
    assert kwargs["body"] == {"name": "Example Sheet.pdf", "parents": ["FOLDER_ID"]}
    assert kwargs["supportsAllDrives"] is True


def test_sheet_export_http_error_and_argument_validation(monkeypatch, tmp_path):
    sheet_export_env(monkeypatch, status=404)
    with pytest.raises(RuntimeError, match="404"):
        gcs.sheet_export(object(), "SPREADSHEET_ID", local_path=str(tmp_path / "a.pdf"))
    assert not (tmp_path / "a.pdf").exists()
    with pytest.raises(ValueError, match="local_path"):
        gcs.sheet_export(object(), "SPREADSHEET_ID")
    with pytest.raises(ValueError, match="format"):
        gcs.sheet_export(object(), "SPREADSHEET_ID", "docx", local_path=str(tmp_path / "a.docx"))
