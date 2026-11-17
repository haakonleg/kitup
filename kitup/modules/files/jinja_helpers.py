"""Ansible-style Jinja2 filters and tests used by kitup templates."""

import base64
import datetime
import ipaddress
import itertools
import json
import os
import re
import shlex
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from functools import cache, partial
from operator import eq, ge, gt, index, le, lt, ne
from typing import Any, Final
from urllib.parse import ParseResult, urlparse, urlsplit

from ...errors import TaskFailed

_LIST_MERGE_STRATEGIES: Final = frozenset({"replace", "keep", "append", "prepend", "append_rp", "prepend_rp"})
_DEFAULT_DATETIME_FORMAT: Final = "%Y-%m-%d %H:%M:%S"
_URL_COMPONENTS: Final = frozenset(
    {"scheme", "netloc", "path", "query", "fragment", "username", "password", "hostname", "port"}
)
_IPADDR_TYPE_QUERIES: Final = frozenset({"ipv4", "ipv6", "ipaddr"})
_IPADDR_NETWORK_QUERIES: Final = frozenset({"address", "network", "broadcast", "prefix", "netmask"})
_VERSION_OPERATORS: Final[Mapping[str, Callable[[Any, Any], bool]]] = {
    "==": eq,
    "=": eq,
    "eq": eq,
    "<": lt,
    "lt": lt,
    "<=": le,
    "le": le,
    ">": gt,
    "gt": gt,
    ">=": ge,
    "ge": ge,
    "!=": ne,
    "<>": ne,
    "ne": ne,
}


def _basename(value: Any) -> str:
    """Return the final component of a path."""
    return os.path.basename(str(value))


def _dirname(value: Any) -> str:
    """Return the directory component of a path."""
    return os.path.dirname(str(value))


def _splitext(value: Any) -> tuple[str, str]:
    """Split a path into its root and extension."""
    return os.path.splitext(str(value))


def _normpath(value: Any) -> str:
    """Normalize redundant separators and up-level references in a path."""
    return os.path.normpath(str(value))


def _expanduser(value: Any) -> str:
    """Expand a leading ``~`` or ``~user`` in a path."""
    return os.path.expanduser(str(value))


def _path_join(value: Any) -> str:
    """Join a path or a sequence of path components."""
    components = [value] if isinstance(value, (str, os.PathLike)) else list(value)
    return os.fsdecode(os.path.join(*(os.fspath(component) for component in components)))


def _regex_replace(
    value: Any,
    pattern: str,
    replacement: str,
    *,
    ignorecase: bool = False,
    multiline: bool = False,
    count: int = 0,
) -> str:
    """Replace regular-expression matches in a string."""
    return re.sub(
        pattern,
        replacement,
        str(value),
        count=count,
        flags=_regex_flags(ignorecase=ignorecase, multiline=multiline),
    )


def _regex_search(
    value: Any,
    regex: str,
    *groups: Any,
    ignorecase: bool = False,
    multiline: bool = False,
) -> Any:
    """Return the first regular-expression match, or selected groups from it."""
    match = re.search(regex, str(value), _regex_flags(ignorecase=ignorecase, multiline=multiline))
    if match is None:
        return None
    if not groups:
        return match.group()
    return [match.group(_group_reference(group)) for group in groups]


def _regex_findall(value: Any, regex: str, *, ignorecase: bool = False, multiline: bool = False) -> list[Any]:
    """Return every non-overlapping regular-expression match."""
    return re.findall(regex, str(value), _regex_flags(ignorecase=ignorecase, multiline=multiline))


def _regex_escape(value: Any, re_type: str = "python") -> str:
    """Escape regular-expression metacharacters for a dialect."""
    text = str(value)
    if re_type == "python":
        return re.escape(text)
    if re_type == "posix_basic":
        return re.sub(r"([.^$*\\[\]])", r"\\\1", text)
    if re_type == "posix_extended":
        return re.sub(r"([.^$*+?{}\[\]\\|()])", r"\\\1", text)
    raise ValueError(f"unsupported regular-expression type: {re_type}")


def _split(value: Any, sep: str | None = None, maxsplit: int = -1) -> list[str]:
    """Split a string into a list of substrings."""
    return str(value).split(sep, maxsplit)


def _quote(value: Any) -> str:
    """Quote a value for safe use as a shell argument."""
    if value is None:
        return "''"
    return shlex.quote(str(value))


