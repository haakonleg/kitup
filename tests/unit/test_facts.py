from __future__ import annotations

from kitup.facts import collect_facts, parse_os_release


def test_parse_os_release_handles_quotes_comments_and_blanks():
    text = """
    # a comment
    NAME="Test Linux"
    ID=testos
    VERSION_ID="1"
    EMPTY=
    BAD LINE
    """

    values = parse_os_release(text)

    assert values == {
        "NAME": "Test Linux",
        "ID": "testos",
        "VERSION_ID": "1",
        "EMPTY": "",
    }


def test_collect_facts_from_os_release(tmp_path):
    path = tmp_path / "os-release"
    path.write_text(
        'NAME="Test Linux"\nID=testos\nVERSION_ID=1\n'
        'PRETTY_NAME="Test Linux 1 (Workstation Edition)"\n',
        encoding="utf-8",
    )

    facts = collect_facts(path)

    assert facts["distro_id"] == "testos"
    assert facts["version_id"] == "1"
    assert facts["os_release"]["PRETTY_NAME"] == "Test Linux 1 (Workstation Edition)"
    assert facts["hostname"]
    assert facts["arch"]
    assert facts["kernel"]


def test_missing_os_release_omits_optional_facts(tmp_path):
    facts = collect_facts(tmp_path / "missing")

    assert "os_release" not in facts
    assert "distro_id" not in facts
    assert "version_id" not in facts
    assert "hostname" in facts


def test_facts_are_read_only(tmp_path):
    path = tmp_path / "os-release"
    path.write_text("ID=testos\nVERSION_ID=1\n", encoding="utf-8")
    facts = collect_facts(path)

    try:
        facts["hostname"] = "other"  # type: ignore[index]
    except TypeError:
        pass
    else:
        raise AssertionError("facts should be read-only")

    try:
        facts["os_release"]["ID"] = "other"  # type: ignore[index]
    except TypeError:
        pass
    else:
        raise AssertionError("nested os_release should be read-only")
