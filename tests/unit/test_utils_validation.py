import pytest

from kitup._utils import text, validation
from kitup.errors import TaskFailed


def test_has_control_characters_detects_controls_and_line_breaks():
    assert not validation.has_control_characters("plain text")
    assert validation.has_control_characters("nul\0")
    assert validation.has_control_characters("del\x7f")
    assert validation.has_control_characters("zero\u200bwidth")  # Cf
    assert all(validation.has_control_characters(f"a{char}b") for char in text.LINE_BREAKS)


def test_has_control_characters_allows_exempt_characters():
    assert not validation.has_control_characters("a\tb", allow="\t")
    assert validation.has_control_characters("a\tb")
    assert validation.has_control_characters("a\tb\n", allow="\t")


def test_require_single_line_rejects_every_splitlines_boundary():
    for char in text.LINE_BREAKS:
        with pytest.raises(TaskFailed, match="must not contain line breaks"):
            validation.require_single_line(f"a{char}b", "line")


def test_require_single_line_allows_tabs_and_plain_text():
    validation.require_single_line("a\tb", "line")


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (None, None),
        (0o600, 0o600),
        ("0600", 0o600),
        ("600", 0o600),
        ("0o600", 0o600),
        (" 0755 ", 0o755),
    ],
)
def test_normalize_mode_accepts_integers_and_octal_strings(mode, expected):
    assert validation.normalize_mode(mode) == expected


@pytest.mark.parametrize("mode", [True, -1, 0o10000, "", "  ", "0899", "rw-r--r--", 1.5])
def test_normalize_mode_rejects_invalid_values(mode):
    with pytest.raises(TaskFailed, match="mode"):
        validation.normalize_mode(mode)
