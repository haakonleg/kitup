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


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])

    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == "kitup 0.1.0"


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


def test_missing_bundles_is_exit_2(tmp_path, capsys):
    code = cli.main(["list", "-D", str(tmp_path)])

    assert code == 2
    assert "error: no bundles found" in capsys.readouterr().err


def test_list_output_and_verbose_details(tmp_path, capsys):
    make_bundle(tmp_path, "base", tasks="""
        from . import bundle

        @bundle.task("Install packages", needs_sudo=True, tags={"packages"},
              description="Install the base package set")
        def install(ctx):
            pass
    """)
    make_bundle(tmp_path, "desktop", depends=("base",), tasks="""
        from . import bundle

        @bundle.task("Desktop")
        def desktop(ctx):
            pass
    """)

    assert cli.main(["list", "-D", str(tmp_path)]) == 0
    plain = capsys.readouterr().out
    assert "bundle: base" in plain
    assert "bundle: desktop" in plain
    assert "depends:" not in plain

    assert cli.main(["list", "-v", "-D", str(tmp_path)]) == 0
    verbose = capsys.readouterr().out
    assert "depends: base" in verbose
    assert "Install packages  [sudo] tags: packages" in verbose
    assert "id: base.install" in verbose
    assert "description: Install the base package set" in verbose
    assert "Desktop  tags: -" in verbose


def test_apply_dry_run_with_tag_filter(tmp_path, capsys):
    make_bundle(tmp_path, "base", tasks="""
        from . import bundle

        @bundle.task("Packages", tags={"packages"})
        def packages(ctx):
            ctx.changed({"added": ["git"]}, "+git")

        @bundle.task("Config", tags={"config"})
        def config(ctx):
            ctx.changed({"file": "x"}, "file changed")
    """)

    code = cli.main(["apply", "-n", "-t", "config", "-D", str(tmp_path)])

    out = capsys.readouterr().out
    assert code == 0
    assert "Config" in out
    assert "Packages" not in out
    assert "file changed" in out
    assert "Summary: 1 changed, 0 ok, 0 failed, 0 not run" in out


def test_empty_tag_filter_selects_no_tasks(tmp_path, capsys):
    make_bundle(tmp_path, "base", tasks="""
        from . import bundle

        @bundle.task("Should not run", tags={"packages"})
        def package_task(ctx):
            ctx.changed({"added": ["git"]}, "+git")
    """)

    code = cli.main(["apply", "-n", "-t", "", "-D", str(tmp_path)])

    out = capsys.readouterr().out
    assert code == 0
    assert "Should not run" not in out
    assert "Summary: 0 changed, 0 ok, 0 failed, 0 not run" in out


def test_empty_skip_tag_filter_is_a_noop(tmp_path, capsys):
    make_bundle(tmp_path, "base", tasks="""
        from . import bundle

        @bundle.task("Should run")
        def normal_task(ctx):
            pass
    """)

    code = cli.main(["apply", "-x", " , ", "-D", str(tmp_path)])

    out = capsys.readouterr().out
    assert code == 0
    assert "Should run" in out
    assert "Summary: 0 changed, 1 ok, 0 failed, 0 not run" in out


def test_tag_filter_with_no_matches_runs_no_tasks(tmp_path, capsys):
    make_bundle(tmp_path, "base", tasks="""
        from . import bundle

        @bundle.task("Should not run", tags={"packages"})
        def package_task(ctx):
            ctx.changed({"added": ["git"]}, "+git")
    """)

    code = cli.main(["apply", "-n", "-t", "missing", "-D", str(tmp_path)])

    out = capsys.readouterr().out
    assert code == 0
    assert "Should not run" not in out
    assert "Summary: 0 changed, 0 ok, 0 failed, 0 not run" in out


def test_apply_prints_file_diffs(tmp_path, capsys):
    target = tmp_path / "managed.txt"
    target.write_text("old\n")
    (tmp_path / "config.py").write_text(f"TARGET = {str(target)!r}\n")
    make_bundle(tmp_path, "base", tasks="""
        from . import bundle
        from kitup.modules import files

        @bundle.task("Write file")
        def write_file(ctx):
            files.file(ctx, ctx.config.TARGET, content="new\\n")
    """)

    code = cli.main(["apply", "-n", "-d", "-D", str(tmp_path)])

    out = capsys.readouterr().out
    assert code == 0
    assert "-old" in out
    assert "+new" in out
    assert target.read_text() == "old\n"


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
