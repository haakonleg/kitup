"""Minimal command-line parser for kitup's fixed option set."""

import sys
from collections.abc import Sequence
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Final, Literal, Never

_HELP_FLAGS: Final = ("-h", "--help")
_HELP_WIDTH: Final = 34
_VALUE_KINDS: Final = frozenset({"store", "append"})


@dataclass(frozen=True, slots=True)
class Option:
    """One command-line option or the optional positional argument."""

    flags: tuple[str, ...]
    dest: str
    default: Any = None
    kind: Literal["store", "append", "flag", "version"] = "store"
    metavar: str | None = None
    help: str | None = None
    version: str | None = None
    positional: bool = False


@dataclass(frozen=True, slots=True)
class Command:
    """One subcommand and the options it accepts."""

    name: str
    help: str
    options: tuple[Option, ...]
    epilog: str = ""

    @property
    def positional(self) -> Option | None:
        """Return the command's optional positional argument, if any."""
        return next((option for option in self.options if option.positional), None)


class ParseError(Exception):
    """A malformed argument sequence, optionally tied to a command."""

    def __init__(self, message: str, *, command: Command | None = None) -> None:
        super().__init__(message)
        self.command = command


class Args(SimpleNamespace):
    """Parsed command-line values; only options actually supplied are present."""


