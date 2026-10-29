"""Git repository checkout management."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from ..context import TaskContext
from ..errors import TaskFailed
from ..validation import require_path, require_success, require_text

_SHA = re.compile(r"[0-9a-f]{40}")
_GIT_ENV = {"GIT_TERMINAL_PROMPT": "0"}


def repo(
    ctx: TaskContext,
    url: str,
    dest: str | Path,
    *,
    version: str = "HEAD",
    single_branch: bool = False,
    depth: int | None = None,
    update: bool = True,
    force: bool = False,
) -> None:
    """Ensure a git repository is checked out at ``dest``.

    A missing or empty ``dest`` is cloned. An existing repository is
    reconciled: the origin URL is set when it differs, and when ``update``
    is enabled (or the URL changed) the remote is fetched and the checkout
    is moved to ``version`` when it differs.
    """
    require_text(url, "git repository URL")
    require_path(dest, "git destination path")
    require_text(version, "git version")
    if url.startswith("-"):
        raise TaskFailed("git repository URL must not start with '-'")
    if version.startswith("-"):
        raise TaskFailed("git version must not start with '-'")
    _require_depth(depth)

    git = _git(ctx)
    path = Path(dest)
    if path.exists() and not path.is_dir():
        raise TaskFailed(f"git destination exists and is not a directory: {dest}")
    if not _is_repo(ctx, git, str(path), path):
        if path.is_dir() and any(path.iterdir()):
            raise TaskFailed(f"git destination is not empty and not a git repository: {dest}")
        _clone(ctx, git, url, path, version=version, single_branch=single_branch, depth=depth)
        return
    _reconcile(ctx, git, url, path, version=version, update=update, force=force)


def _clone(
    ctx: TaskContext,
    git: str,
    url: str,
    path: Path,
    *,
    version: str,
    single_branch: bool,
    depth: int | None,
) -> None:
    command = [git, "clone"]
    if depth is not None:
        command.append(f"--depth={depth}")
    if single_branch:
        command.append("--single-branch")
    sha = _SHA.fullmatch(version)
    if version != "HEAD" and sha is None:
        command.append(f"--branch={version}")
    command.extend([url, str(path)])
    ctx.changed(
        {
            "repo": url,
            "dest": str(path),
            "version": version,
            "single_branch": single_branch,
            "depth": depth,
        },
        f"clone {url} into {path}",
    )
    ctx.run(command, env=_GIT_ENV, record=False)
    if sha is not None and not ctx.dry_run:
        ctx.run([git, "-C", str(path), "checkout", "--force", version], record=False)


def _reconcile(
    ctx: TaskContext,
    git: str,
    url: str,
    path: Path,
    *,
    version: str,
    update: bool,
    force: bool,
) -> None:
    dest = str(path)
    current_url = _remote_url(ctx, git, dest)
    url_changed = current_url != url
    if url_changed:
        data: dict[str, str] = {"repo": url, "dest": dest}
        if current_url is not None:
            data["previous_url"] = current_url
        ctx.changed(data, f"set remote URL of {dest} to {url}")
        verb = "set-url" if current_url is not None else "add"
        ctx.run([git, "-C", dest, "remote", verb, "origin", url], record=False)

    if not update and not url_changed:
        return

    _fetch(ctx, git, dest)
    desired = _resolve_version(ctx, git, dest, version)
    current = _head(ctx, git, dest)
    if current == desired:
        return
    if not force and _is_dirty(ctx, git, dest):
        raise TaskFailed(f"local changes in {dest}; use force=True to discard them")
    ctx.changed(
        {"before": current, "after": desired, "version": version, "dest": dest},
        f"update {dest} {current[:12]}..{desired[:12]}",
    )
    ctx.run([git, "-C", dest, "checkout", "--force", version], record=False)
    ctx.run([git, "-C", dest, "reset", "--hard", desired], record=False)
    if not ctx.dry_run and _head(ctx, git, dest) != desired:
        raise TaskFailed(f"could not update {dest} to {desired}")


def _git(ctx: TaskContext) -> str:
    command = ctx.memo("git-command", lambda: shutil.which("git"))
    if command is None:
        raise TaskFailed("git is required but was not found")
    return command


def _is_repo(ctx: TaskContext, git: str, dest: str, path: Path) -> bool:
    """Return whether ``path`` is itself the top level of a checkout."""
    result = ctx.probe([git, "-C", dest, "rev-parse", "--show-toplevel"])
    if result.returncode != 0:
        return False
    return Path(result.stdout.strip()).resolve() == path.resolve()


def _remote_url(ctx: TaskContext, git: str, dest: str) -> str | None:
    result = ctx.probe([git, "-C", dest, "remote", "get-url", "origin"])
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _fetch(ctx: TaskContext, git: str, dest: str) -> None:
    result = ctx.probe([git, "-C", dest, "fetch", "origin"], env=_GIT_ENV)
    require_success(result, f"could not fetch from origin in {dest}")


def _resolve_version(ctx: TaskContext, git: str, dest: str, version: str) -> str:
    refs = ["origin/HEAD"] if version == "HEAD" else [f"origin/{version}", version]
    for ref in refs:
        result = ctx.probe(
            [git, "-C", dest, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"]
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    raise TaskFailed(f"version {version!r} not found in {dest} after fetch")


def _head(ctx: TaskContext, git: str, dest: str) -> str:
    result = ctx.probe([git, "-C", dest, "rev-parse", "HEAD"])
    require_success(result, f"could not read HEAD of {dest}")
    return result.stdout.strip()


def _is_dirty(ctx: TaskContext, git: str, dest: str) -> bool:
    """Return whether tracked files have uncommitted modifications.

    Untracked files are ignored: they survive ``checkout --force`` and
    ``reset --hard``, so they do not make an update destructive.
    """
    result = ctx.probe([git, "-C", dest, "status", "--porcelain", "--untracked-files=no"])
    require_success(result, f"could not read status of {dest}")
    return bool(result.stdout.strip())


def _require_depth(depth: int | None) -> None:
    if depth is None:
        return
    if isinstance(depth, bool) or not isinstance(depth, int):
        raise TaskFailed("depth must be an integer")
    if depth < 1:
        raise TaskFailed("depth must be at least 1")
