# AGENTS.md

Instructions for AI agents and contributors working in this repository.

## Project

kitup is a Python-driven Linux configuration management tool inspired by
Ansible, with a smaller scope. Profiles, bundles, tasks, conditions, and
composition are ordinary Python code. Tasks are idempotent: a run is resolved
into a plan and executed sequentially, fail-fast, changing only what is needed.

- Python **3.14+** is required.
- Read `pyproject.toml` for more information about dependencies and project
  configuration.

## Repository layout

```text
kitup/             # application package and run orchestration
├── modules/       # built-in idempotent task modules (public task API)
├── _privileged/   # privileged worker client, protocol, and server
├── _utils/        # shared internal helpers (files, HTTP, security)
└── output/        # terminal output and progress rendering
tests/
├── unit/          # default suite; no root, network, or host changes
└── integration/   # opt-in Fedora container suite
tools/             # integration runner and zipapp builder
docs/              # profile, facts, and per-module API reference
examples/          # example profiles and bundles
```

Execution flow: the CLI loads a profile, resolves bundle dependencies into a
plan, then runs the plan sequentially with a shared `TaskContext`. Put new
built-in task APIs in `kitup/modules/`; privileged filesystem operations go
through `kitup/_privileged/`.

## Setup

Always work inside a virtual environment for tests and quality checks. Reuse
`.venv/` when it exists; do not delete or recreate it. Create it only when it
is missing:

```bash
if [ ! -d .venv ]; then
    python3 -m venv .venv
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/python -m pip install -e '.[dev]'
fi
```

Re-run `.venv/bin/python -m pip install -e '.[dev]'` after changing
`pyproject.toml` dependencies.

## Tests

### Unit tests (required)

Run the full unit suite before finishing any change:

```bash
.venv/bin/python -m pytest tests/unit
```

Useful focused runs:

```bash
.venv/bin/python -m pytest tests/unit/test_modules_files.py
.venv/bin/python -m pytest tests/unit -k files -x
```

Unit tests MUST NOT require root, sudo, network access, or mutate the host.
Use `tmp_path`, `monkeypatch`, and the shared doubles in
`tests/unit/helpers.py` (`FakeCommandRunner`, `FakeSudo`, `PassthroughSudo`,
`make_context`).

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

The script builds `dist/kitup`, builds the container image, starts it, and runs
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
  `Self`, `Never`, `@override`, `Literal` for constrained values, `Final` for
  module-level constants, `importlib.resources`, and `dataclass(slots=True)`.
- Annotate every function and method in `kitup/` and `tools/`, including
  private helpers. Include parameter and return types.
- Do not annotate local variables unless inference is unclear. Annotate
  module-level constants with `Final` when they are public or their type is
  non-obvious.
- Tests and examples are exempt from mandatory annotations; annotate test
  helpers and fakes where it improves clarity.
- Use built-in generics (`list[str]`, `dict[str, Any]`), `X | None` instead of
  `Optional[X]`, and `collections.abc` for abstract types (`Callable`,
  `Iterable`, `Mapping`, `Sequence`).
- Put optional parameters after `*` as keyword-only arguments, matching the
  existing module APIs.
- Use `typing.Any` only at genuine boundaries (parsed JSON, dynamic values).
  Use `TYPE_CHECKING` for imports needed only by annotations.
- Every Python file in `kitup/` and `tools/` MUST start with a module docstring
  briefly explaining its purpose.
- Add docstrings to public classes, functions, and methods. Keep them concise
  and imperative. Use reStructuredText double backticks for code references,
  e.g. ``sudo=True``.
- Tests and examples are exempt from mandatory docstrings.
- Add inline comments for edge cases and non-obvious decisions. Keep all
  comments brief and limited to useful information; do not overexplain or
  include unnecessary details.
- Order imports: standard library, third-party, then local. Within the
  `kitup` package, use relative imports (`from ..context import TaskContext`).
  Tests use absolute imports (`from kitup.modules import files`).
- Name functions and variables `snake_case`, classes `PascalCase`, private
  helpers and module-private constants with a leading underscore
  (`_remove_path`, `_PRESENT_OR_ABSENT`).
