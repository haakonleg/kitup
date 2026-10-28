# `decompress`

Extract tar and zip archives, or decompress single compressed files.

```python
from kitup.modules import decompress
from kitup.registry import task
```

Both functions are idempotent: only differences are written. Files that are no
longer present in the source are not removed.

## `archive(ctx, src, dest, *, creates=None, strip_components=0, include=None, exclude=None, mode=None)`

Extract an archive into the directory `dest`.

- `src` resolves like `files.copy()`: a bare filename comes from the bundle's
  `files/` directory, other relative paths from the bundle directory, and
  absolute paths are used as-is.
- `dest` must be a real directory or a missing path whose parent exists. The
  directory is created when missing.
- `creates` is a path, relative to `dest` unless absolute. When it exists the
  archive is skipped.
- `strip_components` removes that many leading path components before filtering.
- `include` and `exclude` are glob-style patterns matched against the stripped
  relative path. When `include` is given, only matching entries are extracted;
  `exclude` then removes matches. A pattern such as `*.txt` also matches nested
  files. Excluding a directory does not exclude its children.
- `mode` overrides the mode of extracted regular files.

```python
from pathlib import Path


@task("Install application", tags={"apps"})
def install_application(ctx):
    decompress.archive(
        ctx,
        "app.tar.gz",
        Path.home() / ".local/share/app",
        strip_components=1,
        include=["bin/*", "lib/*"],
    )
```

### Formats

The format is detected from the archive contents, not the filename:

- tar, optionally compressed with gzip, bzip2, xz, or zstd;
- zip with stored, deflate, bzip2, lzma, or zstd compression.

### Behavior

- Directories that already exist are left unchanged. New directories use the
  archive mode (default `0o755`); implicit parent directories use `0o755`.
- With `mode=None`, existing files keep their current mode and new files use the
  archive mode (`0o644` by default).
- Symlinks are compared and replaced only when their target differs. A real
  directory is never replaced.
- Hardlinks, device nodes, FIFOs, and other special members are rejected.
- When the same path appears more than once, the last entry wins.
- Archive ownership is not restored; files and directories are owned by the
  user running kitup.
- Dry runs compare the archive without creating `dest` or writing entries.

### Safety

- Absolute member paths, `..` components, and NUL bytes are rejected.
- Archives that place a file or symlink where another entry needs a directory
  are rejected.
- Existing symlinks inside `dest` are never followed while creating parents, so
  an archive cannot write outside `dest`.
- Symlink targets are created as recorded in the archive; kitup never follows
  them during extraction.
- A `dest` that is a symlink or a file is rejected.

## `file(ctx, src, dest, *, mode=None)`

Decompress a single compressed file.

- Supported formats: gzip, bzip2, xz, and zstd, detected from the contents.
- `src` resolves against the bundle like `archive()`.
- `dest` may be a file path, an existing directory, or end with `/`. A directory
  destination uses the source filename with its `.gz`, `.bz2`, `.xz`, `.zst`, or
  `.zstd` suffix removed.
- Archive sources are rejected; use `archive()` for tar and zip files.
- With `mode=None`, an existing destination keeps its mode and a new one is
  created with `0o644`.
- An existing destination symlink is rejected rather than followed.

```python
from pathlib import Path


@task("Install application config", tags={"config"})
def install_application_config(ctx):
    decompress.file(
        ctx,
        "app-config.yaml.gz",
        Path.home() / ".config/app/config.yaml",
        mode=0o640,
    )
```

No write occurs when the destination already matches. Dry runs compare the
decompressed content but do not write.

## Notes

- Remote archives are not downloaded automatically. Use
  [`download.to_file()`](download.md) first:

  ```python
  tarball = Path("/tmp/app.tar.gz")
  download.to_file(
      ctx,
      "https://example.test/app.tar.gz",
      tarball,
      checksum="sha256:...",
  )
  decompress.archive(ctx, tarball, Path.home() / ".local/share/app")
  ```

- Privileged extraction is not supported yet. Extract to a user-writable
  directory, then move the result into place with
  [`files.move(..., sudo=True)`](files.md), or run kitup itself as root.
- A failure part-way through extraction leaves earlier entries in place.
