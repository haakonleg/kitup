"""Private helper for privileged filesystem operations."""

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


def _resolve_owner(owner):
    if owner is None or isinstance(owner, int):
        return owner
    return pwd.getpwnam(owner).pw_uid


def _resolve_group(group):
    if group is None or isinstance(group, int):
        return group
    return grp.getgrnam(group).gr_gid


def _lstat(path):
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None


def _info(path, *, follow=False):
    try:
        info = os.stat(path) if follow else os.lstat(path)
    except FileNotFoundError:
        return {"exists": False}
    result = {
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


def _apply_metadata(path, mode, owner, group, info=None, *, preserve=False):
    if info is None:
        info = _lstat(path)
    uid = _resolve_owner(owner)
    gid = _resolve_group(group)
    if preserve and info is not None:
        if uid is None:
            uid = info.st_uid
        if gid is None:
            gid = info.st_gid
    if uid is not None or gid is not None:
        os.chown(path, -1 if uid is None else uid, -1 if gid is None else gid)
    if mode is not None:
        os.chmod(path, mode)
    elif info is not None:
        os.chmod(path, stat.S_IMODE(info.st_mode))


def _stat(request):
    return _info(request["path"], follow=request.get("follow", False))


def _read(request):
    result = _info(request["path"])
    if result["exists"] and result["is_file"]:
        with open(request["path"], "rb") as stream:
            result["content"] = base64.b64encode(stream.read()).decode()
    return result


def _write(request):
    path = request["path"]
    data = base64.b64decode(request["content"])
    info = _lstat(path)
    if info is not None and stat.S_ISDIR(info.st_mode):
        raise IsADirectoryError(path)

    mode = request.get("mode")
    if mode is None:
        preserve = info is not None and not stat.S_ISLNK(info.st_mode)
        mode = stat.S_IMODE(info.st_mode) if preserve and info is not None else 0o644

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
        if info is not None and not stat.S_ISLNK(info.st_mode):
            try:
                shutil.copystat(path, temp, follow_symlinks=False)
            except OSError:
                pass
        _apply_metadata(
            temp,
            mode,
            request.get("owner"),
            request.get("group"),
            info,
            preserve=True,
        )
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def _mkdir(request):
    path = request["path"]
    os.mkdir(path)
    _apply_metadata(
        path,
        request.get("mode"),
        request.get("owner"),
        request.get("group"),
        preserve=False,
    )


def _metadata(request):
    _apply_metadata(
        request["path"],
        request.get("mode"),
        request.get("owner"),
        request.get("group"),
        preserve=False,
    )


def _symlink(request):
    path = request["path"]
    info = _lstat(path)
    if info is not None and stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
        raise IsADirectoryError(path)

    parent = os.path.dirname(path) or "."
    temp = os.path.join(parent, f".{os.path.basename(path)}.{os.getpid()}.tmp")
    try:
        os.unlink(temp)
    except FileNotFoundError:
        pass
    os.symlink(request["target"], temp)
    try:
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def _remove(request):
    path = request["path"]
    info = _lstat(path)
    if info is None:
        return
    if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
        if request.get("force"):
            shutil.rmtree(path)
        else:
            os.rmdir(path)
    else:
        os.unlink(path)


def main():
    with open(sys.argv[1], encoding="utf-8") as stream:
        request = json.load(stream)

    action = request["action"]
    if action == "stat":
        result = _stat(request)
    elif action == "read":
        result = _read(request)
    elif action == "write":
        _write(request)
        result = {}
    elif action == "mkdir":
        _mkdir(request)
        result = {}
    elif action == "metadata":
        _metadata(request)
        result = {}
    elif action == "symlink":
        _symlink(request)
        result = {}
    elif action == "remove":
        _remove(request)
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
