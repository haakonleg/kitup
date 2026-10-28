"""Low-level filesystem operations shared by built-in modules and the worker.

The privileged worker embeds this module's source, so keep the imports
standard-library-only and avoid package imports.
"""

import grp
import hashlib
import os
import pwd
import shutil
import stat
import tempfile
from collections.abc import Callable
from typing import Any, BinaryIO


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


def _resolve_file_mode(
    info: dict[str, Any] | None,
    mode: int | None,
    *,
    default: int = 0o644,
) -> int:
    """Resolve an explicit mode, an existing file mode, or a default."""
    if mode is not None:
        return mode
    if info is not None and not info["is_symlink"]:
        return int(info["mode"])
    return default


def atomic_write_path(
    path: str | os.PathLike[str],
    writer: Callable[[BinaryIO], None],
    *,
    mode: int | None = None,
    owner: str | int | None = None,
    group: str | int | None = None,
    default_mode: int = 0o644,
) -> None:
    """Atomically replace a path using a callback that writes a binary stream.

    Existing metadata is preserved unless overridden. The temporary file is
    created beside the destination and replaced into place after ``writer``
    returns and the stream is flushed.
    """
    info = stat_path(path)
    if info is not None and info["is_dir"]:
        raise IsADirectoryError(path)

    resolved_mode = _resolve_file_mode(info, mode, default=default_mode)
    parent = os.path.dirname(path) or "."
    handle, temp_name = tempfile.mkstemp(
        dir=parent,
        prefix=f".{os.path.basename(path)}.",
        suffix=".tmp",
    )
    temp = temp_name
    try:
        with os.fdopen(handle, "wb") as stream:
            writer(stream)
            stream.flush()
            os.fsync(stream.fileno())
        _apply_replacement_metadata(path, temp, info, resolved_mode, owner, group)
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def write_path(
    path: str | os.PathLike[str],
    data: bytes,
    *,
    mode: int | None = None,
    owner: str | int | None = None,
    group: str | int | None = None,
) -> None:
    """Atomically write bytes, preserving metadata unless overridden."""

    def write(stream: BinaryIO) -> None:
        stream.write(data)

    atomic_write_path(path, write, mode=mode, owner=owner, group=group)


def move_path(
    source: str | os.PathLike[str],
    destination: str | os.PathLike[str],
) -> None:
    """Move a regular file, copying across filesystems when necessary.

    Symlink sources, directories, and symlink destinations are rejected.
    """
    source_info = stat_path(source)
    if source_info is None or source_info["is_symlink"] or not source_info["is_file"]:
        raise ValueError(f"move source is not a regular file: {source}")
    info = stat_path(destination)
    if info is not None:
        if info["is_dir"]:
            raise IsADirectoryError(destination)
        if info["is_symlink"]:
            raise ValueError(f"move destination is a symlink: {destination}")
    shutil.move(source, destination)


def replace_path(
    source: str | os.PathLike[str],
    destination: str | os.PathLike[str],
    *,
    mode: int | None = None,
    owner: str | int | None = None,
    group: str | int | None = None,
    default_mode: int = 0o644,
) -> None:
    """Atomically replace ``destination`` with ``source``.

    ``source`` must be on the same filesystem as ``destination``. Existing
    destination metadata is preserved unless overridden. New destinations use
    ``default_mode`` when no mode is given.
    """
    source_info = stat_path(source)
    if source_info is None or source_info["is_symlink"] or not source_info["is_file"]:
        raise ValueError(f"replace source is not a regular file: {source}")
    info = stat_path(destination)
    if info is not None and info["is_dir"]:
        raise IsADirectoryError(destination)
    resolved_mode = _resolve_file_mode(info, mode, default=default_mode)
    _apply_replacement_metadata(destination, source, info, resolved_mode, owner, group)
    os.replace(source, destination)


def _apply_replacement_metadata(
    path: str | os.PathLike[str],
    temp: str | os.PathLike[str],
    info: dict[str, Any] | None,
    mode: int,
    owner: str | int | None,
    group: str | int | None,
) -> None:
    if info is not None and not info["is_symlink"]:
        try:
            shutil.copystat(path, temp, follow_symlinks=False)
        except OSError:
            pass
        apply_metadata(temp, mode, owner, group, info, preserve=True)
        return
    apply_metadata(temp, mode, owner, group)


def digest_path(path: str | os.PathLike[str], algorithm: str) -> str:
    """Return a hex digest for a file, streaming its contents."""
    try:
        digest = hashlib.new(algorithm)
    except ValueError as exc:
        raise ValueError(f"unsupported checksum algorithm: {algorithm}") from exc
    if digest.digest_size <= 0:
        raise ValueError(f"unsupported checksum algorithm: {algorithm}")
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