def _squote(value: Any) -> str:
    """Wrap a value in single quotes, escaping embedded single quotes."""
    if value is None:
        return "''"
    return "'" + str(value).replace("'", "'\\''") + "'"


def _b64encode(value: Any, encoding: str = "utf-8", *, urlsafe: bool = False) -> str:
    """Encode a value as Base64 text."""
    data = _as_bytes(value, encoding)
    encoded = base64.urlsafe_b64encode(data) if urlsafe else base64.b64encode(data)
    return encoded.decode("ascii")


def _b64decode(value: Any, encoding: str = "utf-8", *, urlsafe: bool = False) -> str:
    """Decode Base64 text into a string."""
    data = _as_bytes(value, "ascii")
    decoded = base64.urlsafe_b64decode(data) if urlsafe else base64.b64decode(data)
    return decoded.decode(encoding)


def _comment(value: Any, prefix: str = "#") -> str:
    """Prefix a value, or every line of a list, with a comment marker."""
    marker = prefix.rstrip()
    text = "\n".join(str(item) for item in value) if isinstance(value, (list, tuple)) else str(value)
    return "\n".join(f"{marker} {line}" if line else marker for line in text.split("\n"))


def _combine(*terms: Any, recursive: bool = False, list_merge: str = "replace") -> dict[str, Any]:
    """Merge mappings, optionally recursively."""
    if list_merge not in _LIST_MERGE_STRATEGIES:
        raise ValueError(f"unsupported list_merge strategy: {list_merge}")
    result: dict[str, Any] = {}
    for term in _flatten(terms, levels=1):
        if not isinstance(term, Mapping):
            raise TypeError("combine arguments must be mappings")
        result = _merge_dicts(result, term, recursive=recursive, list_merge=list_merge)
    return result


def _flatten(value: Iterable[Any], levels: int | None = None, *, skip_nulls: bool = True) -> list[Any]:
    """Flatten nested lists, optionally by a limited number of levels."""
    if levels is not None:
        levels = int(levels)
    result: list[Any] = []
    for element in value:
        if skip_nulls and element in (None, "None", "null"):
            continue
        if not isinstance(element, (list, tuple)):
            result.append(element)
        elif levels is None:
            result.extend(_flatten(element, skip_nulls=skip_nulls))
        elif levels > 0:
            result.extend(_flatten(element, levels - 1, skip_nulls=skip_nulls))
        else:
            result.append(element)
    return result


def _dict2items(value: Mapping[str, Any], key_name: str = "key", value_name: str = "value") -> list[dict[str, Any]]:
    """Convert a mapping into a list of key/value items."""
    return [{key_name: key, value_name: item} for key, item in value.items()]


def _items2dict(value: Iterable[Mapping[str, Any]], key_name: str = "key", value_name: str = "value") -> dict[Any, Any]:
    """Convert a list of key/value items into a mapping."""
    return {item[key_name]: item[value_name] for item in value}


def _union(left: Iterable[Any], right: Iterable[Any]) -> list[Any]:
    """Return the union of two sequences."""
    return _unique([*left, *right])


def _intersect(left: Iterable[Any], right: Iterable[Any]) -> list[Any]:
    """Return the intersection of two sequences."""
    candidates = set(right)
    return _unique([item for item in left if item in candidates])


def _difference(left: Iterable[Any], right: Iterable[Any]) -> list[Any]:
    """Return the items in the first sequence that are absent from the second."""
    candidates = set(right)
    return _unique([item for item in left if item not in candidates])


def _extract(item: Any, container: Any, morekeys: Any = None) -> Any:
    """Extract a value from a mapping by key, or by a sequence of nested keys."""
    if not isinstance(container, Mapping):
        raise TypeError("extract requires a mapping container")
    keys = [item]
    if morekeys is not None:
        if isinstance(morekeys, (list, tuple)):
            keys.extend(morekeys)
        else:
            keys.append(morekeys)
    value: Any = container
    for key in keys:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
        if value is None:
            return None
    return value


