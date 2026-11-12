"""Idempotent Jinja2 template rendering to managed files."""

from collections.abc import Mapping
from functools import cache
from pathlib import Path
from typing import Any, Final

from .._utils import file_actions
from .._utils.paths import expand_path
from .._utils.validation import require_bool, require_metadata, require_mode, require_path
from ..context import TaskContext
from ..errors import TaskFailed
from ..results import CallResult

_RESERVED_VARIABLES: Final = frozenset({"vars", "facts"})


def file(
    ctx: TaskContext,
    src: str | Path,
    dest: str | Path,
    *,
    variables: Mapping[str, Any] | None = None,
    mode: int | None = None,
    owner: str | int | None = None,
    group: str | int | None = None,
    sudo: bool = False,
) -> CallResult:
    """Render a Jinja2 template into a managed file.

    ``src`` resolves like :func:`kitup.modules.files.copy`. Absolute paths are
    used as-is.

    Templates see ``vars`` (the merged variable values), ``facts`` (the
    system facts snapshot), and any keys supplied through ``variables``.
    Undefined variables are errors. The rendered text is compared with the
    destination and only written when it differs.
    """
    require_path(src, "template source")
    require_path(dest, "template destination")
    require_metadata(owner, group)
    require_mode(mode)
    require_bool(sudo, "template sudo")
    extra = _require_variables(variables)
    target = expand_path(dest, error=TaskFailed)
    file_actions.require_parent(ctx, target, sudo=sudo)

    source = ctx.resolve_source(src)
    text = _read_template(ctx, source)
    rendered = _render(ctx, source, text, extra)

    return ctx.changed(
        file_actions.apply_bytes(
            ctx,
            target,
            rendered.encode("utf-8"),
            action="template.file",
            mode=mode,
            owner=owner,
            group=group,
            summary=f"render {source} -> {target}",
            unchanged_summary=f"ensure {target} from {source}",
            data={"dest": str(target), "src": str(source)},
            sudo=sudo,
            check_parent=False,
        )
    )


def _require_variables(variables: Mapping[str, Any] | None) -> dict[str, Any]:
    if variables is None:
        return {}
    if not isinstance(variables, Mapping):
        raise TaskFailed("variables must be a mapping")
    values: dict[str, Any] = {}
    for key, value in variables.items():
        if not isinstance(key, str) or not key.isidentifier():
            raise TaskFailed(f"template variable name must be a valid identifier: {key!r}")
        if key in _RESERVED_VARIABLES:
            raise TaskFailed(f"template variable name is reserved: {key}")
        values[key] = value
    return values


def _read_template(ctx: TaskContext, source: Path) -> str:
    info = file_actions.stat_local(source)
    if info is None:
        raise TaskFailed(f"template source does not exist: {source}")
    if info.is_symlink:
        raise TaskFailed(f"refusing to read symlink template: {source}")
    if not info.is_file:
        raise TaskFailed(f"template source is not a regular file: {source}")
    data = file_actions.read_bytes(ctx, source, info, sudo=False)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TaskFailed(f"template source is not UTF-8 text: {source}") from exc


def _render(
    ctx: TaskContext,
    source: Path,
    text: str,
    variables: Mapping[str, Any],
) -> str:
    jinja2 = _load_jinja2()
    environment = jinja2.Environment(
        undefined=jinja2.StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,
    )
    context = {
        "vars": dict(vars(ctx.vars)),
        "facts": dict(ctx.facts),
        **variables,
    }
    try:
        template = environment.from_string(text)
        return str(template.render(context))
    except jinja2.TemplateSyntaxError as exc:
        location = f"{source}:{exc.lineno}" if exc.lineno else str(source)
        raise TaskFailed(f"template syntax error in {location}: {exc.message}") from exc
    except jinja2.UndefinedError as exc:
        raise TaskFailed(f"undefined variable in template {source}: {exc}") from exc
    except jinja2.TemplateError as exc:
        raise TaskFailed(f"template error in {source}: {exc}") from exc
    except Exception as exc:
        raise TaskFailed(f"template rendering error in {source}: {exc}") from exc


@cache
def _load_jinja2() -> Any:
    try:
        import jinja2  # noqa: PLC0415 - lazy import reports a task-level error
    except ImportError as exc:  # pragma: no cover - packaging guarantees the dependency
        raise TaskFailed("the template module requires Jinja2; install it or use the standalone kitup build") from exc
    return jinja2
