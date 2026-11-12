import re

import pytest

from kitup._utils import ini
from kitup.errors import TaskFailed


def lines(text: str) -> list[str]:
    return text.splitlines(keepends=True)


def test_parse_option():
    assert ini.parse_option("fav = lemonade\n") == ("fav", "lemonade")
    assert ini.parse_option("fav=lemonade\n") == ("fav", "lemonade")
    assert ini.parse_option("  fav  =  lemonade  \n") == ("fav", "lemonade")
    assert ini.parse_option("fav = a = b\n") == ("fav", "a = b")
    assert ini.parse_option("fav = lemonade\r\n") == ("fav", "lemonade")
    # ':' is a delimiter too; the first delimiter wins
    assert ini.parse_option("fav: lemonade\n") == ("fav", "lemonade")
    assert ini.parse_option("fav = https://x\n") == ("fav", "https://x")

    assert ini.parse_option("\n") is None
    assert ini.parse_option("   \n") is None
    assert ini.parse_option("# fav = lemonade\n") is None
    assert ini.parse_option("; fav = lemonade\n") is None
    assert ini.parse_option("  # comment\n") is None
    assert ini.parse_option("[drinks]\n") is None
    assert ini.parse_option("no separator\n") is None
    assert ini.parse_option("= value\n") is None


def test_regex_helpers():
    section = ini.section_re("drinks")
    assert section.match("[drinks]\n") is not None
    assert section.match("[ drinks ]\n") is not None
    assert section.match("[Drinks]\n") is None
    assert section.match("x[drinks]\n") is None
    assert ini.section_re("a.b").match("[a.b]\n") is not None
    assert ini.section_re("a.b").match("[aXb]\n") is None

    option = ini.option_re("fav")
    assert option.match("fav = 1\n") is not None
    assert option.match("fav=1\n") is not None
    assert option.match("fav: 1\n") is not None
    assert option.match("  fav\t= 1\n") is not None
    assert option.match("favicon = 1\n") is None
    assert option.match("# fav = 1\n") is None
    assert option.match(";fav = 1\n") is None
    assert ini.option_re("a.b").match("a.b = 1\n") is not None
    assert ini.option_re("a.b").match("aXb = 1\n") is None


def test_canonical_option_re_matches_pip_normalized_forms():
    pattern = ini.canonical_option_re("find-links")
    for line in ("find-links = x\n", "find-links: x\n", "Find-Links = x\n", "find_links = x\n", "FIND.LINKS: x\n"):
        assert pattern.match(line) is not None, line
    assert pattern.match("find-linkx = 1\n") is None
    assert pattern.match("# find-links = 1\n") is None


def test_set_and_remove_option_with_custom_pattern_normalize_existing_lines():
    pattern = ini.canonical_option_re("find-links")
    original = lines("[global]\nFind-Links = old\nkeep = 1\n")
    new_lines, action = ini.set_option(original, "global", "find-links", "new", None, option_pattern=pattern)
    assert new_lines == lines("[global]\nfind-links = new\nkeep = 1\n")
    assert action == "replaced"

    original = lines("[global]\nfind_links: /a\nkeep = 1\n")
    assert ini.remove_option(original, "global", "find-links", None, option_pattern=pattern) == lines(
        "[global]\nkeep = 1\n"
    )


def test_render_option_uses_canonical_spacing():
    assert ini.render_option("fav", "lemonade") == "fav = lemonade\n"


def test_section_spans():
    text = "preamble\n[a]\nx = 1\n[b]\ny = 2\n[a]\nz = 3\n"
    assert ini.section_spans(lines(text), "a") == [(2, 3), (6, 7)]
    assert ini.section_spans(lines(text), "b") == [(4, 5)]
    assert ini.section_spans(lines(text), "missing") == []

    assert ini.section_spans(lines("a = 1\n# c\n[s]\nb = 2\n"), None) == [(0, 2)]
    assert ini.section_spans(lines("[s]\nb = 2\n"), None) == [(0, 0)]
    assert ini.section_spans(lines(""), None) == [(0, 0)]
    assert ini.section_spans(lines("a = 1\n"), None) == [(0, 1)]


