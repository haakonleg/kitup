"""dconf database management."""

import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Final, overload

from .._utils import gvariant
from .._utils.validation import State, has_control_characters, require_path, require_state, require_text
from ..context import TaskContext, current_context
from ..errors import TaskFailed
from ..registry import module
from ..results import CallResult

module("dconf", "dconf database keys and subtrees (no schema required).")

_SCRATCH_PREFIX: Final = "kitup-dconf-"
_KEYFILE_HEADER: Final = "[/]"
# A leading throwaway group keeps a top-level ``Encoding`` key from being
# interpreted as the keyfile encoding directive.
_KEYFILE_GUARD: Final = "[kitup]"


@overload
def write(path: str, value: Any = ..., *, state: State = "present") -> CallResult: ...


@overload
def write(path: Mapping[str, Any], *, state: State = "present") -> CallResult: ...


def write(path: str | Mapping[str, Any], value: Any = gvariant.MISSING, *, state: State = "present") -> CallResult:
    """Manage one or more dconf keys by absolute path.

    Pass a mapping to set several keys in one call. ``state="absent"`` resets a
    single key, restoring any value provided by the system databases.
    """
    ctx = current_context()
    require_state(state, "dconf")
    gvariant.require_user_session(ctx, "dconf")

    if isinstance(path, Mapping):
        if value is not gvariant.MISSING:
            raise TaskFailed("dconf key mapping does not take a separate value")
        if state == "absent":
            raise TaskFailed("dconf state 'absent' requires a single key")
        if not path:
            raise TaskFailed("dconf keys must not be empty")
        values = dict(path)
        for item_path in values:
            if not isinstance(item_path, str):
                raise TaskFailed("dconf key paths must be strings")
            _require_key(item_path)
        return ctx.changed(_set_keys(ctx, values))

    if not isinstance(path, str):
        raise TaskFailed("dconf key must be a path or a mapping of paths to values")
    _require_key(path)
    if state == "absent":
        if value is not gvariant.MISSING:
            raise TaskFailed("dconf state 'absent' does not take a value")
        return ctx.changed(_reset_key(ctx, path))
    if value is gvariant.MISSING:
        raise TaskFailed("value is required when state is present")
    return ctx.changed(_set_keys(ctx, {path: value}))


def read(paths: str | Iterable[str]) -> str | dict[str, str | None] | None:
    """Read the effective value of one key, or several keys as a mapping.

    A single path returns its canonical GVariant text, or ``None`` when the key
    is unset. Several paths return one entry per path, preserving their order.
    Values include defaults from the system databases.
    """
    ctx = current_context()
    if isinstance(paths, str):
        _require_key(paths)
        return _read_key(ctx, paths)
    if not isinstance(paths, Iterable):
        raise TaskFailed("dconf keys must be a path or an iterable of paths")
    requested = list(paths)
    if not requested:
        raise TaskFailed("dconf keys must not be empty")
    for path in requested:
        if not isinstance(path, str):
            raise TaskFailed("dconf key paths must be strings")
        _require_key(path)
    return {path: _read_key(ctx, path) for path in requested}


def dump(directory: str = "/") -> str:
    """Return the keyfile text for a dconf subtree."""
    ctx = current_context()
    _require_dir(directory)
    return _dconf(ctx, "dump", directory)


