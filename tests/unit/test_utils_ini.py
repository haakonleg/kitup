import re

import pytest

from kitup._utils import ini
from kitup.errors import TaskFailed


def lines(text: str) -> list[str]:
    return text.splitlines(keepends=True)


def test_canonical_option_re_and_custom_pattern():
    pattern = ini.canonical_option_re("find-links")
    for line in ("find-links = x\n", "find-links: x\n", "Find-Links = x\n", "find_links = x\n", "FIND.LINKS: x\n"):
        assert pattern.match(line) is not None, line
    assert pattern.match("find-linkx = 1\n") is None
    assert pattern.match("# find-links = 1\n") is None

    original = lines("[global]\nFind-Links = old\nkeep = 1\n")
    new_lines, action = ini.set_option(original, "global", "find-links", "new", None, option_pattern=pattern)
    assert new_lines == lines("[global]\nfind-links = new\nkeep = 1\n")
    assert action == "replaced"

    original = lines("[global]\nfind_links: /a\nkeep = 1\n")
    assert ini.remove_option(original, "global", "find-links", None, option_pattern=pattern) == lines(
        "[global]\nkeep = 1\n"
    )


def test_set_option_updates_and_appends():
    original = lines("[s]\na = 1\n\n# trailing comment\n[t]\nb = 2\n")
    new_lines, action = ini.set_option(original, "s", "c", "3", None)
    assert new_lines == lines("[s]\na = 1\nc = 3\n\n# trailing comment\n[t]\nb = 2\n")
    assert action == "added"
    assert original == lines("[s]\na = 1\n\n# trailing comment\n[t]\nb = 2\n")

    # duplicate option lines are collapsed
    original = lines("[s]\na = 1\nb = 2\na = 9\n")
    new_lines, action = ini.set_option(original, "s", "a", "3", None)
    assert new_lines == lines("[s]\na = 3\nb = 2\n")
    assert action == "replaced"

    # an equivalent entry with spacing variants is unchanged
    original = lines("[s]\na=1\n")
    new_lines, action = ini.set_option(original, "s", "a", "1", None)
    assert new_lines == original
    assert action == "unchanged"

    # section=None edits the preamble
    original = lines("a = 1\n[s]\nb = 2\n")
    new_lines, _action = ini.set_option(original, None, "c", "3", None)
    assert new_lines == lines("a = 1\nc = 3\n[s]\nb = 2\n")

    # missing sections are appended, even without a final newline
    original = lines("[s]\na = 1\n")
    new_lines, action = ini.set_option(original, "t", "b", "2", None)
    assert new_lines == lines("[s]\na = 1\n[t]\nb = 2\n")
    assert action == "added"

    original = lines("[s]\na = 1")
    new_lines, _action = ini.set_option(original, "t", "b", "2", None)
    assert new_lines == lines("[s]\na = 1\n[t]\nb = 2\n")

    assert ini.set_option(lines("a = 1"), None, "b", "2", None)[0] == lines("a = 1\nb = 2\n")
    assert ini.set_option(lines("[s]"), "s", "a", "1", None)[0] == lines("[s]\na = 1\n")

    assert ini.set_option([], None, "a", "1", None)[0] == lines("a = 1\n")
    assert ini.set_option([], "s", "a", "1", None)[0] == lines("[s]\na = 1\n")

    # an empty section body receives the option before the next header
    original = lines("[s]\n[t]\na = 1\n")
    new_lines, _action = ini.set_option(original, "s", "b", "2", None)
    assert new_lines == lines("[s]\nb = 2\n[t]\na = 1\n")

    # touched lines are rendered with LF even in a CRLF file
    original = lines("[s]\r\na = 1\r\n")
    new_lines, _action = ini.set_option(original, "s", "a", "2", None)
    assert new_lines == ["[s]\r\n", "a = 2\n"]
    assert _action == "replaced"
    new_lines, _action = ini.set_option(original, "s", "b", "2", None)
    assert new_lines == ["[s]\r\n", "a = 1\r\n", "b = 2\n"]
    assert _action == "added"


def test_set_and_remove_option_scenarios():
    original = lines("a = 1\n[s]\na = 2\nb = 3\n[t]\na = 4\n")
    assert ini.remove_option(original, "s", "a", None) == lines("a = 1\n[s]\nb = 3\n[t]\na = 4\n")
    assert ini.remove_option(original, None, "a", None) == lines("[s]\na = 2\nb = 3\n[t]\na = 4\n")

    original = lines("[s]\na = 1\n")
    assert ini.remove_option(original, "missing", "a", None) == original
    assert ini.remove_option(original, "s", "a", {"b": "2"}) == original

    original = lines("[Peer]\nPublicKey = aaa\n[Peer]\nPublicKey = bbb\n")
    result = ini.remove_option(original, "Peer", "PublicKey", {"PublicKey": "bbb"})
    assert result == lines("[Peer]\nPublicKey = aaa\n[Peer]\n")

    # continuation lines are removed with their option
    original = lines("[s]\nkeep = 1\nfind-links =\n    a\n    b\nother = 2\n")
    assert ini.remove_option(original, "s", "find-links", None) == lines("[s]\nkeep = 1\nother = 2\n")