def test_span_matches():
    body = lines("a = 1\nb = 2\n")
    assert ini.span_matches(body, 0, 2, {"a": "1", "b": "2"})
    assert not ini.span_matches(body, 0, 2, {"a": "2"})
    assert not ini.span_matches(body, 0, 2, {"a": "1", "b": "1"})

    regex_body = lines("AllowedIPs = 10.128.0.11/32\nPublicKey = xxxx\n")
    assert ini.span_matches(regex_body, 0, 2, {r"Allowed.*": r"10\.128\.0\.\d+/32"})
    assert ini.span_matches(regex_body, 0, 2, {"PublicKey": "xxx"})
    assert not ini.span_matches(regex_body, 0, 2, {r"Allowed.*": "192\\."})
    # comments never satisfy a match pair
    assert not ini.span_matches(lines("# a = 1\n"), 0, 1, {"a": "1"})


def test_edits_scan_the_file_once(monkeypatch):
    body = lines("[a]\nx = 1\n[a]\ny = 2\n[a]\nz = 3\n")
    calls = []
    original = ini._scan

    def counting(lines):
        calls.append(lines)
        return original(lines)

    monkeypatch.setattr(ini, "_scan", counting)

    ini.set_option(body, "a", "x", "9", {"y": "2"})
    assert len(calls) == 1

    calls.clear()
    ini.remove_option(body, "a", "x", {"y": "2"})
    assert len(calls) == 1

    calls.clear()
    ini.remove_section(body, "a", {"y": "2"})
    assert len(calls) == 1


def test_set_option_updates_existing_section():
    original = lines("[s]\na = 1\n\n# trailing comment\n[t]\nb = 2\n")
    new_lines, action = ini.set_option(original, "s", "c", "3", None)
    assert new_lines == lines("[s]\na = 1\nc = 3\n\n# trailing comment\n[t]\nb = 2\n")
    assert action == "added"
    assert original == lines("[s]\na = 1\n\n# trailing comment\n[t]\nb = 2\n")

    original = lines("[s]\na = 1\nb = 2\na = 9\n")
    new_lines, action = ini.set_option(original, "s", "a", "3", None)
    assert new_lines == lines("[s]\na = 3\nb = 2\n")
    assert action == "replaced"

    original = lines("[s]\na=1\n")
    new_lines, action = ini.set_option(original, "s", "a", "1", None)
    assert new_lines == original
    assert action == "unchanged"

    original = lines("a = 1\n[s]\nb = 2\n")
    new_lines, _action = ini.set_option(original, None, "c", "3", None)
    assert new_lines == lines("a = 1\nc = 3\n[s]\nb = 2\n")


def test_set_option_appends_and_creates():
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

    original = lines("[s]\n[t]\na = 1\n")
    new_lines, _action = ini.set_option(original, "s", "b", "2", None)
    assert new_lines == lines("[s]\nb = 2\n[t]\na = 1\n")


def test_set_option_match_scenarios():
    original = lines("[Peer]\nPublicKey = aaa\n[Peer]\nPublicKey = bbb\n")
    new_lines, _action = ini.set_option(original, "Peer", "Endpoint", "1.2.3.4:51820", {"PublicKey": "bbb"})
    assert new_lines == lines("[Peer]\nPublicKey = aaa\n[Peer]\nPublicKey = bbb\nEndpoint = 1.2.3.4:51820\n")

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


def test_set_option_renders_touched_lines_with_lf_in_crlf_files():
    original = lines("[s]\r\na = 1\r\n")
    new_lines, _action = ini.set_option(original, "s", "a", "2", None)
    assert new_lines == ["[s]\r\n", "a = 2\n"]
    assert _action == "replaced"
    new_lines, _action = ini.set_option(original, "s", "b", "2", None)
    assert new_lines == ["[s]\r\n", "a = 1\r\n", "b = 2\n"]
    assert _action == "added"


def test_remove_option_scenarios():
    original = lines("a = 1\n[s]\na = 2\nb = 3\n[t]\na = 4\n")
    assert ini.remove_option(original, "s", "a", None) == lines("a = 1\n[s]\nb = 3\n[t]\na = 4\n")
    assert ini.remove_option(original, None, "a", None) == lines("[s]\na = 2\nb = 3\n[t]\na = 4\n")

    original = lines("[s]\na = 1\n")
    assert ini.remove_option(original, "missing", "a", None) == original
    assert ini.remove_option(original, "s", "a", {"b": "2"}) == original

    original = lines("[Peer]\nPublicKey = aaa\n[Peer]\nPublicKey = bbb\n")
    result = ini.remove_option(original, "Peer", "PublicKey", {"PublicKey": "bbb"})
    assert result == lines("[Peer]\nPublicKey = aaa\n[Peer]\n")


