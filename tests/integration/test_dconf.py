"""Integration tests for real dconf behavior under a D-Bus session."""

from collections.abc import Iterator

import pytest

from .helpers import SCHEMA, SCHEMA_DEFAULT, SCHEMA_KEY, run, run_kitup, write_bundle

pytestmark = pytest.mark.integration

PREFIX = "/org/kitup/IntegrationTestDconf"
SCHEMA_PATH = f"/{SCHEMA.replace('.', '/')}"


@pytest.fixture(autouse=True)
def reset_keys() -> Iterator[None]:
    for path in (f"{PREFIX}/", f"{SCHEMA_PATH}/"):
        run(["dconf", "reset", "-f", path], check=False)
    yield
    for path in (f"{PREFIX}/", f"{SCHEMA_PATH}/"):
        run(["dconf", "reset", "-f", path], check=False)


def read_key(key: str) -> str:
    """Return the canonical value of a key under the test prefix."""
    return run(["dconf", "read", f"{PREFIX}/{key}"]).stdout.strip()


def test_set_read_dump_and_reset_round_trip(tmp_path, capsys) -> None:
    root = write_bundle(
        tmp_path / "set",
        "dconf_set",
        f"""
        from kitup.modules import dconf

        @task("set dconf keys")
        def set_keys():
            dconf.write({{
                {PREFIX + "/string"!r}: "kitup-value",
                {PREFIX + "/uint"!r}: 5,
            }})

        @task("read dconf keys")
        def read_keys():
            ctx.note(repr(dconf.read([{PREFIX + "/string"!r}, {PREFIX + "/missing"!r}])))

        @task("dump dconf keys")
        def dump_keys():
            ctx.note(dconf.dump({PREFIX + "/"!r}))
        """,
    )

    assert run_kitup("apply", root) == 0
    out = capsys.readouterr().out
    assert "'kitup-value'" in out
    assert f"'{PREFIX}/missing': None" in out
    assert "string='kitup-value'" in out
    assert read_key("string") == "'kitup-value'"
    assert read_key("uint") == "5"

    assert run_kitup("apply", root) == 0
    assert "0 changed" in capsys.readouterr().out

    reset_root = write_bundle(
        tmp_path / "reset",
        "dconf_reset",
        f"""
        from kitup.modules import dconf

        @task("reset dconf key")
        def reset_key():
            dconf.write({PREFIX + "/string"!r}, state="absent")
        """,
    )

    assert run_kitup("apply", reset_root) == 0
    assert read_key("string") == ""
    assert read_key("uint") == "5"

    assert run_kitup("apply", reset_root) == 0
    assert "0 changed" in capsys.readouterr().out


def test_value_types_are_set_and_idempotent(tmp_path, capsys) -> None:
    root = write_bundle(
        tmp_path / "types",
        "dconf_types",
        f"""
        from kitup.modules import dconf

        @task("set typed dconf keys")
        def set_keys():
            dconf.write({{
                {PREFIX + "/bool"!r}: True,
                {PREFIX + "/int"!r}: 5,
                {PREFIX + "/double"!r}: 0.1,
                {PREFIX + "/list"!r}: [1, 2],
                {PREFIX + "/tuple"!r}: ("us", "no"),
                {PREFIX + "/dict"!r}: {{"a": "b"}},
                {PREFIX + "/bytes"!r}: b"abc",
                {PREFIX + "/variant"!r}: dconf.variant("<[1, 2]>"),
                {PREFIX + "/empty-list"!r}: dconf.variant("@as []"),
                {PREFIX + "/nothing"!r}: dconf.variant("@ms nothing"),
            }})
        """,
    )

    assert run_kitup("apply", root) == 0
    assert read_key("bool") == "true"
    assert read_key("int") == "5"
    assert read_key("double") == "0.10000000000000001"
    assert read_key("list") == "[1, 2]"
    assert read_key("tuple") == "('us', 'no')"
    assert read_key("dict") == "{'a': 'b'}"
    assert read_key("bytes") == "[97, 98, 99]"
    assert read_key("variant") == "<[1, 2]>"
    assert read_key("empty-list") == "@as []"
    assert read_key("nothing") == "@ms nothing"

    assert run_kitup("apply", root) == 0
    assert "0 changed" in capsys.readouterr().out


def test_load_sets_only_listed_keys_and_is_idempotent(tmp_path, capsys) -> None:
    run(["dconf", "write", f"{PREFIX}/other", "1"])
    content = "[/]\nstring='loaded'\nbool=true\n"
    root = write_bundle(
        tmp_path / "load",
        "dconf_load",
        f"""
        from kitup.modules import dconf

        @task("load dconf keys")
        def load_keys():
            dconf.load({PREFIX + "/"!r}, {content!r})
        """,
    )

    assert run_kitup("apply", root) == 0
    assert read_key("string") == "'loaded'"
    assert read_key("bool") == "true"
    assert read_key("other") == "1"

    assert run_kitup("apply", root) == 0
    assert "0 changed" in capsys.readouterr().out


