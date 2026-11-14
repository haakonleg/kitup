# `uv`

Manage uv projects, per-user CLI tools, and uv configuration files with uv's native interface (`uv sync`, `uv tool`, `uv.toml`).

```python
from kitup.modules import uv
```

## `dependencies(names, *, project, state="present", group=None, python=None, extra_args=None)`

Declare dependencies of a uv project in its `pyproject.toml`, then sync the project (`uv.lock` and `.venv`) with `uv sync`.

### Parameters

- `names` — a requirement string or an iterable of them, e.g. `["requests>=2", "psycopg[binary]"]`, written verbatim into the project table. Registry requirements only: URLs, VCS, and local paths are rejected (they need `[tool.uv.sources]`), as are uv tool `@` specifiers; URL/path entries already in the table are left untouched. With `state="absent"`, entries must be plain package names (a specifier is ignored).
- `project` — the project directory containing `pyproject.toml`. A missing directory is initialized with `uv init --bare` (never for `state="absent"`); an existing `pyproject.toml` without a `[project]` table is rejected.
- `state` — `"present"` (default) adds missing entries and replaces entries with the same package name when the specifier differs. `"latest"` additionally upgrades bare names to the newest available version. `"absent"` removes entries by name.
- `group` — dependency group (PEP 735): edits the `<group>` key of `[dependency-groups]` and syncs that group.
- `python` — passed as `--python` to `uv init`/`uv sync` to select the project interpreter.
- `extra_args` — extra options for `uv sync` and `uv lock`, e.g. `["--no-index", "--find-links=/srv/wheels"]`.

### Result fields

- `added` — requirements added or replaced (`"present"`/`"latest"` results); empty when nothing changed.
- `removed` — table entries removed (`"absent"` results); empty when nothing was present.
- `dependencies` — the requested requirements, as given.
- `project` — the project directory.

```python
uv.dependencies(["requests>=2.32", "psycopg[binary]"], project="~/src/myapp")
uv.dependencies(["requests==2.32.3"], project="~/src/myapp")
uv.dependencies(["requests"], project="~/src/myapp", state="latest")
uv.dependencies(["ruff"], project="~/src/myapp", group="dev")
uv.dependencies(["old-lib"], project="~/src/myapp", state="absent")
```

## `tools(names, *, state="present", python=None, extra_args=None)`

Ensure per-user uv CLI tools are installed, up to date, or uninstalled.

### Parameters

- `names` — a tool requirement or an iterable, e.g. `["ruff", "httpie==3.2"]`, including uv's `{package}@{version}` / `{package}@latest` specifiers. Registry requirements only. With `state="absent"`, entries must be plain names.
- `state` — `"present"` (default) installs missing tools and reinstalls a tool whose requested specifier, extras, or marker differ from the installed one (`pkg@1.0` matches `pkg==1.0`; spacing doesn't matter), but leaves a satisfied bare name alone. `"latest"` additionally refreshes installed tools with `uv tool upgrade`, or reinstalls at `@latest` for exact `==` pins, which `uv tool upgrade` won't move. `"absent"` uninstalls by name. Reinstalling a `--with` tool under a different specifier is rejected.
- `python` — passed as `--python` to `uv tool install`/`uv tool upgrade`.
- `extra_args` — extra options for `uv tool install` and `uv tool upgrade`, e.g. `["--no-index", "--find-links=/srv/wheels"]`.

### Result fields

- `added` — tools installed or upgraded; empty when nothing changed.
- `removed` — tool names uninstalled; empty when nothing was installed.
- `tools` — the requested names, as given.

```python
uv.tools(["ruff", "httpie==3.2.2"])
uv.tools(["ruff", "httpie"], state="latest")
uv.tools(["httpie"], state="absent")
```

## `config(option, value=None, *, state="present", scope="user", project=None, path=None, sudo=None)`

Set or remove one option in a uv configuration file: system (`/etc/uv/uv.toml`), user (`$XDG_CONFIG_HOME/uv/uv.toml`, default `~/.config/uv/uv.toml`), or project (a project-level `uv.toml` or `[tool.uv]` in `pyproject.toml`). Precedence is project > user > system for scalars; a project-level `uv.toml` beats `[tool.uv]`.

### Parameters

- `option` — a (possibly dotted) TOML key such as `index-url`, written as `option = value`. Dotted options edit the matching sub-table when it exists (`pip.no-cache` → `[tool.uv.pip]`). Existing entries are matched by parsed value, so spacing and quote style don't cause rewrites. Array-of-tables (`[[tool.uv.index]]`) are rejected.
- `value` — required for `state="present"`: a string, a boolean (`true`/`false`), an integer, a float, or a list or tuple of strings as an array (`[]` when empty).
- `state` — `"present"` sets the option; `"absent"` removes it and takes no `value`.
- `scope` — default file to edit: `"system"`, `"user"` (default), or `"project"` (needs `project=`). User scope cannot run as root or with `sudo=True`.
- `project` — project directory whose `pyproject.toml` `[tool.uv]` table is edited with `scope="project"`.
- `path` — edit an explicit file instead of a scope default: a `pyproject.toml` path automatically uses its `[tool.uv]` table, a `uv.toml` path its top-level keys. A missing file is created (`0644`, parent directories `0755`).
- `sudo` — request privilege for reading and writing the file; defaults to `True` for `scope="system"` without an explicit `path`, and `False` otherwise. With an explicit `path`, `sudo=True` is allowed.

### Result fields

- `path`, `table`, `key` — the edited file, table (`None` for top-level keys), and key.
- `value` — the value written (a list for array values), or `None` for `state="absent"`.
- `toml_action` — `"added"`, `"replaced"`, `"removed"`, or `"unchanged"`.

```python
uv.config("index-url", "https://pypi.example.com/simple", scope="system")
uv.config("concurrent-downloads", 8, scope="user")
uv.config("python-downloads", "manual", scope="project", project="~/src/myapp")
uv.config("native-tls", state="absent", scope="user")
```

## Notes

- The integration suite exercises uv 0.13.5; other versions are untested.
- `dependencies` and `tools` take no `sudo`: they run with the invoking user's permissions, so the project directory must be writable by that user. Run kitup as root for root-owned projects; only `config` takes `sudo` (for the system-wide `/etc/uv/uv.toml`).
- `dependencies` edits only `pyproject.toml`; `uv sync` manages `uv.lock` and `.venv`.
- Without `group=`, the sync includes the default `dev` group; `group=` syncs the named group in addition.
- uv re-resolves the lockfile for its up-to-date checks, so an offline run can report a pending change even when the project is correct.
- A `tools(state="latest")` dry run uses `uv tool list --outdated` to decide which installed tools would change.
- `config(state="absent")` edits only the selected file. uv still applies the same option from the other config files it loads (project > user > system for scalars), so removing a user-scope option has no effect while the system file defines it.