- Raise `TaskFailed` for expected task failures and the specific
  `KitupError` subclass (`ConfigError`, `BundleError`, `ResolutionError`,
  `PrivilegeError`) elsewhere. Never swallow exceptions silently. Chain with
  `raise ... from exc`.
- Validate public API arguments at the top of the function using the helpers
  in `kitup/validation.py` (`require_path`, `require_text`, `require_state`,
  `require_collection`, `normalize_strings`, and friends).
- Use `pathlib.Path` internally. Accept `str | Path` at public boundaries and
  convert immediately.

### Module conventions

- Every public function in `kitup/modules/` takes `ctx: TaskContext` first,
  returns `None`, and documents its behavior.
- Modules MUST be idempotent: inspect current state first, change only what is
  needed, and call `ctx.changed(data, summary)` only when something actually
  changes.
- Modules MUST honor `ctx.dry_run`: report what would change without mutating
  the system.
- Use `ctx.probe([...])` for read-only queries and `ctx.run([...])` for
  mutations. Pass argv lists, never shell strings.
- Request privilege with `sudo=True` on `ctx.run` or the module helper. Never
  embed `sudo` in the command list.
- Expensive per-run work belongs in `ctx.memo(key, loader)`. Use `ctx.skip()`
  and `ctx.fail()` for control flow, and `ctx.note()` for extra context.

### Tests conventions

- Write pytest functions named `test_<behavior>`, not test classes.
- Test observable behavior and state transitions, not implementation details.
- For every new module behavior, cover the happy path, idempotency (run twice,
  assert a single change), dry-run behavior, and expected failures.
- Use `pytest.raises(..., match="...")` for error paths.
- Use `pytest.mark.parametrize` for input matrices.
- Do not mock what you can exercise with `tmp_path`; use the existing fakes
  for commands and sudo.

## Do

- DO add or update tests with every behavior change.
- DO run `pytest`, `ruff`, and `mypy` before declaring work complete.
- DO keep modules idempotent, dry-run safe, and fail-fast friendly.
- DO route privileged filesystem work through `kitup/_privileged/` and keep
  `worker.py`, `protocol.py`, and `_utils/file_helper.py` in sync.
- DO keep the privileged worker standard-library-only, free of runtime package
  imports, and without a `__main__` guard; it is embedded into a `python -c`
  script.
- DO keep `README.md`, `docs/modules/index.md`, and `docs/modules/<name>.md`
  user-facing: document the public API and commands, and omit internal
  implementation details or unnecessary information. Update them when public
  APIs or commands change.
- DO preserve bundle and module ordering semantics; dependencies run first and
  each bundle runs at most once.
- DO use `ctx.changed()` with a short summary and useful structured `data`.
- DO prefer small, focused changes that match the surrounding code.

## Don't

- DON'T use `shell=True`, `os.system`, or build shell command strings. Use
  argv lists through `ctx.run` / `ctx.probe`.
- DON'T call `subprocess` directly from task modules; use the context helpers.
- DON'T put `sudo` in an argv list or invoke it manually; use `sudo=True`.
- DON'T add non-idempotent operations or unconditional `ctx.changed()` calls.
- DON'T mutate the system during a dry run.
- DON'T use `print()` in library code. Use the output layer, `ctx.note()`, or
  the CLI's error reporting.
- DON'T add a runtime dependency without updating `pyproject.toml` and
  documenting it.
- DON'T import third-party packages in `_privileged/worker.py`.
- DON'T run `pytest tests/integration` directly; always use
  `tools/integration.sh`.
- DON'T weaken, skip, or delete tests to make a change pass.
- DON'T use mutable default arguments (`def f(items=[])`).
- DON'T change public APIs, task names, or CLI flags without discussing the
  change first. When changed, update tests and docs.

## Quality gates

Run all of these before finishing. All must pass:

```bash
.venv/bin/python -m pytest tests/unit
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy kitup tools
```

`ruff` is configured in `pyproject.toml` for Python 3.14 with a 120-character
line length, and formatting is enforced with `ruff format --check`. `mypy` runs
in strict mode for `kitup/` and `tools/`. Fix type errors rather than weakening
annotations or adding blanket ignores. Use narrow, justified `# noqa` or
`# type: ignore[...]` comments only when unavoidable.

If a change touches system-facing behavior, also run:

```bash
tools/integration.sh
```
