# `files`

Idempotent file, directory, symlink, and file-move management.

```python
from kitup.modules import files
```

Parent directories are not created automatically; use `directory()` first.
Recursive copies are left to task code using `directory()`, `copy()`, and
`symlink()`.

For remote file download over HTTP(S), use the [download](download.md) module.

## `file(ctx, path, *, state="present", content=None, mode=None, owner=None, group=None, sudo=False)`

Ensure a text file exists with optional content, or is absent.

### Parameters

- `path` — file to create, update, or remove.
- `content=None` leaves an existing file unchanged and creates a missing file
  empty. Symlinks are refused.
- `state="absent"` removes the file or symlink, but not a directory.
- `mode=None` preserves an existing mode; new files default to `0o644`.
- `owner` and `group` accept a name or numeric ID.
- `sudo=True` performs the operation as root.

### Result fields

- `path` — managed file path.

```python
files.file(ctx, "/etc/example.conf", content="enabled=true\n", mode=0o644, sudo=True)
files.file(ctx, "/etc/example.conf", state="absent", sudo=True)
```

## `copy(ctx, src, dest, *, mode=None, owner=None, group=None, sudo=False)`

Copy one regular file. `mode=None` preserves the source mode. Source symlinks are
refused.

### Parameters

- `src` — source file. A bare filename such as `"example.conf"` resolves to the
  bundle's `files/example.conf`; other relative paths resolve against the bundle
  directory; absolute paths are used as-is.
- `dest` — destination path. If it ends with `/`, it is treated as a directory
  and the source filename is appended.
- `mode`, `owner`, and `group` override the destination metadata. `owner` and
  `group` accept a name or numeric ID.
- `sudo=True` performs the operation as root.

### Result fields

- `path` — destination path.
- `src` — resolved source path.

```python
files.copy(ctx, "example.conf", "/etc/example.conf", sudo=True)
files.copy(ctx, "dotfiles/.zshrc", Path.home() / ".zshrc")
files.copy(ctx, "example.conf", "/etc/example.d/", sudo=True)
```

## `move(ctx, src, dest, *, mode=None, owner=None, group=None, sudo=False)`

Move one regular file, preserving its mode and ownership when renamed.

### Parameters

- `src` — source file. Unlike `copy()`, it is used as given and is not resolved
  against the bundle.
- `dest` — destination path. If it ends with `/`, it is treated as a directory
  and the source filename is appended.
- `mode`, `owner`, and `group` override the destination metadata after the move.
  Without them, a rename preserves the source metadata; a cross-filesystem copy
  is owned by the moving process.
- `sudo=True` performs the move and metadata changes as root.

When both paths are on the same filesystem, the file is moved without copying.
Across filesystems, kitup copies the file and removes the source. If `src` is
already missing and `dest` exists, the move is treated as complete and only
requested metadata is reconciled. Directories, symlink sources, and symlink
destinations are rejected.

### Result fields

- `dest` — destination path.
- `src` — source path.

```python
files.move(
    ctx,
    "/tmp/example.key",
    "/etc/pki/rpm-gpg/RPM-GPG-KEY-example",
    mode=0o644,
    owner="root",
    group="root",
    sudo=True,
)
files.move(ctx, "/tmp/example.conf", "/etc/example.d/", sudo=True)
```

## `line(ctx, path, line, *, match=None, state="present", sudo=False)`

Add, replace, or remove a line.

### Parameters

- `path` — file to edit.
- `line` — desired line, without a trailing newline.
- `match=None` compares the full line; otherwise `match` is a regular expression.
- `state="present"` replaces the first match or appends the line.
- `state="absent"` removes all matches.
- `sudo=True` edits the file through the privileged worker.

### Result fields

- `path` — edited file path.

## `block(ctx, path, marker, content, *, state="present", sudo=False)`

Manage a marked region:

```text
# BEGIN marker
content
# END marker
```

### Parameters

- `path` — file to edit.
- `marker` — block name used in the `# BEGIN` and `# END` markers.
- `content` — block body. A trailing newline is added when missing.
- `state="present"` creates or updates the block; `"absent"` removes it.
- `sudo=True` edits the file through the privileged worker.

Malformed or duplicate markers raise a task failure.

### Result fields

- `path` — edited file path.

## `directory(ctx, path, *, state="present", force=False, mode=None, owner=None, group=None, sudo=False)`

Ensure a directory exists or is absent.

### Parameters

- `path` — directory to create, update, or remove.
- `state="absent"` removes an empty directory; `force=True` removes it
  recursively.
- `mode=None` preserves an existing mode; new directories default to `0o755`.
- `owner` and `group` accept a name or numeric ID.
- `sudo=True` performs the operation as root.

Existing non-directory paths and symlinks are rejected.

### Result fields

- `path` — managed directory path.

## `symlink(ctx, target, path, *, sudo=False)`

Create or verify a symlink, replacing an existing file or symlink. Directories
are never replaced.

### Parameters

- `target` — symlink target.
- `path` — symlink path.
- `sudo=True` creates the symlink through the privileged worker.

### Result fields

- `path` — symlink path.
- `target` — symlink target.

## Notes

- Identical content and metadata are a no-op.
- `--dry-run` reports changes without touching the filesystem; `--diff` prints
  unified diffs for changed text files.
- Writes never follow a destination symlink; the symlink itself is replaced.
- Only regular files are read or written. Non-UTF-8 files are rejected when
  editing lines or blocks.
- `sudo=True` runs the operation as root; otherwise it runs as the current user.
- Very large privileged reads and writes are rejected. Use `command` for very large
  files.
