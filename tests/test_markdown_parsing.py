"""_parse_markdown_segments and _parse_simple_markdown — the markdown-to-Docs
conversion logic doc_write/doc_append depend on. Pure functions, no network."""

from google_cloud_services import _parse_markdown_segments, _parse_simple_markdown


# --------------------------------------------------------------------------- #
# _parse_markdown_segments — splitting text from tables
# --------------------------------------------------------------------------- #


def test_plain_text_with_no_table_is_one_text_segment():
    segments = _parse_markdown_segments("# Title\nSome text\n- a bullet")
    assert len(segments) == 1
    assert segments[0]["kind"] == "text"
    assert segments[0]["lines"] == ["# Title", "Some text", "- a bullet"]


def test_table_between_text_produces_three_segments():
    md = "Intro line\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n\nOutro line"
    segments = _parse_markdown_segments(md)
    kinds = [s["kind"] for s in segments]
    assert kinds == ["text", "table", "text"]
    assert segments[1]["rows"] == [["a", "b"], ["1", "2"]]


def test_table_at_the_very_start_of_input():
    md = "| h1 | h2 |\n|---|---|\n| v1 | v2 |\nafter"
    segments = _parse_markdown_segments(md)
    assert segments[0]["kind"] == "table"
    assert segments[0]["rows"] == [["h1", "h2"], ["v1", "v2"]]
    assert segments[1] == {"kind": "text", "lines": ["after"]}


def test_table_at_the_very_end_of_input_with_no_trailing_text():
    md = "before\n| h1 |\n|---|\n| v1 |"
    segments = _parse_markdown_segments(md)
    assert [s["kind"] for s in segments] == ["text", "table"]
    assert segments[1]["rows"] == [["h1"], ["v1"]]


def test_two_consecutive_tables_are_kept_separate():
    md = "| a |\n|---|\n| 1 |\n| b |\n|---|\n| 2 |"
    segments = _parse_markdown_segments(md)
    assert [s["kind"] for s in segments] == ["table", "table"]
    assert segments[0]["rows"] == [["a"], ["1"]]
    assert segments[1]["rows"] == [["b"], ["2"]]


def test_pipe_lines_without_a_separator_row_are_not_treated_as_a_table():
    """A line starting with '|' only becomes a table if immediately followed
    by a '|---|' separator — otherwise it's just text that happens to use pipes."""
    md = "| not a table |\njust some text with a | pipe in it"
    segments = _parse_markdown_segments(md)
    assert len(segments) == 1
    assert segments[0]["kind"] == "text"


def test_multi_column_table_rows_split_correctly():
    md = "| Col1 | Col2 | Col3 |\n| --- | --- | --- |\n| a | b | c |"
    segments = _parse_markdown_segments(md)
    assert segments[0]["rows"] == [["Col1", "Col2", "Col3"], ["a", "b", "c"]]


def test_empty_input_produces_no_segments():
    assert _parse_markdown_segments("") == [{"kind": "text", "lines": [""]}]


# --------------------------------------------------------------------------- #
# _parse_simple_markdown — headings, bullets, paragraphs, bold-stripping
# --------------------------------------------------------------------------- #


def test_headings_at_each_level():
    blocks = _parse_simple_markdown(["# H1", "## H2", "### H3"])
    assert blocks == [
        {"type": "heading1", "text": "H1"},
        {"type": "heading2", "text": "H2"},
        {"type": "heading3", "text": "H3"},
    ]


def test_dash_and_star_bullets_both_recognized():
    blocks = _parse_simple_markdown(["- dash bullet", "* star bullet"])
    assert blocks[0] == {"type": "bullet", "text": "dash bullet"}
    assert blocks[1] == {"type": "bullet", "text": "star bullet"}


def test_blank_line_becomes_empty_paragraph():
    blocks = _parse_simple_markdown(["text", "", "more text"])
    assert blocks[1] == {"type": "paragraph", "text": ""}


def test_plain_line_is_a_paragraph():
    blocks = _parse_simple_markdown(["just a plain sentence."])
    assert blocks == [{"type": "paragraph", "text": "just a plain sentence."}]


def test_bold_markers_are_stripped_not_rendered():
    blocks = _parse_simple_markdown(["This is **bold** and __also bold__."])
    assert blocks[0]["text"] == "This is bold and also bold."


def test_bold_stripping_applies_to_headings_and_bullets_too():
    blocks = _parse_simple_markdown(["# **Bold Heading**", "- **bold item**"])
    assert blocks[0]["text"] == "Bold Heading"
    assert blocks[1]["text"] == "bold item"


def test_leading_and_trailing_whitespace_is_stripped():
    blocks = _parse_simple_markdown(["   # Heading with padding   "])
    assert blocks[0] == {"type": "heading1", "text": "Heading with padding"}


def test_a_line_that_merely_contains_a_hash_is_not_a_heading():
    """Only '# ' (hash + space) at the start marks a heading — '#hashtag' is a paragraph."""
    blocks = _parse_simple_markdown(["#hashtag not a heading"])
    assert blocks[0]["type"] == "paragraph"
