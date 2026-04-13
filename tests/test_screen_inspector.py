"""Tests for carbonyl_agent.screen_inspector.ScreenInspector."""
from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from carbonyl_agent.screen_inspector import ScreenInspector

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_lines(texts: list[str], start_row: int = 1) -> list[dict]:
    """Build the raw_lines list expected by ScreenInspector."""
    return [{"row": start_row + i, "text": t} for i, t in enumerate(texts)]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_screen():
    """A 5-row sample screen."""
    return ScreenInspector([
        {"row": 1, "text": "Hello World"},
        {"row": 2, "text": "Sign In  |  Register"},
        {"row": 3, "text": "https://example.com"},
        {"row": 4, "text": "  Submit  [X] Remember me"},
        {"row": 5, "text": ""},
    ])

@pytest.fixture
def empty_screen():
    return ScreenInspector([])

@pytest.fixture
def single_cell():
    return ScreenInspector([{"row": 1, "text": "X"}])


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

class TestProperties:

    def test_empty_screen(self, empty_screen):
        assert empty_screen.row_count == 1  # default _max_row
        assert empty_screen.col_count == 0

    def test_single_cell(self, single_cell):
        assert single_cell.row_count == 1
        assert single_cell.col_count == 1

    def test_sample_screen(self, sample_screen):
        assert sample_screen.row_count == 5
        assert sample_screen.col_count > 0

    def test_col_count_is_longest_line(self, sample_screen):
        # Row 4 "  Submit  [X] Remember me" is 25 chars -- the longest
        assert sample_screen.col_count == 25

    def test_single_row(self):
        si = ScreenInspector(_make_lines(["hello"]))
        assert si.row_count == 1
        assert si.col_count == 5

    def test_multi_row_col_count(self):
        texts = ["short", "a bit longer text", "mid", "another line!!", "end"]
        si = ScreenInspector(_make_lines(texts))
        assert si.row_count == 5
        assert si.col_count == len("a bit longer text")


# ---------------------------------------------------------------------------
# line()
# ---------------------------------------------------------------------------

class TestLine:

    def test_existing_row(self, sample_screen):
        assert sample_screen.line(1) == "Hello World"

    def test_out_of_range(self, sample_screen):
        assert sample_screen.line(99) == ""

    def test_empty_row(self, sample_screen):
        assert sample_screen.line(5) == ""

    def test_zero_row(self, sample_screen):
        assert sample_screen.line(0) == ""

    def test_negative_row(self, sample_screen):
        assert sample_screen.line(-1) == ""

    def test_line_returns_each_row(self):
        texts = ["alpha", "bravo", "charlie"]
        si = ScreenInspector(_make_lines(texts))
        for i, t in enumerate(texts, start=1):
            assert si.line(i) == t

    @pytest.mark.parametrize("row", [0, -1, 999, 1000000])
    def test_line_out_of_range_parametrized(self, row: int):
        si = ScreenInspector(_make_lines(["only row"]))
        assert si.line(row) == ""


# ---------------------------------------------------------------------------
# text_at()
# ---------------------------------------------------------------------------

class TestTextAt:

    def test_single_char(self, sample_screen):
        assert sample_screen.text_at(1, 1) == "H"

    def test_multi_char(self, sample_screen):
        assert sample_screen.text_at(1, 1, 5) == "Hello"

    def test_last_char(self, sample_screen):
        # "Hello World" col 11 = "d"
        assert sample_screen.text_at(11, 1) == "d"

    def test_out_of_range_col(self, sample_screen):
        assert sample_screen.text_at(999, 1) == ""

    def test_out_of_range_row(self, sample_screen):
        assert sample_screen.text_at(1, 99) == ""

    def test_zero_col(self, sample_screen):
        # col 0 is out of 1-indexed range; c0 = -1, condition fails
        assert sample_screen.text_at(0, 1) == ""

    def test_span_past_end(self, sample_screen):
        # Request 20 chars starting at col 7 of "Hello World" (11 chars total)
        result = sample_screen.text_at(7, 1, 20)
        assert result == "World"

    def test_empty_row_text_at(self, sample_screen):
        assert sample_screen.text_at(1, 5) == ""

    @pytest.mark.parametrize(
        "col, row, expected",
        [
            (1, 1, "h"),
            (5, 1, "o"),
            (1, 2, "w"),
            (5, 2, "d"),
        ],
    )
    def test_text_at_parametrized(self, col: int, row: int, expected: str):
        si = ScreenInspector(_make_lines(["hello", "world"]))
        assert si.text_at(col, row) == expected

    @pytest.mark.parametrize(
        "col, row",
        [
            (0, 1),   # col 0 is out of range (1-indexed)
            (100, 1), # col beyond line length
            (1, 99),  # row beyond screen
        ],
    )
    def test_text_at_out_of_range_parametrized(self, col: int, row: int):
        si = ScreenInspector(_make_lines(["hello"]))
        assert si.text_at(col, row) == ""

    def test_text_at_length(self):
        si = ScreenInspector(_make_lines(["abcdefghij"]))
        assert si.text_at(3, 1, length=4) == "cdef"
        assert si.text_at(8, 1, length=5) == "hij"  # truncated at end


