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
