from kitup._utils import text


def test_line_body_strips_one_line_ending():
    assert text.line_body("value\n") == "value"
    assert text.line_body("value\r\n") == "value"
    assert text.line_body("value\r") == "value"
    assert text.line_body("value") == "value"
    # Only the line ending is removed, not other whitespace or inner breaks.
    assert text.line_body("  value \n") == "  value "
    assert text.line_body("a\nb\n") == "a\nb"


def test_line_breaks_matches_splitlines_boundaries():
    assert all(len(("a" + char + "b").splitlines()) == 2 for char in text.LINE_BREAKS)


def test_truncate_leaves_fitting_text_unchanged():
    assert text.truncate("value", 5) == "value"
    assert text.truncate("value", 10) == "value"


def test_truncate_ends_with_an_ellipsis():
    assert text.truncate("value", 4) == "val…"
    assert text.truncate("value", 1) == "…"


def test_truncate_with_non_positive_width_is_empty():
    assert text.truncate("value", 0) == ""
    assert text.truncate("value", -1) == ""
