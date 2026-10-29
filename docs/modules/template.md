# `template`

Render [Jinja2](https://jinja.palletsprojects.com/) templates into managed
files. Templates are ordinary bundle resources, and rendering is idempotent:
the destination is compared with the result and only written when it differs.

```python
from kitup.modules import template
from kitup.registry import task
```

## `file(ctx, src, dest, *, variables=None, mode=None, owner=None, group=None, sudo=False)`

Render the template `src` and write the result to `dest`.

- `src` resolves like [`files.copy()`](files.md): a bare filename comes from
  the bundle's `files/` directory, other relative paths from the bundle
  directory, and absolute paths are used as-is.
- `variables` — an optional mapping of extra values exposed to the template.
- `dest` — destination file path. The parent directory must already exist; use
  [`files.directory()`](files.md) first.
- `mode=None` preserves an existing mode; new files default to `0o644`.
- `owner` and `group` accept a name or numeric ID.
- `sudo=True` performs the write as root.

```python
@task("Harden SSH", tags={"security"})
def harden_ssh(ctx):
    template.file(
        ctx,
        "sshd.conf.j2",
        "/etc/ssh/sshd_config.d/99-kitup.conf",
        mode=0o644,
        sudo=True,
    )
```

With `files/sshd.conf.j2` in the bundle:

```jinja
Port {{ config.SSH_PORT }}
PermitRootLogin no
PasswordAuthentication no
```

## Template variables

Templates receive three things:

- `config` — the merged configuration values, available as attributes or
  items: `{{ config.SSH_PORT }}` or `{{ config["SSH_PORT"] }}`.
- `facts` — the host facts snapshot: `{{ facts.hostname }}`.
- every key from `variables`, at the top level: `{{ workers }}`.

```python
@task("Write application environment")
def write_environment(ctx):
    template.file(
        ctx,
        "app.env.j2",
        "/etc/app.env",
        variables={"workers": 4},
        mode=0o640,
        sudo=True,
    )
```

```jinja
WORKERS={{ workers }}
HOSTNAME={{ facts.hostname }}
PACKAGES={{ config.PACKAGES | join(",") }}
```

`config` and `facts` are reserved and cannot be replaced through `variables`.
Variable names must be valid identifiers.

Undefined variables are errors rather than empty strings. Use Jinja's
`default` filter or `is defined` when a value is genuinely optional:

```jinja
LOG_LEVEL={{ config.LOG_LEVEL | default("info") }}
```

## Rendering behavior

- Templates are rendered as UTF-8 strings with Jinja2's default delimiters and
  whitespace handling. Autoescaping is disabled.
- A trailing newline in the template is preserved.
- `include`, `extends`, and custom loaders are not supported.
- Templates are trusted content, like profiles. No sandbox is used.

## Idempotency

- The rendered text is compared byte-for-byte with the destination.
- Identical content and metadata are a no-op.
- `--dry-run` renders the template and reports the change without writing.
  Template syntax and variable errors still fail in dry-run mode.
- `--diff` prints a unified diff for changed content.
- Writes never follow a destination symlink; the symlink itself is replaced.
- Destination directories and symlink template sources are rejected.

## Notes

- Any change to configuration values, facts, or variables can change the
  rendered result, so a later run may rewrite the file.
- Template sources must be regular UTF-8 text files.
- Very large privileged reads and writes are rejected; use [`command`](command.md)
  for very large files.
- Jinja2 is a runtime dependency. The standalone zipapp bundles it; a source
  installation gets it through `pip`.
