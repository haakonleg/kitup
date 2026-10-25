# `files`

Idempotent file, directory, and symlink management.

```python
from kitup.modules import files
```

Writes are atomic: content goes to a temporary file, is flushed and given its
metadata, then moved into place with `os.replace`. Parent directories are not
created automatically; use `directory()` first. Recursive copies are left to task
code using `directory()`, `copy()`, and `symlink()`.

## `file(ctx, path, *, state="present", content=None, mode=None, owner=None, group=None, sudo=False)`

Ensure a text file exists with optional content, or is absent.

- `content=None` leaves an existing file unchanged and creates a missing file
  empty. Symlinks are refused.
- `state="absent"` removes the file or symlink, but not a directory.
- `mode=None` preserves an existing mode; new files default to `0o644`.
- `owner` and `group` accept a name or numeric ID.
- `sudo=True` performs the operation through a privileged helper.

```python
files.file(ctx, "/etc/example.conf", content="enabled=true\n", mode=0o644, sudo=True)
files.file(ctx, "/etc/example.conf", state="absent", sudo=True)
```

## `copy(ctx, src, dest, *, mode=None, owner=None, group=None, sudo=False)`

Copy one regular file. `mode=None` preserves the source mode. Source symlinks are
refused.

- A bare filename in `src`, such as `"example.conf"`, resolves to the bundle's
  `files/example.conf`.
- Other relative paths resolve against the bundle directory.
- Absolute paths are used as-is.
- If `dest` ends with `/`, it is treated as a directory and the source filename
  is appended.

```python
files.copy(ctx, "example.conf", "/etc/example.conf", sudo=True)
files.copy(ctx, "dotfiles/.zshrc", Path.home() / ".zshrc")
files.copy(ctx, "example.conf", "/etc/example.d/", sudo=True)
```

## `line(ctx, path, line, *, match=None, state="present", sudo=False)`

Add, replace, or remove a line.

- `match=None` compares the full line; otherwise `match` is a regular expression.
- `state="present"` replaces the first match or appends the line.
- `state="absent"` removes all matches.

## `block(ctx, path, marker, content, *, state="present", sudo=False)`

Manage a marked region:

```text
# BEGIN marker
content
# END marker
```

`state="present"` creates or updates the block; `"absent"` removes it. Malformed
or duplicate markers raise a task failure.

## `directory(ctx, path, *, state="present", force=False, mode=None, owner=None, group=None, sudo=False)`

Ensure a directory exists or is absent.

- `state="absent"` removes an empty directory; `force=True` removes it recursively.
- `mode=None` preserves an existing mode; new directories default to `0o755`.
- Existing non-directory paths and symlinks are rejected.

## `symlink(ctx, target, path, *, sudo=False)`

Create or verify a symlink, replacing an existing file or symlink. Directories
are never replaced.

## Notes

- Identical content and metadata are a no-op.
- `--dry-run` reports changes without touching the filesystem; `--diff` prints
  unified diffs for changed text files.
- Writes never follow a destination symlink; the symlink itself is replaced.
- Only regular files are read or written. Non-UTF-8 files are rejected when
  editing lines or blocks.
- `sudo=True` routes every read, write, and metadata operation in that call
  through the privileged helper. Without it, operations run as the current
  user.
