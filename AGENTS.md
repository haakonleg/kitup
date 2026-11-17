# AGENTS.md

Instructions for AI agents and contributors working in this repository.

## Project

kitup is a Python-driven Linux configuration management tool inspired by
Ansible, but smaller in scope and limited to local machines. Users describe a
machine's desired state, and kitup idempotently applies it. Projects and tasks
are ordinary Python code, so conditions, loops, and imports replace a separate
configuration language. The CLI resolves bundles and dependencies into an
ordered plan, then runs it sequentially, fail-fast, with a shared `TaskContext`
for variables, facts, and task results.

- Python **3.14+** is required.
- Read `pyproject.toml` for dependencies and project configuration.

## Repository layout

```text
kitup/             # CLI, project loading, resolution, and run orchestration
├── modules/       # built-in idempotent task modules
├── host_facts.py  # host fact collection
├── variables.py   # variable file loading and merging
├── presets.py     # reusable CLI option presets
├── _proxy.py      # ambient ctx, vars, and facts proxies
├── _privileged/   # privileged worker client, protocol, and server
├── _utils/        # shared filesystem, text, HTTP, security, and validation helpers
└── output/        # terminal output, progress, and diff rendering
tests/             # test suites and fixtures
├── helpers.py     # shared project builders
├── unit/          # default host-safe unit suite
└── integration/   # disposable Fedora container suite
tools/             # integration runner and zipapp builder
docs/              # project, CLI, facts, and module docs
examples/          # example project with bundles, variables, and presets
```

Put new built-in task APIs in `kitup/modules/`.

## Setup

Use `uv` for dependency management and command execution. Always work inside
the uv-managed `.venv/`; let `uv sync` manage it and never delete or recreate it
manually.

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
Project scaffolding shared with the integration suite lives in
`tests/helpers.py`.

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

The script builds `dist/kitup` and a container image, then runs the suite as a
non-root user; it requires `podman` and MUST NOT be run as root. Run it for
changes to system-facing modules, the privileged worker, privileges, or the
zipapp build, but be conservative: it is slow, so skip trivial changes such as
docs, comments, or refactors already covered by unit tests.

### Build check

When changing packaging or the zipapp builder:

```bash
./build.sh
./dist/kitup --version
```

## Code style

- Follow PEP 8 with 4-space indentation and a **120**-character line limit
  (`ruff` enforces `E501`).
- Do not add `from __future__ import annotations`; Python 3.14 defers
  annotations by default.
- Prefer modern Python 3.14+ syntax and typing features: PEP 695 generics,
  `Self`, `Never`, `@override`, `Literal`, `Final`, `importlib.resources`, and
  `dataclass(slots=True)`.
- Annotate every function and method in `kitup/` and `tools/`, including private
  helpers and their parameters and return types. Annotate local variables only
  when inference is unclear, and module-level constants with `Final` when public
  or non-obvious. Tests and examples are exempt from mandatory annotations and
  docstrings, though annotating test helpers and fakes is encouraged.
- Use built-in generics (`list[str]`, `dict[str, Any]`), `X | None` instead of
  `Optional[X]`, and `collections.abc` for abstract types (`Callable`,
  `Iterable`, `Mapping`, `Sequence`). Use `typing.Any` only at genuine
  boundaries (parsed JSON, dynamic values) and `TYPE_CHECKING` for
  annotation-only imports.
- Put optional parameters after `*` as keyword-only arguments, matching existing
  module APIs.
- Every Python file in `kitup/` and `tools/` MUST start with a brief module
  docstring explaining its purpose, and public classes, functions, and methods
  need concise, imperative docstrings. Use reStructuredText double backticks for
  code references (``sudo=True``), and reserve inline comments for edge cases
  and non-obvious decisions.
- Order imports: standard library, third-party, then local. Within `kitup`, use
  relative imports (`from ..context import TaskContext`); tests use absolute
  imports (`from kitup.modules import files`).
- Name functions and variables `snake_case`, classes `PascalCase`, private
  helpers and module-private constants with a leading underscore
  (`_matching_line`, `_PRESENT_OR_ABSENT`).

### Module conventions

- Public functions in `kitup/modules/` that need the task context call
  `ctx = current_context()` as the first statement after the docstring; private
  helpers take an explicit `ctx: TaskContext`. Never use the `ctx`, `vars`, or
  `facts` proxies in library code; they are for user code.
