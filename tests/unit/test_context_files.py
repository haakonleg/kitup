"""Tests for the merged ``ctx.files`` view and module source resolution."""

from pathlib import Path

import pytest

from kitup._utils.paths import OverlayPath, real_path
from kitup.errors import TaskFailed
from kitup.modules import files
from kitup.project import Project
from kitup.registry import Bundle

from .helpers import local_sudo_context, make_context


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def project(tmp_path, *, context=None):
    bundle_dir = tmp_path / "bundles" / "base"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    bundle = Bundle("base", path=bundle_dir)
    bundle.task("Task")(lambda: None)
    ctx = context or make_context(tmp_path)
    ctx.begin_task(bundle.tasks[0])
    return ctx, bundle_dir


def test_files_merges_the_overlay_over_the_bundle(tmp_path):
    ctx, bundle_dir = project(tmp_path)
    write(bundle_dir / "files" / "shared.conf", "bundle\n")
    write(bundle_dir / "files" / "bundle-only.conf", "bundle\n")
    write(tmp_path / "files" / "base" / "shared.conf", "overlay\n")
    write(tmp_path / "files" / "base" / "overlay-only.conf", "overlay\n")

    merged = ctx.files

    assert (merged / "shared.conf").read_text(encoding="utf-8") == "overlay\n"
    assert (merged / "bundle-only.conf").read_text(encoding="utf-8") == "bundle\n"
    assert (merged / "overlay-only.conf").read_text(encoding="utf-8") == "overlay\n"
    assert sorted(entry.name for entry in merged.iterdir()) == [
        "bundle-only.conf",
        "overlay-only.conf",
        "shared.conf",
    ]

    child = merged / "nested" / "app.conf"
    assert isinstance(child, OverlayPath)
    assert isinstance(child.parent, OverlayPath)

    empty_root = tmp_path / "empty"
    empty_ctx, _ = project(empty_root)
    write(empty_root / "files" / "base" / "only.conf", "overlay\n")
    assert (empty_ctx.files / "only.conf").read_text(encoding="utf-8") == "overlay\n"
    assert [entry.name for entry in empty_ctx.files.iterdir()] == ["only.conf"]


def test_files_merges_extra_overlay_roots_with_later_roots_winning(tmp_path):
    extra_one = tmp_path / "extra-one"
    extra_two = tmp_path / "extra-two"
    write(extra_one / "base" / "shared.conf", "extra-one\n")
    write(extra_two / "base" / "shared.conf", "extra-two\n")
    write(extra_one / "base" / "one-only.conf", "one\n")
    write(extra_two / "base" / "two-only.conf", "two\n")
    context = make_context(tmp_path, project=Project(root=tmp_path, files_roots=(extra_one, extra_two)))
    ctx, bundle_dir = project(tmp_path, context=context)
    write(bundle_dir / "files" / "shared.conf", "bundle\n")
    write(bundle_dir / "files" / "bundle-only.conf", "bundle\n")

    assert (ctx.files / "shared.conf").read_text(encoding="utf-8") == "extra-two\n"
    assert (ctx.files / "one-only.conf").read_text(encoding="utf-8") == "one\n"
    assert (ctx.files / "two-only.conf").read_text(encoding="utf-8") == "two\n"
    assert (ctx.files / "bundle-only.conf").read_text(encoding="utf-8") == "bundle\n"


def test_files_overlay_shadows_a_bundle_directory(tmp_path):
    ctx, bundle_dir = project(tmp_path)
    write(bundle_dir / "files" / "entry" / "inside.conf", "bundle\n")
    write(tmp_path / "files" / "base" / "entry", "overlay\n")

    entry = ctx.files / "entry"

    assert entry.is_file()
    assert not entry.is_dir()
    assert entry.read_text(encoding="utf-8") == "overlay\n"
    with pytest.raises(NotADirectoryError):
        list(entry.iterdir())


def test_files_keeps_symlink_entries_from_the_winning_root(tmp_path):
    ctx, bundle_dir = project(tmp_path)
    (bundle_dir / "files").mkdir(parents=True)
    overlay = tmp_path / "files" / "base"
    overlay.mkdir(parents=True)
    (bundle_dir / "files" / "link").symlink_to("bundle-target")
    (overlay / "link").symlink_to("overlay-target")

    entry = ctx.files / "link"

    assert entry.is_symlink()
    assert entry.readlink() == Path("overlay-target")


def test_files_requires_a_bundle(tmp_path):
    ctx = make_context(tmp_path)

    with pytest.raises(TaskFailed, match="no owning bundle"):
        _ = ctx.files


def test_files_requires_at_least_one_root(tmp_path):
    ctx, _ = project(tmp_path)

    with pytest.raises(TaskFailed, match="no files for bundle 'base'"):
        _ = ctx.files


