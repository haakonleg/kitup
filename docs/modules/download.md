# `download`

Remote file download over HTTP(S).

```python
from kitup.modules import download
```

## `to_file(ctx, url, dest, *, checksum=None, force=False, headers=None, timeout=30, validate_certs=True, mode=None, owner=None, group=None, sudo=False)`

Download a URL to a destination file.

- `dest` may be a file path, an existing directory, or end with `/`. A directory
  destination uses the URL basename.
- `force=False` leaves an existing destination alone unless `checksum` is given
  and does not match.
- `checksum` uses `algorithm:digest` format, for example
  `sha256:b5bb9d...`. The digest is verified before the destination is replaced.
- `headers` adds request headers. kitup does not add any headers by default.
- `timeout` is the network timeout in seconds.
- `validate_certs=False` disables TLS certificate verification. Use it only for
  trusted hosts with self-signed certificates.
- `mode=None` preserves an existing mode; new files default to `0o644`.
- `owner` and `group` accept a name or numeric ID and preserve existing values
  unless specified.
- `sudo=True` installs the file through the privileged worker.

```python
@task("Install example repository key", tags={"repos"})
def install_example_key(ctx):
    download.to_file(
        ctx,
        "https://example.test/repo/key.asc",
        "/etc/pki/rpm-gpg/RPM-GPG-KEY-example",
        checksum="sha256:...",
        mode=0o644,
        sudo=True,
    )
```

Downloads are written to a temporary file and installed atomically. Existing
destination symlinks are rejected rather than followed. Parent directories are
not created automatically; use `files.directory()` first.

With `force=False` and no checksum, an existing file is never re-downloaded.
Use `force=True` to check the remote content on each run; the file is reported
as changed only when its bytes differ. Dry runs do not contact the remote server.

## `to_bytes(ctx, url, *, checksum=None, headers=None, timeout=30, validate_certs=True, max_bytes=10485760)`

Download a URL into memory and return its raw bytes.

- `checksum` is verified before returning.
- `max_bytes` limits the response size. Set it to `None` to disable the limit.
  The default is 10 MiB.
- No destination file is created.

```python
@task("Read example metadata")
def read_example_metadata(ctx):
    content = download.to_bytes(ctx, "https://example.test/metadata.json")
    metadata = json.loads(content)
```

Prefer `to_file()` for large files.

## Notes

- Only `http` and `https` URLs are supported.
- Redirects are followed.
- Environment proxy settings are honored.
- Responses are returned or written as received; content encoding is not
  automatically decompressed.
- Checksum URLs, backup files, HTTP basic authentication, client certificates,
  and FTP or `file://` URLs are not supported.
