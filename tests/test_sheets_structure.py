"""Sheets structure/formatting tools: sheet_info, header + cell formatting, freeze,
column widths, auto-resize, tab add/rename/delete. All API calls are mocked."""

from unittest.mock import MagicMock

import pytest

import google_cloud_services as gcs
from test_google_apis import fake_build

META = {
    "spreadsheetUrl": "https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit",
    "properties": {"title": "Example Sheet"},
    "sheets": [
        {"properties": {"sheetId": 0, "title": "Main", "index": 0,
                        "gridProperties": {"rowCount": 100, "columnCount": 8, "frozenRowCount": 1}}},
        {"properties": {"sheetId": 77, "title": "Bob's Data", "index": 1, "hidden": True,
                        "gridProperties": {"rowCount": 10, "columnCount": 3}}},
    ],
}


def make(monkeypatch):
    service = MagicMock()
    service.spreadsheets.return_value.get.return_value.execute.return_value = META
    service.spreadsheets.return_value.batchUpdate.return_value.execute.return_value = {"replies": [{}]}
    monkeypatch.setattr(gcs, "build", fake_build({"sheets": service}))
    return service


def requests_sent(service):
    kwargs = service.spreadsheets.return_value.batchUpdate.call_args.kwargs
    return kwargs["body"]["requests"]


def test_sheet_info_summarises_tabs(monkeypatch):
    make(monkeypatch)
    info = gcs.sheet_info(object(), "SPREADSHEET_ID")
    assert info["title"] == "Example Sheet"
    assert info["sheets"][0] == {
        "sheetId": 0, "title": "Main", "index": 0, "rowCount": 100, "columnCount": 8,
        "frozenRowCount": 1, "frozenColumnCount": 0, "hidden": False,
    }
    assert info["sheets"][1]["hidden"] is True
    assert info["sheets"][1]["frozenRowCount"] == 0


# ── header / cell formatting ──────────────────────────────────────────────────


def test_format_header_defaults_to_bold_first_row_of_first_tab(monkeypatch):
    service = make(monkeypatch)
    gcs.sheet_format_header(object(), "SPREADSHEET_ID")
    (req,) = requests_sent(service)
    rc = req["repeatCell"]
    assert rc["range"] == {"sheetId": 0, "startRowIndex": 0, "endRowIndex": 1,
                           "startColumnIndex": 0, "endColumnIndex": 8}
    assert rc["cell"]["userEnteredFormat"] == {"textFormat": {"bold": True}}
    assert rc["fields"] == "userEnteredFormat.textFormat.bold"


def test_format_header_with_colours_and_last_column(monkeypatch):
    service = make(monkeypatch)
    gcs.sheet_format_header(object(), "SPREADSHEET_ID", sheet="Bob's Data", row=2,
                            background_color="#FF0000", text_color="00FF00", last_column="c")
    rc = requests_sent(service)[0]["repeatCell"]
    assert rc["range"] == {"sheetId": 77, "startRowIndex": 1, "endRowIndex": 2,
                           "startColumnIndex": 0, "endColumnIndex": 3}
    fmt = rc["cell"]["userEnteredFormat"]
    assert fmt["backgroundColor"] == {"red": 1.0, "green": 0.0, "blue": 0.0}
    assert fmt["textFormat"]["foregroundColor"] == {"red": 0.0, "green": 1.0, "blue": 0.0}
    assert set(rc["fields"].split(",")) == {
        "userEnteredFormat.textFormat.bold",
        "userEnteredFormat.textFormat.foregroundColor",
        "userEnteredFormat.backgroundColor",
    }


def test_format_header_uses_gid_from_url_when_no_sheet_given(monkeypatch):
    service = make(monkeypatch)
    url = "https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit?gid=77#gid=77"
    gcs.sheet_format_header(object(), url)
    assert requests_sent(service)[0]["repeatCell"]["range"]["sheetId"] == 77
    assert service.spreadsheets.return_value.get.call_args.kwargs["spreadsheetId"] == "SPREADSHEET_ID"


@pytest.mark.parametrize("bad", ["red", "#12345", "#GGGGGG", ""])
def test_format_header_rejects_bad_colours_without_writing(monkeypatch, bad):
    service = make(monkeypatch)
    with pytest.raises(ValueError):
        gcs.sheet_format_header(object(), "SPREADSHEET_ID", background_color=bad)
    service.spreadsheets.return_value.batchUpdate.assert_not_called()


def test_format_header_with_nothing_to_do_is_an_error(monkeypatch):
    make(monkeypatch)
    with pytest.raises(ValueError, match="Nothing to format"):
        gcs.sheet_format_header(object(), "SPREADSHEET_ID", bold=None)


