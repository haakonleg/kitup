# AGENTS.md

Instructions for AI agents and contributors working in this repository.

## Project

kitup is a Python-driven Linux configuration management tool inspired by
Ansible, with a smaller scope. Projects, bundles, tasks, conditions, and
composition are ordinary Python code. Tasks are idempotent. The CLI loads a
project, resolves bundle dependencies into a plan, then runs it sequentially,
fail-fast, with a shared `TaskContext`, changing only what is needed.

- Python **3.14+** is required.
- Read `pyproject.toml` for dependencies and project configuration.

## Repository layout

```text
kitup/             # application package and run orchestration
├── modules/       # built-in idempotent task modules (public task API)
├── _privileged/   # privileged worker client, protocol, and server
├── _utils/        # shared internal helpers (files, HTTP, security)
└── output/        # terminal output and progress rendering
tests/
├── unit/          # default suite
└── integration/   # Fedora container suite
tools/             # integration runner and zipapp builder
docs/              # project, CLI, facts, and per-module API reference
examples/          # example projects and bundles
```

Put new built-in task APIs in `kitup/modules/`.

## Setup

Use `uv` for dependency management and command execution.

Always work inside the uv-managed `.venv/`. Let `uv sync` manage it; do not
delete or recreate it manually.

```bash
uv sync
```

`uv sync` creates or updates `.venv/` from `uv.lock` and installs the `dev`
dependency group. Run `uv lock` after changing dependencies in `pyproject.toml`,
then `uv sync`. Use `uv sync --locked` in automation.

## Tests

### Unit tests (required)

Run the full suite via the quality gates below. Useful focused runs:

```bash
uv run pytest tests/unit/test_modules_files.py
uv run pytest tests/unit -k files -x
```

Unit tests MUST NOT require root, sudo, network access, or mutate the host.
Use `tmp_path`, `monkeypatch`, and the shared doubles in
`tests/unit/helpers.py` (`FakeCommandRunner`, `FakeSudo`, `PassthroughSudo`,
`make_context`); prefer exercising real files under `tmp_path` over mocks.

Tests run in random order via `pytest-randomly`. If a failure looks
order-dependent, rerun with `--randomly-seed=last`; use `-p no:randomly` for
deterministic ordering.

### Integration tests (opt-in)

Integration tests run against a disposable, privileged Fedora container with
systemd. They are gated behind `KITUP_INTEGRATION=1` and MUST be run through
the script, never by invoking pytest on `tests/integration` directly:

```bash
tools/integration.sh
```

The script builds `dist/kitup`, builds and starts the container image, and runs
the suite as a non-root user. It requires `podman` and MUST NOT be run as root.
Run it when a change affects system-facing modules, the privileged worker,
privileges, or the zipapp build.

### Build check

When changing packaging or the zipapp builder:

```bash
./build.sh
./dist/kitup --version
```

## Code style

- Follow PEP 8. Use 4-space indentation and a maximum line length of **120**
  characters (`ruff` enforces `E501`).
- Do not add `from __future__ import annotations`; Python 3.14 defers
  annotations by default.
- Prefer modern Python 3.14+ syntax and typing features: PEP 695 generics,
  `Self`, `Never`, `@override`, `Literal`, `Final`, `importlib.resources`, and
  `dataclass(slots=True)`.
- Annotate every function and method in `kitup/` and `tools/`, including private
  helpers and parameter and return types. Do not annotate local variables unless
  inference is unclear; annotate module-level constants with `Final` when public
  or non-obvious. Tests and examples are exempt from mandatory annotations and
  docstrings, though annotating test helpers and fakes where it improves clarity
  is encouraged.
- Use built-in generics (`list[str]`, `dict[str, Any]`), `X | None` instead of
  `Optional[X]`, and `collections.abc` for abstract types (`Callable`,
  `Iterable`, `Mapping`, `Sequence`). Use `typing.Any` only at genuine
  boundaries (parsed JSON, dynamic values) and `TYPE_CHECKING` for
  annotation-only imports.
- Put optional parameters after `*` as keyword-only arguments, matching existing
  module APIs.
- Every Python file in `kitup/` and `tools/` MUST start with a module docstring
  briefly explaining its purpose. Add concise, imperative docstrings to public
  classes, functions, and methods; use reStructuredText double backticks for code
  references (``sudo=True``). Add brief inline comments only for edge cases and
  non-obvious decisions.
- Order imports: standard library, third-party, then local. Within `kitup`, use
  relative imports (`from ..context import TaskContext`); tests use absolute
  imports (`from kitup.modules import files`).
- Name functions and variables `snake_case`, classes `PascalCase`, private
  helpers and module-private constants with a leading underscore
  (`_matching_line`, `_PRESENT_OR_ABSENT`).

### Module conventions

- Every public function in `kitup/modules/` takes `ctx: TaskContext` first.
- Modules MUST be idempotent: inspect current state first and change only what
  is needed. Use `ctx.changed(action=..., summary=..., **data)` for inline
  results or `ctx.changed(result)` when a helper already returns a `CallResult`.
