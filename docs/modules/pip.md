# `pip`

Install and remove Python packages with pip — in the system Python by default, or inside a virtual environment — and edit pip's configuration files.

```python
from kitup.modules import pip
```

## `packages(names, *, state="present", venv=None, python=None, extra_args=None, sudo=False)`

Ensure Python packages are installed, up to date, or removed.

### Parameters

- `names` — a package or an iterable of packages, e.g. `["flask==3.0", "psycopg[binary]"]`. Entries may include version specifiers, extras, VCS or archive URLs, and local paths. With `state="absent"`, entries must be plain package names (a version specifier is ignored); URLs and paths cannot be removed.
- `state` — `"present"` (default) installs missing packages and adjusts the installed version to a requested specifier, but leaves a bare name that is already installed alone. `"latest"` also updates bare names to the newest available version. `"absent"` uninstalls the named packages.
- `venv` — virtual environment to install into. A missing venv is created automatically (never for `state="absent"`); an existing directory that is not a venv is rejected.
- `python` — interpreter used to create a missing venv, or the one pip runs as without a venv. A bare name is resolved via `PATH`; when `None` (default), the system interpreter is found automatically.
- `extra_args` — extra arguments for `pip install` and `pip uninstall`, e.g. `["--no-index", "--find-links=/srv/wheels"]`. For an externally-managed system Python (PEP 668) without a venv, pass `"--break-system-packages"`, usually with `sudo=True`. Don't pass `--force-reinstall`, `--ignore-installed`, or `--target` — see Notes.
- `sudo` — request privilege for venv creation, install, and uninstall. With `sudo=True` on the system Python, packages in your user site (`~/.local`) are ignored when deciding what to change.

### Result fields

- `added` — package names installed (`"present"`/`"latest"` results); empty when nothing changed.
- `removed` — package names uninstalled (`"absent"` results); empty when nothing was installed.
- `packages` — the requested names, as given.
- `venv` — the virtual environment path, when `venv` was given.

```python
pip.packages(["gunicorn==23.0", "psycopg[binary]"], venv="/opt/myapp/venv")
pip.packages(["requests==2.32.3"], venv="/opt/myapp/venv")
pip.packages(["requests"], state="latest", venv="/opt/myapp/venv")
pip.packages(["old-tool"], state="absent", venv="/opt/myapp/venv")
pip.packages(["httpie"], sudo=True, extra_args=["--break-system-packages"])
```

## `config(option, value=None, *, section="global", state="present", scope="user", venv=None, path=None, sudo=None)`

Set or remove one option in a pip configuration file: global (`/etc/pip.conf`), user (`$XDG_CONFIG_HOME/pip/pip.conf`, default `~/.config/pip/pip.conf`), or site (`<venv>/pip.conf`).

### Parameters

- `option` — option name such as `index-url`, written as `option = value`. Existing entries are matched in pip's normalized form (any case, `-`/`_`/`.`, `=` or `:`), so a line like `Find-Links: …` is replaced rather than duplicated.
- `value` — required for `state="present"`: a string (stripped, written as-is), a boolean (`true`/`false`), an integer, or a list or tuple of strings as a multi-line value with one item per line. Negative boolean options such as `no-cache-dir` are enabled with a falsy value, e.g. `False`.
- `section` — INI section, default `"global"`. Per-command sections such as `[install]` override `[global]`.
- `state` — `"present"` sets the option; `"absent"` removes it and takes no `value`.
- `scope` — default file to edit: `"global"`, `"user"` (default), or `"site"` (needs `venv` and an existing venv). User scope cannot run as root or with `sudo=True`.
- `venv` — venv whose `pip.conf` is edited with `scope="site"`.
- `path` — edit an explicit file instead of a scope default; a missing file is created (`0644`, parent directories `0755`).
- `sudo` — request privilege for reading and writing the file; defaults to `True` for `scope="global"` without an explicit `path`, and `False` otherwise.

### Result fields

- `path`, `section`, `option` — the edited file, section, and option.
- `value` — the value written (a list for multi-line values), or `None` for `state="absent"`.
- `ini_action` — `"added"`, `"replaced"`, `"removed"`, or `"unchanged"`.

```python
pip.config("index-url", "https://pypi.example.com/simple", scope="global")
pip.config("find-links", ["/srv/wheels", "https://mirror.example.com/wheels"], scope="user")
pip.config("timeout", 10, scope="site", venv="/opt/myapp/venv")
pip.config("retries", state="absent", scope="user")
```

## Notes

- Requires pip 23.0 or newer.
- With `state="present"` or `"latest"`, pip decides what needs to change, so specifiers, extras, URLs, and paths behave like a manual `pip install`. Don't use `--force-reinstall`, `--ignore-installed`, or `--target` in `extra_args`: the task cannot confirm the result with them and fails even after a successful install.
- `config(state="absent")` edits only the selected file. pip still applies the same option from the other config files it loads (site overrides user overrides global), so removing a user-scope option has no effect while the global file defines it.
