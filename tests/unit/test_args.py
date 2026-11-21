import pytest

from kitup import cli
from kitup.args import Command, ParseError, Parser


@pytest.fixture
def parser():
    return cli.build_parser()


def test_option_forms(parser):
    assert parser.parse_args(["apply", "--preset", "laptop"]).preset == ["laptop"]
    assert parser.parse_args(["apply", "--preset=laptop"]).preset == ["laptop"]
    assert parser.parse_args(["apply", "-p=laptop"]).preset == ["laptop"]
    assert parser.parse_args(["apply", "-t", "a", "--tags=b"]).tags == ["a", "b"]
    assert parser.parse_args(["apply", "--dry-run"]).dry_run is True


def test_optional_positional_and_absent_options(parser):
    args = parser.parse_args(["apply"])

    assert not hasattr(args, "project_dir")
    assert not hasattr(args, "include_bundles")
    assert not hasattr(args, "dry_run")
    assert parser.parse_args(["apply", "machine"]).project_dir == "machine"


def test_invalid_arguments_are_rejected(parser, capsys):
    cases = [
        (["apply", "--nope"], "unrecognized arguments: --nope"),
        (["apply", "one", "two"], "unrecognized arguments: two"),
        (["apply", "--preset"], "expected one argument"),
        (["apply", "--dry-run=yes"], "ignored explicit argument"),
        (["bogus"], "invalid choice"),
        ([], "following arguments are required"),
    ]

    for argv, message in cases:
        with pytest.raises(SystemExit) as exc:
            parser.parse_args(argv)

        assert exc.value.code == 2, argv
        assert message in capsys.readouterr().err, argv


def test_help_and_version_exit_successfully(parser, capsys):
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--version"])

    assert exc.value.code == 0
    assert "kitup 3.0.0" in capsys.readouterr().out

    custom = Parser(
        prog="demo",
        description="demo tool",
        commands=(Command(name="run", help="run things", options=()),),
    )

    with pytest.raises(SystemExit) as exc:
        custom.parse_args(["--help"])

    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "usage: demo" in output
    assert "run things" in output


def test_double_dash_ends_option_parsing(parser):
    args = parser.parse_args(["apply", "--", "--not-an-option"])

    assert args.project_dir == "--not-an-option"


def test_parse_known_args(parser):
    args, extras = parser.parse_known_args(["list", "--dry-run", "--include-bundles", "base"])

    assert extras == ["--dry-run"]
    assert args.include_bundles == ["base"]
    assert not hasattr(args, "dry_run")

    args, extras = parser.parse_known_args(["apply", "one", "two"])
    assert args.project_dir == "one"
    assert extras == ["two"]

    args, extras = parser.parse_known_args(["apply", "--nope", "value"])
    assert extras == ["--nope"]
    assert args.project_dir == "value"

    with pytest.raises(ParseError, match="expected one argument"):
        parser.parse_known_args(["apply", "--preset"])


def test_list_modules_command_is_parsed(parser):
    args = parser.parse_args(["list-modules", "machine", "-v"])

    assert args.command == "list-modules"
    assert args.project_dir == "machine"
    assert args.verbose is True
