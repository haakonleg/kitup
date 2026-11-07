"""Parsing and normalization of PEP 508 requirement strings.

Covers PEP 503 distribution names, PEP 440 version specifiers, and PEP 508
requirements (``name[extras]specifier ; marker``), plus uv's tool-only
``name@<version>`` / ``name@latest`` syntax as an extension. URLs, VCS
references, and local paths are rejected — modules that accept them handle
them separately.
"""

import re
from dataclasses import dataclass
from typing import Final

from ..errors import TaskFailed

_DIST_NAME_RE: Final = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")
_NORMALIZE_RE: Final = re.compile(r"[-_.]+")
_REQUIREMENT_RE: Final = re.compile(
    r"(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)(?:\[(?P<extras>[^\]]*)\])?\s*(?P<rest>.*)",
)
_SPECIFIER_CLAUSE_RE: Final = re.compile(r"(===|==|!=|~=|>=|<=|>|<)\s*([^\s,;=<>~]+)")
_SPECIFIER_OP_RE: Final = re.compile(r"(?:===|==|!=|~=|>=|<=|>|<)")


def normalize_dist_name(name: str) -> str:
    """Normalize a distribution name per PEP 503."""
    return _NORMALIZE_RE.sub("-", name).lower()


def dist_name_of(requirement: str) -> str:
    """Return the distribution name prefix of a requirement string."""
    match = _REQUIREMENT_RE.match(requirement.strip())
    if match is None:
        raise TaskFailed(f"cannot determine a distribution name from {requirement!r}")
    return match.group("name")


def normalize_specifier(text: str, *, source: str = "") -> str:
    """Normalize a PEP 440 specifier set (``">=2, <3"`` -> ``">=2,<3"``), rejecting invalid clauses."""
    clauses: list[str] = []
    for clause in text.split(","):
        clause = clause.strip()
        if not clause:
            continue
        match = _SPECIFIER_CLAUSE_RE.fullmatch(clause)
        if match is None:
            where = f" in requirement {source!r}" if source else ""
            raise TaskFailed(f"invalid version specifier {clause!r}{where}")
        clauses.append(match.group(1) + match.group(2))
    return ",".join(clauses)


@dataclass(frozen=True, slots=True)
class Requirement:
    """A parsed registry requirement: PEP 508 plus uv tool ``@`` specifiers.

    ``specifier``, ``extras``, and ``marker`` are normalized for comparison:
    specifier whitespace is removed and extras are PEP 503-normalized and
    sorted. ``source`` keeps the original string verbatim. A uv tool
    ``name@<version>`` maps to ``specifier="==<version>"`` and ``name@latest``
    to an empty specifier, matching how uv records them in tool receipts.
    """

    source: str
    name: str
    specifier: str
    extras: tuple[str, ...]
    marker: str
    at: str | None = None

    @property
    def normalized(self) -> str:
        """Return the PEP 503-normalized distribution name."""
        return normalize_dist_name(self.name)

    @property
    def is_bare(self) -> bool:
        """Return whether the requirement is a bare name with no specifier, extras, marker, or ``@``."""
        return not self.specifier and not self.extras and not self.marker and self.at is None

    def matches(self, other: Requirement) -> bool:
        """Return whether ``other`` declares the same specifier, extras, and marker (names assumed equal)."""
        return self.specifier == other.specifier and self.extras == other.extras and self.marker == other.marker


def parse_requirement(requirement: str, *, tool: bool = False) -> Requirement:
    """Parse a registry requirement string, rejecting URLs, paths, and invalid syntax.

    The PEP 508 form is ``name[extras]specifier ; marker``. With ``tool=True``,
    uv's ``name@<specifier>`` / ``name@latest`` tool syntax is accepted too.
    """
    if not isinstance(requirement, str):
        raise TaskFailed("requirement must be a string")
    text = requirement.strip()
    if not text:
        raise TaskFailed("requirement must not be empty")
    if "://" in text or "/" in text or "\\" in text or text.startswith("~") or " @ " in text:
        raise TaskFailed(f"registry requirements only, not URLs or paths: {requirement!r}")
    match = _REQUIREMENT_RE.fullmatch(text)
    if match is None:
        raise TaskFailed(f"invalid requirement: {requirement!r}")
    name = match.group("name")
    extras = _parse_extras(match.group("extras"), requirement)
    rest = match.group("rest").strip()
    at: str | None = None
    if rest.startswith("@"):
        if not tool:
            raise TaskFailed(f"'@' specifiers are uv tool syntax, not valid in a requirement: {requirement!r}")
        at = rest[1:].strip()
        if not at:
            raise TaskFailed(f"invalid requirement (empty '@' specifier): {requirement!r}")
        rest = ""
    elif "@" in rest:
        raise TaskFailed(f"invalid requirement: {requirement!r}")
    specifier, marker = _parse_specifier_marker(rest, requirement)
    if at is not None:
        if at == "latest":
            specifier = ""
        elif _SPECIFIER_OP_RE.match(at) is not None:
            specifier = normalize_specifier(at, source=requirement)
        else:
            specifier = "==" + at
    return Requirement(
        source=requirement,
        name=name,
        specifier=specifier,
        extras=extras,
        marker=marker,
        at=at,
    )


def _parse_extras(raw: str | None, source: str) -> tuple[str, ...]:
    """Parse the ``[extras]`` part of a requirement into normalized, sorted names."""
    if raw is None:
        return ()
    extras: list[str] = []
    for part in raw.split(","):
        part = part.strip()
        if _DIST_NAME_RE.fullmatch(part) is None:
            raise TaskFailed(f"invalid extra {part!r} in requirement: {source!r}")
        extras.append(normalize_dist_name(part))
    return tuple(sorted(dict.fromkeys(extras)))


def _parse_specifier_marker(rest: str, source: str) -> tuple[str, str]:
    """Split the remainder of a requirement into a normalized specifier and a marker."""
    if ";" in rest:
        spec_part, marker = rest.split(";", 1)
        marker = marker.strip()
        if not marker:
            raise TaskFailed(f"invalid requirement (empty marker): {source!r}")
    else:
        spec_part, marker = rest, ""
    return normalize_specifier(spec_part, source=source), marker
