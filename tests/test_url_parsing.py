"""extract_id_from_url — accepts a full Google URL or a bare ID, no network."""

import pytest

from google_cloud_services import extract_id_from_url


@pytest.mark.parametrize(
    "url,expected_id",
    [
        (
            "https://docs.google.com/document/d/1AbC-defGHI_23/edit",
            "1AbC-defGHI_23",
        ),
        (
            "https://docs.google.com/document/d/1AbC-defGHI_23/edit?usp=sharing",
            "1AbC-defGHI_23",
        ),
        (
            "https://docs.google.com/spreadsheets/d/1XyZ_987-abc/edit#gid=0",
            "1XyZ_987-abc",
        ),
        (
            "https://drive.google.com/file/d/1FileID_here/view",
            "1FileID_here",
        ),
        (
            "https://drive.google.com/drive/folders/1FolderID_here",
            "1FolderID_here",
        ),
    ],
)
def test_extracts_id_from_each_known_url_shape(url, expected_id):
    assert extract_id_from_url(url) == expected_id


@pytest.mark.parametrize("bare_id", ["1AAoyx0W4bZfHf2oWAl4cENPAs3qG9ZIt", "abc123", "x"])
def test_bare_id_is_returned_unchanged(bare_id):
    assert extract_id_from_url(bare_id) == bare_id


def test_document_url_pattern_does_not_match_a_spreadsheet_url():
    """Guards against the patterns being checked out of order and misfiring."""
    sheet_url = "https://docs.google.com/spreadsheets/d/abc123/edit"
    assert extract_id_from_url(sheet_url) == "abc123"


def test_unrecognized_url_falls_back_to_returning_it_as_is():
    weird = "https://example.com/not-a-google-url"
    assert extract_id_from_url(weird) == weird