- Every `CallResult` MUST set `action` to the API method identifier in
  ``"<module>.<method>"`` form, for example ``"command.run"`` or
  ``"files.copy"``. User task code may omit it; `ctx.changed` then uses
  ``"task.<function name>"``.
- Helpers that build `CallResult` MUST accept the caller's `action` rather than
  hard-coding one, unless the helper serves exactly one public method.
- Record no-change calls too, with `changed=False`. Use these summary forms:
  - Changed: concise action/past-tense summary, e.g. ``write /etc/x`` or
    ``enabled foo.service``.
  - No change: ``ensure <desired state>``, e.g. ``ensure file /etc/x`` or
    ``ensure foo.service is enabled``; use ``ensure <resource> is absent`` for
    removals.
  - Guarded command skip: ``skip <command>``.
  - Simulated dry-run command: ``would ...``; other modules report their normal
    changed summary.
  - ``changed=False`` probes may use the command or operation as the summary.
  Join multiple steps with ``"; "``.
- No-change calls use the same data fields as the changed case, with empty lists
  or false flags where appropriate.
- Modules MUST honor `ctx.dry_run`: report what would change without mutating;
  check `ctx.dry_run` before every mutating `ctx.run()`. Use `ctx.probe([...])`
  for read-only queries and `ctx.run([...])` for mutations.
- Request privilege with `sudo=True` on `ctx.run` or the module helper. Never
  embed `sudo` in the command list.
- Expensive per-run work belongs in `ctx.memo(key, loader)`. Use `ctx.skip()`
  and `ctx.fail()` for control flow, and `ctx.note()` for extra context.

### Test conventions

- Add or update tests with every behavior change. For every new module behavior,
  cover the happy path, idempotency (run twice, assert a single change), dry-run
  behavior, and expected failures.
- Write pytest functions named `test_<behavior>`, not test classes.
- Test observable behavior and state transitions, not implementation details.
- Use `pytest.raises(..., match="...")` for error paths.

## Do

- DO record exactly one flat `CallResult` per public module method invocation
  and merge multi-step operations into it.
- DO return `CallResult` from every method that can record a change, whether or
  not this invocation changed anything.
- DO route Python-implemented privileged operations through `kitup/_privileged/`
  and keep `worker.py`, `protocol.py`, and shared helpers in sync. The worker
  must be standard-library-only, free of runtime package imports, and have no
  `__main__` guard because it is embedded into a `python -c` script.
- DO keep `README.md`, `docs/modules/index.md`, and `docs/modules/<name>.md`
  user-facing: document the public API and commands, omit internal
  implementation details or unnecessary information, and update them when public
  APIs or commands change.
- DO raise `TaskFailed` for expected task failures and the specific `KitupError`
  subclass (`VarsError`, `BundleError`, `ResolutionError`, `PrivilegeError`)
  elsewhere. Never swallow exceptions silently; chain with `raise ... from exc`.
- DO validate public API arguments at the top of the function using
  `kitup/_utils/validation.py` (`require_path`, `require_text`, `require_state`,
  `require_collection`, `normalize_strings`, and friends).
- DO use `pathlib.Path` internally. Accept `str | Path` at public boundaries
  and convert immediately.
- DO resolve relative source paths with `ctx.resolve_source`: a bare name maps
  to the bundle's `files/` directory, other relative paths to the bundle
  directory. Do not add module-specific path resolvers.
- DO check for an existing shared helper before implementing generic validation,
  filesystem, or similar logic. If none exists and the logic is generic, add it
  to `kitup/_utils/` (`validation.py` for argument checks) rather than duplicating it.

## Don't

- DON'T record changes from helpers, `TaskContext` methods, or the output
  layer; only module and user task code may call `ctx.changed(...)`. Helpers
  return `CallResult` objects or data instead.
- DON'T use `shell=True`, `os.system`, or build shell command strings; pass argv
  lists.
- DON'T use `print()` in library code. Use the output layer, `ctx.note()`, or the
  CLI's error reporting.
- DON'T add a runtime dependency without updating `pyproject.toml`, running
  `uv lock`, and documenting it.
- DON'T weaken, skip, or delete tests to make a change pass.
- DON'T use mutable default arguments (`def f(items=[])`).
- DON'T change public APIs, task names, or CLI flags without discussing the
  change first. When changed, update tests and docs.

## Quality gates

Run all of these before finishing. All must pass:

```bash
uv lock --check
uv run --locked pytest tests/unit
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy kitup tools
```

`ruff` and `mypy` are configured in `pyproject.toml`. `mypy` runs in strict
mode for `kitup/` and `tools/`. Fix type errors rather than weakening
annotations or adding blanket ignores. Use narrow, justified `# noqa` or
`# type: ignore[...]` comments only when unavoidable.

Run `tools/integration.sh` when applicable; see
[Integration tests](#integration-tests-opt-in).
