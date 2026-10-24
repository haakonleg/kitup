# `dnf`

RPM package, package-group, and repository management using `dnf5` (Fedora 44+).

```python
from kitup.modules import dnf
```

## `packages(ctx, names, *, state="present", group=False, weak_deps=False)`

Ensure packages or groups are present or absent.

- `names` — a name or an iterable of names.
- `state` — `"present"` or `"absent"`.
- `group` — treat names as package groups.
- `weak_deps` — install with `--setopt=install_weak_deps=False` when `False`.

Changes are reported with `added` or `removed` lists.

```python
@bundle.task("Install base packages", needs_sudo=True, tags={"packages"})
def install_base_packages(ctx):
    dnf.packages(ctx, ctx.config.PACKAGES)


@bundle.task("Remove legacy tools", needs_sudo=True)
def remove_legacy_tools(ctx):
    dnf.packages(ctx, ["old-tool"], state="absent")
```

## `autoremove(ctx)`

Remove unused package dependencies if `dnf repoquery --unneeded` reports any.

```python
@bundle.task("Remove unused packages", needs_sudo=True, tags={"packages"})
def remove_unused_packages(ctx):
    dnf.autoremove(ctx)
```

## `copr(ctx, project, *, state="present")`

Enable or remove a COPR project.

- `state="present"` enables the project unless it is already listed.
- `state="absent"` removes its repository file if configured.

```python
@bundle.task("Enable example COPR", needs_sudo=True, tags={"repos"})
def enable_example_copr(ctx):
    dnf.copr(ctx, "owner/project")
```

## `repository(ctx, source=None, *, state="present", name=None)`

Manage a repository from a `.repo` file.

- `source` — local path or URL of a `.repo` file; required for `"present"`.
- `name` — installed filename. Required for `"absent"`; for `"present"` it
  defaults to the source filename.

`"present"` uses `dnf config-manager addrepo --from-repofile=<source>`;
`"absent"` removes the installed file from dnf's repository directory.

```python
@bundle.task("Add vendor repository", needs_sudo=True, tags={"repos"})
def add_vendor_repository(ctx):
    dnf.repository(ctx, "https://example.test/tool.repo")


@bundle.task("Remove vendor repository", needs_sudo=True, tags={"repos"})
def remove_vendor_repository(ctx):
    dnf.repository(ctx, state="absent", name="tool.repo")
```

## `rpm(ctx, source)`

Install a release RPM from a URL or local path. `dnf install` handles an
already-installed package.

```python
@bundle.task("Install example release RPM", needs_sudo=True, tags={"repos"})
def install_example_release(ctx):
    dnf.rpm(ctx, "https://example.test/tool-release-2-1.noarch.rpm")
```

## Notes

- Package installs are verified with `rpm -q`; groups are matched by exact ID or
  name.
- Empty names, COPR projects, repository sources/names, and RPM sources are
  rejected.
- Mutating operations use sudo; mark tasks `needs_sudo=True`.
- Dry-run reports the would-be change without mutating anything.
