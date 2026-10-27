# `flatpak`

Manage Flatpak remote repositories, applications, and runtimes.

```python
from kitup.modules import flatpak
from kitup.registry import task
```

## `remote(ctx, name, url=None, *, state="present", scope="system", gpg_verify=True)`

Manage a Flatpak remote repository.

- `name` — remote name, for example `flathub`.
- `url` — repository location. Required for `"present"`; ignored for `"absent"`.
- `state` — `"present"` or `"absent"`.
- `scope` — `"system"` or `"user"`.
- `gpg_verify` — when `False`, disable GPG verification for an added remote.
  Useful for unsigned local repositories.

`"present"` adds the remote only when the name is missing. An existing remote is
left unchanged, including its URL. `"absent"` deletes the remote without
`--force`; if installed applications still use it, Flatpak fails and the task
reports the error.

```python
@task("Add Flathub", tags={"flatpak"})
def add_flathub(ctx):
    flatpak.remote(
        ctx,
        "flathub",
        "https://dl.flathub.org/repo/flathub.flatpakrepo",
    )


@task("Add local Flatpak repository", tags={"flatpak"})
def add_local_repository(ctx):
    flatpak.remote(
        ctx,
        "local",
        "file:///srv/flatpak-repo",
        gpg_verify=False,
    )
```

## `packages(ctx, names, *, state="present", scope="system", remote=None)`

Ensure Flatpak applications or runtimes are installed or removed.

- `names` — an ID or full ref, or an iterable of them. A single string is one
  entry.
- `state` — `"present"` or `"absent"`.
- `scope` — `"system"` or `"user"`.
- `remote` — remote to install from. Only valid for `"present"`.

A bare ID such as `org.gnome.Calculator` matches any installed branch. A full
ref such as `org.gnome.Calculator/x86_64/stable` or
`app/org.gnome.Calculator/x86_64/stable` matches that exact ref.

`"present"` installs only missing refs and then verifies the result. `"absent"`
removes only installed refs and then verifies the result. `remote` only selects
the source for new installs; an already-installed app is not moved or
reinstalled.

```python
@task("Install Flatpak apps", tags={"flatpak"})
def install_flatpak_apps(ctx):
    flatpak.packages(
        ctx,
        ["org.gnome.Calculator", "org.gnome.TextEditor"],
        remote="flathub",
    )


@task("Remove Flatpak app", tags={"flatpak"})
def remove_flatpak_app(ctx):
    flatpak.packages(ctx, "org.gnome.Calculator", state="absent")
```

## Notes

- `flatpak` must be installed.
- Flatpak treats the system installation as the default. `scope="system"`
  requests sudo so kitup controls authentication, caching, and prompts. This
  also works over SSH, in headless sessions, and in containers.
- `scope="user"` never uses sudo and fails clearly when kitup runs as root.
  Flatpak itself refuses `sudo flatpak --user`.
- State is read with `flatpak list --columns=application,ref`. Read-only probes
  do not request sudo.
- `--dry-run` reports changes without mutating anything.
- Updates, permission overrides, bundles, and `.flatpakref` files are out of
  scope.
