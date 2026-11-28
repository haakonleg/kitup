# `files`

Filesystem and text-file management, including templating.

```python
from kitup.modules import files
```

## `file(path, *, state="present", content=None, mode=None, owner=None, group=None, sudo=False)`

Ensure a text file exists with optional content, or is absent.

### Parameters

- `path` — file to create, update, or remove. The parent directory must
  already exist.
- `content=None` leaves an existing file unchanged and creates a missing file
  empty. Symlinks are refused.
- `state="absent"` removes the file or symlink, but not a directory.
- `mode=None` preserves an existing mode; new files default to `0o644`.
  Accepts an integer or an octal string such as `"0600"`.
- `owner` and `group` accept a name or numeric ID.
- `sudo=True` performs the operation as root.

### Result fields

- `path` — managed file path.

```python
files.file("/etc/example.conf", content="enabled=true\n", mode=0o644, sudo=True)
files.file("/etc/example.conf", state="absent", sudo=True)
```

## `copy(src, dest, *, mode=None, owner=None, group=None, sudo=False)`

Copy one regular file. `mode=None` preserves the source mode. Source symlinks are
refused.

### Parameters

- `src` — source file. Relative paths use the project and bundle lookup described
  in [Projects](../project.md#resources). Absolute paths are used as-is. `~` is
  expanded.
- `dest` — destination path. If it ends with `/`, it is treated as a directory
  and the source filename is appended. The parent directory must already exist.
- `mode`, `owner`, and `group` override the destination metadata. `mode`
  accepts an integer or an octal string such as `"0600"`; `owner` and `group`
  accept a name or numeric ID.
- `sudo=True` performs the operation as root.

### Result fields

- `path` — destination path.
- `src` — resolved source path.

```python
files.copy("example.conf", "/etc/example.conf", sudo=True)
files.copy("files/dotfiles/.zshrc", "~/.zshrc")
files.copy("example.conf", "/etc/example.d/", sudo=True)
```

## `move(src, dest, *, mode=None, owner=None, group=None, sudo=False)`

Move one regular file, preserving its mode and ownership when renamed.

### Parameters

- `src` — source file. It is used as given and is not resolved against the
  bundle.
- `dest` — destination path. If it ends with `/`, it is treated as a directory
  and the source filename is appended.
- `mode`, `owner`, and `group` override the destination metadata after the move.
  `mode` accepts an integer or an octal string such as `"0600"`. Without them, a
  rename preserves the source metadata; moving across filesystems may not.
- `sudo=True` performs the move and metadata changes as root.

Directories, symlink sources, and symlink destinations are rejected.

### Result fields

- `dest` — destination path.
- `src` — source path.

```python
files.move(
    "/tmp/example.key",
    "/etc/pki/rpm-gpg/RPM-GPG-KEY-example",
    mode=0o644,
    owner="root",
    group="root",
    sudo=True,
)
files.move("/tmp/example.conf", "/etc/example.d/", sudo=True)
```

## `line(path, line, *, match=None, state="present", sudo=False)`

Add, replace, or remove a line.

### Parameters

- `path` — file to edit.
- `line` — desired line, without a trailing newline or other line breaks.
- `match=None` compares the full line; otherwise `match` is a regular expression.
- `state="present"` replaces the first match or appends the line.
- `state="absent"` removes all matches.
- `sudo=True` edits the file as root.

### Result fields

- `path` — edited file path.

## `block(path, marker, content, *, state="present", sudo=False)`

Manage a marked region:

```text
# BEGIN marker
content
# END marker
```

### Parameters

- `path` — file to edit.
- `marker` — block name used in the `# BEGIN` and `# END` markers. It must not
  contain line breaks.
- `content` — block body. A trailing newline is added when missing.
- `state="present"` creates or updates the block; `"absent"` removes it.
- `sudo=True` edits the file as root.

Markers in an existing file must be well-formed and unique.

### Result fields

- `path` — edited file path.

## `ini(path, *, section=None, option=None, value=None, match=None, state="present", create=True, sudo=False)`

Set or remove one option in an INI-style file.

### Parameters

- `path` — file to edit.
- `section=None` — target `[section]` header, matched case-sensitively.
  `None` edits the preamble before the first header.
- `option` — option name. Required with `state="present"`.
- `value` — option value, required with `state="present"` and rejected with
  `state="absent"`. Written as `option = value`; surrounding whitespace is
  ignored, so `key=value` and `key = value` are equivalent. Values may contain
  `=`.
- `match=None` — option names to required values, selecting one of several
  same-named sections. Keys and values are regular expressions; escape literals
  with `re.escape`. Requires `section` and is never written to the file.
- `state="present"` replaces the first matching option line with
  `option = value` and drops duplicates; a missing option is inserted at the
  end of the section; a missing section is appended at the end of the file.
- `state="absent"` removes all matching option lines in the target section, or
  the whole section when `option=None`.
- `create=True` creates a missing file with mode `0o644`; `create=False` fails
  when the file does not exist.
- `sudo=True` edits the file as root.

With `match` and `state="present"`, a new section is appended only if the new
`option = value` line itself satisfies `match`; otherwise the call fails. To
add a new same-named section, set the option you match on, for example
`match={"fav": re.escape(v)}, option="fav", value=v`.

### Result fields

- `path` — edited file path.
- `ini_action` — `"added"`, `"replaced"`, `"removed"`, or `"unchanged"`.
- `section`, `option`, `value` — the arguments as given; `None` when not given.

```python
files.ini("/etc/example.conf", section="drinks", option="fav", value="lemonade", sudo=True)
files.ini("/etc/example.conf", section="server", match={"id": "db"}, option="host", value="db.internal", sudo=True)
files.ini("/etc/example.conf", section="drinks", option="fav", state="absent", sudo=True)
```

## `toml(path, *, table=None, key=None, value=None, state="present", create=True, sudo=False)`

Set or remove one key in a TOML file, table by table.

### Parameters

- `path` — file to edit.
- `table=None` — target table as a dotted path (e.g. `tool.uv`); `None` edits
  the top-level keys before the first header.
- `key` — a possibly dotted bare TOML key (e.g. `pip.no-cache`), resolving into
  an existing sub-table (`[tool.uv.pip]`) when that header exists. Required
  with `state="present"`.
- `value` — required with `state="present"`: a string, boolean, integer, float,
  or list/tuple of strings written as a TOML value.
- `state="present"` writes `key = value` in the target table; a missing key or
  table is appended at the end.
- `state="absent"` removes the `key`, or the whole `table` when `key=None`
  (comments inside it go with it), and takes no `value`.
- `create=True` creates a missing file with mode `0o644`; `create=False` fails
  when the file does not exist. An absent edit does not create the file.
- `sudo=True` edits the file as root.

Array-of-tables (`[[...]]`) cannot be edited.

### Result fields

- `path` — edited file path.
- `toml_action` — `"added"`, `"replaced"`, `"removed"`, or `"unchanged"`.
- `table`, `key`, `value` — the arguments as given; `None` when not given.

```python
files.toml("/etc/app/config.toml", table="server", key="host", value="db.internal", sudo=True)
files.toml("/etc/app/config.toml", table="tool.uv", key="native-tls", value=True, sudo=True)
files.toml("/etc/app/config.toml", table="server", key="port", state="absent", sudo=True)
files.toml("/etc/app/config.toml", table="legacy", state="absent", sudo=True)
```

## `directory(path, *, state="present", force=False, parents=False, mode=None, owner=None, group=None, sudo=False)`

Ensure a directory exists or is absent.

### Parameters

- `path` — directory to create, update, or remove.
- `state="absent"` removes an empty directory; `force=True` removes it
  recursively.
- `parents=True` also creates any missing parent directories, outermost first.
  New parents receive the same `mode`, `owner`, and `group` as the target;
  existing parents are never modified. It requires `state="present"`.
- `mode=None` preserves an existing mode; new directories default to `0o755`.
  Accepts an integer or an octal string such as `"0755"`.
- `owner` and `group` accept a name or numeric ID.
- `sudo=True` performs the operation as root.

Existing non-directory paths and symlinks are rejected. With `parents=True`,
an existing parent that is not a directory is rejected as well.

### Result fields

- `path` — managed directory path.

```python
files.directory("/srv/app/data", mode=0o750, owner="app", group="app", parents=True, sudo=True)
```

## `symlink(target, path, *, sudo=False)`

Create or verify a symlink, replacing an existing file or symlink. Directories
are never replaced.

### Parameters

- `target` — symlink target.
- `path` — symlink path.
- `sudo=True` creates the symlink as root.

### Result fields

- `path` — symlink path.
- `target` — symlink target.

## `template(src, dest, *, variables=None, mode=None, owner=None, group=None, sudo=False)`

Render a [Jinja2](https://jinja.palletsprojects.com/) template into a managed
file.

### Parameters

- `src` — template source. Relative paths use the project and bundle lookup
  described in [Projects](../project.md#resources). Absolute paths are used
  as-is. `~` is expanded.
- `dest` — destination file path. The parent directory must already exist; use
  `directory()` first.
- `variables` — an optional mapping of extra values exposed to the template.
- `trim_blocks=True` removes the first newline after a block tag. Set it to
  `False` when that newline is significant.
- `lstrip_blocks=False` strips leading spaces and tabs before a block tag when
  enabled.
- `mode=None` preserves an existing mode; new files default to `0o644`.
  Accepts an integer or an octal string such as `"0644"`.
- `owner` and `group` accept a name or numeric ID.
- `sudo=True` performs the write as root.

### Result fields

- `dest` — destination file path.
- `src` — resolved template path.

```python
files.template(
    "sshd.conf.j2",
    "/etc/ssh/sshd_config.d/99-kitup.conf",
    mode=0o644,
    sudo=True,
)
```

With `files/sshd.conf.j2` in the bundle:

```jinja
Port {{ vars.SSH_PORT }}
PermitRootLogin no
PasswordAuthentication no
```

## Rendering templates

Templates receive three things:

- `vars` — the merged variable values for the current task, including any
  bundle-scoped values: `{{ vars.SSH_PORT }}`, `{{ vars["SSH_PORT"] }}`, or
  `{{ vars.SETTINGS.theme.name }}`.
- `facts` — the host facts snapshot: `{{ facts.hostname }}`.
- every key from `variables`, at the top level: `{{ workers }}`.

```python
files.template(
    "app.env.j2",
    "/etc/app.env",
    variables={"workers": 4},
    mode=0o640,
    sudo=True,
)
```

```jinja
WORKERS={{ workers }}
HOSTNAME={{ facts.hostname }}
PACKAGES={{ vars.PACKAGES | join(",") }}
```

`vars` and `facts` are reserved and cannot be replaced through `variables`.
Variable names must be valid identifiers.

Undefined variables are errors rather than empty strings. Use Jinja's
`default` filter or `is defined` when a value is genuinely optional:

```jinja
LOG_LEVEL={{ vars.LOG_LEVEL | default("info") }}
```

Rendering behavior:

- Templates use Jinja2's default syntax, with `trim_blocks` enabled by default:
  the first newline after a block tag is removed, so loops and conditionals do
  not leave blank lines in the output. Both `trim_blocks` and `lstrip_blocks`
  can be overridden per call. Autoescaping is disabled.
- Trailing newlines in the template are preserved, including newlines consumed
  by a block tag at the end of the file.
- `include`, `extends`, and custom loaders are not supported.
- `--dry-run` renders the template and reports the change without writing.
  Template syntax and variable errors still fail in dry-run mode.
- `--diff` prints a unified diff for changed content.
- Destination directories and symlink template sources are rejected.
- Template sources must be regular UTF-8 text files.

## Template helpers

Templates also expose additional filters and tests, alongside Jinja2's built-in
filters and tests (`join`, `map`, `default`, `tojson`, `defined`, `in`,
`boolean`, and so on).

### Path filters

- `basename`, `dirname`, `splitext`, `normpath`, `expanduser` — standard path
  operations.
- `path_join` — join a path or a list of components.

```jinja
{{ "/etc/ssh/sshd_config" | basename }}      {# sshd_config #}
{{ "/etc/ssh/sshd_config" | dirname }}       {# /etc/ssh #}
{{ "archive.tar.gz" | splitext | list }}     {# ['archive.tar', '.gz'] #}
{{ ["/etc", "app", "config"] | path_join }}  {# /etc/app/config #}
```

### String and regular-expression filters

- `regex_replace(value, pattern, replacement, ignorecase=False,
  multiline=False, count=0)`
- `regex_search(value, regex, *groups, ignorecase=False, multiline=False)` —
  returns the first match, or the selected group values.
- `regex_findall(value, regex, ignorecase=False, multiline=False)`
- `regex_escape(value, re_type="python")` — `python`, `posix_basic`, or
  `posix_extended`.
- `split(value, sep=None, maxsplit=-1)`
- `quote` — shell-quote a value; `None` becomes `''`.
- `squote` — wrap a value in single quotes and escape embedded quotes.
- `b64encode` / `b64decode` — UTF-8 Base64, with `urlsafe=True` for the URL-safe
  alphabet.
- `comment(value, prefix="#")` — prefix a value, or every line of a list, with a
  comment marker.

```jinja
{{ "Foo foo" | regex_replace("foo", "bar", ignorecase=True) }}
{{ "version 42" | regex_search("([0-9]+)", "\\1") }}
{{ "a,b,c" | split(",") | join("|") }}
{{ "hello world" | quote }}
{{ "hello" | b64encode }}
{{ ["one", "two"] | comment }}
```

### Data filters

- `combine(*mappings, recursive=False, list_merge="replace")` — merge
  mappings; `list_merge` accepts `replace`, `keep`, `append`, `prepend`,
  `append_rp`, or `prepend_rp`.
- `flatten(levels=None, skip_nulls=True)`
- `dict2items(key_name="key", value_name="value")` /
  `items2dict(key_name="key", value_name="value")`
- `union`, `intersect`, `difference` — set operations that preserve order.
- `extract(item, container, morekeys=None)` — extract a value from a mapping by
  key, or by a sequence of nested keys.
- `subelements(subkey)` — yield `(parent, sub-element)` pairs.

```jinja
{{ {"a": {"b": 1}} | combine({"a": {"c": 2}}, recursive=True) }}
{{ [1, [2, [3]]] | flatten }}
{{ {"name": "app"} | dict2items }}
{{ [1, 2] | union([2, 3]) }}
{{ [0, 2] | map("extract", {0: "a", 2: "c"}) | list }}
{{ users | subelements("keys") | list }}
```

### Date and time filters

- `to_datetime(value, format=None)` — parse a string; the default format is
  `%Y-%m-%d %H:%M:%S`.
- `strftime(value, format)` — format a date or datetime value.

Both require an explicit value.

```jinja
{{ "2020-01-02 03:04:05" | to_datetime | strftime("%Y/%m/%d") }}
```

### Sequence filters

- `zip(*sequences)`
- `zip_longest(*sequences, fillvalue=None)`

```jinja
{{ [1, 2] | zip([3, 4]) | list }}
{{ [1] | zip_longest([2, 3], fillvalue=0) | list }}
```

### URL filter

- `urlsplit(value, query=None)` — return a mapping of URL components, or one
  component such as `scheme`, `hostname`, `port`, `path`, or `query`.

```jinja
{{ "https://example.com:8443/app?q=1" | urlsplit("hostname") }}
{{ ("https://example.com/app" | urlsplit).path }}
```

### Serialization filters

- `to_yaml(value, *, default_flow_style=False, **kwargs)` / `from_yaml` — YAML.
  Output defaults to block style; pass `default_flow_style=None` for flow style
  where possible.
- `to_json(value, **kwargs)` / `from_json` — JSON.

Extra keyword arguments are forwarded to the serializer, so
`to_json(indent=2, sort_keys=True)` works. Jinja2's built-in `tojson` remains
available under its own name.

```jinja
{{ {"a": [1, 2]} | to_yaml }}
{{ '{"a": 1}' | from_json }}
{{ {"b": 2, "a": 1} | to_json(sort_keys=True) }}
```

### Network filters

- `ipaddr(value, query="")` — validate an address or CIDR. Without a query it
  returns the normalized value, or `False` when invalid. Type queries (`ipv4`,
  `ipv6`, and `ipaddr`) return the input value when the address family matches,
  or `False` otherwise. Network queries are `address`, `network`, `broadcast`,
  `prefix`, and `netmask`.
- `ipmath(value, amount)` — add an integer offset to an address, return the
  Nth address of a network (negative offsets count from the end), or return a
  named network value: `network`, `broadcast`, `first_usable`, `last_usable`,
  `prefix`, or `netmask`. Integer offsets on a CIDR index the network range.
  Float offsets are rejected.

Both filters support IPv4 and IPv6.

```jinja
{{ "192.168.0.0/24" | ipaddr("network") }}
{{ "192.168.0.0/24" | ipaddr("broadcast") }}
{{ "192.168.0.1" | ipmath(5) }}
{{ "192.168.0.0/24" | ipmath("last_usable") }}
```

### Type filter

- `bool` — convert `yes`/`on`/`true`/`1` and `no`/`off`/`false`/`0`
  case-insensitively.

### Tests

- `version(value, version, operator="eq", *, strict=False, version_type=None)`
  and its alias `version_compare` — loose version comparison with `==`, `=`,
  `eq`, `<`, `lt`, `<=`, `le`, `>`, `gt`, `>=`, `ge`, `!=`, `<>`, or `ne`. Only
  `version_type="loose"` is supported.
- `search`, `match`, `regex` — regular-expression tests; `regex` accepts
  `match_type="search"` or `"match"`. All accept `ignorecase` and `multiline`.
- `any`, `all` — truthiness over a sequence.
- `contains(seq, value)` — membership.
- `uri`, `url`, `urn` — URL tests. Each accepts an optional `schemes` allowlist;
  `url` additionally requires a network location or the `file` scheme.
- `ipaddr`, `ipv4`, `ipv6` — address validation.

```jinja
{% if facts.version_id is version("40", ">=") %}supported{% endif %}
{% if "foobar" is search("bar") %}matched{% endif %}
{% if "http://example.com" is url %}is a URL{% endif %}
{% if "192.168.0.1" is ipv4 %}is IPv4{% endif %}
```

## Notes

- `--dry-run` reports changes without touching the filesystem; `--diff` prints
  unified diffs for changed text files.
- Writes never follow a destination symlink; the symlink itself is replaced.
- Only regular files are read or written. Non-UTF-8 files are rejected when
  editing lines, blocks, INI options, or TOML keys.
- `sudo=True` runs the operation as root; otherwise it runs as the current user.
