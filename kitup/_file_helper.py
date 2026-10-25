"""Low-level filesystem operations shared by the files module and its helper.

This module must stay importable as a standalone script: ``files`` embeds its
source and runs it with ``python -c`` when privileged access is required. Keep
the imports standard-library-only and avoid ``kitup`` imports.
"""

from __future__ import annotations

import base64
import grp
import json
import os
import pwd
import shutil
import stat
import sys
import tempfile
from typing import Any


def resolve_owner(owner: str | int | None) -> int | None:
    """Resolve an optional user name to a numeric UID."""
    if owner is None or isinstance(owner, int):
        return owner
    try:
        return pwd.getpwnam(owner).pw_uid
    except KeyError as exc:
        raise ValueError(f"unknown user: {owner}") from exc


def resolve_group(group: str | int | None) -> int | None:
    """Resolve an optional group name to a numeric GID."""
    if group is None or isinstance(group, int):
        return group
    try:
        return grp.getgrnam(group).gr_gid
    except KeyError as exc:
        raise ValueError(f"unknown group: {group}") from exc


def stat_path(path: str | os.PathLike[str], *, follow: bool = False) -> dict[str, Any] | None:
    """Return file metadata, or ``None`` when the path does not exist."""
    try:
        info = os.stat(path) if follow else os.lstat(path)
    except FileNotFoundError:
        return None
    result: dict[str, Any] = {
        "exists": True,
        "is_dir": stat.S_ISDIR(info.st_mode),
        "is_symlink": stat.S_ISLNK(info.st_mode),
        "is_file": stat.S_ISREG(info.st_mode),
        "mode": stat.S_IMODE(info.st_mode),
        "uid": info.st_uid,
        "gid": info.st_gid,
    }
    if result["is_symlink"]:
        result["link"] = os.readlink(path)
    return result


def read_path(path: str | os.PathLike[str]) -> bytes:
    """Read a file as bytes."""
    with open(path, "rb") as stream:
        return stream.read()


def apply_metadata(
    path: str | os.PathLike[str],
    mode: int | None,
    owner: str | int | None,
    group: str | int | None,
    info: dict[str, Any] | None = None,
    *,
    preserve: bool = False,
) -> None:
    """Apply ownership and mode to a path, optionally preserving owner/group."""
    if info is None:
        info = stat_path(path)
    uid = resolve_owner(owner)
    gid = resolve_group(group)
    if preserve and info is not None:
        if uid is None:
            uid = info["uid"]
        if gid is None:
            gid = info["gid"]
    if uid is not None or gid is not None:
        os.chown(path, -1 if uid is None else uid, -1 if gid is None else gid)
    if mode is not None:
        os.chmod(path, mode)
    elif info is not None:
        os.chmod(path, info["mode"])


def write_path(
    path: str | os.PathLike[str],
    data: bytes,
    *,
    mode: int | None = None,
    owner: str | int | None = None,
    group: str | int | None = None,
) -> None:
    """Atomically write bytes, preserving metadata unless overridden."""
    info = stat_path(path)
    if info is not None and info["is_dir"]:
        raise IsADirectoryError(path)

    if mode is None:
        preserve = info is not None and not info["is_symlink"]
        mode = info["mode"] if preserve and info is not None else 0o644

    parent = os.path.dirname(path) or "."
    handle, temp_name = tempfile.mkstemp(
        dir=parent,
        prefix=f".{os.path.basename(path)}.",
        suffix=".tmp",
    )
    temp = temp_name
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if info is not None and not info["is_symlink"]:
            try:
                shutil.copystat(path, temp, follow_symlinks=False)
            except OSError:
                pass
        apply_metadata(temp, mode, owner, group, info, preserve=True)
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def mkdir_path(
    path: str | os.PathLike[str],
    *,
    mode: int | None = None,
    owner: str | int | None = None,
    group: str | int | None = None,
) -> None:
    """Create one directory and apply metadata."""
    os.mkdir(path)
    apply_metadata(path, mode, owner, group, preserve=False)


def symlink_path(target: str, path: str | os.PathLike[str]) -> None:
    """Atomically create or replace a symlink."""
    info = stat_path(path)
    if info is not None and info["is_dir"] and not info["is_symlink"]:
        raise IsADirectoryError(path)

    parent = os.path.dirname(path) or "."
    handle, temp = tempfile.mkstemp(
        dir=parent,
        prefix=f".{os.path.basename(path)}.",
        suffix=".tmp",
    )
    os.close(handle)
    try:
        os.unlink(temp)
        os.symlink(target, temp)
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def remove_path(path: str | os.PathLike[str], *, force: bool = False) -> None:
    """Remove a file, symlink, or directory."""
    info = stat_path(path)
    if info is None:
        return
    if info["is_dir"] and not info["is_symlink"]:
        if force:
            shutil.rmtree(path)
        else:
            os.rmdir(path)
    else:
        os.unlink(path)


def main() -> None:
    with open(sys.argv[1], encoding="utf-8") as stream:
        request = json.load(stream)

    action = request["action"]
    if action == "stat":
        result = stat_path(request["path"], follow=request.get("follow", False))
        result = result if result is not None else {"exists": False}
    elif action == "read":
        result = stat_path(request["path"])
        result = result if result is not None else {"exists": False}
        if result["exists"] and result["is_file"]:
            result["content"] = base64.b64encode(read_path(request["path"])).decode()
    elif action == "write":
        write_path(
            request["path"],
            base64.b64decode(request["content"]),
            mode=request.get("mode"),
            owner=request.get("owner"),
            group=request.get("group"),
        )
        result = {}
    elif action == "mkdir":
        mkdir_path(
            request["path"],
            mode=request.get("mode"),
            owner=request.get("owner"),
            group=request.get("group"),
        )
        result = {}
    elif action == "metadata":
        apply_metadata(
            request["path"],
            request.get("mode"),
            request.get("owner"),
            request.get("group"),
        )
        result = {}
    elif action == "symlink":
        symlink_path(request["target"], request["path"])
        result = {}
    elif action == "remove":
        remove_path(request["path"], force=bool(request.get("force")))
        result = {}
    else:
        raise ValueError(f"unknown file helper action: {action}")

    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - report any helper failure
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
