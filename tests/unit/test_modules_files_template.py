import os
import stat
from types import SimpleNamespace

import pytest

from kitup.errors import TaskFailed
from kitup.modules import files
from kitup.registry import Bundle

from .helpers import call_data, make_context, privileged_context, write_source
from .helpers import template_context as context


def bundle_context(tmp_path, *, vars=None, facts=None):
    bundle_dir = tmp_path / "bundles" / "base"
    (bundle_dir / "files").mkdir(parents=True)
    bundle = Bundle("base", path=bundle_dir)
    bundle.task("Task")(lambda: None)
    ctx = make_context(tmp_path, vars=vars, facts=facts)
    ctx.begin_task(bundle.tasks[0])
    return ctx, bundle_dir


def test_template_renders_vars_facts_and_variables(tmp_path):
    source = tmp_path / "template.j2"
    write_source(
        source,
        'port={{ vars.SSH_PORT }}\nhost={{ vars["HOSTNAME"] }}\nfact={{ facts.hostname }}\nuser={{ user }}\n',
    )
    dest = tmp_path / "rendered.conf"
    ctx = context(
        tmp_path,
        vars=SimpleNamespace(SSH_PORT=2222, HOSTNAME="example"),
        facts={"hostname": "example"},
    )

    files.template(source, dest, variables={"user": "ada"})

    assert dest.read_text() == "port=2222\nhost=example\nfact=example\nuser=ada\n"
    assert stat.S_IMODE(dest.stat().st_mode) == 0o644
    assert len(ctx.changes) == 1
    assert ctx.changes[0].action == "files.template"
    assert call_data(ctx.changes[0]) == {"dest": str(dest), "src": str(source)}


def test_template_is_idempotent(tmp_path):
    source = tmp_path / "template.j2"
    write_source(source, "value={{ vars.VALUE }}\n")
    dest = tmp_path / "rendered.conf"
    ctx = context(tmp_path, vars=SimpleNamespace(VALUE=1))

    files.template(source, dest)
    files.template(source, dest)

    assert dest.read_text() == "value=1\n"
    assert len(ctx.changes) == 1
    assert ctx.calls[-1].summary == f"ensure {dest} from {source}"


@pytest.mark.parametrize(
    ("template_text", "expected"),
    [
        ("line\n", b"line\n"),
        ("{% if true %}line{% endif %}\n", b"line\n"),
        ("{% if true %}line{% endif %}\n\n", b"line\n\n"),
    ],
    ids=["literal", "block-tag", "multiple"],
)
def test_template_preserves_trailing_newlines(tmp_path, template_text, expected):
    source = tmp_path / "template.j2"
    write_source(source, template_text)
    dest = tmp_path / "rendered.conf"
    context(tmp_path)

    files.template(source, dest)

    assert dest.read_bytes() == expected


def test_template_trims_newlines_after_block_tags(tmp_path):
    source = tmp_path / "plugins.j2"
    write_source(
        source,
        "{% for plugin in vars.plugins %}\n"
        "{% if plugin.enabled %}\n"
        "source {{ plugin.name }}\n"
        "{% endif %}\n"
        "{% endfor %}\n",
    )
    dest = tmp_path / "plugins.zsh"
    plugins = [
        SimpleNamespace(name="first", enabled=True),
        SimpleNamespace(name="second", enabled=True),
    ]
    context(tmp_path, vars=SimpleNamespace(plugins=plugins))

    files.template(source, dest)

    assert dest.read_text() == "source first\nsource second\n"


def test_template_can_preserve_newlines_after_block_tags(tmp_path):
    source = tmp_path / "template.j2"
    write_source(source, "{% if vars.ENABLED %}\nline\n{% endif %}\n")
    dest = tmp_path / "rendered.conf"
    context(tmp_path, vars=SimpleNamespace(ENABLED=True))

    files.template(source, dest, trim_blocks=False)

    assert dest.read_bytes() == b"\nline\n\n"


def test_template_can_strip_leading_whitespace_before_block_tags(tmp_path):
    source = tmp_path / "template.j2"
    write_source(source, "  {% if vars.ENABLED %}\nline\n  {% endif %}\n")
    dest = tmp_path / "rendered.conf"
    context(tmp_path, vars=SimpleNamespace(ENABLED=True))

    files.template(source, dest, lstrip_blocks=True)

    assert dest.read_bytes() == b"line\n"


@pytest.mark.parametrize("option", ["trim_blocks", "lstrip_blocks"])
def test_template_rejects_non_boolean_whitespace_options(tmp_path, option):
    source = tmp_path / "template.j2"
    write_source(source, "line\n")
    context(tmp_path)

    with pytest.raises(TaskFailed, match=f"template {option} must be a boolean"):
        files.template(source, tmp_path / "rendered.conf", **{option: "yes"})


def test_template_renders_empty_template(tmp_path):
    source = tmp_path / "template.j2"
    write_source(source, "")
    dest = tmp_path / "rendered.conf"
    context(tmp_path)

    files.template(source, dest)

    assert dest.read_text() == ""