def test_files_view_is_read_only(tmp_path):
    ctx, bundle_dir = project(tmp_path)
    write(bundle_dir / "files" / "existing.conf", "value\n")
    merged = ctx.files

    actions = (
        merged.mkdir,
        merged.touch,
        merged.rmdir,
        lambda: (merged / "new.conf").write_text("x"),
        lambda: (merged / "new.conf").write_bytes(b"x"),
        lambda: (merged / "existing.conf").unlink(),
        lambda: (merged / "existing.conf").rename(merged / "other.conf"),
        lambda: (merged / "existing.conf").replace(merged / "other.conf"),
        lambda: (merged / "existing.conf").chmod(0o600),
        lambda: (merged / "link").symlink_to("target"),
        lambda: (merged / "link").hardlink_to(merged / "existing.conf"),
        lambda: (merged / "existing.conf").open("w"),
    )
    for action in actions:
        with pytest.raises(TaskFailed, match="read-only"):
            action()


def test_resolve_source_and_real_path_apply_overlays_and_fallbacks(tmp_path):
    ctx, bundle_dir = project(tmp_path)
    write(bundle_dir / "files" / "shared.conf", "bundle\n")
    write(bundle_dir / "files" / "bundle-only.conf", "bundle\n")
    write(bundle_dir / "files" / "nested" / "app.conf", "bundle\n")
    write(tmp_path / "files" / "base" / "shared.conf", "overlay\n")
    write(tmp_path / "files" / "base" / "nested" / "app.conf", "overlay\n")
    write(tmp_path / "files" / "base" / "other.conf", "overlay\n")
    plain = tmp_path / "plain"

    assert ctx.resolve_source("shared.conf") == tmp_path / "files" / "base" / "shared.conf"
    assert ctx.resolve_source("files/shared.conf") == tmp_path / "files" / "base" / "shared.conf"
    assert ctx.resolve_source("files/nested/app.conf") == tmp_path / "files" / "base" / "nested" / "app.conf"
    assert ctx.resolve_source("bundle-only.conf") == bundle_dir / "files" / "bundle-only.conf"
    assert not isinstance(ctx.resolve_source("shared.conf"), OverlayPath)
    assert real_path(ctx.files / "shared.conf") == tmp_path / "files" / "base" / "shared.conf"
    assert real_path(ctx.files / "missing.conf") == tmp_path / "files" / "base" / "missing.conf"
    assert real_path(plain) is plain

    fallback = tmp_path / "fallback"
    ctx, bundle_dir = project(fallback)
    write(bundle_dir / "files" / "nested" / "app.conf", "bundle\n")
    write(fallback / "files" / "base" / "other.conf", "overlay\n")
    assert ctx.resolve_source("files/nested/app.conf") == bundle_dir / "files" / "nested" / "app.conf"


def test_resolve_source_keeps_absolute_expanded_paths_and_source_labels(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    ctx, bundle_dir = project(tmp_path)
    write(bundle_dir / "files" / "shared.conf", "bundle\n")
    write(tmp_path / "files" / "base" / "nested" / "app.conf", "overlay\n")

    assert ctx.resolve_source("/srv/config") == Path("/srv/config")
    assert ctx.resolve_source("~/config") == home / "config"
    assert ctx.source_label("shared.conf") == "shared.conf"
    assert ctx.source_label("files/shared.conf") == "shared.conf"
    assert ctx.source_label("files/nested/app.conf") == "nested/app.conf"
    assert ctx.source_label("templates/app.conf") == "templates/app.conf"
    assert ctx.source_label(ctx.files / "nested" / "app.conf") == "nested/app.conf"
    assert ctx.source_label("/srv/config") == "/srv/config"
    assert ctx.source_label("~/config") == "~/config"


def test_copy_from_a_merged_entry(tmp_path):
    ctx, bundle_dir = project(tmp_path)
    write(bundle_dir / "files" / "sshd.conf", "bundle\n")
    write(tmp_path / "files" / "base" / "sshd.conf", "overlay\n")
    (tmp_path / "out").mkdir()
    destination = tmp_path / "out" / "sshd.conf"

    result = files.copy(ctx.files / "sshd.conf", destination)

    assert result.changed
    assert result.summary == f"copy sshd.conf -> {destination}"
    assert destination.read_text(encoding="utf-8") == "overlay\n"

    write(bundle_dir / "files" / "nested" / "app.conf", "bundle\n")
    short_destination = tmp_path / "out" / "app.conf"
    result = files.copy("files/nested/app.conf", short_destination)

    assert result.summary == f"copy nested/app.conf -> {short_destination}"

    result = files.copy("files/nested/app.conf", short_destination)

    assert result.summary == f"ensure {short_destination} from nested/app.conf"


def test_copy_from_a_merged_entry_with_sudo(tmp_path):
    ctx, bundle_dir = project(tmp_path, context=local_sudo_context(tmp_path))
    write(bundle_dir / "files" / "sshd.conf", "bundle\n")
    write(tmp_path / "files" / "base" / "sshd.conf", "overlay\n")
    (tmp_path / "out").mkdir()
    destination = tmp_path / "out" / "sshd.conf"

    result = files.copy(ctx.files / "sshd.conf", destination, sudo=True)

    assert result.changed
    assert destination.read_text(encoding="utf-8") == "overlay\n"