def test_set_option_match_scenarios():
    original = lines("[Peer]\nPublicKey = aaa\n[Peer]\nPublicKey = bbb\n")
    new_lines, _action = ini.set_option(original, "Peer", "Endpoint", "1.2.3.4:51820", {"PublicKey": "bbb"})
    assert new_lines == lines("[Peer]\nPublicKey = aaa\n[Peer]\nPublicKey = bbb\nEndpoint = 1.2.3.4:51820\n")

    # regex keys select the matching section
    original = lines("[Peer]\nAllowedIPs = 10.128.0.11/32\n[Peer]\nAllowedIPs = 10.128.0.12/32\n")
    new_lines, _action = ini.set_option(original, "Peer", "PublicKey", "xxxx", {r"Allowed.*": r"10\.128\.0\.12/32"})
    assert new_lines == lines(
        "[Peer]\nAllowedIPs = 10.128.0.11/32\n[Peer]\nAllowedIPs = 10.128.0.12/32\nPublicKey = xxxx\n"
    )

    original = lines("[Peer]\nPublicKey = aaa\n")
    # the matched option is the one being set
    new_lines, _action = ini.set_option(original, "Peer", "PublicKey", "bbbb", {"PublicKey": "bbbb"})
    assert new_lines == lines("[Peer]\nPublicKey = aaa\n[Peer]\nPublicKey = bbbb\n")
    # a substring regex still counts as satisfying (re.search semantics)
    new_lines, _action = ini.set_option(original, "Peer", "PublicKey", "bbbb", {"PublicKey": "bbb"})
    assert new_lines == lines("[Peer]\nPublicKey = aaa\n[Peer]\nPublicKey = bbbb\n")
    # section missing entirely, self-satisfying match, escaped literal value
    new_lines, _action = ini.set_option(
        lines("a = 1\n"), "Peer", "PublicKey", "abc+def", {"PublicKey": re.escape("abc+def")}
    )
    assert new_lines == lines("a = 1\n[Peer]\nPublicKey = abc+def\n")

    # match selects on an option that is not being set
    with pytest.raises(TaskFailed, match="would not satisfy the match"):
        ini.set_option(original, "Peer", "Endpoint", "1.2.3.4:51820", {"PublicKey": "zzz"})
    # regex metacharacters in an unescaped literal match value never match the written line
    with pytest.raises(TaskFailed, match="would not satisfy the match"):
        ini.set_option(original, "Peer", "PublicKey", "abc+def", {"PublicKey": "abc+def"})
    with pytest.raises(TaskFailed, match="would not satisfy the match"):
        ini.set_option([], "Peer", "PublicKey", "abc+def", {"PublicKey": "abc+def"})
    assert original == lines("[Peer]\nPublicKey = aaa\n")

    # the match is satisfied only by a continuation line of the new block
    new_lines, action = ini.set_option(lines("a = 1\n"), "global", "find-links", ["x", "y"], {"find-links": "y"})
    assert new_lines == lines("a = 1\n[global]\nfind-links =\n    x\n    y\n")
    assert action == "added"
    # a match no line of the block satisfies still raises
    with pytest.raises(TaskFailed, match="would not satisfy the match"):
        ini.set_option(lines("a = 1\n"), "global", "find-links", ["x", "y"], {"find-links": "z"})


def test_remove_section_and_malformed_header_boundaries():
    original = lines("preamble\n[s]\na = 1\n\n[t]\nb = 2\n[s]\nc = 3\n")
    assert ini.remove_section(original, "s", None) == lines("preamble\n[t]\nb = 2\n[s]\nc = 3\n")
    assert ini.remove_section(original, "s", {"c": "3"}) == lines("preamble\n[s]\na = 1\n\n[t]\nb = 2\n")
    assert ini.remove_section(original, "missing", None) == original

    # malformed headers are not section boundaries
    body = lines("a = 1\n[broken\nb = 2\n")
    assert ini.section_spans(body, None) == [(0, 3)]
    assert ini.section_spans(body, "broken") == []
    assert ini.section_spans(lines("a = 1\n[s]\nb = 2\n"), None) == [(0, 1)]