# ---------------------------------------------------------------------------
# find()
# ---------------------------------------------------------------------------

class TestFind:

    def test_find_existing(self, sample_screen):
        results = sample_screen.find("Hello")
        assert len(results) == 1
        assert results[0]["col"] == 1
        assert results[0]["row"] == 1
        assert results[0]["end_col"] == 5
        assert results[0]["text"] == "Hello"

    def test_find_multiple(self, sample_screen):
        # "e" appears in Hello, Register, example, Remember
        results = sample_screen.find("e")
        assert len(results) >= 3

    def test_find_no_match(self, sample_screen):
        assert sample_screen.find("ZZZZZ") == []

    def test_find_in_specific_row(self, sample_screen):
        results = sample_screen.find("Sign In")
        assert len(results) == 1
        assert results[0]["row"] == 2

    def test_find_empty_screen(self, empty_screen):
        assert empty_screen.find("anything") == []

    def test_find_url(self, sample_screen):
        results = sample_screen.find("https://example.com")
        assert len(results) == 1
        assert results[0]["row"] == 3
        assert results[0]["col"] == 1

    def test_find_overlapping(self):
        """find() uses start+1 so overlapping matches are found."""
        si = ScreenInspector([{"row": 1, "text": "aaa"}])
        results = si.find("aa")
        assert len(results) == 2
        assert results[0]["col"] == 1
        assert results[1]["col"] == 2

    def test_find_returns_sorted_by_row(self, sample_screen):
        results = sample_screen.find("e")
        rows = [r["row"] for r in results]
        assert rows == sorted(rows)

    def test_find_multiple_rows(self):
        si = ScreenInspector(_make_lines(["foo bar", "baz foo", "no match", "foo"]))
        results = si.find("foo")
        assert len(results) == 3
        rows = [r["row"] for r in results]
        assert rows == [1, 2, 4]

    def test_find_overlapping_four_chars(self):
        si = ScreenInspector(_make_lines(["aaaa"]))
        results = si.find("aa")
        # "aa" in "aaaa" starting at idx 0,1,2 -> cols 1,2,3
        assert len(results) == 3
        cols = [r["col"] for r in results]
        assert cols == [1, 2, 3]

    def test_find_empty_string(self):
        """Searching for empty string matches at every position."""
        si = ScreenInspector([{"row": 1, "text": "ab"}])
        results = si.find("")
        # Python str.find("", 0) returns 0, then 1, then 2, then -1 at pos 3
        assert len(results) == 3


# ---------------------------------------------------------------------------
# render_grid()
# ---------------------------------------------------------------------------