def test_template_rejects_undefined_variable(tmp_path):
    source = tmp_path / "template.j2"
    write_source(source, "{{ missing }}\n")
    dest = tmp_path / "rendered.conf"
    context(tmp_path)

    with pytest.raises(TaskFailed, match="undefined variable"):
        files.template(source, dest)

    assert not dest.exists()


def test_template_rejects_undefined_var_field(tmp_path):
    source = tmp_path / "template.j2"
    write_source(source, "{{ vars.MISSING }}\n")
    dest = tmp_path / "rendered.conf"
    context(tmp_path, vars=SimpleNamespace(PRESENT="yes"))

    with pytest.raises(TaskFailed, match="undefined variable"):
        files.template(source, dest)

    assert not dest.exists()


def test_template_wraps_runtime_rendering_errors(tmp_path):
    source = tmp_path / "template.j2"
    write_source(source, "{{ 1 / 0 }}\n")
    context(tmp_path)

    with pytest.raises(TaskFailed, match="template rendering error"):
        files.template(source, tmp_path / "rendered.conf")


def test_template_reports_syntax_error_location(tmp_path):
    source = tmp_path / "template.j2"
    write_source(source, "{% if %}\n")
    context(tmp_path)

    with pytest.raises(TaskFailed) as caught:
        files.template(source, tmp_path / "rendered.conf")

    message = str(caught.value)
    assert "template syntax error" in message
    assert f"{source}:1" in message


@pytest.mark.parametrize(
    ("variables", "message"),
    [
        (["not", "a", "mapping"], "variables must be a mapping"),
        ({"bad-name": 1}, "valid identifier"),
        ({1: "value"}, "valid identifier"),
        ({"vars": {}}, "reserved"),
        ({"facts": {}}, "reserved"),
    ],
)
def test_template_rejects_invalid_variables(tmp_path, variables, message):
    source = tmp_path / "template.j2"
    write_source(source, "value\n")
    context(tmp_path)

    with pytest.raises(TaskFailed, match=message):
        files.template(source, tmp_path / "rendered.conf", variables=variables)


def test_template_rejects_non_boolean_sudo(tmp_path):
    source = tmp_path / "template.j2"
    write_source(source, "value\n")
    context(tmp_path)

    with pytest.raises(TaskFailed, match="template sudo must be a boolean"):
        files.template(source, tmp_path / "rendered.conf", sudo="yes")


def test_template_resolves_sources_relative_to_the_bundle(tmp_path):
    _ctx, bundle_dir = bundle_context(tmp_path)
    write_source(bundle_dir / "files" / "bare.j2", "bare\n")
    write_source(bundle_dir / "nested" / "relative.j2", "relative\n")

    files.template("bare.j2", tmp_path / "bare.out")
    files.template("nested/relative.j2", tmp_path / "relative.out")
    files.template(
        bundle_dir / "nested" / "relative.j2",
        tmp_path / "absolute.out",
    )

    assert (tmp_path / "bare.out").read_text() == "bare\n"
    assert (tmp_path / "relative.out").read_text() == "relative\n"
    assert (tmp_path / "absolute.out").read_text() == "relative\n"


def test_template_allows_bundle_context(tmp_path):
    _ctx, bundle_dir = bundle_context(
        tmp_path,
        vars=SimpleNamespace(NAME="kitup"),
        facts={"hostname": "example"},
    )
    write_source(bundle_dir / "files" / "template.j2", "{{ vars.NAME }}@{{ facts.hostname }}\n")

    files.template("template.j2", tmp_path / "out")

    assert (tmp_path / "out").read_text() == "kitup@example\n"


def test_template_requires_regular_source(tmp_path):
    context(tmp_path)
    dest = tmp_path / "rendered.conf"

    with pytest.raises(TaskFailed, match="does not exist"):
        files.template(tmp_path / "missing.j2", dest)

    directory = tmp_path / "source-dir"
    directory.mkdir()
    with pytest.raises(TaskFailed, match="directory"):
        files.template(directory, dest)

    source = tmp_path / "source.j2"
    source.write_text("value\n")
    link = tmp_path / "link.j2"
    link.symlink_to(source)
    with pytest.raises(TaskFailed, match="symlink"):
        files.template(link, dest)

    assert not dest.exists()


def test_template_rejects_non_utf8_source(tmp_path):
    source = tmp_path / "template.j2"
    source.write_bytes(b"\xff\xfe")
    context(tmp_path)

    with pytest.raises(TaskFailed, match="UTF-8"):
        files.template(source, tmp_path / "rendered.conf")


def test_template_requires_parent_directory(tmp_path):
    source = tmp_path / "template.j2"
    write_source(source, "value\n")
    context(tmp_path)

    with pytest.raises(TaskFailed, match="parent directory does not exist"):
        files.template(source, tmp_path / "missing" / "rendered.conf")


