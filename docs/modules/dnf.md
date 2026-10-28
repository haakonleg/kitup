# `dnf`

RPM package, package-group, and repository management using `dnf5` (Fedora 44+).

```python
from kitup.modules import dnf
from kitup.registry import task
```

## `packages(ctx, names, *, state="present", group=False, weak_deps=False, exclude=None)`

Ensure packages or groups are present or absent.

- `names` — a name or an iterable of names.
- `state` — `"present"` or `"absent"`.
- `group` — treat every name as a group.
- `weak_deps` — when `False`, install without weak dependencies.
- `exclude` — package name or iterable of names/globs to exclude during
  installation.

```python
@task("Install multimedia tools", tags={"packages"})
def install_multimedia_tools(ctx):
    dnf.packages(
        ctx,
        ["@multimedia", "ripgrep"],
        exclude=["PackageKit-gstreamer-plugin"],
    )


@task("Remove legacy tools")
def remove_legacy_tools(ctx):
    dnf.packages(ctx, ["old-tool"], state="absent")
```

## `swap(ctx, old, new, *, allowerasing=False)`

Replace `old` with `new`, unless `new` is already installed.

- `allowerasing` allows dnf to remove conflicting packages.

```python
@task("Install full ffmpeg", tags={"packages"})
def install_full_ffmpeg(ctx):
    dnf.swap(ctx, "ffmpeg-free", "ffmpeg", allowerasing=True)
```

## `autoremove(ctx)`

Remove unused package dependencies.

```python
@task("Remove unused packages", tags={"packages"})
def remove_unused_packages(ctx):
    dnf.autoremove(ctx)
```

## `copr(ctx, project, *, state="present")`

Enable or remove a COPR project.

- `state="present"` enables the project unless it is already enabled.
- `state="absent"` removes the project's repository if configured.

```python
@task("Enable example COPR", tags={"repos"})
def enable_example_copr(ctx):
    dnf.copr(ctx, "owner/project")
```

## `repository(ctx, source=None, *, state="present", name=None)`

Manage a repository from a `.repo` file.

- `source` — local path or URL of a `.repo` file; required for `"present"`.
  Relative paths resolve like `files.copy()`: a bare filename comes from the
  bundle's `files/` directory, other relative paths from the bundle directory,
  and absolute paths or URLs are used as-is.
- `name` — installed filename. Required for `"absent"`; for `"present"` it
  defaults to the source filename.

`"present"` adds the repository; `"absent"` removes the installed repository
file.

```python
@task("Add vendor repository", tags={"repos"})
def add_vendor_repository(ctx):
    dnf.repository(ctx, "https://example.test/tool.repo")


@task("Remove vendor repository", tags={"repos"})
def remove_vendor_repository(ctx):
    dnf.repository(ctx, state="absent", name="tool.repo")
```

## `rpm(ctx, sources)`

Install one or more release RPMs from URLs or local paths. `sources` may be a
single value or an iterable. Local paths follow the same bundle-relative rules
as `files.copy()`; URLs and absolute paths are used as-is. Already-installed
packages are left alone.

```python
@task("Install example release RPMs", tags={"repos"})
def install_example_releases(ctx):
    dnf.rpm(
        ctx,
        [
            "https://example.test/tool-release-2-1.noarch.rpm",
            "/var/tmp/extra-release-1-1.noarch.rpm",
        ],
    )
```

## Notes

- `exclude` is only valid with `state="present"`.
- Mutating operations request sudo themselves.