def test_remove_section_drops_header_and_body_of_first_matching_span():
    original = lines("preamble\n[s]\na = 1\n\n[t]\nb = 2\n[s]\nc = 3\n")
    assert ini.remove_section(original, "s", None) == lines("preamble\n[t]\nb = 2\n[s]\nc = 3\n")
    assert ini.remove_section(original, "s", {"c": "3"}) == lines("preamble\n[s]\na = 1\n\n[t]\nb = 2\n")
    assert ini.remove_section(original, "missing", None) == original


def test_require_ini_tokens_validate_and_strip():
    assert ini.require_ini_section(" drinks ") == "drinks"
    for bad in ("", "  ", "[s]", "s]", "a\nb", "a\rb"):
        with pytest.raises(TaskFailed, match="ini section"):
            ini.require_ini_section(bad)

    assert ini.require_ini_option(" fav ") == "fav"
    for bad in ("", " ", "a=b", "[a", "a]", "a\nb"):
        with pytest.raises(TaskFailed, match="ini option"):
            ini.require_ini_option(bad)

    assert ini.require_ini_value(" lemonade ") == "lemonade"
    for bad in ("", "  ", "a\nb", "a\rb", "a\0b"):
        with pytest.raises(TaskFailed, match="ini value"):
            ini.require_ini_value(bad)


def test_require_ini_match_validates_regex_keys_and_values():
    assert ini.require_ini_match({"AllowedIPs": r"10\.\d+"}) == {"AllowedIPs": r"10\.\d+"}
    with pytest.raises(TaskFailed, match="must not be empty"):
        ini.require_ini_match({})
    with pytest.raises(TaskFailed, match="ini match key"):
        ini.require_ini_match({"": "1"})
    with pytest.raises(TaskFailed, match="ini match value"):
        ini.require_ini_match({"a": ""})
    with pytest.raises(TaskFailed, match="invalid ini match value"):
        ini.require_ini_match({"a": "["})
    with pytest.raises(TaskFailed, match="invalid ini match key"):
        ini.require_ini_match({"[": "1"})
    with pytest.raises(TaskFailed, match="must be strings"):
        ini.require_ini_match({"a": 1})