def _subelements(value: Any, subkey: str | Sequence[str]) -> Iterator[tuple[Any, Any]]:
    """Yield parent/sub-element pairs from a list of mappings."""
    items = list(value.values()) if isinstance(value, Mapping) else value
    keys = subkey.split(".") if isinstance(subkey, str) else list(subkey)
    for item in items:
        current: Any = item
        for key in keys:
            if not isinstance(current, Mapping) or key not in current:
                raise ValueError(f"could not find {key!r} in {current!r}")
            current = current[key]
        if not isinstance(current, (list, tuple)):
            raise TypeError(f"subelements key {subkey!r} must point to a list: {current!r}")
        for subitem in current:
            yield item, subitem


def _to_datetime(value: Any, format: str | None = None) -> datetime.datetime:
    """Parse a date and time string into a ``datetime`` value."""
    if isinstance(value, datetime.datetime):
        return value
    pattern = _DEFAULT_DATETIME_FORMAT if format is None else format
    return datetime.datetime.strptime(str(value), pattern)  # noqa: DTZ007 - Ansible-compatible naive datetimes


def _strftime(value: Any, format: str) -> str:
    """Format a date and time value with ``strftime``."""
    if isinstance(value, str):
        value = _to_datetime(value)
    if not isinstance(value, (datetime.date, datetime.time)):
        raise TypeError("strftime requires a date, time, or datetime value")
    return value.strftime(format)


def _zip(*values: Iterable[Any]) -> list[tuple[Any, ...]]:
    """Zip sequences into a list of tuples."""
    return list(zip(*values, strict=False))


def _zip_longest(*values: Iterable[Any], fillvalue: Any = None) -> list[tuple[Any, ...]]:
    """Zip sequences into a list of tuples, padding shorter sequences."""
    return list(itertools.zip_longest(*values, fillvalue=fillvalue))


def _urlsplit(value: Any, query: str | None = None) -> Any:
    """Split a URL into its components, or return a single component."""
    parsed = urlsplit(str(value))
    if query:
        if query not in _URL_COMPONENTS:
            raise ValueError(f"unsupported URL component: {query}")
        return getattr(parsed, query)
    return dict(parsed._asdict())


def _to_yaml(value: Any, *, default_flow_style: bool | None = False, **kwargs: Any) -> str:
    """Serialize a value as YAML, defaulting to block style."""
    return str(_load_yaml().safe_dump(value, default_flow_style=default_flow_style, **kwargs))


def _from_yaml(value: Any) -> Any:
    """Parse a YAML document, passing non-string values through unchanged."""
    if not isinstance(value, str):
        return value
    return _load_yaml().safe_load(value)


def _to_json(value: Any, **kwargs: Any) -> str:
    """Serialize a value as JSON."""
    return json.dumps(value, **kwargs)


def _from_json(value: Any) -> Any:
    """Parse a JSON document."""
    return json.loads(value)


def _ipaddr(value: Any, query: str = "") -> Any:
    """Validate an address or network and return a selected component."""
    text = str(value)
    try:
        interface = ipaddress.ip_interface(text) if "/" in text else None
        address = interface.ip if interface is not None else ipaddress.ip_address(text)
    except ValueError:
        return False

    if not query:
        return str(interface) if interface is not None else str(address)
    if query in _IPADDR_TYPE_QUERIES:
        if query == "ipv4" and address.version != 4:
            return False
        if query == "ipv6" and address.version != 6:
            return False
        return text
    if query in _IPADDR_NETWORK_QUERIES:
        network = interface.network if interface is not None else ipaddress.ip_network(address)
        if query == "address":
            return str(address)
        return _network_query(network, query)
    raise ValueError(f"unsupported ipaddr query: {query}")


def _ipmath(value: Any, amount: Any) -> str | int:
    """Add an offset to an address, or return a named network address."""
    text = str(value)
    if isinstance(amount, str):
        return _ipmath_query(text, amount)
    try:
        offset = index(amount)
    except TypeError as exc:
        raise TypeError(f"ipmath offset must be an integer or a named query: {amount!r}") from exc

    if "/" in text:
        network = _ip_network(text)
        count = network.num_addresses
        position = offset if offset >= 0 else count + offset
        if position < 0 or position >= count:
            raise ValueError(f"ipmath offset {offset} is out of range for {text}")
        return str(network[position])

    address = _ip_address(text)
    try:
        return str(address + offset)
    except ValueError as exc:
        raise ValueError(f"ipmath offset {offset} is out of range for {text}") from exc


