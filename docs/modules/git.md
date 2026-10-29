# `git`

Manage git repository checkouts: clone a repository into a directory and keep
an existing checkout at the desired version.

```python
from kitup.modules import git
from kitup.registry import task
```

## `repo(ctx, url, dest, *, version="HEAD", single_branch=False, depth=None, update=True, force=False)`

Ensure the repository `url` is checked out at `dest`.

- `url` — repository address: an HTTPS URL, an SSH address such as
  `git@host:owner/repo.git`, or a local path.
- `dest` — checkout directory. A missing or empty directory is cloned into.
  A non-empty directory must itself be the top level of a git repository,
  otherwise the task fails.
- `version` — branch, tag, commit, or `"HEAD"` (the remote's default branch).
  At clone time a branch or tag is passed as `--branch`; a full commit SHA is
  checked out after cloning.
- `single_branch` — clone only the history of one branch (`--single-branch`).
  Only applies when cloning.
- `depth` — shallow clone with `--depth=<n>`, where `n` is at least 1. Only
  applies when cloning.
- `update` — when the checkout already exists, verify it against the remote
  and move it to `version` when it differs. With `update=False` the remote is
  not contacted — no fetch, no comparison — except when the origin URL
  changed, which always triggers a fetch and update. A `--dry-run` with
  `update=False` makes no network requests.
- `force` — discard local modifications to tracked files when updating.
  Without it, a modified worktree fails the task. Untracked files are never
  removed and do not block an update.

```python
from pathlib import Path


@task("Clone dotfiles", tags={"dotfiles"})
def clone_dotfiles(ctx):
    git.repo(
        ctx,
        "https://github.com/example/dotfiles.git",
        Path.home() / "src/dotfiles",
    )


@task("Clone a single branch, shallow", tags={"dotfiles"})
def clone_single_branch(ctx):
    git.repo(
        ctx,
        "git@github.com:example/dotfiles.git",
        Path.home() / "src/dotfiles",
        version="main",
        single_branch=True,
        depth=1,
    )
```

## Behavior

- Cloning runs `git clone [--depth=N] [--single-branch] [--branch=<version>]
  <url> <dest>`.
- An existing checkout is reconciled: the origin URL is set with
  `git remote set-url` when it differs from `url`. When `update=True` (or the
  URL changed), the remote is fetched and `HEAD` is compared with the resolved
  `version` (`origin/HEAD` for `"HEAD"`, otherwise `origin/<version>` or
  `<version>` itself). When they differ, the checkout is moved with
  `git checkout --force <version>` followed by `git reset --hard <commit>`.
- Changes are reported as `clone <url> into <dest>`,
  `set remote URL of <dest> to <url>`, and
  `update <dest> <before>..<after>`.

## Notes

- `git` must be installed.
- The module runs as the current user; there is no sudo support. Clone into
  user-writable locations, or run kitup itself as root.
- Network operations set `GIT_TERMINAL_PROMPT=0`, so missing credentials fail
  fast instead of hanging.
- The fetch during an update only adds objects under `.git`; it never changes
  `HEAD` or the worktree, so it also runs during `--dry-run` to report
  accurately.
- Submodules, archives, bare repositories, separate git directories,
  refspecs, SSH options, and GPG commit verification are out of scope.
