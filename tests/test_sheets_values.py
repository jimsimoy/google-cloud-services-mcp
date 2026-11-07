"""Sheets value tools: read render modes, input options, sheet_find and
sheet_update_row_by_key. Every API call is faked via a mocked `build()`."""

from unittest.mock import MagicMock

import pytest

import google_cloud_services as gcs
from test_google_apis import fake_build


def values_api(service):
    return service.spreadsheets.return_value.values.return_value


def make(monkeypatch):
    service = MagicMock()
    monkeypatch.setattr(gcs, "build", fake_build({"sheets": service}))
    return service


@pytest.mark.parametrize(
    "mode,api_value",
    [("formatted", "FORMATTED_VALUE"), ("unformatted", "UNFORMATTED_VALUE"), ("formula", "FORMULA")],
)
def test_sheet_read_maps_value_render_modes(monkeypatch, mode, api_value):
    service = make(monkeypatch)
    values_api(service).get.return_value.execute.return_value = {"values": [["x"]]}

    assert gcs.sheet_read(object(), "SPREADSHEET_ID", "A1", value_render=mode) == [["x"]]
    values_api(service).get.assert_called_once_with(
        spreadsheetId="SPREADSHEET_ID", range="A1", valueRenderOption=api_value
    )


def test_sheet_read_rejects_unknown_render_mode_without_calling_api(monkeypatch):
    service = make(monkeypatch)
    with pytest.raises(ValueError):
        gcs.sheet_read(object(), "SPREADSHEET_ID", "A1", value_render="pretty")
    values_api(service).get.assert_not_called()


@pytest.mark.parametrize("option", ["RAW", "raw", "USER_ENTERED"])
def test_sheet_write_and_append_pass_value_input_option(monkeypatch, option):
    service = make(monkeypatch)
    values_api(service).update.return_value.execute.return_value = {}
    values_api(service).append.return_value.execute.return_value = {}

    gcs.sheet_write(object(), "SPREADSHEET_ID", "A1", [["=1+1"]], value_input_option=option)
    gcs.sheet_append(object(), "SPREADSHEET_ID", "A1", [["=1+1"]], value_input_option=option)

    assert values_api(service).update.call_args.kwargs["valueInputOption"] == option.upper()
    assert values_api(service).append.call_args.kwargs["valueInputOption"] == option.upper()


def test_value_input_option_is_validated(monkeypatch):
    service = make(monkeypatch)
    with pytest.raises(ValueError):
        gcs.sheet_write(object(), "SPREADSHEET_ID", "A1", [["x"]], value_input_option="FORMULAS")
    with pytest.raises(ValueError):
        gcs.sheet_append(object(), "SPREADSHEET_ID", "A1", [["x"]], value_input_option="")
    values_api(service).update.assert_not_called()
    values_api(service).append.assert_not_called()


# ── sheet_find ────────────────────────────────────────────────────────────────

COLUMN = [["Header"], ["abc"], [], ["ABC"], ["xabcx"], ["abc"]]


def test_sheet_find_exact_returns_one_based_rows_and_reads_the_whole_column(monkeypatch):
    service = make(monkeypatch)
    values_api(service).get.return_value.execute.return_value = {"values": COLUMN}

    result = gcs.sheet_find(object(), "SPREADSHEET_ID", "h", "abc")

    assert result["count"] == 2
    assert [m["row"] for m in result["matches"]] == [2, 6]
    assert values_api(service).get.call_args.kwargs["range"] == "H:H"


def test_sheet_find_quotes_the_tab_name(monkeypatch):
    service = make(monkeypatch)
    values_api(service).get.return_value.execute.return_value = {"values": []}

    result = gcs.sheet_find(object(), "SPREADSHEET_ID", "A", "x", sheet="Bob's Tab")

    assert result == {"column": "A", "sheet": "Bob's Tab", "count": 0, "matches": []}
    assert values_api(service).get.call_args.kwargs["range"] == "'Bob''s Tab'!A:A"


def test_sheet_find_contains_and_case_insensitive(monkeypatch):
    service = make(monkeypatch)
    values_api(service).get.return_value.execute.return_value = {"values": COLUMN}

    result = gcs.sheet_find(object(), "SPREADSHEET_ID", "A", "ABC", match="contains", case_sensitive=False)

    assert [m["row"] for m in result["matches"]] == [2, 4, 5, 6]


def test_sheet_find_matches_numbers_as_text(monkeypatch):
    service = make(monkeypatch)
    values_api(service).get.return_value.execute.return_value = {"values": [["id"], [42]]}
    assert gcs.sheet_find(object(), "SPREADSHEET_ID", "A", 42)["matches"][0]["row"] == 2


