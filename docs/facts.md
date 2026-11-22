# Facts

`facts` is a read-only snapshot of the host, collected once at startup and
shared by all tasks. Optional facts are omitted when unavailable.

| Key | Type | Description |
| --- | --- | --- |
| `hostname` | `str` | Host name of the machine. |
| `username` | `str` | Login name of the user running kitup. |
| `os_release` | `Mapping[str, str]` | Parsed operating-system release metadata, for example `ID`, `NAME`, and `VERSION_ID`. |
| `distro_id` | `str` | Distribution identifier from `os_release`, such as `fedora`. |
| `version_id` | `str` | Distribution version from `os_release`, such as `44`. |
| `arch` | `str` | Machine architecture, such as `x86_64`. |
| `kernel` | `str` | Release identifier of the running kernel. |
| `mounts` | `tuple[Mapping[str, Any], ...]` | Mounted filesystems and their capacity; see the fields below. |

Each mount contains:

| Field | Type | Description |
| --- | --- | --- |
| `device` | `str` | Source device or filesystem, e.g. `/dev/nvme0n1p1` |
| `fstype` | `str` | Filesystem type, e.g. `ext4` or `btrfs` |
| `mount` | `str` | Mount path |
| `options` | `tuple[str, ...]` | Mount options |
| `bytes_free` | `int` | Free bytes |
| `bytes_used` | `int` | Used bytes |
| `bytes_total` | `int` | Total bytes |

## Details

- `distro_id` and `version_id` are present only when the corresponding
  `os_release` field exists.
- `username` comes from the process's real user ID, so it names the account
  kitup runs as. Running kitup through `sudo` therefore reports the target
  account, typically `root`. The fact is omitted when that account cannot be
  resolved.
- `os_release` is omitted when the host does not provide release data.
- `arch` and `kernel` are omitted when unavailable.
- `mounts` is omitted when mount information is unavailable. Mounts whose
  capacity cannot be read are skipped.
- `bytes_free` is the filesystem's free space, so
  `bytes_used + bytes_free == bytes_total`.
- Values are snapshots, not live state. Tasks needing current information should
  query it directly.

## Example

```python
from kitup import ctx, facts, task


@task
def report_host_facts():
    """Report host details and root filesystem usage."""
    ctx.note(f"{facts.hostname} ({facts.get('distro_id', 'unknown')})")
    root = next((m for m in facts.get("mounts", ()) if m.mount == "/"), None)
    if root:
        ctx.note(f"root is {root.bytes_used} of {root.bytes_total} bytes used")
```

Bytes are raw values suitable for calculations; format them for display as needed.
