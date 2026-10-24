from __future__ import annotations

import textwrap

import pytest

from kitup import cli
from kitup.errors import PrivilegeError


def write(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


def make_bundle(root, folder, *, name=None, depends=(), tasks=""):
    name = name or folder
    write(
        root / "bundles" / folder / "__init__.py",
        f"""
        from kitup.registry import Bundle

        bundle = Bundle({name!r}, "description", depends_on={tuple(depends)!r})

        from . import tasks  # noqa: E402,F401
        """,
    )
    write(root / "bundles" / folder / "tasks.py", tasks)


@pytest.fixture(autouse=True)
def fake_facts(monkeypatch):
    monkeypatch.setattr(
        cli,
        "collect_facts",
        lambda: {
            "hostname": "host",
            "distro_id": "testos",
            "version_id": "1",
            "os_release": {
                "NAME": "Test Linux",
                "ID": "testos",
                "VERSION_ID": "1",
                "VARIANT": "Workstation Edition",
            },
        },
    )


def test_all_apply_short_flags_parse():
    args = cli.build_parser().parse_args(
        [
            "apply",
            "-n",
            "-d",
            "-s",
            "-t",
            "one,two",
            "-t",
            "three",
            "-x",
            "skip",
            "-D",
            "root",
            "-C",
            "override.py",
            "-v",
            "-c",
            "desktop",
        ]
    )

    assert args.dry_run and args.diff and args.sudo and args.verbose and args.no_color
    assert args.tags == ["one,two", "three"]
    assert args.skip_tags == ["skip"]
    assert args.dir == "root"
    assert args.config == "override.py"
    assert args.bundles == ["desktop"]


def test_config_dir_resolution_uses_process_cwd(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.chdir(tmp_path)

    assert cli._resolve_config_dir(None) == tmp_path.resolve()
    assert cli._resolve_config_dir("config") == config_dir.resolve()
    assert cli._resolve_config_dir(str(config_dir)) == config_dir.resolve()


def test_missing_bundles_is_exit_2(tmp_path, capsys):
    code = cli.main(["list", "-D", str(tmp_path)])

    assert code == 2
    assert "error: no bundles found" in capsys.readouterr().err


def test_apply_uses_override_config(tmp_path, capsys):
    (tmp_path / "config.py").write_text("VALUE = 'base'\n")
    override = tmp_path / "work.py"
    override.write_text("VALUE = 'work'\n")
    make_bundle(tmp_path, "base", tasks="""
        from . import bundle

        @bundle.task("Show config")
        def show(ctx):
            ctx.changed({"value": ctx.config.VALUE}, f"value={ctx.config.VALUE}")
    """)

    code = cli.main(["apply", "-n", "-C", str(override), "-D", str(tmp_path)])

    assert code == 0
    assert "value=work" in capsys.readouterr().out


def test_unknown_bundle_is_exit_2(tmp_path, capsys):
    make_bundle(tmp_path, "base")

    code = cli.main(["apply", "-D", str(tmp_path), "missing"])

    assert code == 2
    assert "error: unknown bundle: missing" in capsys.readouterr().err


def test_sudo_validation_failure_is_exit_2(tmp_path, capsys, monkeypatch):
    make_bundle(tmp_path, "base")

    def fail_validation(self):
        raise PrivilegeError("no credentials")

    monkeypatch.setattr(cli.PrivilegeManager, "validate", fail_validation)

    code = cli.main(["apply", "-s", "-D", str(tmp_path)])

    assert code == 2
    assert "error: no credentials" in capsys.readouterr().err


def test_missing_override_is_exit_2(tmp_path, capsys):
    make_bundle(tmp_path, "base")

    code = cli.main(["list", "-D", str(tmp_path), "-C", str(tmp_path / "missing.py")])

    assert code == 2
    assert "error: config override not found" in capsys.readouterr().err