def test_format_cells_wrap_valign_and_number_format(monkeypatch):
    service = make(monkeypatch)
    result = gcs.sheet_format_cells(
        object(), "SPREADSHEET_ID", "B2:D10", wrap="wrap", vertical_align="top",
        number_format_type="number", number_format_pattern="0.00",
    )
    rc = requests_sent(service)[0]["repeatCell"]
    assert rc["range"] == {"sheetId": 0, "startRowIndex": 1, "endRowIndex": 10,
                           "startColumnIndex": 1, "endColumnIndex": 4}
    assert rc["cell"]["userEnteredFormat"] == {
        "wrapStrategy": "WRAP", "verticalAlignment": "TOP",
        "numberFormat": {"type": "NUMBER", "pattern": "0.00"},
    }
    assert set(result["formatted"]) == {"wrapStrategy", "verticalAlignment", "numberFormat"}


def test_format_cells_tab_prefix_overrides_sheet_argument(monkeypatch):
    service = make(monkeypatch)
    gcs.sheet_format_cells(object(), "SPREADSHEET_ID", "'Bob''s Data'!A:A", sheet="Main", bold=True)
    assert requests_sent(service)[0]["repeatCell"]["range"] == {
        "sheetId": 77, "startColumnIndex": 0, "endColumnIndex": 1,
    }