def test_load_reads_a_source_file_using_project_and_bundle_lookup(tmp_path, capsys) -> None:
    root = write_bundle(
        tmp_path / "source",
        "dconf_source",
        f"""
        from kitup.modules import dconf

        @task("load dconf source")
        def load_source():
            dconf.load({PREFIX + "/"!r}, source="interface.dconf")
        """,
    )
    bundle_files = root / "bundles" / "dconf_source" / "files"
    bundle_files.mkdir(parents=True)
    (bundle_files / "interface.dconf").write_text("[/]\nstring='bundle'\n", encoding="utf-8")
    overlay = root / "files" / "dconf_source"
    overlay.mkdir(parents=True)
    (overlay / "interface.dconf").write_text("[/]\nstring='overlay'\n", encoding="utf-8")

    assert run_kitup("apply", root) == 0
    assert read_key("string") == "'overlay'"

    assert run_kitup("apply", root) == 0
    assert "0 changed" in capsys.readouterr().out


def test_apply_a_batch_of_desktop_settings(tmp_path, capsys) -> None:
    settings = {
        f"{PREFIX}/interface/color-scheme": "prefer-dark",
        f"{PREFIX}/interface/text-scaling-factor": 1.25,
        f"{PREFIX}/interface/clock-format": "24h",
        f"{PREFIX}/favorite-apps": ["firefox.desktop", "org.gnome.Terminal.desktop"],
        f"{PREFIX}/input-sources": [("xkb", "us"), ("xkb", "de")],
    }
    root = write_bundle(
        tmp_path / "desktop",
        "dconf_desktop",
        f"""
        from kitup.modules import dconf

        @task("apply desktop settings")
        def apply_settings():
            dconf.write({settings!r})
        """,
    )

    assert run_kitup("apply", root) == 0
    assert "1 changed" in capsys.readouterr().out
    assert read_key("interface/color-scheme") == "'prefer-dark'"
    assert read_key("interface/text-scaling-factor") == "1.25"
    assert read_key("interface/clock-format") == "'24h'"
    assert read_key("favorite-apps") == "['firefox.desktop', 'org.gnome.Terminal.desktop']"
    assert read_key("input-sources") == "[('xkb', 'us'), ('xkb', 'de')]"

    assert run_kitup("apply", root) == 0
    assert "0 changed" in capsys.readouterr().out


def test_reset_restores_the_schema_default(tmp_path, capsys) -> None:
    path = f"{SCHEMA_PATH}/{SCHEMA_KEY}"
    run(["dconf", "write", path, "'overridden'"])
    root = write_bundle(
        tmp_path / "default",
        "dconf_default",
        f"""
        from kitup.modules import dconf

        @task("reset overridden key")
        def reset_key():
            dconf.write({path!r}, state="absent")
        """,
    )

    assert run_kitup("apply", root) == 0
    assert "1 changed" in capsys.readouterr().out
    assert run(["dconf", "read", path]).stdout.strip() == ""
    assert run(["gsettings", "get", SCHEMA, SCHEMA_KEY]).stdout.strip() == SCHEMA_DEFAULT

    assert run_kitup("apply", root) == 0
    assert "0 changed" in capsys.readouterr().out


def test_dump_can_seed_another_subtree(tmp_path, capsys) -> None:
    source = f"{PREFIX}/source"
    target = f"{PREFIX}/target"
    run(["dconf", "write", f"{source}/name", "'kitup'"])
    run(["dconf", "write", f"{source}/enabled", "true"])
    run(["dconf", "write", f"{source}/nested/count", "3"])
    root = write_bundle(
        tmp_path / "clone",
        "dconf_clone",
        f"""
        from kitup.modules import dconf

        @task("clone dconf subtree")
        def clone_keys():
            dconf.load({target + "/"!r}, dconf.dump({source + "/"!r}))
        """,
    )

    assert run_kitup("apply", root) == 0
    assert "1 changed" in capsys.readouterr().out
    assert read_key("target/name") == "'kitup'"
    assert read_key("target/enabled") == "true"
    assert read_key("target/nested/count") == "3"

    assert run_kitup("apply", root) == 0
    assert "0 changed" in capsys.readouterr().out


def test_dry_run_does_not_write_settings(tmp_path, capsys) -> None:
    root = write_bundle(
        tmp_path / "dry",
        "dconf_dry",
        f"""
        from kitup.modules import dconf

        @task("apply a setting")
        def apply_setting():
            dconf.write({PREFIX + "/dry"!r}, "value")
        """,
    )

    assert run_kitup("apply", root, "--dry-run") == 0
    assert "1 would change" in capsys.readouterr().out
    assert read_key("dry") == ""

    assert run_kitup("apply", root) == 0
    assert read_key("dry") == "'value'"


def test_invalid_value_fails_before_writing(tmp_path) -> None:
    root = write_bundle(
        tmp_path / "invalid",
        "dconf_invalid",
        f"""
        from kitup.modules import dconf

        @task("write an untyped empty array")
        def write_invalid():
            dconf.write({PREFIX + "/empty"!r}, [])
        """,
    )

    assert run_kitup("apply", root) != 0
    assert read_key("empty") == ""