@pytest.mark.parametrize("column", ["", "1", "H1", "Notes", "AAAA"])
def test_sheet_find_rejects_non_letter_columns(monkeypatch, column):
    make(monkeypatch)
    with pytest.raises(ValueError):
        gcs.sheet_find(object(), "SPREADSHEET_ID", column, "x")


def test_sheet_find_rejects_bad_match_mode(monkeypatch):
    make(monkeypatch)
    with pytest.raises(ValueError):
        gcs.sheet_find(object(), "SPREADSHEET_ID", "A", "x", match="regex")


# ── sheet_update_row_by_key ───────────────────────────────────────────────────


def wire_find(service, column):
    values_api(service).get.return_value.execute.return_value = {"values": column}
    values_api(service).batchUpdate.return_value.execute.return_value = {"totalUpdatedCells": 1}


def test_update_row_by_key_writes_only_the_named_cells(monkeypatch):
    service = make(monkeypatch)
    wire_find(service, [["id"], ["k1"], ["k2"]])

    result = gcs.sheet_update_row_by_key(
        object(), "SPREADSHEET_ID", "H", "k2", {"M": "done", "n": 3}, sheet="Log"
    )

    assert result == {"action": "updated", "rows": [3], "updated_cells": 1}
    body = values_api(service).batchUpdate.call_args.kwargs["body"]
    assert body["valueInputOption"] == "USER_ENTERED"
    assert body["data"] == [
        {"range": "'Log'!M3", "values": [["done"]]},
        {"range": "'Log'!N3", "values": [[3]]},
    ]


def test_update_row_by_key_errors_when_key_missing_by_default(monkeypatch):
    service = make(monkeypatch)
    wire_find(service, [["id"], ["k1"]])
    with pytest.raises(ValueError, match="No row"):
        gcs.sheet_update_row_by_key(object(), "SPREADSHEET_ID", "A", "nope", {"B": "x"})
    values_api(service).batchUpdate.assert_not_called()
    values_api(service).append.assert_not_called()


def test_update_row_by_key_can_append_a_new_row_when_missing(monkeypatch):
    service = make(monkeypatch)
    wire_find(service, [["id"], ["k1"]])
    values_api(service).append.return_value.execute.return_value = {"updates": {"updatedCells": 3}}

    result = gcs.sheet_update_row_by_key(
        object(), "SPREADSHEET_ID", "B", "k9", {"D": "note"}, append_if_missing=True
    )

    assert result == {"action": "appended", "rows": [], "updated_cells": 3}
    kwargs = values_api(service).append.call_args.kwargs
    assert kwargs["range"] == "A1"
    assert kwargs["body"] == {"values": [["", "k9", "", "note"]]}
    values_api(service).batchUpdate.assert_not_called()


def test_update_row_by_key_multiple_matches_error_first_all(monkeypatch):
    service = make(monkeypatch)
    wire_find(service, [["id"], ["k"], ["k"]])

    with pytest.raises(ValueError, match="2 rows"):
        gcs.sheet_update_row_by_key(object(), "SPREADSHEET_ID", "A", "k", {"B": "x"})
    values_api(service).batchUpdate.assert_not_called()

    first = gcs.sheet_update_row_by_key(object(), "SPREADSHEET_ID", "A", "k", {"B": "x"}, on_multiple="first")
    assert first["rows"] == [2]
    both = gcs.sheet_update_row_by_key(object(), "SPREADSHEET_ID", "A", "k", {"B": "x"}, on_multiple="all")
    assert both["rows"] == [2, 3]
    assert len(values_api(service).batchUpdate.call_args.kwargs["body"]["data"]) == 2


def test_update_row_by_key_validates_arguments(monkeypatch):
    make(monkeypatch)
    with pytest.raises(ValueError):
        gcs.sheet_update_row_by_key(object(), "SPREADSHEET_ID", "A", "k", {})
    with pytest.raises(ValueError):
        gcs.sheet_update_row_by_key(object(), "SPREADSHEET_ID", "A", "k", {"B": 1}, on_multiple="some")
    with pytest.raises(ValueError):
        gcs.sheet_update_row_by_key(object(), "SPREADSHEET_ID", "A", "k", {"B": 1}, value_input_option="X")


def test_update_row_by_key_contains_matches_a_key_inside_a_longer_cell(monkeypatch):
    service = make(monkeypatch)
    wire_find(service, [["id"], ["https://example.com/watch?v=abc123"], ["other"]])

    with pytest.raises(ValueError, match="No row"):
        gcs.sheet_update_row_by_key(object(), "SPREADSHEET_ID", "H", "abc123", {"M": "x"})
    result = gcs.sheet_update_row_by_key(object(), "SPREADSHEET_ID", "H", "abc123", {"M": "x"}, match="contains")

    assert result["rows"] == [2]
    assert values_api(service).batchUpdate.call_args.kwargs["body"]["data"][0]["range"] == "M2"