- Modules MUST be idempotent: inspect current state first and change only what
  is needed. Record exactly one flat `CallResult` per public module method
  invocation, merging multi-step operations into it, and return one from every
  method that can record a change, whether or not it changed anything. Use
  `ctx.changed(action=..., summary=..., **data)` for inline results or
  `ctx.changed(result)` when a helper already returns a `CallResult`.
- Every `CallResult` MUST set `action` to the API method identifier in
  ``"<module>.<method>"`` form, for example ``"command.run"`` or
  ``"files.copy"``. User task code may omit it; `ctx.changed` then uses
  ``"task.<function name>"``.
- Helpers that build `CallResult` MUST accept the caller's `action` rather than
  hard-coding one, unless the helper serves exactly one public method.
- Only module and user task code may call `ctx.changed(...)`; helpers,
  `TaskContext` methods, and the output layer return `CallResult` objects or
  data instead.
- Record no-change calls too, with `changed=False` and the same data fields as
  the changed case (empty lists or false flags where appropriate). Use these
  summary forms:
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
- Modules MUST honor `ctx.dry_run`: report what would change without mutating;
  check `ctx.dry_run` before every mutating `ctx.run()`. Use `ctx.probe([...])`
  for read-only queries and `ctx.run([...])` for mutations.
- Request privilege with `sudo=True` on `ctx.run` or the module helper. Never
  embed `sudo` in the command list.
- Expensive per-run work belongs in `ctx.memo(key, loader)`. Use `ctx.skip()`
  and `ctx.fail()` for control flow, and `ctx.note()` for extra context.

### Test conventions

- Add or update tests with every behavior change; for new module behavior, cover
  the happy path, idempotency (run twice, assert one change), dry-run, and
  failures.
- Write pytest functions named `test_<behavior>`, not test classes.
- Test observable behavior and state transitions, not implementation details.
- Use `pytest.raises(..., match="...")` for error paths.

## Guidelines

- Route Python-implemented privileged operations through `kitup/_privileged/`
  and keep `worker.py`, `protocol.py`, and shared helpers in sync. The worker
  must be standard-library-only, free of runtime package imports, and have no
  `__main__` guard because it is embedded into a `python -c` script.
- Raise `TaskFailed` for expected task failures and the specific `KitupError`
  subclass (`VarsError`, `BundleError`, `ResolutionError`, `PrivilegeError`)
  elsewhere. Never swallow exceptions silently; chain with `raise ... from exc`.
- Validate public API arguments at the top of the function using
  `kitup/_utils/validation.py` (`require_path`, `require_text`, `require_state`,
  `require_collection`, `normalize_strings`, and friends). Before adding generic
  validation, filesystem, or similar logic, check for an existing shared helper
  and extend `kitup/_utils/` rather than duplicating it.
- Use `pathlib.Path` internally. Accept `str | Path` at public boundaries and
  convert immediately.
- Resolve relative source paths with `ctx.resolve_source`: a bare name or a
  `files/<path>` resolves inside the merged `ctx.files` view (the bundle's
  `files/` directory, the project's `files/<bundle id>/` overlay, and any
  `--files-path` overlays, in increasing precedence), and any other relative
  path resolves against the bundle directory without overlays. Do not add
  module-specific path resolvers.
- Keep all documentation user-facing: document the public API and commands,
  omit internal implementation details or unnecessary information, and update it
  when public APIs or commands change.
- Pass argv lists instead of using `shell=True`, `os.system()`, or building
  shell command strings.
- Use the output layer, `ctx.note()`, or the CLI's error reporting instead of
  `print()` in library code.
- Document new runtime dependencies and update `pyproject.toml` and `uv.lock`.
- Avoid mutable default arguments (`def f(items=[])`).

## Rules

- Never run commands that might change the host system, including installing
  packages or writing files outside the repository or `/tmp`; run such commands
  only inside a disposable podman container.
- Discuss public API, task name, or CLI flag changes first.
- Never weaken, skip, or delete tests to make a change pass.

## Quality gates

Run all of these before finishing. All must pass:

```bash
uv lock --check
uv run --locked pytest tests/unit
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy kitup tools
```

`ruff` and `mypy` are configured in `pyproject.toml`; `mypy` runs in strict mode
for `kitup/` and `tools/`. Fix type errors rather than weakening annotations or
adding blanket ignores, and use narrow, justified `# noqa` or `# type: ignore[...]`
comments only when unavoidable.

Run `tools/integration.sh` when applicable; see
[Integration tests](#integration-tests-opt-in).
