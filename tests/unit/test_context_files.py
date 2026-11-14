"""Tests for the merged ``ctx.files`` view and module source resolution."""

import os
from pathlib import Path

import pytest

from kitup._utils.paths import OverlayPath, real_path
from kitup.errors import TaskFailed
from kitup.modules import files
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


def test_files_is_a_property(tmp_path):
    ctx, bundle_dir = project(tmp_path)
    (bundle_dir / "files").mkdir()

    assert isinstance(type(ctx).files, property)
    assert isinstance(ctx.files, OverlayPath)


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


def test_files_uses_the_overlay_when_the_bundle_has_no_files_directory(tmp_path):
    ctx, _ = project(tmp_path)
    write(tmp_path / "files" / "base" / "only.conf", "overlay\n")

    assert (ctx.files / "only.conf").read_text(encoding="utf-8") == "overlay\n"
    assert [entry.name for entry in ctx.files.iterdir()] == ["only.conf"]


def test_files_lists_empty_directories_from_both_roots(tmp_path):
    ctx, bundle_dir = project(tmp_path)
    (bundle_dir / "files" / "bundle-empty").mkdir(parents=True)
    (tmp_path / "files" / "base" / "overlay-empty").mkdir(parents=True)

    assert sorted(entry.name for entry in ctx.files.iterdir()) == ["bundle-empty", "overlay-empty"]
    assert (ctx.files / "bundle-empty").is_dir()
    assert (ctx.files / "overlay-empty").is_dir()


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


def test_files_children_stay_virtual(tmp_path):
    ctx, bundle_dir = project(tmp_path)
    write(bundle_dir / "files" / "nested" / "app.conf", "bundle\n")

    child = ctx.files / "nested" / "app.conf"

    assert isinstance(child, OverlayPath)
    assert child.name == "app.conf"
    assert isinstance(child.parent, OverlayPath)
    assert child.parent.is_dir()
    assert [entry.name for entry in child.parent.iterdir()] == ["app.conf"]


def test_files_entries_are_synthetic_paths(tmp_path):
    ctx, bundle_dir = project(tmp_path)
    write(bundle_dir / "files" / "sshd.conf", "bundle\n")
    write(tmp_path / "files" / "base" / "sshd.conf", "overlay\n")

    entry = ctx.files / "sshd.conf"

    assert str(entry).startswith("/@kitup-merged/base/")
    with pytest.raises(FileNotFoundError):
        os.stat(str(entry))
    assert entry.read_bytes() == b"overlay\n"


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


def test_real_path_returns_the_winning_entry(tmp_path):
    ctx, bundle_dir = project(tmp_path)
    write(bundle_dir / "files" / "shared.conf", "bundle\n")
    write(tmp_path / "files" / "base" / "shared.conf", "overlay\n")
    plain = tmp_path / "plain"

    assert real_path(ctx.files / "shared.conf") == tmp_path / "files" / "base" / "shared.conf"
    assert real_path(ctx.files / "missing.conf") == tmp_path / "files" / "base" / "missing.conf"
    assert real_path(plain) is plain


def test_resolve_source_applies_overlays_to_bare_names_and_files_paths(tmp_path):
    ctx, bundle_dir = project(tmp_path)
    write(bundle_dir / "files" / "shared.conf", "bundle\n")
    write(bundle_dir / "files" / "bundle-only.conf", "bundle\n")
    write(bundle_dir / "files" / "nested" / "app.conf", "bundle\n")
    write(tmp_path / "files" / "base" / "shared.conf", "overlay\n")
    write(tmp_path / "files" / "base" / "nested" / "app.conf", "overlay\n")

    assert ctx.resolve_source("shared.conf") == tmp_path / "files" / "base" / "shared.conf"
    assert ctx.resolve_source("files/shared.conf") == tmp_path / "files" / "base" / "shared.conf"
    assert ctx.resolve_source("files/nested/app.conf") == tmp_path / "files" / "base" / "nested" / "app.conf"
    assert ctx.resolve_source("bundle-only.conf") == bundle_dir / "files" / "bundle-only.conf"
    assert not isinstance(ctx.resolve_source("shared.conf"), OverlayPath)


def test_resolve_source_files_path_falls_back_to_the_bundle(tmp_path):
    ctx, bundle_dir = project(tmp_path)
    write(bundle_dir / "files" / "nested" / "app.conf", "bundle\n")
    write(tmp_path / "files" / "base" / "other.conf", "overlay\n")

    assert ctx.resolve_source("files/nested/app.conf") == bundle_dir / "files" / "nested" / "app.conf"


def test_resolve_source_leaves_other_relative_paths_in_the_bundle(tmp_path):
    ctx, bundle_dir = project(tmp_path)
    write(bundle_dir / "templates" / "app.conf", "bundle\n")
    write(tmp_path / "files" / "base" / "templates" / "app.conf", "overlay\n")

    assert ctx.resolve_source("templates/app.conf") == bundle_dir / "templates" / "app.conf"


def test_resolve_source_does_not_special_case_files(tmp_path):
    ctx, bundle_dir = project(tmp_path)
    write(bundle_dir / "files" / "files" / "inner.conf", "bundle\n")

    assert ctx.resolve_source("files") == bundle_dir / "files" / "files"


def test_resolve_source_keeps_absolute_and_expanded_paths(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    ctx, _ = project(tmp_path)

    assert ctx.resolve_source("/srv/config") == Path("/srv/config")
    assert ctx.resolve_source("~/config") == home / "config"


def test_copy_from_a_merged_entry(tmp_path):
    ctx, bundle_dir = project(tmp_path)
    write(bundle_dir / "files" / "sshd.conf", "bundle\n")
    write(tmp_path / "files" / "base" / "sshd.conf", "overlay\n")
    (tmp_path / "out").mkdir()
    destination = tmp_path / "out" / "sshd.conf"

    result = files.copy(ctx.files / "sshd.conf", destination)

    assert result.changed
    assert destination.read_text(encoding="utf-8") == "overlay\n"


def test_copy_from_a_merged_entry_with_sudo(tmp_path):
    ctx, bundle_dir = project(tmp_path, context=local_sudo_context(tmp_path))
    write(bundle_dir / "files" / "sshd.conf", "bundle\n")
    write(tmp_path / "files" / "base" / "sshd.conf", "overlay\n")
    (tmp_path / "out").mkdir()
    destination = tmp_path / "out" / "sshd.conf"

    result = files.copy(ctx.files / "sshd.conf", destination, sudo=True)

    assert result.changed
    assert destination.read_text(encoding="utf-8") == "overlay\n"


def test_copy_from_a_merged_entry_in_dry_run(tmp_path):
    ctx, bundle_dir = project(tmp_path, context=make_context(tmp_path, dry_run=True))
    write(bundle_dir / "files" / "sshd.conf", "bundle\n")
    write(tmp_path / "files" / "base" / "sshd.conf", "overlay\n")
    (tmp_path / "out").mkdir()
    destination = tmp_path / "out" / "sshd.conf"

    result = files.copy(ctx.files / "sshd.conf", destination)

    assert result.changed
    assert not destination.exists()
