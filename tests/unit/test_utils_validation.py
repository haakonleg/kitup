import pytest

from kitup._utils import text, validation
from kitup.errors import TaskFailed


def test_has_control_characters_covers_exemptions_and_line_breaks():
    assert not validation.has_control_characters("plain text")
    assert not validation.has_control_characters("a\tb", allow="\t")
    for value in ("nul\0", "del\x7f", "zero\u200bwidth", "a\tb"):
        assert validation.has_control_characters(value)
    for char in text.LINE_BREAKS:
        assert validation.has_control_characters(f"a{char}b")
        with pytest.raises(TaskFailed, match="must not contain line breaks"):
            validation.require_single_line(f"a{char}b", "line")

    validation.require_single_line("a\tb", "line")


def test_normalize_mode_accepts_integers_and_octal_strings():
    for mode, expected in [
        (None, None),
        (0o600, 0o600),
        ("0600", 0o600),
        ("600", 0o600),
        ("0o600", 0o600),
        (" 0755 ", 0o755),
    ]:
        assert validation.normalize_mode(mode) == expected

    for mode in (True, -1, 0o10000, "", "  ", "0899", "rw-r--r--", 1.5):
        with pytest.raises(TaskFailed, match="mode"):
            validation.normalize_mode(mode)