class TestRenderGrid:

    def test_renders_without_error(self, sample_screen):
        output = sample_screen.render_grid()
        assert isinstance(output, str)
        assert len(output) > 0

    def test_contains_row_labels(self, sample_screen):
        output = sample_screen.render_grid()
        assert "1 \u2502" in output  # "1 |"
        assert "5 \u2502" in output  # "5 |"

    def test_marks_appear(self, sample_screen):
        output = sample_screen.render_grid(marks=[(1, 1)], mark_char="X")
        lines = output.split("\n")
        row1_lines = [line for line in lines if "1 \u2502" in line and line.strip().startswith("1")]
        assert len(row1_lines) >= 1
        assert "X" in row1_lines[0]

    def test_mark_char_default(self, sample_screen):
        output = sample_screen.render_grid(marks=[(1, 1)])
        assert "\u25cf" in output  # default mark_char

    def test_row_range(self, sample_screen):
        output = sample_screen.render_grid(row_range=(2, 3))
        assert "   2 \u2502" in output
        assert "   3 \u2502" in output
        assert "   1 \u2502" not in output
        assert "   5 \u2502" not in output

    def test_col_range(self, sample_screen):
        output = sample_screen.render_grid(col_range=(1, 5))
        lines = output.split("\n")
        for line in lines:
            if "\u2502" in line and not line.strip().startswith("\u2502"):
                after_sep = line.split("\u2502", 1)[1]
                assert len(after_sep.rstrip()) <= 5

    def test_regions_brackets(self, sample_screen):
        output = sample_screen.render_grid(regions=[(1, 1, 11, 1)])
        assert "[" in output
        assert "]" in output

    def test_empty_screen(self, empty_screen):
        output = empty_screen.render_grid()
        assert isinstance(output, str)

    def test_render_grid_basic(self):
        si = ScreenInspector(_make_lines(["hello world", "second line"]))
        grid = si.render_grid()
        assert "   1 \u2502" in grid
        assert "   2 \u2502" in grid
        assert "hello world" in grid

    def test_render_grid_mark_replaces_char(self):
        si = ScreenInspector(_make_lines(["hello world"]))
        grid = si.render_grid(marks=[(1, 1)], mark_char="X")
        lines = grid.split("\n")
        content_line = [line for line in lines if "1 \u2502" in line and "\u2502" in line][-1]
        after_bar = content_line.split("\u2502", 1)[1]
        assert after_bar[0] == "X"

    def test_render_grid_col_range_content(self):
        si = ScreenInspector(_make_lines(["abcdefghij"]))
        grid = si.render_grid(col_range=(3, 7))
        lines = grid.split("\n")
        content_line = [line for line in lines if "1 \u2502" in line][-1]
        after_bar = content_line.split("\u2502", 1)[1]
        assert "cdefg" in after_bar

    def test_render_grid_all_options(self, sample_screen):
        """Calling render_grid with all options simultaneously should not error."""
        output = sample_screen.render_grid(
            marks=[(1, 1), (5, 3)],
            regions=[(1, 2, 20, 4)],
            row_range=(1, 4),
            col_range=(1, 20),
            ruler_every=5,
            mark_char="*",
        )
        assert isinstance(output, str)
        assert "*" in output


# ---------------------------------------------------------------------------
# summarise_region()
# ---------------------------------------------------------------------------

class TestSummariseRegion:

    def test_detects_button(self, sample_screen):
        summary = sample_screen.summarise_region(1, 4, 26, 4)
        assert "button" in summary["indicators"]  # "Submit"

    def test_detects_checkbox(self, sample_screen):
        summary = sample_screen.summarise_region(1, 4, 26, 4)
        assert "checkbox" in summary["indicators"]  # "[X]"

    def test_detects_url(self, sample_screen):
        summary = sample_screen.summarise_region(1, 3, 25, 3)
        assert "url" in summary["indicators"]

    def test_region_text(self, sample_screen):
        summary = sample_screen.summarise_region(1, 1, 11, 1)
        assert "Hello World" in summary["text"]

    def test_region_metadata(self, sample_screen):
        summary = sample_screen.summarise_region(1, 1, 11, 1)
        assert summary["col_range"] == (1, 11)
        assert summary["row_range"] == (1, 1)

    def test_lines_structure(self, sample_screen):
        summary = sample_screen.summarise_region(1, 1, 11, 2)
        assert isinstance(summary["lines"], list)
        assert all("row" in item and "text" in item for item in summary["lines"])

    def test_empty_region(self, sample_screen):
        # Row 5 is empty, so lines should be empty
        summary = sample_screen.summarise_region(1, 5, 10, 5)
        assert summary["lines"] == []
        assert summary["text"] == ""
        assert summary["indicators"] == []

    def test_no_false_indicators(self):
        si = ScreenInspector([{"row": 1, "text": "plain text here"}])
        summary = si.summarise_region(1, 1, 15, 1)
        assert summary["indicators"] == []

    def test_input_field_detection(self):
        si = ScreenInspector([{"row": 1, "text": "Name: <input>"}])
        summary = si.summarise_region(1, 1, 13, 1)
        assert "input_field" in summary["indicators"]

    def test_multi_row_region(self, sample_screen):
        summary = sample_screen.summarise_region(1, 1, 26, 5)
        assert "Hello World" in summary["text"]
        assert "Submit" in summary["text"]

    def test_full_indicator_set(self):
        texts = [
            "Welcome to the page",
            "[X] Accept terms",
            "Click Submit to continue",
            "Visit https://example.com",
        ]
        si = ScreenInspector(_make_lines(texts))
        summary = si.summarise_region(1, 1, 30, 4)
        assert summary["col_range"] == (1, 30)
        assert summary["row_range"] == (1, 4)
        assert len(summary["lines"]) == 4
        assert "checkbox" in summary["indicators"]
        assert "button" in summary["indicators"]
        assert "url" in summary["indicators"]

    def test_summarise_region_all_whitespace(self):
        si = ScreenInspector(_make_lines(["   ", "   "]))
        summary = si.summarise_region(1, 1, 3, 2)
        assert summary["lines"] == []
        assert summary["text"] == ""
        assert summary["indicators"] == []


