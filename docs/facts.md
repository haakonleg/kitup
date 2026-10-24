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

## Details

- `hostname` is omitted if the system call fails.
- `os_release` contains the parsed `KEY=value` entries from `/etc/os-release`.
  Comments and blank lines are ignored, and quoted values are unquoted.
- `os_release` is omitted when the file is missing or contains no entries.
- `distro_id` and `version_id` are present only when the corresponding
  `os_release` field exists.
- `arch` and `kernel` are omitted when the platform reports an empty value.
- The mapping and nested `os_release` mapping are immutable.
- Values are snapshots, not live state. Tasks needing current information should
  query it directly.

## Example

```python
hostname = ctx.facts.get("hostname", "unknown")
os_release = ctx.facts.get("os_release", {})

if ctx.facts.get("distro_id") == "fedora":
    ctx.note(f"Fedora {ctx.facts.get('version_id', '')}".strip())
```
