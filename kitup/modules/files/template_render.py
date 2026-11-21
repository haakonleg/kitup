"""Idempotent Jinja2 template rendering to managed files."""

from collections.abc import Mapping
from functools import cache
from pathlib import Path
from typing import Any, Final

from ..._utils import file_actions
from ..._utils.file_actions import PathPolicy, require_path_kind
from ..._utils.paths import expand_path
from ..._utils.validation import normalize_mode, require_bool, require_metadata, require_path
from ...context import TaskContext, current_context
from ...errors import TaskFailed
from ...results import CallResult
from .jinja_helpers import JINJA_FILTERS, JINJA_TESTS

_RESERVED_VARIABLES: Final = frozenset({"vars", "facts"})


def template(
    src: str | Path,
    dest: str | Path,
    *,
    variables: Mapping[str, Any] | None = None,
    trim_blocks: bool = True,
    lstrip_blocks: bool = False,
    mode: int | str | None = None,
    owner: str | int | None = None,
    group: str | int | None = None,
    sudo: bool = False,
) -> CallResult:
    """Render a Jinja2 template into a managed file.

    ``src`` resolves like :func:`kitup.modules.files.copy`. Absolute paths are
    used as-is.

    ``trim_blocks`` removes the first newline after a block tag, and
    ``lstrip_blocks`` strips leading whitespace before one.

    Templates see ``vars`` (the merged variable values), ``facts`` (the
    system facts snapshot), and any keys supplied through ``variables``.
    Undefined variables are errors. The rendered text is compared with the
    destination and only written when it differs.
    """
    ctx = current_context()
    require_path(src, "template source")
    require_path(dest, "template destination")
    require_metadata(owner, group)
    mode = normalize_mode(mode)
    require_bool(sudo, "template sudo")
    require_bool(trim_blocks, "template trim_blocks")
    require_bool(lstrip_blocks, "template lstrip_blocks")
    extra = _require_variables(variables)
    target = expand_path(dest, error=TaskFailed)

    source = ctx.resolve_source(src)
    source_label = ctx.source_label(src)
    text = _read_template(ctx, source)
    rendered = _render(ctx, source, text, extra, trim_blocks=trim_blocks, lstrip_blocks=lstrip_blocks)

    return ctx.changed(
        file_actions.apply_bytes(
            ctx,
            target,
            rendered.encode("utf-8"),
            mode=mode,
            owner=owner,
            group=group,
            summary=f"render {source_label} -> {target}",
            unchanged_summary=f"ensure {target} from {source_label}",
            data={"dest": str(target), "src": str(source)},
            sudo=sudo,
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
    require_path_kind(source, info, policy=PathPolicy.FILE, label="template source", missing_ok=False)
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
    *,
    trim_blocks: bool,
    lstrip_blocks: bool,
) -> str:
    jinja2 = _load_jinja2()
    environment = _jinja_environment(trim_blocks=trim_blocks, lstrip_blocks=lstrip_blocks)
    context = {
        # Builtin vars() exposes the Vars namespace as a mapping.
        "vars": dict(vars(ctx.vars)),
        "facts": dict(ctx.facts),
        **variables,
    }
    try:
        template = environment.from_string(text)
        rendered = str(template.render(context))
    except jinja2.TemplateSyntaxError as exc:
        location = f"{source}:{exc.lineno}" if exc.lineno else str(source)
        raise TaskFailed(f"template syntax error in {location}: {exc.message}") from exc
    except jinja2.UndefinedError as exc:
        raise TaskFailed(f"undefined variable in template {source}: {exc}") from exc
    except jinja2.TemplateError as exc:
        raise TaskFailed(f"template error in {source}: {exc}") from exc
    except Exception as exc:
        raise TaskFailed(f"template rendering error in {source}: {exc}") from exc

    return _preserve_trailing_newlines(text, rendered)


def _preserve_trailing_newlines(source: str, rendered: str) -> str:
    """Restore trailing newlines that rendering removed from the template."""
    source_count = len(source) - len(source.rstrip("\n"))
    rendered_count = len(rendered) - len(rendered.rstrip("\n"))
    return rendered + "\n" * max(source_count - rendered_count, 0)


@cache
def _load_jinja2() -> Any:
    try:
        import jinja2  # noqa: PLC0415 - lazy import reports a task-level error
    except ImportError as exc:  # pragma: no cover - packaging guarantees the dependency
        raise TaskFailed("the template module requires Jinja2; install it or use the standalone kitup build") from exc
    return jinja2


@cache
def _jinja_environment(*, trim_blocks: bool = True, lstrip_blocks: bool = False) -> Any:
    """Return a cached Jinja2 environment for the given whitespace settings."""
    jinja2 = _load_jinja2()
    environment = jinja2.Environment(
        undefined=jinja2.StrictUndefined,
        trim_blocks=trim_blocks,
        lstrip_blocks=lstrip_blocks,
        keep_trailing_newline=True,
        autoescape=False,
    )
    environment.filters.update(JINJA_FILTERS)
    environment.tests.update(JINJA_TESTS)
    return environment