@pytest.mark.parametrize("char", ["\n", "\r", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029", "\0"])
def test_require_ini_tokens_reject_line_breaks_and_null_bytes(char):
    with pytest.raises(TaskFailed, match="ini section must not contain"):
        ini.require_ini_section(f"a{char}b")
    with pytest.raises(TaskFailed, match="ini option must not contain"):
        ini.require_ini_option(f"a{char}b")
    with pytest.raises(TaskFailed, match="ini value must not contain"):
        ini.require_ini_value(f"a{char}b")


def test_require_ini_tokens_reject_non_utf8_encodable_text():
    with pytest.raises(TaskFailed, match="ini section is not UTF-8 encodable"):
        ini.require_ini_section("\ud800")
    with pytest.raises(TaskFailed, match="ini option is not UTF-8 encodable"):
        ini.require_ini_option("\ud800")
    with pytest.raises(TaskFailed, match="ini value is not UTF-8 encodable"):
        ini.require_ini_value("\ud800")


def test_require_ini_option_rejects_comment_prefixes():
    with pytest.raises(TaskFailed, match="comment character"):
        ini.require_ini_option("#foo")
    with pytest.raises(TaskFailed, match="comment character"):
        ini.require_ini_option("  ;foo")


def test_malformed_header_lines_are_not_section_boundaries():
    body = lines("a = 1\n[broken\nb = 2\n")
    assert ini.section_spans(body, None) == [(0, 3)]
    assert ini.section_spans(body, "broken") == []
    assert ini.section_spans(lines("a = 1\n[s]\nb = 2\n"), None) == [(0, 1)]


def test_render_option_multi_line_value():
    assert ini.render_option("find-links", ["https://a", "https://b"]) == (
        "find-links =\n    https://a\n    https://b\n"
    )
    # a single item stays on the option line
    assert ini.render_option("find-links", ["https://a"]) == "find-links = https://a\n"
    assert ini.render_option("fav", "lemonade") == "fav = lemonade\n"


def test_option_entries_includes_continuation_lines():
    body = lines("[s]\na = 1\n    continued\n# comment\nb = 2\n")
    assert ini.option_entries(body, 1, 5) == [
        (1, "a", ["1", "continued"], 3),
        (4, "b", ["2"], 5),
    ]
    # a continuation line with '=' belongs to the preceding option
    body = lines("[s]\nurl = https://x/?a=b\n    https://y\n")
    assert ini.option_entries(body, 1, 3) == [(1, "url", ["https://x/?a=b", "https://y"], 3)]


def test_span_matches_matches_multi_line_values():
    body = lines("find-links =\n    a\n    b\n")
    assert ini.span_matches(body, 0, 3, {"find-links": "a\nb"})
    assert not ini.span_matches(body, 0, 3, {"find-links": "a\nc"})


def test_set_option_multi_line_round_trip():
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


def test_set_option_replaces_multi_line_with_single_line_and_back():
    original = lines("[global]\nfind-links =\n    https://a\n    https://b\n")
    new_lines, action = ini.set_option(original, "global", "find-links", "https://c", None)
    assert new_lines == lines("[global]\nfind-links = https://c\n")
    assert action == "replaced"

    new_lines, action = ini.set_option(new_lines, "global", "find-links", ["https://a", "https://b"], None)
    assert new_lines == lines("[global]\nfind-links =\n    https://a\n    https://b\n")
    assert action == "replaced"


def test_set_option_drops_duplicate_multi_line_entries():
    original = lines("[s]\nfind-links =\n    a\nfind-links =\n    b\nkeep = 1\n")
    new_lines, action = ini.set_option(original, "s", "find-links", ["a"], None)
    assert new_lines == lines("[s]\nfind-links = a\nkeep = 1\n")
    assert action == "replaced"


def test_set_option_appends_section_with_multi_line_value():
    new_lines, action = ini.set_option(lines("[s]\na = 1\n"), "global", "find-links", ["x", "y"], None)
    assert new_lines == lines("[s]\na = 1\n[global]\nfind-links =\n    x\n    y\n")
    assert action == "added"


def test_set_option_match_checks_all_lines_of_a_multi_line_block():
    # the match is satisfied only by a continuation line of the new block
    new_lines, action = ini.set_option(lines("a = 1\n"), "global", "find-links", ["x", "y"], {"find-links": "y"})
    assert new_lines == lines("a = 1\n[global]\nfind-links =\n    x\n    y\n")
    assert action == "added"
    # a match no line of the block satisfies still raises
    with pytest.raises(TaskFailed, match="would not satisfy the match"):
        ini.set_option(lines("a = 1\n"), "global", "find-links", ["x", "y"], {"find-links": "z"})


def test_remove_option_removes_continuation_lines():
    original = lines("[s]\nkeep = 1\nfind-links =\n    a\n    b\nother = 2\n")
    assert ini.remove_option(original, "s", "find-links", None) == lines("[s]\nkeep = 1\nother = 2\n")


def test_require_ini_values_validates_and_strips():
    assert ini.require_ini_values([" a ", "b"]) == ["a", "b"]
    assert ini.require_ini_values(("a", "b")) == ["a", "b"]  # tuples are sequences too
    for bad in ("a", [], ["a", 1], [""], ["a\nb"], ["a\0b"], ["\ud800"], ["#foo"], [";foo"]):
        with pytest.raises(TaskFailed, match="ini value list"):
            ini.require_ini_values(bad)


def test_require_ini_values_rejects_unordered_iterables():
    # sets/dicts/generators iterate in an order that can change between runs,
    # which would make writes non-idempotent
    for bad in ({"a", "b"}, {"a": 1}, (x for x in ["a"])):
        with pytest.raises(TaskFailed, match="must be a sequence"):
            ini.require_ini_values(bad)
        with pytest.raises(TaskFailed, match="must be a sequence"):
            ini.require_ini_value_list(bad)


def test_same_indent_option_lines_are_separate_entries():
    # indented keys are allowed; a same-indent line is a new option,
    # not a continuation of the previous one
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


def test_deeper_indented_bracket_item_is_a_continuation_not_a_header():
    body = lines("[global]\ntrusted-host =\n    [::1]\n    example.com\n")
    assert ini.section_spans(body, "global") == [(1, 4)]
    assert ini.option_entries(body, 1, 4) == [(1, "trusted-host", ["[::1]", "example.com"], 4)]
    # setting the same value again is unchanged (no growth on every run)
    again, action = ini.set_option(body, "global", "trusted-host", ["[::1]", "example.com"], None)
    assert again is body
    assert action == "unchanged"


def test_blank_line_inside_multiline_value_keeps_the_entry_together():
    body = lines("[global]\nfind-links =\n    /a\n\n    /b\nother = 1\n")
    assert ini.option_entries(body, 1, 5) == [(1, "find-links", ["/a", "/b"], 5)]
    assert ini.remove_option(body, "global", "find-links", None) == lines("[global]\nother = 1\n")
    # and re-setting the value is unchanged, not a growing duplicate
    again, action = ini.set_option(body, "global", "find-links", ["/a", "/b"], None)
    assert again is body
    assert action == "unchanged"