def test_render_option_and_option_entries():
    assert ini.render_option("find-links", ["https://a", "https://b"]) == (
        "find-links =\n    https://a\n    https://b\n"
    )
    # a single item stays on the option line
    assert ini.render_option("find-links", ["https://a"]) == "find-links = https://a\n"
    assert ini.render_option("fav", "lemonade") == "fav = lemonade\n"

    body = lines("[s]\na = 1\n    continued\n# comment\nb = 2\n")
    assert ini.option_entries(body, 1, 5) == [
        (1, "a", ["1", "continued"], 3),
        (4, "b", ["2"], 5),
    ]
    # a continuation line with '=' belongs to the preceding option
    body = lines("[s]\nurl = https://x/?a=b\n    https://y\n")
    assert ini.option_entries(body, 1, 3) == [(1, "url", ["https://x/?a=b", "https://y"], 3)]

    body = lines("find-links =\n    a\n    b\n")
    assert ini.span_matches(body, 0, 3, {"find-links": "a\nb"})
    assert not ini.span_matches(body, 0, 3, {"find-links": "a\nc"})


def test_set_option_multiline_round_trips():
    original = lines("[global]\ntimeout = 30\n")
    new_lines, action = ini.set_option(original, "global", "find-links", ["https://a", "https://b"], None)
    assert new_lines == lines("[global]\ntimeout = 30\nfind-links =\n    https://a\n    https://b\n")
    assert action == "added"

    again, action = ini.set_option(new_lines, "global", "find-links", ["https://a", "https://b"], None)
    assert again is new_lines
    assert action == "unchanged"

    # different indentation in the file still reads back as the same value
    spaced = lines("[global]\nfind-links =\n  https://a\n  https://b\n")
    again, action = ini.set_option(spaced, "global", "find-links", ["https://a", "https://b"], None)
    assert again is spaced
    assert action == "unchanged"

    # multi-line to single-line and back
    original = lines("[global]\nfind-links =\n    https://a\n    https://b\n")
    new_lines, action = ini.set_option(original, "global", "find-links", "https://c", None)
    assert new_lines == lines("[global]\nfind-links = https://c\n")
    assert action == "replaced"

    new_lines, action = ini.set_option(new_lines, "global", "find-links", ["https://a", "https://b"], None)
    assert new_lines == lines("[global]\nfind-links =\n    https://a\n    https://b\n")
    assert action == "replaced"

    # duplicate multi-line entries are collapsed
    original = lines("[s]\nfind-links =\n    a\nfind-links =\n    b\nkeep = 1\n")
    new_lines, action = ini.set_option(original, "s", "find-links", ["a"], None)
    assert new_lines == lines("[s]\nfind-links = a\nkeep = 1\n")
    assert action == "replaced"

    # a missing section is appended with the multi-line block
    new_lines, action = ini.set_option(lines("[s]\na = 1\n"), "global", "find-links", ["x", "y"], None)
    assert new_lines == lines("[s]\na = 1\n[global]\nfind-links =\n    x\n    y\n")
    assert action == "added"


def test_indented_and_blank_line_continuations():
    # indented keys are allowed; a same-indent line is a new option, not a continuation
    body = lines("[global]\n  timeout = 30\n  index-url = https://x\n")
    assert ini.option_entries(body, 1, 3) == [
        (1, "timeout", ["30"], 2),
        (2, "index-url", ["https://x"], 3),
    ]
    new_lines, action = ini.set_option(body, "global", "timeout", "60", None)
    # the replaced line is rendered canonically (unindented), but the
    # neighboring option is preserved instead of being swallowed
    assert new_lines == lines("[global]\ntimeout = 60\n  index-url = https://x\n")
    assert action == "replaced"
    assert ini.remove_option(body, "global", "timeout", None) == lines("[global]\n  index-url = https://x\n")

    # a deeper-indented bracket item is a continuation, not a header
    body = lines("[global]\ntrusted-host =\n    [::1]\n    example.com\n")
    assert ini.section_spans(body, "global") == [(1, 4)]
    assert ini.option_entries(body, 1, 4) == [(1, "trusted-host", ["[::1]", "example.com"], 4)]
    # setting the same value again is unchanged (no growth on every run)
    again, action = ini.set_option(body, "global", "trusted-host", ["[::1]", "example.com"], None)
    assert again is body
    assert action == "unchanged"

    # a blank line inside a multi-line value keeps the entry together
    body = lines("[global]\nfind-links =\n    /a\n\n    /b\nother = 1\n")
    assert ini.option_entries(body, 1, 5) == [(1, "find-links", ["/a", "/b"], 5)]
    assert ini.remove_option(body, "global", "find-links", None) == lines("[global]\nother = 1\n")
    # and re-setting the value is unchanged, not a growing duplicate
    again, action = ini.set_option(body, "global", "find-links", ["/a", "/b"], None)
    assert again is body
    assert action == "unchanged"