def test_template_defers_parent_directory_check_in_dry_run(tmp_path):
    # An earlier task may create the parent in a real run, so a dry run reports
    # the render without requiring the parent to exist yet.
    source = tmp_path / "template.j2"
    write_source(source, "value\n")
    ctx = context(tmp_path, dry_run=True)

    files.template(source, tmp_path / "missing" / "rendered.conf")

    assert not (tmp_path / "missing").exists()
    assert len(ctx.changes) == 1
    assert ctx.changes[0].changed


def test_template_refuses_directory_destination(tmp_path):
    source = tmp_path / "template.j2"
    write_source(source, "value\n")
    context(tmp_path)
    dest = tmp_path / "dest"
    dest.mkdir()

    with pytest.raises(TaskFailed, match="directory"):
        files.template(source, dest)


def test_template_replaces_destination_symlink_without_following_it(tmp_path):
    source = tmp_path / "template.j2"
    write_source(source, "managed\n")
    target = tmp_path / "target"
    target.write_text("target content\n")
    link = tmp_path / "link"
    link.symlink_to(target)
    context(tmp_path)

    files.template(source, link)

    assert not link.is_symlink()
    assert link.read_text() == "managed\n"
    assert target.read_text() == "target content\n"


def test_template_preserves_existing_mode_and_applies_explicit_mode(tmp_path):
    source = tmp_path / "template.j2"
    write_source(source, "value\n")
    dest = tmp_path / "rendered.conf"
    dest.write_text("value\n")
    dest.chmod(0o600)
    ctx = context(tmp_path)

    files.template(source, dest)
    assert len(ctx.changes) == 0
    assert stat.S_IMODE(dest.stat().st_mode) == 0o600

    files.template(source, dest, mode=0o640)
    assert len(ctx.changes) == 1
    assert ctx.changes[0].action == "files.template"
    assert stat.S_IMODE(dest.stat().st_mode) == 0o640


def test_template_accepts_octal_string_mode(tmp_path):
    source = tmp_path / "template.j2"
    write_source(source, "value\n")
    dest = tmp_path / "rendered.conf"
    context(tmp_path)

    files.template(source, dest, mode="0640")

    assert stat.S_IMODE(dest.stat().st_mode) == 0o640


def test_template_reports_metadata_only_change(tmp_path):
    source = tmp_path / "template.j2"
    write_source(source, "same\n")
    dest = tmp_path / "rendered.conf"
    dest.write_text("same\n")
    dest.chmod(0o644)
    ctx = context(tmp_path)

    files.template(source, dest, mode=0o600)

    assert dest.read_text() == "same\n"
    assert stat.S_IMODE(dest.stat().st_mode) == 0o600
    assert len(ctx.changes) == 1


def test_dry_run_reports_without_writing(tmp_path):
    source = tmp_path / "template.j2"
    write_source(source, "value={{ vars.VALUE }}\n")
    dest = tmp_path / "rendered.conf"
    ctx = context(tmp_path, vars=SimpleNamespace(VALUE=1), dry_run=True)

    files.template(source, dest)

    assert not dest.exists()
    assert len(ctx.changes) == 1


def test_dry_run_still_reports_template_errors(tmp_path):
    source = tmp_path / "template.j2"
    write_source(source, "{{ missing }}\n")
    context(tmp_path, dry_run=True)

    with pytest.raises(TaskFailed, match="undefined variable"):
        files.template(source, tmp_path / "rendered.conf")


def test_dry_run_validates_metadata_identities(tmp_path):
    source = tmp_path / "template.j2"
    write_source(source, "value\n")
    context(tmp_path, dry_run=True)

    with pytest.raises(TaskFailed, match="unknown user"):
        files.template(source, tmp_path / "out", owner="kitup-missing-user")

    with pytest.raises(TaskFailed, match="unknown group"):
        files.template(source, tmp_path / "out", group="kitup-missing-group")


def test_diff_buffers_unified_diff(tmp_path):
    source = tmp_path / "template.j2"
    write_source(source, "new\n")
    dest = tmp_path / "rendered.conf"
    dest.write_text("old\n")
    ctx = context(tmp_path, diff=True)

    files.template(source, dest)

    assert any(line.startswith("---") for line in ctx.diffs)
    assert "-old" in ctx.diffs
    assert "+new" in ctx.diffs


def test_privileged_write_uses_worker(tmp_path):
    source = tmp_path / "template.j2"
    write_source(source, "value={{ vars.VALUE }}\n")
    dest = tmp_path / "root-owned.conf"
    ctx = privileged_context(tmp_path)
    ctx.vars = SimpleNamespace(VALUE=1)

    try:
        files.template(source, dest, mode=0o600, sudo=True)

        assert ctx.sudo.refresh_count > 0
        assert dest.read_text() == "value=1\n"
        assert stat.S_IMODE(dest.stat().st_mode) == 0o600
    finally:
        ctx.close()


def test_template_accepts_path_objects_and_numeric_metadata(tmp_path):
    source = tmp_path / "template.j2"
    write_source(source, "value\n")
    dest = tmp_path / "rendered.conf"
    context(tmp_path)

    files.template(source, dest, owner=os.getuid(), group=os.getgid())

    assert dest.read_text() == "value\n"