def load(directory: str, content: str | None = None, *, source: str | Path | None = None) -> CallResult:
    """Load keys under ``directory`` from keyfile text or a source file.

    Pass ``content`` with keyfile text, or ``source`` with a path to a keyfile.
    Relative ``source`` paths use the project and bundle lookup described in the
    project documentation. Keys absent from the keyfile are left untouched.
    """
    ctx = current_context()
    _require_dir(directory)
    if content is not None and source is not None:
        raise TaskFailed("dconf load takes either content or source, not both")
    if content is None and source is None:
        raise TaskFailed("dconf load requires content or source")
    data: dict[str, Any] = {"dir": directory}
    if source is not None:
        require_path(source, "dconf source")
        source_path = ctx.resolve_source(source)
        try:
            content = source_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise TaskFailed(f"could not read dconf source {source_path}: {exc}") from exc
        except UnicodeDecodeError as exc:
            raise TaskFailed(f"dconf source is not UTF-8 text: {source_path}") from exc
        data["src"] = str(source_path)
    else:
        if not isinstance(content, str):
            raise TaskFailed("dconf keyfile content must be a string")
    gvariant.require_text_value(content, "dconf keyfile content")
    gvariant.require_user_session(ctx, "dconf")

    canonical = _scratch_canonical(ctx, directory, content)
    paths = list(canonical)
    changed = any(_read_key(ctx, path) != text for path, text in canonical.items())
    count = _key_count(len(paths))
    data["paths"] = paths
    if not changed:
        return ctx.changed(changed=False, summary=f"ensure {count} under {directory}", **data)

    result = CallResult(changed=True, summary=f"load {count} under {directory}", **data)
    if ctx.dry_run:
        return ctx.changed(result)
    ctx.run("dconf", "load", directory, stdin=content, sudo=False)
    return ctx.changed(result)


def variant(text: str) -> gvariant.Variant:
    """Wrap verbatim GVariant text for values without a native Python mapping.

    Use it for values whose type cannot be inferred from the text, such as an
    empty array (``@as []``), a maybe value (``@ms nothing``), or a variant
    (``<42>``). The text is validated by ``dconf`` before the key is changed.
    """
    return gvariant.variant(text, "dconf")


def _set_keys(ctx: TaskContext, values: Mapping[str, Any]) -> CallResult:
    paths = list(values)
    texts = {path: gvariant.format_value(value, label="dconf") for path, value in values.items()}
    canonical = _scratch_canonical(ctx, "/", _keyfile_for(texts))

    summaries: list[str] = []
    writes: list[str] = []
    for path in paths:
        if path not in canonical:
            raise TaskFailed(f"dconf {path}: value could not be read back from the scratch session")
        if _read_key(ctx, path) == canonical[path]:
            summaries.append(f"ensure {path}={texts[path]}")
        else:
            summaries.append(f"set {path}={texts[path]}")
            writes.append(path)

    result = CallResult(
        changed=bool(writes),
        summary="; ".join(summaries),
        paths=paths,
        values=dict(values),
    )
    if not writes or ctx.dry_run:
        return result
    for path in writes:
        ctx.run("dconf", "write", path, canonical[path], sudo=False)
    return result


def _reset_key(ctx: TaskContext, path: str) -> CallResult:
    current = _read_key(ctx, path)
    if current == _read_key(ctx, path, default=True):
        return CallResult(changed=False, summary=f"ensure {path} is reset", paths=[path])

    result = CallResult(changed=True, summary=f"reset {path}", paths=[path])
    if ctx.dry_run:
        return result
    ctx.run("dconf", "reset", path, sudo=False)
    return result


def _read_key(ctx: TaskContext, path: str, *, default: bool = False) -> str | None:
    command = ("read", "-d", path) if default else ("read", path)
    return _dconf(ctx, *command).strip() or None


def _dconf(ctx: TaskContext, *argv: str) -> str:
    """Run a read-only dconf command and return stdout, failing on error."""
    result = ctx.probe("dconf", *argv, sudo=False)
    if result.returncode != 0:
        raise TaskFailed(f"dconf {' '.join(argv)}: {gvariant.detail(result)}")
    return result.stdout