class Parser:
    """Parse kitup's command line using exact option names only.

    Long-option abbreviations and clustered short options are not recognized.
    Accepted forms are ``-x VALUE``, ``-x=VALUE``, ``--long VALUE``, and
    ``--long=VALUE``. Defaults are suppressed so preset merging can distinguish
    an option that was supplied from one that was absent.
    """

    def __init__(
        self,
        *,
        prog: str,
        description: str,
        commands: Sequence[Command],
        top_options: Sequence[Option] = (),
    ) -> None:
        self.prog = prog
        self.description = description
        self._commands = {command.name: command for command in commands}
        self._top_option_list = tuple(top_options)
        self._top_options = _option_map(top_options)
        self._command_options = {command.name: _option_map(command.options) for command in commands}

    def parse_args(self, argv: Sequence[str]) -> Args:
        """Parse ``argv`` and reject anything that is not recognized."""
        try:
            args, extras = self._parse(argv)
        except ParseError as exc:
            self.error(str(exc), command=exc.command)
        if extras:
            command_name = getattr(args, "command", None)
            command = self._commands.get(command_name) if isinstance(command_name, str) else None
            self.error(f"unrecognized arguments: {' '.join(extras)}", command=command)
        return args

    def parse_known_args(self, argv: Sequence[str]) -> tuple[Args, list[str]]:
        """Parse ``argv`` and return unrecognized tokens instead of rejecting them.

        Raises ``ParseError`` when a recognized option is malformed.
        """
        return self._parse(argv)

    def error(self, message: str, *, command: Command | None = None) -> Never:
        """Print a usage error to stderr and exit with status 2."""
        print(self._usage(command), file=sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise SystemExit(2)

    def _parse(self, argv: Sequence[str]) -> tuple[Args, list[str]]:
        args = Args()
        extras: list[str] = []
        tokens = list(argv)
        command: Command | None = None
        options = self._top_options
        positionals: list[str] = []
        index = 0

        while index < len(tokens):
            token = tokens[index]
            if token == "--":
                positionals.extend(tokens[index + 1 :])
                break
            if command is None and token in self._commands:
                command = self._commands[token]
                args.command = command.name
                options = self._command_options[command.name]
                index += 1
                continue
            if token in _HELP_FLAGS:
                self._print_help(command)
                raise SystemExit(0)
            if _looks_like_option(token):
                name, separator, inline = token.partition("=")
                option = options.get(name)
                if option is None:
                    extras.append(token)
                    index += 1
                    continue
                index = self._consume(option, inline if separator else None, tokens, index + 1, args, command)
                continue
            positionals.append(token)
            index += 1

        if command is None:
            if positionals:
                raise ParseError(f"invalid choice: {positionals[0]!r} (choose from {self._choices()})")
            raise ParseError("the following arguments are required: COMMAND")

        positional_option = command.positional
        if positionals:
            if positional_option is not None:
                setattr(args, positional_option.dest, positionals.pop(0))
            extras.extend(positionals)

        return args, extras

    def _consume(
        self,
        option: Option,
        inline: str | None,
        tokens: Sequence[str],
        index: int,
        args: Args,
        command: Command | None,
    ) -> int:
        if option.kind == "version":
            if inline is not None:
                raise ParseError(f"argument {option.flags[-1]}: ignored explicit argument {inline!r}", command=command)
            print(option.version)
            raise SystemExit(0)
        if option.kind == "flag":
            if inline is not None:
                raise ParseError(f"argument {option.flags[-1]}: ignored explicit argument {inline!r}", command=command)
            setattr(args, option.dest, True)
            return index

        if inline is not None:
            value = inline
        else:
            if index >= len(tokens) or _looks_like_option(tokens[index]):
                raise ParseError(f"argument {option.flags[-1]}: expected one argument", command=command)
            value = tokens[index]
            index += 1

        if option.kind == "append":
            values = getattr(args, option.dest, None)
            if values is None:
                setattr(args, option.dest, [value])
            else:
                values.append(value)
        else:
            setattr(args, option.dest, value)
        return index

    def _choices(self) -> str:
        return ", ".join(repr(name) for name in self._commands)

    def _usage(self, command: Command | None) -> str:
        if command is None:
            parts = [self.prog, "[-h]", *_bracketed_flags(self._top_option_list), "COMMAND", "..."]
        else:
            parts = [self.prog, command.name]
            if command.positional is not None:
                parts.append(f"[{command.positional.metavar or command.positional.dest.upper()}]")
            parts.append("[OPTIONS]")
        return "usage: " + " ".join(parts)

    def _print_help(self, command: Command | None) -> None:
        lines = [self._usage(command), ""]
        if command is None:
            lines.append(self.description)
            lines.extend(["", "commands:"])
            lines.extend(f"  {item.name:<10} {item.help}" for item in self._commands.values())
            lines.extend(["", "options:", "  -h, --help  show this help message and exit"])
            lines.extend(_format_options(self._top_option_list))
        else:
            lines.append(command.help)
            positionals = [option for option in command.options if option.positional]
            if positionals:
                lines.extend(["", "positional arguments:"])
                lines.extend(_format_positionals(positionals))
            lines.extend(["", "options:", "  -h, --help  show this help message and exit"])
            lines.extend(_format_options(command.options))
            if command.epilog:
                lines.extend(["", command.epilog])
        print("\n".join(lines))


def _option_map(options: Sequence[Option]) -> dict[str, Option]:
    return {flag: option for option in options if not option.positional for flag in option.flags}


def _looks_like_option(token: str) -> bool:
    return token.startswith("-") and token != "-"


def _bracketed_flags(options: Sequence[Option]) -> list[str]:
    return [f"[{option.flags[0]}]" for option in options]


def _format_options(options: Sequence[Option]) -> list[str]:
    return [_format_row(_option_label(option), option.help) for option in options if not option.positional]


def _format_positionals(options: Sequence[Option]) -> list[str]:
    return [_format_row(option.metavar or option.dest.upper(), option.help) for option in options]


def _format_row(label: str, help_text: str | None) -> str:
    if help_text:
        return f"  {label:<{_HELP_WIDTH}} {help_text}"
    return f"  {label}"


def _option_label(option: Option) -> str:
    labels: list[str] = []
    for flag in option.flags:
        if option.kind in _VALUE_KINDS and option.metavar is not None:
            labels.append(f"{flag} {option.metavar}")
        else:
            labels.append(flag)
    return ", ".join(labels)
