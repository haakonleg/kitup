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
    assert option.match("  fav\t= 1\n") is not None
    assert option.match("favicon = 1\n") is None
    assert option.match("# fav = 1\n") is None
    assert option.match(";fav = 1\n") is None
    assert ini.option_re("a.b").match("a.b = 1\n") is not None
    assert ini.option_re("a.b").match("aXb = 1\n") is None


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
    with pytest.raises(TaskFailed, match="invalid ini match regex"):
        ini.require_ini_match({"a": "["})
    with pytest.raises(TaskFailed, match="invalid ini match regex"):
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
