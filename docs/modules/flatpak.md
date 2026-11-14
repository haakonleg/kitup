# `flatpak`

Manage Flatpak remote repositories, applications, and runtimes.

```python
from kitup.modules import flatpak
```

## `remote(name, url=None, *, state="present", scope="system", gpg_verify=True)`

Manage a Flatpak remote repository.

### Parameters

- `name` — remote name, for example `flathub`.
- `url` — repository location. Required for `"present"`; ignored for `"absent"`.
- `state` — `"present"` or `"absent"`.
- `scope` — `"system"` or `"user"`.
- `gpg_verify` — when `False`, disable GPG verification for an added remote.
  Useful for unsigned local repositories.

`"present"` adds the remote only when the name is missing. An existing remote is
left unchanged, including its URL. `"absent"` removes the remote; if installed
applications still use it, the task fails.

### Result fields

- `remote` — remote name.
- `scope` — `"system"` or `"user"`.
- `url` — repository URL (present-state results only).

```python
flatpak.remote(
    "flathub",
    "https://dl.flathub.org/repo/flathub.flatpakrepo",
)
flatpak.remote(
    "local",
    "file:///srv/flatpak-repo",
    gpg_verify=False,
)
```

## `packages(names, *, state="present", scope="system", remote=None)`

Ensure Flatpak applications or runtimes are installed or removed.

### Parameters

- `names` — an ID or full ref, or an iterable of them. A single string is one
  entry.
- `state` — `"present"` or `"absent"`.
- `scope` — `"system"` or `"user"`.
- `remote` — remote to install from. Only valid for `"present"`.

A bare ID such as `org.gnome.Calculator` matches any installed branch. A full
ref such as `org.gnome.Calculator/x86_64/stable` or
`app/org.gnome.Calculator/x86_64/stable` matches that exact ref.

`"present"` installs only missing refs. `"absent"` removes only installed refs.
`remote` only selects the source for new installs; an already-installed app is
not moved or reinstalled.

### Result fields

- `added` — refs installed (present results).
- `removed` — refs removed (absent results).
- `scope` — `"system"` or `"user"`.

No-change results use an empty `added` or `removed` list.

```python
flatpak.packages(
    ["org.gnome.Calculator", "org.gnome.TextEditor"],
    remote="flathub",
)
flatpak.packages("org.gnome.Calculator", state="absent")
```

## Notes

- `flatpak` must be installed.
- `scope="system"` requests sudo. `scope="user"` never uses sudo and fails when
  kitup runs as root.
