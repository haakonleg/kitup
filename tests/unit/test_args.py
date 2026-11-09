import pytest

from kitup import cli
from kitup.args import Command, ParseError, Parser


@pytest.fixture
def parser():
    return cli.build_parser()


def test_long_option_takes_next_token(parser):
    args = parser.parse_args(["apply", "--preset", "laptop"])

    assert args.preset == ["laptop"]


def test_long_option_accepts_inline_value(parser):
    args = parser.parse_args(["apply", "--preset=laptop"])

    assert args.preset == ["laptop"]


def test_short_option_accepts_inline_value(parser):
    args = parser.parse_args(["apply", "-p=laptop"])

    assert args.preset == ["laptop"]


def test_append_option_collects_repeated_values(parser):
    args = parser.parse_args(["apply", "-t", "a", "--tags=b"])

    assert args.tags == ["a", "b"]


def test_flag_option_sets_true(parser):
    args = parser.parse_args(["apply", "--dry-run"])

    assert args.dry_run is True


def test_positional_is_optional(parser):
    args = parser.parse_args(["apply"])

    assert not hasattr(args, "project_dir")


def test_positional_value_is_stored(parser):
    args = parser.parse_args(["apply", "machine"])

    assert args.project_dir == "machine"


def test_only_supplied_options_are_present(parser):
    args = parser.parse_args(["apply"])

    assert not hasattr(args, "include_bundles")
    assert not hasattr(args, "dry_run")


def test_unknown_long_option_is_rejected(parser, capsys):
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["apply", "--include-bundl", "base"])

    assert exc.value.code == 2
    assert "unrecognized arguments: --include-bundl" in capsys.readouterr().err


def test_clustered_short_options_are_rejected(parser, capsys):
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["apply", "-nm"])

    assert exc.value.code == 2
    assert "unrecognized arguments: -nm" in capsys.readouterr().err


def test_extra_positional_is_rejected(parser, capsys):
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["apply", "one", "two"])

    assert exc.value.code == 2
    assert "unrecognized arguments: two" in capsys.readouterr().err


def test_missing_option_value_is_an_error(parser, capsys):
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["apply", "--preset"])

    assert exc.value.code == 2
    error = capsys.readouterr().err
    assert "expected one argument" in error
    assert "usage: kitup apply" in error


def test_unknown_option_error_uses_command_usage(parser, capsys):
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["apply", "--nope"])

    assert exc.value.code == 2
    assert "usage: kitup apply" in capsys.readouterr().err


def test_option_value_that_looks_like_an_option_is_an_error(parser, capsys):
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["apply", "--preset", "--dry-run"])

    assert exc.value.code == 2
    assert "expected one argument" in capsys.readouterr().err


def test_flag_with_explicit_value_is_an_error(parser, capsys):
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["apply", "--dry-run=yes"])

    assert exc.value.code == 2
    assert "ignored explicit argument" in capsys.readouterr().err


def test_command_is_required(parser, capsys):
    with pytest.raises(SystemExit) as exc:
        parser.parse_args([])

    assert exc.value.code == 2
    assert "the following arguments are required: COMMAND" in capsys.readouterr().err


def test_unknown_command_is_an_error(parser, capsys):
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["bogus"])

    assert exc.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_version_prints_and_exits(parser, capsys):
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--version"])

    assert exc.value.code == 0
    assert "kitup 3.0.0" in capsys.readouterr().out


def test_version_flag_works_after_the_command(parser, capsys):
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["apply", "-V"])

    assert exc.value.code == 0
    assert "kitup 3.0.0" in capsys.readouterr().out


def test_help_prints_and_exits(parser, capsys):
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["apply", "--help"])

    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "usage: kitup apply" in output
    assert "--dry-run" in output


def test_double_dash_ends_option_parsing(parser):
    args = parser.parse_args(["apply", "--", "--not-an-option"])

    assert args.project_dir == "--not-an-option"


def test_parse_known_args_returns_unknown_options(parser):
    args, extras = parser.parse_known_args(["list", "--dry-run", "--include-bundles", "base"])

    assert extras == ["--dry-run"]
    assert args.include_bundles == ["base"]
    assert not hasattr(args, "dry_run")


def test_parse_known_args_ignores_extra_positionals(parser):
    args, extras = parser.parse_known_args(["apply", "one", "two"])

    assert args.project_dir == "one"
    assert extras == ["two"]


def test_parse_known_args_does_not_raise_on_unknown(parser):
    args, extras = parser.parse_known_args(["apply", "--nope", "value"])

    assert extras == ["--nope"]
    assert args.project_dir == "value"


def test_parse_known_args_raises_on_missing_value(parser):
    with pytest.raises(ParseError, match="expected one argument"):
        parser.parse_known_args(["apply", "--preset"])


def test_help_lists_commands(capsys):
    parser = Parser(
        prog="demo",
        description="demo tool",
        commands=(Command(name="run", help="run things", options=()),),
    )

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--help"])

    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert "usage: demo" in output
    assert "run things" in output