def _to_bool(value: Any) -> bool:
    """Convert a boolean-like value to ``True`` or ``False``."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"yes", "on", "true", "1"}:
            return True
        if normalized in {"no", "off", "false", "0"}:
            return False
    elif isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
    raise ValueError(f"cannot convert {value!r} to a boolean")


def _version(
    value: Any,
    version: Any,
    operator: str = "eq",
    *,
    strict: bool = False,
    version_type: str | None = None,
) -> bool:
    """Compare two loose version strings with an operator."""
    if version_type not in (None, "loose"):
        raise ValueError(f"unsupported version_type: {version_type}")
    if strict:
        raise ValueError("strict version comparison is not supported")
    if not value:
        raise ValueError("input version value cannot be empty")
    if not version:
        raise ValueError("version parameter to compare against cannot be empty")
    try:
        compare = _VERSION_OPERATORS[operator]
    except KeyError as exc:
        raise ValueError(f"unsupported version operator: {operator}") from exc
    return compare(_version_key(value), _version_key(version))


def _search(value: Any, pattern: str, *, ignorecase: bool = False, multiline: bool = False) -> bool:
    """Return whether a regular expression matches anywhere in a string."""
    return re.search(pattern, str(value), _regex_flags(ignorecase=ignorecase, multiline=multiline)) is not None


def _match(value: Any, pattern: str, *, ignorecase: bool = False, multiline: bool = False) -> bool:
    """Return whether a regular expression matches at the start of a string."""
    return re.match(pattern, str(value), _regex_flags(ignorecase=ignorecase, multiline=multiline)) is not None


def _regex(
    value: Any,
    pattern: str,
    *,
    ignorecase: bool = False,
    multiline: bool = False,
    match_type: str = "search",
) -> bool:
    """Return whether a regular expression matches, using ``search`` or ``match``."""
    if match_type == "search":
        return _search(value, pattern, ignorecase=ignorecase, multiline=multiline)
    if match_type == "match":
        return _match(value, pattern, ignorecase=ignorecase, multiline=multiline)
    raise ValueError(f"unsupported match_type: {match_type}")


def _contains(seq: Any, value: Any) -> bool:
    """Return whether a sequence contains an item."""
    return value in seq


def _uri(value: Any, schemes: Any = None) -> bool:
    """Return whether a string looks like a URI."""
    return _parse_uri(value, schemes) is not None


def _url(value: Any, schemes: Any = None) -> bool:
    """Return whether a string looks like a URL."""
    parsed = _parse_uri(value, schemes)
    return parsed is not None and (parsed.scheme == "file" or bool(parsed.netloc))


def _urn(value: Any, schemes: Any = None) -> bool:
    """Return whether a string looks like a URN."""
    parsed = _parse_uri(value, schemes)
    return parsed is not None and parsed.scheme == "urn" and bool(parsed.path)


def _is_ipaddr(value: Any, version: int | None = None) -> bool:
    try:
        address = ipaddress.ip_address(str(value))
    except ValueError:
        return False
    return version is None or address.version == version


def _regex_flags(*, ignorecase: bool, multiline: bool) -> int:
    flags = 0
    if ignorecase:
        flags |= re.IGNORECASE
    if multiline:
        flags |= re.MULTILINE
    return flags


def _group_reference(group: Any) -> int | str:
    if isinstance(group, int):
        return group
    if not isinstance(group, str):
        raise TypeError(f"regular-expression group must be an integer or name: {group!r}")
    if group.startswith("\\g<") and group.endswith(">"):
        group = group[3:-1]
    elif group.startswith("\\") and group[1:].isdigit():
        group = group[1:]
    return int(group) if group.isdigit() else group


def _as_bytes(value: Any, encoding: str) -> bytes:
    if isinstance(value, str):
        return value.encode(encoding)
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    return str(value).encode(encoding)


def _unique(values: Iterable[Any]) -> list[Any]:
    return list(dict.fromkeys(values))


def _merge_lists(left: list[Any], right: list[Any], strategy: str) -> list[Any]:
    if strategy == "replace":
        return list(right)
    if strategy == "keep":
        return list(left)
    if strategy == "append":
        return [*left, *right]
    if strategy == "prepend":
        return [*right, *left]
    if strategy == "append_rp":
        return [item for item in left if item not in right] + list(right)
    if strategy == "prepend_rp":
        return list(right) + [item for item in left if item not in right]
    raise ValueError(f"unsupported list_merge strategy: {strategy}")


def _merge_dicts(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    *,
    recursive: bool,
    list_merge: str,
) -> dict[str, Any]:
    result = dict(left)
    for key, value in right.items():
        existing = result.get(key)
        if recursive and isinstance(existing, Mapping) and isinstance(value, Mapping):
            result[key] = _merge_dicts(existing, value, recursive=True, list_merge=list_merge)
        elif isinstance(existing, list) and isinstance(value, list):
            result[key] = _merge_lists(existing, value, list_merge)
        else:
            result[key] = value
    return result


def _ip_address(value: Any) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    return ipaddress.ip_address(str(value))


def _ip_network(value: Any) -> ipaddress.IPv4Network | ipaddress.IPv6Network:
    return ipaddress.ip_network(str(value), strict=False)


def _network_query(network: ipaddress.IPv4Network | ipaddress.IPv6Network, query: str) -> str | int:
    """Return a named component of a network."""
    if query == "network":
        return str(network.network_address)
    if query == "broadcast":
        return str(network.broadcast_address)
    if query == "prefix":
        return network.prefixlen
    if query == "netmask":
        return str(network.netmask)
    raise ValueError(f"unsupported network query: {query}")


def _ipmath_query(text: str, query: str) -> str | int:
    network = _ip_network(text)
    if query == "first_usable":
        if network.num_addresses <= 2:
            return str(network.network_address)
        return str(network.network_address + 1)
    if query == "last_usable":
        if network.num_addresses <= 2:
            return str(network.broadcast_address)
        return str(network.broadcast_address - 1)
    return _network_query(network, query)


def _version_key(value: Any) -> list[tuple[int, Any]]:
    parts = re.findall(r"\d+|[A-Za-z]+|[^.\dA-Za-z]+", str(value))
    return [(0, int(part)) if part.isdigit() else (1, part) for part in parts]


def _schemes(value: Any) -> set[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return {value}
    return {str(scheme) for scheme in value}


def _parse_uri(value: Any, schemes: Any) -> ParseResult | None:
    """Parse a URI and apply the optional scheme allowlist."""
    parsed = urlparse(str(value))
    if not parsed.scheme:
        return None
    allowed = _schemes(schemes)
    if allowed is not None and parsed.scheme not in allowed:
        return None
    return parsed


@cache
def _load_yaml() -> Any:
    """Import PyYAML on first use so a broken install reports a task failure."""
    try:
        import yaml  # noqa: PLC0415 - lazy import reports a task-level error
    except ImportError as exc:  # pragma: no cover - packaging guarantees the dependency
        raise TaskFailed("the YAML filters require PyYAML; install it or use the standalone kitup build") from exc
    return yaml


JINJA_FILTERS: Final[Mapping[str, Callable[..., Any]]] = {
    "b64decode": _b64decode,
    "b64encode": _b64encode,
    "basename": _basename,
    "bool": _to_bool,
    "combine": _combine,
    "comment": _comment,
    "dict2items": _dict2items,
    "difference": _difference,
    "dirname": _dirname,
    "expanduser": _expanduser,
    "extract": _extract,
    "flatten": _flatten,
    "from_json": _from_json,
    "from_yaml": _from_yaml,
    "intersect": _intersect,
    "ipaddr": _ipaddr,
    "ipmath": _ipmath,
    "items2dict": _items2dict,
    "normpath": _normpath,
    "path_join": _path_join,
    "quote": _quote,
    "regex_escape": _regex_escape,
    "regex_findall": _regex_findall,
    "regex_replace": _regex_replace,
    "regex_search": _regex_search,
    "split": _split,
    "splitext": _splitext,
    "squote": _squote,
    "strftime": _strftime,
    "subelements": _subelements,
    "to_datetime": _to_datetime,
    "to_json": _to_json,
    "to_yaml": _to_yaml,
    "union": _union,
    "urlsplit": _urlsplit,
    "zip": _zip,
    "zip_longest": _zip_longest,
}

JINJA_TESTS: Final[Mapping[str, Callable[..., bool]]] = {
    "all": all,
    "any": any,
    "contains": _contains,
    "ipaddr": _is_ipaddr,
    "ipv4": partial(_is_ipaddr, version=4),
    "ipv6": partial(_is_ipaddr, version=6),
    "match": _match,
    "regex": _regex,
    "search": _search,
    "uri": _uri,
    "url": _url,
    "urn": _urn,
    "version": _version,
    "version_compare": _version,
}
