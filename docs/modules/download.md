# `download`

Remote file download over HTTP(S).

```python
from kitup.modules import download
```

## `to_file(ctx, url, dest, *, checksum=None, force=False, headers=None, timeout=30, validate_certs=True, mode=None)`

Download a URL to a destination file.

### Parameters

- `url` — HTTP(S) source URL.
- `dest` may be a file path, an existing directory, or end with `/`. A directory
  destination uses the URL basename.
- `force=False` leaves an existing destination alone unless `checksum` is given
  and does not match.
- `checksum` uses `algorithm:digest` format, for example
  `sha256:b5bb9d...`. The digest is verified before the destination is replaced.
- `headers` adds request headers.
- `timeout` is the network timeout in seconds.
- `validate_certs=False` disables TLS certificate verification. Use it only for
  trusted hosts with self-signed certificates.
- `mode=None` preserves an existing mode; new files default to `0o644`.
  Ownership is never changed.

### Result fields

- `url` — sanitized source URL.
- `dest` — destination path.
- `checksum_dest` — SHA-256 checksum of the downloaded file in
  `sha256:<digest>` form; `None` when no download was performed.
- `duration` — elapsed download time in seconds; `0.0` when no download was
  performed.
- `size` — size of the destination file in bytes.

```python
from pathlib import Path

from kitup.modules import files


@task("Install example repository key", tags={"repos"})
def install_example_key(ctx):
    key = Path("/tmp/example-key.asc")
    download.to_file(
        ctx,
        "https://example.test/repo/key.asc",
        key,
        checksum="sha256:...",
    )
    files.move(
        ctx,
        key,
        "/etc/pki/rpm-gpg/RPM-GPG-KEY-example",
        mode=0o644,
        owner="root",
        group="root",
        sudo=True,
    )
```

Existing destination symlinks are rejected rather than followed. Parent
directories are not created automatically; use `files.directory()` first.

With `force=False` and no checksum, an existing file is never re-downloaded.
Use `force=True` to check the remote content on each run; the file is reported
as changed only when its bytes differ. Dry runs do not contact the remote server.

## `to_bytes(ctx, url, *, checksum=None, headers=None, timeout=30, validate_certs=True, max_bytes=10485760)`

Download a URL into memory and return its raw bytes.

### Parameters

- `url` — HTTP(S) source URL.
- `checksum` is verified before returning.
- `headers` adds request headers.
- `timeout` is the network timeout in seconds.
- `validate_certs=False` disables TLS certificate verification.
- `max_bytes` limits the response size. Set it to `None` to disable the limit.
  The default is 10 MiB.

Returns the raw response body as `bytes`. This method records no call.

```python
@task("Read example metadata")
def read_example_metadata(ctx):
    content = download.to_bytes(ctx, "https://example.test/metadata.json")
    metadata = json.loads(content)
```

Prefer `to_file()` for large files.

## Notes

- Downloads run as the current user and never use sudo. Move a downloaded file
  into a privileged location with [`files.move(..., sudo=True)`](files.md), or
  use `files.copy(...)` when the destination should be owned by the privileged
  user.
- Only `http` and `https` URLs are supported. Redirects are followed.
- Requests send `Accept-Encoding: gzip, deflate, zstd` by default. Override it
  through `headers` to request a different encoding, for example
  `headers={"Accept-Encoding": "identity"}`.
- Content encodings `gzip`, `deflate`, and `zstd` are decoded automatically.
  Other content encodings fail the task.
- Checksum URLs, authentication, client certificates, and non-HTTP(S) URLs are
  not supported.