@pytest.mark.parametrize(
    "a1,expected",
    [
        ("A1", {"startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 1}),
        ("A:C", {"startColumnIndex": 0, "endColumnIndex": 3}),
        ("2:3", {"startRowIndex": 1, "endRowIndex": 3}),
        ("A2:C", {"startRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 3}),
        ("AA5", {"startRowIndex": 4, "endRowIndex": 5, "startColumnIndex": 26, "endColumnIndex": 27}),
    ],
)
def test_a1_to_grid_range(a1, expected):
    assert gcs._a1_to_grid_range(a1, 5) == {"sheetId": 5, **expected}


@pytest.mark.parametrize("bad", ["", "A1:B2:C3", "!!", "A0"])
def test_a1_to_grid_range_rejects_garbage(bad):
    with pytest.raises(ValueError):
        gcs._a1_to_grid_range(bad, 0)


def test_format_cells_number_pattern_requires_type(monkeypatch):
    make(monkeypatch)
    with pytest.raises(ValueError, match="number_format_type"):
        gcs.sheet_format_cells(object(), "SPREADSHEET_ID", "A1", number_format_pattern="0.0")


@pytest.mark.parametrize("kwargs", [{"wrap": "squish"}, {"vertical_align": "up"},
                                    {"horizontal_align": "mid"}, {"number_format_type": "WEIRD"}])
def test_format_cells_validates_enums(monkeypatch, kwargs):
    service = make(monkeypatch)
    with pytest.raises(ValueError):
        gcs.sheet_format_cells(object(), "SPREADSHEET_ID", "A1", **kwargs)
    service.spreadsheets.return_value.batchUpdate.assert_not_called()


# ── freeze / widths / auto-resize ─────────────────────────────────────────────


def test_freeze_rows_only_touches_rows(monkeypatch):
    service = make(monkeypatch)
    result = gcs.sheet_freeze(object(), "SPREADSHEET_ID", rows=2)
    usp = requests_sent(service)[0]["updateSheetProperties"]
    assert usp["properties"] == {"sheetId": 0, "gridProperties": {"frozenRowCount": 2}}
    assert usp["fields"] == "gridProperties.frozenRowCount"
    assert result == {"sheet": "Main", "frozenRowCount": 2}


def test_freeze_rows_and_columns_and_zero_unfreezes(monkeypatch):
    service = make(monkeypatch)
    gcs.sheet_freeze(object(), "SPREADSHEET_ID", sheet="Bob's Data", rows=0, columns=1)
    usp = requests_sent(service)[0]["updateSheetProperties"]
    assert usp["properties"]["sheetId"] == 77
    assert usp["properties"]["gridProperties"] == {"frozenRowCount": 0, "frozenColumnCount": 1}
    assert set(usp["fields"].split(",")) == {"gridProperties.frozenRowCount", "gridProperties.frozenColumnCount"}


def test_freeze_validates(monkeypatch):
    make(monkeypatch)
    with pytest.raises(ValueError):
        gcs.sheet_freeze(object(), "SPREADSHEET_ID")
    with pytest.raises(ValueError):
        gcs.sheet_freeze(object(), "SPREADSHEET_ID", rows=-1)


def test_set_column_width_single_and_range(monkeypatch):
    service = make(monkeypatch)
    gcs.sheet_set_column_width(object(), "SPREADSHEET_ID", {"A": 120, "c:e": 300})
    reqs = [r["updateDimensionProperties"] for r in requests_sent(service)]
    assert reqs[0]["range"] == {"sheetId": 0, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1}
    assert reqs[0]["properties"] == {"pixelSize": 120}
    assert reqs[1]["range"]["startIndex"] == 2 and reqs[1]["range"]["endIndex"] == 5
    assert reqs[1]["fields"] == "pixelSize"


@pytest.mark.parametrize("widths", [{}, {"A": 0}, {"A": -5}, {"E:C": 100}, {"A:B:C": 100}, {"1": 100}])
def test_set_column_width_validates(monkeypatch, widths):
    service = make(monkeypatch)
    with pytest.raises(ValueError):
        gcs.sheet_set_column_width(object(), "SPREADSHEET_ID", widths)
    service.spreadsheets.return_value.batchUpdate.assert_not_called()


def test_auto_resize_defaults_to_every_column(monkeypatch):
    service = make(monkeypatch)
    result = gcs.sheet_auto_resize_columns(object(), "SPREADSHEET_ID")
    dims = requests_sent(service)[0]["autoResizeDimensions"]["dimensions"]
    assert dims == {"sheetId": 0, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 8}
    assert result["columns"] == "A:H"


def test_auto_resize_with_bounds_and_bad_bounds(monkeypatch):
    service = make(monkeypatch)
    gcs.sheet_auto_resize_columns(object(), "SPREADSHEET_ID", first_column="B", last_column="D")
    dims = requests_sent(service)[0]["autoResizeDimensions"]["dimensions"]
    assert (dims["startIndex"], dims["endIndex"]) == (1, 4)
    with pytest.raises(ValueError):
        gcs.sheet_auto_resize_columns(object(), "SPREADSHEET_ID", first_column="D", last_column="B")


# ── tabs ──────────────────────────────────────────────────────────────────────


def test_add_tab_with_size(monkeypatch):
    service = make(monkeypatch)
    service.spreadsheets.return_value.batchUpdate.return_value.execute.return_value = {
        "replies": [{"addSheet": {"properties": {"sheetId": 9, "title": "New"}}}]
    }
    result = gcs.sheet_add_tab(object(), "SPREADSHEET_ID", "New", rows=50, columns=5)
    assert result == {"sheetId": 9, "title": "New"}
    assert requests_sent(service) == [
        {"addSheet": {"properties": {"title": "New", "gridProperties": {"rowCount": 50, "columnCount": 5}}}}
    ]


def test_add_tab_without_size_sends_only_the_title(monkeypatch):
    service = make(monkeypatch)
    service.spreadsheets.return_value.batchUpdate.return_value.execute.return_value = {
        "replies": [{"addSheet": {"properties": {"sheetId": 9, "title": "New"}}}]
    }
    gcs.sheet_add_tab(object(), "SPREADSHEET_ID", "New")
    assert requests_sent(service) == [{"addSheet": {"properties": {"title": "New"}}}]


def test_rename_tab(monkeypatch):
    service = make(monkeypatch)
    result = gcs.sheet_rename_tab(object(), "SPREADSHEET_ID", "Main", "Renamed")
    assert requests_sent(service) == [{
        "updateSheetProperties": {"properties": {"sheetId": 0, "title": "Renamed"}, "fields": "title"}
    }]
    assert result == {"sheetId": 0, "old_title": "Main", "title": "Renamed"}


def test_delete_tab_requires_exact_existing_title(monkeypatch):
    service = make(monkeypatch)
    with pytest.raises(ValueError, match="No tab"):
        gcs.sheet_delete_tab(object(), "SPREADSHEET_ID", "main")
    with pytest.raises(ValueError, match="required"):
        gcs.sheet_delete_tab(object(), "SPREADSHEET_ID", None)
    service.spreadsheets.return_value.batchUpdate.assert_not_called()

    result = gcs.sheet_delete_tab(object(), "SPREADSHEET_ID", "Bob's Data")
    assert requests_sent(service) == [{"deleteSheet": {"sheetId": 77}}]
    assert result == {"deleted": "Bob's Data", "sheetId": 77}


def test_unknown_tab_lists_the_available_ones(monkeypatch):
    make(monkeypatch)
    with pytest.raises(ValueError, match="Main"):
        gcs.sheet_freeze(object(), "SPREADSHEET_ID", sheet="Nope", rows=1)
