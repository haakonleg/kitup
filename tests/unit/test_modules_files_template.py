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


def test_template_preserves_trailing_newlines(tmp_path):
    context(tmp_path)
    cases = [
        ("line\n", b"line\n", "literal"),
        ("{% if true %}line{% endif %}\n", b"line\n", "block-tag"),
        ("{% if true %}line{% endif %}\n\n", b"line\n\n", "multiple"),
    ]
    for template_text, expected, label in cases:
        source = tmp_path / "template.j2"
        write_source(source, template_text)
        dest = tmp_path / "rendered.conf"

        files.template(source, dest)

        assert dest.read_bytes() == expected, label


def test_template_whitespace_controls(tmp_path):
    # Default trimming removes newlines after block tags.
    source = tmp_path / "plugins.j2"
    write_source(
        source,
        "{% for plugin in vars.plugins %}\n"
        "{% if plugin.enabled %}\n"
        "source {{ plugin.name }}\n"
        "{% endif %}\n"
        "{% endfor %}\n",
    )
    plugins = [
        SimpleNamespace(name="first", enabled=True),
        SimpleNamespace(name="second", enabled=True),
    ]
    context(tmp_path, vars=SimpleNamespace(plugins=plugins))

    files.template(source, tmp_path / "plugins.zsh")

    assert (tmp_path / "plugins.zsh").read_text() == "source first\nsource second\n"

    # trim_blocks=False preserves the newline after a block tag.
    source = tmp_path / "preserve.j2"
    write_source(source, "{% if vars.ENABLED %}\nline\n{% endif %}\n")
    context(tmp_path, vars=SimpleNamespace(ENABLED=True))

    files.template(source, tmp_path / "preserve.out", trim_blocks=False)

    assert (tmp_path / "preserve.out").read_bytes() == b"\nline\n\n"

    # lstrip_blocks strips leading whitespace before block tags.
    source = tmp_path / "lstrip.j2"
    write_source(source, "  {% if vars.ENABLED %}\nline\n  {% endif %}\n")
    context(tmp_path, vars=SimpleNamespace(ENABLED=True))

    files.template(source, tmp_path / "lstrip.out", lstrip_blocks=True)

    assert (tmp_path / "lstrip.out").read_bytes() == b"line\n"


def test_template_rejects_non_boolean_whitespace_options(tmp_path):
    source = tmp_path / "template.j2"
    write_source(source, "line\n")
    context(tmp_path)

    for option in ["trim_blocks", "lstrip_blocks"]:
        with pytest.raises(TaskFailed, match=f"template {option} must be a boolean"):
            files.template(source, tmp_path / "rendered.conf", **{option: "yes"})


def test_template_rendering_errors(tmp_path):
    context(tmp_path)

    # Undefined variables fail without creating the destination.
    source = tmp_path / "undefined.j2"
    write_source(source, "{{ missing }}\n")
    dest = tmp_path / "rendered.conf"
    with pytest.raises(TaskFailed, match="undefined variable"):
        files.template(source, dest)
    assert not dest.exists()

    # Runtime rendering errors are wrapped as task failures.
    source = tmp_path / "runtime.j2"
    write_source(source, "{{ 1 / 0 }}\n")
    with pytest.raises(TaskFailed, match="template rendering error"):
        files.template(source, tmp_path / "runtime.conf")

    # Syntax errors report the source file and line.
    source = tmp_path / "syntax.j2"
    write_source(source, "{% if %}\n")
    with pytest.raises(TaskFailed) as caught:
        files.template(source, tmp_path / "syntax.conf")

    message = str(caught.value)
    assert "template syntax error" in message
    assert f"{source}:1" in message


def test_template_rejects_invalid_variables(tmp_path):
    source = tmp_path / "template.j2"
    write_source(source, "value\n")
    context(tmp_path)
    cases = [
        (["not", "a", "mapping"], "variables must be a mapping"),
        ({"bad-name": 1}, "valid identifier"),
        ({1: "value"}, "valid identifier"),
        ({"vars": {}}, "reserved"),
        ({"facts": {}}, "reserved"),
    ]

    for variables, message in cases:
        try:
            files.template(source, tmp_path / "rendered.conf", variables=variables)
        except TaskFailed as exc:
            assert message in str(exc), variables
        else:
            pytest.fail(f"expected TaskFailed for variables={variables!r}")


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


def test_template_validates_source(tmp_path):
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

    binary = tmp_path / "binary.j2"
    binary.write_bytes(b"\xff\xfe")
    with pytest.raises(TaskFailed, match="UTF-8"):
        files.template(binary, dest)

    assert not dest.exists()


def test_template_validates_destination(tmp_path):
    source = tmp_path / "template.j2"
    write_source(source, "value\n")
    context(tmp_path)

    with pytest.raises(TaskFailed, match="parent directory does not exist"):
        files.template(source, tmp_path / "missing" / "rendered.conf")

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


def test_dry_run(tmp_path):
    source = tmp_path / "template.j2"
    write_source(source, "value={{ vars.VALUE }}\n")
    dest = tmp_path / "rendered.conf"
    ctx = context(tmp_path, vars=SimpleNamespace(VALUE=1), dry_run=True)

    # A dry run reports the change without writing it.
    files.template(source, dest)

    assert not dest.exists()
    assert len(ctx.changes) == 1

    # Rendering errors are still reported in dry-run mode.
    write_source(source, "{{ missing }}\n")
    with pytest.raises(TaskFailed, match="undefined variable"):
        files.template(source, dest)

    # Metadata identities are validated even though nothing is written.
    write_source(source, "value\n")
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
