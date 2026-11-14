# `dnf`

RPM package, package-group, repository, and dnf.conf configuration management using `dnf5`.

```python
from kitup.modules import dnf
```

## `packages(names, *, state="present", group=False, weak_deps=False, exclude=None)`

Ensure packages or groups are present or absent.

### Parameters

- `names` — a name or an iterable of names.
- `state` — `"present"` or `"absent"`.
- `group` — treat every name as a group.
- `weak_deps` — when `False`, install without weak dependencies.
- `exclude` — package name or iterable of names/globs to exclude during
  installation.

### Result fields

- `added` — package names installed (present results).
- `removed` — package names removed (absent results).
- `groups` — affected group names.
- `packages` — affected package names.

```python
dnf.packages(
    ["@multimedia", "ripgrep"],
    exclude=["PackageKit-gstreamer-plugin"],
)
dnf.packages(["old-tool"], state="absent")
```

## `swap(old, new, *, allowerasing=False)`

Replace `old` with `new`, unless `new` is already installed.

### Parameters

- `old` — package to remove.
- `new` — package to install.
- `allowerasing` allows dnf to remove conflicting packages.

### Result fields

- `removed` — old package.
- `installed` — new package.

```python
dnf.swap("ffmpeg-free", "ffmpeg", allowerasing=True)
```

## `autoremove()`

Remove unused package dependencies.

### Result fields

- `removed` — package names removed; empty when nothing needed removal.

```python
dnf.autoremove()
```

## `copr(project, *, state="present")`

Enable or remove a COPR project.

### Parameters

- `project` — COPR project in `owner/project` form.
- `state="present"` enables the project unless it is already enabled.
- `state="absent"` removes the project's repository if configured.

### Result fields

- `repository` — COPR project.

```python
dnf.copr("owner/project")
```

## `repository(source=None, *, state="present", name=None)`

Manage a repository from a `.repo` file.

### Parameters

- `source` — local path or URL of a `.repo` file; required for `"present"`.
  Relative local paths resolve from the current working directory. Absolute
  paths or URLs are used as-is. `~` is expanded.
- `state="present"` adds the repository; `"absent"` removes the installed
  repository file.
- `name` — installed filename. Required for `"absent"`; for `"present"` it
  defaults to the source filename.

### Result fields

- `repository` — resolved source or installed filename.
- `path` — installed `.repo` file.

```python
dnf.repository("https://example.test/tool.repo")
dnf.repository(state="absent", name="tool.repo")
```

## `rpm(sources)`

Install one or more release RPMs from URLs or local paths.

### Parameters

- `sources` — a single source or an iterable. Local paths resolve from the
  current working directory. URLs and absolute paths are used as-is, and `~` is
  expanded. Already-installed packages are left alone.

### Result fields

- `installed` — package names installed by this call; empty when nothing was
  installed.

```python
dnf.rpm(
    [
        "https://example.test/tool-release-2-1.noarch.rpm",
        "/var/tmp/extra-release-1-1.noarch.rpm",
    ],
)
```

## `config(option, value=None, *, section="main", state="present", path=None, sudo=True)`

Set or remove one option in a `dnf.conf`-style INI file.

### Parameters

- `option` — option name, written canonically as `option = value`.
- `value` — required for `state="present"` and rejected with `state="absent"`.
  A string is written as-is, a boolean as `True`/`False`, an integer in
  decimal form, and a list or tuple of strings as a comma-separated list.
- `section` — INI section to edit, default `"main"`.
- `state="present"` sets the option; `state="absent"` removes it.
- `path` — config file to edit, default `/etc/dnf/dnf.conf`. A missing file
  is created with mode `0644`. Pass an explicit path with `sudo=False` for
  user-scoped config files.
- `sudo` — request privilege when reading and writing the file; default
  `True`.

### Result fields

- `path`, `section`, `option` — the edited file, section, and option.
- `value` — the normalized value written, or `None` when `state="absent"`.
- `ini_action` — `"added"`, `"replaced"`, `"removed"`, or `"unchanged"`.

```python
dnf.config("best", True)
dnf.config("installonly_limit", 2)
dnf.config("max_parallel_downloads", state="absent")
dnf.config(
    "keepcache",
    True,
    path="/home/alice/.config/dnf/dnf.conf",
    sudo=False,
)
```

## Notes

- `exclude` is only valid with `state="present"`.
- Mutating operations request sudo themselves.
- `config()` edits `/etc/dnf/dnf.conf` by default; dnf reads the file on every
  run, so no reload is needed after a change.
