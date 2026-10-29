from __future__ import annotations

import os
import stat
from io import StringIO
from types import SimpleNamespace

import pytest

from kitup.commands import CommandRunner
from kitup.errors import TaskFailed
from kitup.modules import template
from kitup.output import Output
from kitup.registry import Bundle

from .helpers import PassthroughSudo, make_context


def context(tmp_path, *, config=None, facts=None, dry_run=False, diff=False):
    return make_context(
        tmp_path,
        dry_run=dry_run,
        diff=diff,
        config=config,
        facts=facts,
        output=Output(StringIO(), no_color=True),
    )


def privileged_context(tmp_path):
    sudo = PassthroughSudo()
    return make_context(
        tmp_path,
        command=CommandRunner(sudo),
        sudo=sudo,
        output=Output(StringIO(), no_color=True),
    )


def bundle_context(tmp_path, *, config=None, facts=None):
    bundle_dir = tmp_path / "bundles" / "base"
    (bundle_dir / "files").mkdir(parents=True)
    bundle = Bundle("base", path=bundle_dir)
    bundle.task("Task")(lambda ctx: None)
    ctx = make_context(tmp_path, config=config, facts=facts)
    ctx.begin_task(bundle.tasks[0])
    return ctx, bundle_dir


def write_source(path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_file_renders_config_facts_and_variables(tmp_path):
    source = tmp_path / "template.j2"
    write_source(
        source,
        "port={{ config.SSH_PORT }}\n"
        'host={{ config["HOSTNAME"] }}\n'
        "fact={{ facts.hostname }}\n"
        "user={{ user }}\n",
    )
    dest = tmp_path / "rendered.conf"
    ctx = context(
        tmp_path,
        config=SimpleNamespace(SSH_PORT=2222, HOSTNAME="example"),
        facts={"hostname": "example"},
    )

    template.file(ctx, source, dest, variables={"user": "ada"})

    assert dest.read_text() == "port=2222\nhost=example\nfact=example\nuser=ada\n"
    assert stat.S_IMODE(dest.stat().st_mode) == 0o644
    assert len(ctx.changes) == 1
    assert ctx.changes[0].data["action"] == "template"
    assert ctx.changes[0].data["source"] == str(source)


def test_file_is_idempotent(tmp_path):
    source = tmp_path / "template.j2"
    write_source(source, "value={{ config.VALUE }}\n")
    dest = tmp_path / "rendered.conf"
    ctx = context(tmp_path, config=SimpleNamespace(VALUE=1))

    template.file(ctx, source, dest)
    template.file(ctx, source, dest)

    assert dest.read_text() == "value=1\n"
    assert len(ctx.changes) == 1


def test_file_preserves_trailing_newline(tmp_path):
    source = tmp_path / "template.j2"
    write_source(source, "line\n")
    dest = tmp_path / "rendered.conf"
    ctx = context(tmp_path)

    template.file(ctx, source, dest)

    assert dest.read_bytes() == b"line\n"


def test_file_renders_empty_template(tmp_path):
    source = tmp_path / "template.j2"
    write_source(source, "")
    dest = tmp_path / "rendered.conf"
    ctx = context(tmp_path)

    template.file(ctx, source, dest)

    assert dest.read_text() == ""


def test_file_rejects_undefined_variable(tmp_path):
    source = tmp_path / "template.j2"
    write_source(source, "{{ missing }}\n")
    dest = tmp_path / "rendered.conf"
    ctx = context(tmp_path)

    with pytest.raises(TaskFailed, match="undefined variable"):
        template.file(ctx, source, dest)

    assert not dest.exists()


def test_file_rejects_undefined_config_field(tmp_path):
    source = tmp_path / "template.j2"
    write_source(source, "{{ config.MISSING }}\n")
    dest = tmp_path / "rendered.conf"
    ctx = context(tmp_path, config=SimpleNamespace(PRESENT="yes"))

    with pytest.raises(TaskFailed, match="undefined variable"):
        template.file(ctx, source, dest)

    assert not dest.exists()


def test_file_reports_syntax_error_location(tmp_path):
    source = tmp_path / "template.j2"
    write_source(source, "{% if %}\n")
    ctx = context(tmp_path)

    with pytest.raises(TaskFailed) as caught:
        template.file(ctx, source, tmp_path / "rendered.conf")

    message = str(caught.value)
    assert "template syntax error" in message
    assert f"{source}:1" in message


@pytest.mark.parametrize(
    ("variables", "message"),
    [
        (["not", "a", "mapping"], "variables must be a mapping"),
        ({"bad-name": 1}, "valid identifier"),
        ({1: "value"}, "valid identifier"),
        ({"config": {}}, "reserved"),
        ({"facts": {}}, "reserved"),
    ],
)
def test_file_rejects_invalid_variables(tmp_path, variables, message):
    source = tmp_path / "template.j2"
    write_source(source, "value\n")
    ctx = context(tmp_path)

    with pytest.raises(TaskFailed, match=message):
        template.file(ctx, source, tmp_path / "rendered.conf", variables=variables)


def test_file_resolves_sources_relative_to_the_bundle(tmp_path):
    ctx, bundle_dir = bundle_context(tmp_path)
    write_source(bundle_dir / "files" / "bare.j2", "bare\n")
    write_source(bundle_dir / "nested" / "relative.j2", "relative\n")

    template.file(ctx, "bare.j2", tmp_path / "bare.out")
    template.file(ctx, "nested/relative.j2", tmp_path / "relative.out")
    template.file(
        ctx,
        bundle_dir / "nested" / "relative.j2",
        tmp_path / "absolute.out",
    )

    assert (tmp_path / "bare.out").read_text() == "bare\n"
    assert (tmp_path / "relative.out").read_text() == "relative\n"
    assert (tmp_path / "absolute.out").read_text() == "relative\n"


def test_file_allows_bundle_context(tmp_path):
    ctx, bundle_dir = bundle_context(
        tmp_path,
        config=SimpleNamespace(NAME="kitup"),
        facts={"hostname": "example"},
    )
    write_source(bundle_dir / "files" / "template.j2", "{{ config.NAME }}@{{ facts.hostname }}\n")

    template.file(ctx, "template.j2", tmp_path / "out")

    assert (tmp_path / "out").read_text() == "kitup@example\n"


def test_file_requires_regular_source(tmp_path):
    ctx = context(tmp_path)
    dest = tmp_path / "rendered.conf"

    with pytest.raises(TaskFailed, match="does not exist"):
        template.file(ctx, tmp_path / "missing.j2", dest)

    directory = tmp_path / "source-dir"
    directory.mkdir()
    with pytest.raises(TaskFailed, match="not a regular file"):
        template.file(ctx, directory, dest)

    source = tmp_path / "source.j2"
    source.write_text("value\n")
    link = tmp_path / "link.j2"
    link.symlink_to(source)
    with pytest.raises(TaskFailed, match="symlink"):
        template.file(ctx, link, dest)

    assert not dest.exists()


def test_file_rejects_non_utf8_source(tmp_path):
    source = tmp_path / "template.j2"
    source.write_bytes(b"\xff\xfe")
    ctx = context(tmp_path)

    with pytest.raises(TaskFailed, match="UTF-8"):
        template.file(ctx, source, tmp_path / "rendered.conf")


def test_file_requires_parent_directory(tmp_path):
    source = tmp_path / "template.j2"
    write_source(source, "value\n")
    ctx = context(tmp_path)

    with pytest.raises(TaskFailed, match="parent directory does not exist"):
        template.file(ctx, source, tmp_path / "missing" / "rendered.conf")


def test_file_refuses_directory_destination(tmp_path):
    source = tmp_path / "template.j2"
    write_source(source, "value\n")
    ctx = context(tmp_path)
    dest = tmp_path / "dest"
    dest.mkdir()

    with pytest.raises(TaskFailed, match="directory"):
        template.file(ctx, source, dest)


def test_file_replaces_destination_symlink_without_following_it(tmp_path):
    source = tmp_path / "template.j2"
    write_source(source, "managed\n")
    target = tmp_path / "target"
    target.write_text("target content\n")
    link = tmp_path / "link"
    link.symlink_to(target)
    ctx = context(tmp_path)

    template.file(ctx, source, link)

    assert not link.is_symlink()
    assert link.read_text() == "managed\n"
    assert target.read_text() == "target content\n"


def test_file_preserves_existing_mode_and_applies_explicit_mode(tmp_path):
    source = tmp_path / "template.j2"
    write_source(source, "value\n")
    dest = tmp_path / "rendered.conf"
    dest.write_text("value\n")
    dest.chmod(0o600)
    ctx = context(tmp_path)

    template.file(ctx, source, dest)
    assert len(ctx.changes) == 0
    assert stat.S_IMODE(dest.stat().st_mode) == 0o600

    template.file(ctx, source, dest, mode=0o640)
    assert len(ctx.changes) == 1
    assert ctx.changes[0].data["action"] == "template"
    assert stat.S_IMODE(dest.stat().st_mode) == 0o640


def test_file_reports_metadata_only_change(tmp_path):
    source = tmp_path / "template.j2"
    write_source(source, "same\n")
    dest = tmp_path / "rendered.conf"
    dest.write_text("same\n")
    dest.chmod(0o644)
    ctx = context(tmp_path)

    template.file(ctx, source, dest, mode=0o600)

    assert dest.read_text() == "same\n"
    assert stat.S_IMODE(dest.stat().st_mode) == 0o600
    assert len(ctx.changes) == 1


def test_dry_run_reports_without_writing(tmp_path):
    source = tmp_path / "template.j2"
    write_source(source, "value={{ config.VALUE }}\n")
    dest = tmp_path / "rendered.conf"
    ctx = context(tmp_path, config=SimpleNamespace(VALUE=1), dry_run=True)

    template.file(ctx, source, dest)

    assert not dest.exists()
    assert len(ctx.changes) == 1


def test_dry_run_still_reports_template_errors(tmp_path):
    source = tmp_path / "template.j2"
    write_source(source, "{{ missing }}\n")
    ctx = context(tmp_path, dry_run=True)

    with pytest.raises(TaskFailed, match="undefined variable"):
        template.file(ctx, source, tmp_path / "rendered.conf")


def test_dry_run_validates_metadata_identities(tmp_path):
    source = tmp_path / "template.j2"
    write_source(source, "value\n")
    ctx = context(tmp_path, dry_run=True)

    with pytest.raises(TaskFailed, match="unknown user"):
        template.file(ctx, source, tmp_path / "out", owner="kitup-missing-user")

    with pytest.raises(TaskFailed, match="unknown group"):
        template.file(ctx, source, tmp_path / "out", group="kitup-missing-group")


def test_diff_prints_unified_diff(tmp_path):
    source = tmp_path / "template.j2"
    write_source(source, "new\n")
    dest = tmp_path / "rendered.conf"
    dest.write_text("old\n")
    ctx = context(tmp_path, diff=True)

    template.file(ctx, source, dest)

    stream = ctx.output.stream
    output = stream.getvalue()
    assert "---" in output
    assert "-old" in output
    assert "+new" in output


def test_privileged_write_uses_worker(tmp_path):
    source = tmp_path / "template.j2"
    write_source(source, "value={{ config.VALUE }}\n")
    dest = tmp_path / "root-owned.conf"
    ctx = privileged_context(tmp_path)
    ctx.config = SimpleNamespace(VALUE=1)

    try:
        template.file(ctx, source, dest, mode=0o600, sudo=True)

        assert ctx.sudo.refresh_count > 0
        assert dest.read_text() == "value=1\n"
        assert stat.S_IMODE(dest.stat().st_mode) == 0o600
    finally:
        ctx.close()


def test_file_accepts_path_objects_and_numeric_metadata(tmp_path):
    source = tmp_path / "template.j2"
    write_source(source, "value\n")
    dest = tmp_path / "rendered.conf"
    ctx = context(tmp_path)

    template.file(ctx, source, dest, owner=os.getuid(), group=os.getgid())

    assert dest.read_text() == "value\n"