# ---------------------------------------------------------------------------
# crosshair()
# ---------------------------------------------------------------------------

class TestCrosshair:

    def test_renders(self, sample_screen):
        output = sample_screen.crosshair(5, 2)
        assert isinstance(output, str)
        assert len(output) > 0

    def test_contains_mark(self, sample_screen):
        output = sample_screen.crosshair(5, 2)
        assert "\u25cf" in output  # mark

    def test_clamps_to_bounds(self, sample_screen):
        # Near top-left corner -- should not error
        output = sample_screen.crosshair(1, 1, radius=10)
        assert isinstance(output, str)

    def test_custom_radius(self):
        texts = ["." * 40] * 10
        si = ScreenInspector(_make_lines(texts))
        result = si.crosshair(20, 5, radius=2)
        assert "   5 \u2502" in result
        assert "   3 \u2502" in result
        assert "   7 \u2502" in result
        assert "\u25cf" in result


# ---------------------------------------------------------------------------
# dot_map()
# ---------------------------------------------------------------------------

class TestDotMap:

    def test_renders(self, sample_screen):
        output = sample_screen.dot_map(step_col=5, step_row=2)
        assert isinstance(output, str)
        assert "Dot map" in output

    def test_contains_dots(self, sample_screen):
        output = sample_screen.dot_map(step_col=5, step_row=1)
        assert "\u00b7" in output  # dot character

    def test_shows_step_params(self, sample_screen):
        output = sample_screen.dot_map(step_col=10, step_row=3)
        assert "step_col=10" in output
        assert "step_row=3" in output

    def test_contains_row_labels(self, sample_screen):
        output = sample_screen.dot_map()
        assert "1 \u2502" in output

    def test_large_screen(self):
        texts = ["x" * 60] * 20
        si = ScreenInspector(_make_lines(texts))
        result = si.dot_map(step_col=10, step_row=5)
        assert "Dot map" in result
        assert "\u00b7" in result
        assert "   1 \u2502" in result


# ---------------------------------------------------------------------------
# annotate()
# ---------------------------------------------------------------------------

