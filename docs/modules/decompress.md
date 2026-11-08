# `decompress`

Extract tar and zip archives, or decompress single compressed files.

```python
from kitup.modules import decompress
```

Both functions are idempotent: only differences are written. Files that are no
longer present in the source are not removed.

## `archive(ctx, src, dest, *, creates=None, strip_components=0, include=None, exclude=None, mode=None)`

Extract an archive into the directory `dest`.

### Parameters

- `src` — archive source. Relative paths resolve like [`files.copy()`](files.md).
  Absolute paths are used as-is. `~` is expanded.
- `dest` — destination directory. It is created when missing; its parent must
  exist.
- `creates` — path, relative to `dest` unless absolute. When it exists the
  archive is skipped.
- `strip_components` — number of leading path components to remove before
  filtering.
- `include` and `exclude` — glob-style patterns matched against the stripped
  relative path. When `include` is given, only matching entries are extracted;
  `exclude` then removes matches. A pattern such as `*.txt` also matches nested
  files. Excluding a directory does not exclude its children.
- `mode` — overrides the mode of extracted regular files.

### Result fields

- `src` — resolved archive path.
- `dest` — destination directory.
- `files` — selected archive entries relative to `dest`, including files and
  symlinks but not directories. Empty when `creates` skips the archive.
- `size` — total size in bytes of regular files under `dest`; `0` when `dest`
  does not exist.

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

- Existing directories are left unchanged. New files use the archive mode
  unless `mode` is set.
- Symlinks are replaced only when their target differs.
- Archive ownership is not restored; extracted files are owned by the user
  running kitup.
- Absolute member paths, `..` components, and entries that would write outside
  `dest` are rejected.
- A `dest` that is a symlink or a file is rejected.
- Dry runs compare the archive without creating `dest` or writing files.

## `file(ctx, src, dest, *, mode=None)`

Decompress a single compressed file.

### Parameters

- `src` — compressed source, resolved like `archive()`.
- `dest` — destination file, existing directory, or path ending with `/`. A
  directory destination uses the source filename with its `.gz`, `.bz2`, `.xz`,
  `.zst`, or `.zstd` suffix removed.
- `mode` — overrides the destination mode. Existing files keep their mode when
  omitted; new files use `0o644`.

Supported formats are gzip, bzip2, xz, and zstd, detected from the contents.
Archive sources are rejected; use `archive()` for tar and zip files. An existing
destination symlink is rejected rather than followed.

### Result fields

- `dest` — destination file.
- `src` — resolved compressed source.

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