def _scratch_canonical(ctx: TaskContext, directory: str, content: str) -> dict[str, str]:
    """Validate keyfile text in a throwaway database and return canonical values.

    A scratch profile with only the user database keeps system defaults out of
    the read-back. ``dbus-run-session`` provides the session bus that dconf needs
    for writes; reads are then issued without a bus against the same scratch
    home.
    """
    with tempfile.TemporaryDirectory(prefix=_SCRATCH_PREFIX) as home:
        # dconf expects these directories to be distinct; sharing one corrupts
        # the scratch database.
        config = Path(home, "config")
        runtime = Path(home, "runtime")
        profile = Path(home, "profile")
        config.mkdir()
        runtime.mkdir(mode=0o700)
        profile.write_text("user-db:user\n", encoding="utf-8")
        env = {
            "DCONF_PROFILE": str(profile),
            "XDG_CONFIG_HOME": str(config),
            "XDG_RUNTIME_DIR": str(runtime),
        }
        loaded = ctx.probe("dbus-run-session", "--", "dconf", "load", directory, sudo=False, env=env, stdin=content)
        if loaded.returncode == 127:
            raise TaskFailed(
                "dconf validation requires dbus-run-session and dconf; "
                f"the scratch session could not be started: {gvariant.detail(loaded)}"
            )
        if loaded.returncode != 0:
            raise TaskFailed(f"dconf load {directory}: {gvariant.detail(loaded)}")
        dumped = _scratch_dump(ctx, directory, env=env)
    return _parse_keyfile(dumped, directory)


def _scratch_dump(ctx: TaskContext, directory: str, *, env: Mapping[str, str]) -> str:
    result = ctx.probe("dconf", "dump", directory, sudo=False, env=env)
    if result.returncode == 0:
        return result.stdout
    # Reads normally need no bus; retry inside a session if this one does.
    fallback = ctx.probe("dbus-run-session", "--", "dconf", "dump", directory, sudo=False, env=env)
    if fallback.returncode != 0:
        raise TaskFailed(f"dconf dump {directory}: {gvariant.detail(fallback)}")
    return fallback.stdout


def _keyfile_for(values: Mapping[str, str]) -> str:
    entries = "".join(f"{path[1:]}={text}\n" for path, text in values.items())
    return f"{_KEYFILE_GUARD}\n{_KEYFILE_HEADER}\n{entries}"


def _parse_keyfile(content: str, directory: str) -> dict[str, str]:
    """Return absolute paths mapped to canonical values from keyfile text."""
    values: dict[str, str] = {}
    group = "/"
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            end = line.find("]")
            if end < 0:
                raise TaskFailed(f"invalid dconf keyfile group: {raw!r}")
            group = line[1:end].strip()
            continue
        name, separator, value = line.partition("=")
        if not separator:
            raise TaskFailed(f"invalid dconf keyfile line: {raw!r}")
        prefix = directory if group == "/" else f"{directory}{group}/"
        values[f"{prefix}{name.strip()}"] = value.strip()
    return values


def _key_count(count: int) -> str:
    return f"{count} key" if count == 1 else f"{count} keys"


def _require_key(path: str) -> None:
    _require_absolute(path, "dconf key")
    if path.endswith("/"):
        raise TaskFailed("dconf key must not end with a slash")
    _require_keyfile_path(path)


def _require_dir(directory: str) -> None:
    _require_absolute(directory, "dconf dir")
    if not directory.endswith("/"):
        raise TaskFailed("dconf dir must end with a slash")


def _require_absolute(value: str, label: str) -> None:
    require_text(value, label)
    if not value.startswith("/"):
        raise TaskFailed(f"{label} must begin with a slash")
    if "//" in value:
        raise TaskFailed(f"{label} must not contain two consecutive slashes")


def _require_keyfile_path(path: str) -> None:
    """Reject keys that a dconf keyfile cannot represent losslessly."""
    if has_control_characters(path):
        raise TaskFailed("dconf key must not contain control characters")
    relative = path[1:]
    for element in relative.split("/"):
        if any(character in element for character in "=[]"):
            raise TaskFailed(f"dconf key element must not contain '=', '[', or ']': {element!r}")
    if relative.startswith("#") or relative[0].isspace() or relative[-1].isspace():
        raise TaskFailed("dconf key must not start with '#' or whitespace, or end with whitespace")
