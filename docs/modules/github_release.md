# `github_release`

Read metadata for releases in public GitHub repositories through the GitHub REST
API.

```python
from kitup.modules import github_release
from kitup.registry import task
```

The module only reads release metadata and selects assets by regular expression.
Use `by_asset()` to fetch a release and select an asset in one call. It does not
download files; use [`download`](download.md) with an asset's URL and digest.

## `latest(ctx, project, *, timeout=30)`

Return the latest published full release for a public repository.

- `project` — repository in `owner/repo` form.
- `timeout` — request timeout in seconds.

GitHub defines the latest release as the most recent non-draft,
non-prerelease release. Use `by_tag()` to fetch a prerelease.

```python
@task("Show latest uv release")
def show_latest_uv(ctx):
    release = github_release.latest(ctx, "astral-sh/uv")
    ctx.note(f"uv {release.tag}")
```

## `by_tag(ctx, project, tag, *, timeout=30)`

Return the published release for `tag`. Tags are URL-encoded, so tags containing
`/` work.

```python
release = github_release.by_tag(ctx, "owner/project", "v1.2.3-rc.1")
```

## `by_asset(ctx, project, pattern, *, tag=None, timeout=30)`

Return the single asset whose name matches the regular expression `pattern`.
Matching uses `re.search`, so `^` and `$` can anchor the pattern when needed.

By default the asset is selected from the latest release. Pass `tag` to select a
specific release:

```python
asset = github_release.by_asset(
    ctx,
    "kovidgoyal/kitty",
    r"^kitty-.*-x86_64\.txz$",
)

asset = github_release.by_asset(
    ctx,
    "kovidgoyal/kitty",
    r"^kitty-.*-x86_64\.txz$",
    tag="0.44.0",
)
```

The task fails when the regex is invalid, when no asset matches, or when more
than one asset matches. The error lists the available or matching asset names.

## `Release`

| Field | Type | Description |
| --- | --- | --- |
| `project` | `str` | Requested `owner/repo`. |
| `id` | `int` | GitHub release ID. |
| `tag` | `str` | Release tag name. |
| `name` | `str \| None` | Release title. |
| `body` | `str \| None` | Release notes. |
| `url` | `str` | Release page URL. |
| `draft` | `bool` | Whether the release is a draft. |
| `prerelease` | `bool` | Whether the release is a prerelease. |
| `immutable` | `bool \| None` | Whether the release is immutable, when reported. |
| `created_at` | `str \| None` | Creation timestamp. |
| `published_at` | `str \| None` | Publication timestamp. |
| `updated_at` | `str \| None` | Last update timestamp. |
| `tarball_url` | `str \| None` | Source tarball URL. |
| `zipball_url` | `str \| None` | Source zip archive URL. |
| `assets` | `tuple[Asset, ...]` | Release assets. |

Timestamps are the ISO 8601 strings returned by GitHub.

### `Release.asset(pattern)`

Return the single asset whose name matches the regular expression `pattern`.
Matching uses `re.search`.

```python
asset = release.asset(r"^tool-linux-x86_64\.tar\.gz$")
```

The task fails when nothing matches or when multiple assets match, listing the
available or matching names.

### `Release.assets_matching(pattern)`

Return every asset whose name matches the regular expression as a tuple:

```python
archives = release.assets_matching(r"^tool-linux-.*\.tar\.gz$")
```

## `Asset`

| Field | Type | Description |
| --- | --- | --- |
| `id` | `int` | GitHub asset ID. |
| `name` | `str` | Asset filename. |
| `url` | `str` | Browser download URL. |
| `size` | `int` | Size in bytes. |
| `content_type` | `str` | Media type reported by GitHub. |
| `digest` | `str \| None` | Asset digest in `algorithm:digest` form, when available. |
| `download_count` | `int` | Number of downloads. |
| `label` | `str \| None` | Optional asset label. |
| `state` | `str` | Asset state, usually `uploaded`. |
| `created_at` | `str \| None` | Creation timestamp. |
| `updated_at` | `str \| None` | Last update timestamp. |

## Downloading an asset

Pass `asset.url` to [`download.to_file()`](download.md) and `asset.digest` as
the checksum when GitHub provides one:

```python
from pathlib import Path

from kitup.modules import decompress, download, github_release
from kitup.registry import task


@task("Install uv", tags={"packages"})
def install_uv(ctx):
    release = github_release.latest(ctx, "astral-sh/uv")
    asset = release.asset(r"^uv-x86_64-unknown-linux-gnu\.tar\.gz$")
    archive = Path("/tmp") / asset.name

    download.to_file(ctx, asset.url, archive, checksum=asset.digest)
    decompress.archive(
        ctx,
        archive,
        Path.home() / ".local/share/uv",
        strip_components=1,
    )
```

Older releases may not provide a digest. Either omit `checksum` or supply a
known digest yourself.

## Notes

- Only public repositories are supported. No authentication is used, so private
  repositories are not accessible.
- Requests go to `https://api.github.com` using the GitHub REST API headers and
  the standard Python `urllib` user agent. No token is read from the environment
  or sent.
- Unauthenticated GitHub API requests are rate-limited. Metadata is cached for
  the rest of the run, so repeated tasks do not make repeated requests.
- These functions are read-only: they record no changes and still contact the
  API during `--dry-run`. This lets task logic branch on real release data.
  A subsequent `download.to_file()` dry run does not contact the asset server.
- Failures raise `TaskFailed` for missing releases, rate limits, invalid JSON,
  malformed responses, network errors, and timeouts.
