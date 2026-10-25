# Facts

`ctx.facts` is a read-only snapshot of the host, collected once at startup and
shared by all tasks. It is a `Mapping[str, Any]`; optional facts are omitted when
their source is unavailable.

| Key | Type | Source |
| --- | --- | --- |
| `hostname` | `str` | `socket.gethostname()` |
| `os_release` | `Mapping[str, str]` | `/etc/os-release` |
| `distro_id` | `str` | `os_release["ID"]` |
| `version_id` | `str` | `os_release["VERSION_ID"]` |
| `arch` | `str` | `platform.machine()` |
| `kernel` | `str` | `platform.release()` |
| `mounts` | `tuple[Mapping[str, Any], ...]` | `/proc/self/mountinfo`, `os.statvfs()` |

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

- `hostname` is omitted if the system call fails.
- `os_release` contains the parsed `KEY=value` entries from `/etc/os-release`.
  Comments and blank lines are ignored, and quoted values are unquoted.
- `os_release` is omitted when the file is missing or contains no entries.
- `distro_id` and `version_id` are present only when the corresponding
  `os_release` field exists.
- `arch` and `kernel` are omitted when the platform reports an empty value.
- `mounts` is omitted when `/proc/self/mountinfo` is unavailable. Mounts whose
  capacity cannot be read are skipped.
- `bytes_free` is the filesystem's free space, so
  `bytes_used + bytes_free == bytes_total`.
- The mapping, nested `os_release` mapping, and mount mappings are immutable.
- Values are snapshots, not live state. Tasks needing current information should
  query it directly.

## Example

```python
hostname = ctx.facts.get("hostname", "unknown")
os_release = ctx.facts.get("os_release", {})

if ctx.facts.get("distro_id") == "fedora":
    ctx.note(f"Fedora {ctx.facts.get('version_id', '')}".strip())

for mount in ctx.facts.get("mounts", ()):
    if mount["mount"] == "/":
        ctx.note(
            f"root is {mount['bytes_used']} of {mount['bytes_total']} bytes used"
        )
```

Bytes are raw values suitable for calculations; format them for display as needed.