class TestAnnotate:

    def test_with_marks(self, sample_screen):
        output = sample_screen.annotate(marks=[(1, 1)])
        assert "Marked Coordinates" in output
        assert "Mark 1" in output

    def test_with_regions_only(self, sample_screen):
        output = sample_screen.annotate(regions=[(1, 1, 10, 3)])
        assert "Region 1" in output

    def test_empty(self, sample_screen):
        output = sample_screen.annotate()
        assert isinstance(output, str)

    def test_marks_show_text_at_mark(self, sample_screen):
        output = sample_screen.annotate(marks=[(1, 1)])
        # text_at(1, 1, 10) -> "Hello Worl", shown repr'd in summary table
        assert "Hello Worl" in output

    def test_multiple_marks(self, sample_screen):
        output = sample_screen.annotate(marks=[(1, 1), (1, 2)])
        assert "Mark 1" in output
        assert "Mark 2" in output

    def test_context_rows_limits_output(self, sample_screen):
        output_small = sample_screen.annotate(marks=[(5, 3)], context_rows=1)
        output_large = sample_screen.annotate(marks=[(5, 3)], context_rows=5)
        assert len(output_small) < len(output_large)

    def test_marks_with_regions(self, sample_screen):
        output = sample_screen.annotate(
            marks=[(1, 1)],
            regions=[(1, 1, 10, 3)],
        )
        # When marks are present, regions are passed to render_grid but
        # the "Region N" header is not shown (only shown when no marks)
        assert "Mark 1" in output
        assert "Region 1" not in output

    def test_annotate_context_window(self):
        texts = ["line one", "line two", "line three", "line four", "line five"]
        si = ScreenInspector(_make_lines(texts))
        result = si.annotate(marks=[(1, 3)], context_rows=1)
        assert "Marked Coordinates" in result
        assert "Mark 1" in result
        # Context window should include rows 2-4
        assert "   2 \u2502" in result
        assert "   4 \u2502" in result


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_single_row_screen(self):
        si = ScreenInspector([{"row": 1, "text": "only row"}])
        assert si.row_count == 1
        assert si.col_count == 8
        assert si.line(1) == "only row"
        assert si.find("only") == [{"col": 1, "row": 1, "end_col": 4, "text": "only"}]

    def test_sparse_rows(self):
        """Rows don't have to be contiguous."""
        si = ScreenInspector([
            {"row": 1, "text": "first"},
            {"row": 10, "text": "tenth"},
        ])
        assert si.row_count == 10
        assert si.line(5) == ""  # gap row
        assert si.line(10) == "tenth"

    def test_wide_screen(self):
        si = ScreenInspector([{"row": 1, "text": "A" * 200}])
        assert si.col_count == 200
        assert si.text_at(200, 1) == "A"

    def test_special_characters(self):
        si = ScreenInspector([{"row": 1, "text": "[X] \u25cf <input> https://x.com"}])
        summary = si.summarise_region(1, 1, 30, 1)
        assert "checkbox" in summary["indicators"]
        assert "input_field" in summary["indicators"]
        assert "url" in summary["indicators"]


# ---------------------------------------------------------------------------
# Property-based tests (hypothesis)
# ---------------------------------------------------------------------------

_text_char = st.sampled_from("abcdefghijklmnopqrstuvwxyz 0123456789.,-")

@st.composite
def screen_inspector_strategy(draw):
    """Generate a ScreenInspector with random content."""
    num_rows = draw(st.integers(min_value=1, max_value=20))
    texts = [
        draw(st.text(alphabet=_text_char, min_size=1, max_size=80))
        for _ in range(num_rows)
    ]
    return ScreenInspector(_make_lines(texts)), texts


class TestPropertyBased:

    @given(data=screen_inspector_strategy())
    @settings(max_examples=50)
    def test_text_at_within_bounds(self, data):
        si, texts = data
        for row_idx, text in enumerate(texts, start=1):
            for col in range(1, len(text) + 1):
                result = si.text_at(col, row_idx, length=1)
                assert isinstance(result, str)
                assert len(result) == 1

    @given(data=screen_inspector_strategy())
    @settings(max_examples=50)
    def test_find_returns_valid_coordinates(self, data):
        si, texts = data
        if texts:
            first = texts[0]
            if len(first) >= 2:
                needle = first[:2]
                results = si.find(needle)
                for r in results:
                    assert r["col"] >= 1
                    assert r["row"] >= 1
                    assert r["end_col"] >= r["col"]

    @given(row=st.integers(min_value=-1000, max_value=1000))
    @settings(max_examples=100)
    def test_line_never_raises(self, row: int):
        si = ScreenInspector(_make_lines(["test line", "another"]))
        result = si.line(row)
        assert isinstance(result, str)

    @given(data=screen_inspector_strategy())
    @settings(max_examples=50)
    def test_row_count_matches_max_row(self, data):
        si, texts = data
        assert si.row_count == len(texts)

    @given(data=screen_inspector_strategy())
    @settings(max_examples=50)
    def test_col_count_matches_longest_line(self, data):
        si, texts = data
        expected = max(len(t) for t in texts)
        assert si.col_count == expected

    @given(data=screen_inspector_strategy())
    @settings(max_examples=30)
    def test_render_grid_never_raises(self, data):
        si, texts = data
        output = si.render_grid()
        assert isinstance(output, str)
        assert len(output) > 0

    @given(data=screen_inspector_strategy())
    @settings(max_examples=30)
    def test_summarise_region_full_screen(self, data):
        si, texts = data
        summary = si.summarise_region(1, 1, si.col_count, si.row_count)
        assert isinstance(summary["text"], str)
        assert isinstance(summary["indicators"], list)
        assert summary["col_range"] == (1, si.col_count)
        assert summary["row_range"] == (1, si.row_count)
