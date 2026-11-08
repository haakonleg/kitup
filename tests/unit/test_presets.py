import pytest

from kitup import cli
from kitup.errors import PresetError
from kitup.presets import parse_with_presets, read_preset_tokens

from ..helpers import make_project, write


def parse(tmp_path, argv):
    args, _ = parse_with_presets(cli.build_parser(), argv, root=tmp_path)
    return args


def parse_with_paths(tmp_path, argv):
    return parse_with_presets(cli.build_parser(), argv, root=tmp_path)


def test_read_preset_tokens_splits_shell_style_lines(tmp_path):
    make_project(
        tmp_path,
        preset={
            "laptop": """
            # a comment
            --include-bundles base,workstation

            -i laptop -t "packages, config"
            """
        },
    )

    assert read_preset_tokens(tmp_path / "presets" / "laptop") == [
        "--include-bundles",
        "base,workstation",
        "-i",
        "laptop",
        "-t",
        "packages, config",
    ]


def test_only_full_line_hash_comments_are_ignored(tmp_path):
    write(tmp_path / "presets" / "laptop", "# full-line comment\n--tags a#b\n")

    assert read_preset_tokens(tmp_path / "presets" / "laptop") == ["--tags", "a#b"]


@pytest.mark.parametrize("option", ["-h", "--help", "-V", "--version"])
def test_help_and_version_in_preset_are_ignored(tmp_path, option):
    make_project(tmp_path, preset={"laptop": f"{option}\n--include-bundles base\n"})

    args = parse(tmp_path, ["apply", str(tmp_path), "--preset", "laptop"])

    assert args.include_bundles == ["base"]


def test_preset_values_are_merged_into_the_real_arguments(tmp_path):
    make_project(tmp_path, preset={"laptop": "--include-bundles base\n-t packages\n"})

    args, preset_paths = parse_with_paths(tmp_path, ["apply", str(tmp_path), "--preset", "laptop"])

    assert args.command == "apply"
    assert args.project_dir == str(tmp_path)
    assert args.include_bundles == ["base"]
    assert args.tags == ["packages"]
    assert preset_paths == (tmp_path / "presets" / "laptop",)


def test_real_cli_values_replace_preset_values(tmp_path):
    make_project(tmp_path, preset={"laptop": "--include-bundles base\n-t packages\n"})

    args = parse(
        tmp_path,
        ["apply", str(tmp_path), "--preset", "laptop", "--include-bundles", "desktop", "-t", "docs"],
    )

    assert args.include_bundles == ["desktop"]
    assert args.tags == ["docs"]


def test_later_presets_win(tmp_path):
    make_project(
        tmp_path,
        preset={"first": "--include-bundles base\n", "second": "--include-bundles desktop\n"},
    )

    args, preset_paths = parse_with_paths(
        tmp_path,
        ["apply", str(tmp_path), "--preset", "first", "--preset", "second"],
    )

    assert args.include_bundles == ["desktop"]
    assert preset_paths == (tmp_path / "presets" / "first", tmp_path / "presets" / "second")


def test_append_options_replace_wholesale(tmp_path):
    make_project(tmp_path, preset={"laptop": "-t packages -t config\n"})

    args = parse(tmp_path, ["apply", str(tmp_path), "--preset", "laptop", "-t", "docs"])

    assert args.tags == ["docs"]


def test_without_presets_only_explicit_options_are_present(tmp_path):
    args, preset_paths = parse_with_paths(tmp_path, ["list", str(tmp_path)])

    assert not hasattr(args, "include_bundles")
    assert not hasattr(args, "dry_run")
    assert preset_paths == ()


def test_absolute_preset_paths_are_used_as_is(tmp_path):
    path = tmp_path / "elsewhere" / "laptop"
    write(path, "--include-bundles base\n")

    args = parse(tmp_path, ["apply", str(tmp_path), "--preset", str(path)])

    assert args.include_bundles == ["base"]


def test_relative_presets_resolve_under_presets(tmp_path):
    make_project(tmp_path, preset={"web": "--include-bundles base\n"})

    args, preset_paths = parse_with_paths(tmp_path, ["apply", str(tmp_path), "--preset", "web"])

    assert args.include_bundles == ["base"]
    assert preset_paths == (tmp_path / "presets" / "web",)


def test_subdirectory_presets_are_allowed(tmp_path):
    make_project(tmp_path, preset={"web/laptop": "--include-bundles base\n"})

    args, preset_paths = parse_with_paths(tmp_path, ["apply", str(tmp_path), "--preset", "web/laptop"])

    assert args.include_bundles == ["base"]
    assert preset_paths == (tmp_path / "presets" / "web" / "laptop",)


def test_relative_preset_traversal_is_rejected(tmp_path):
    with pytest.raises(PresetError, match="preset path must stay inside presets/"):
        parse(tmp_path, ["apply", str(tmp_path), "--preset", "../laptop"])


def test_missing_preset_is_an_error(tmp_path):
    with pytest.raises(PresetError, match=r"preset not found:.*presets.*missing"):
        parse(tmp_path, ["apply", str(tmp_path), "--preset", "missing"])


def test_unknown_user_in_preset_path_is_an_error(tmp_path):
    with pytest.raises(PresetError, match="cannot resolve path '~bogususer/laptop'"):
        parse(tmp_path, ["apply", str(tmp_path), "--preset", "~bogususer/laptop"])


def test_unknown_user_in_project_dir_is_an_error():
    with pytest.raises(PresetError, match="cannot resolve path '~bogususer/machine'"):
        parse_with_presets(cli.build_parser(), ["apply", "~bogususer/machine"])


def test_directory_preset_is_an_error(tmp_path):
    (tmp_path / "presets").mkdir()

    with pytest.raises(PresetError, match="preset is not a file"):
        parse(tmp_path, ["apply", str(tmp_path), "--preset", "."])


def test_disallowed_options_in_preset_are_ignored(tmp_path):
    make_project(tmp_path, preset={"laptop": "--nope value\n--include-bundles base\n"})

    args = parse(tmp_path, ["apply", str(tmp_path), "--preset", "laptop"])

    assert args.include_bundles == ["base"]
    assert args.project_dir == str(tmp_path)


def test_positional_tokens_in_preset_are_ignored(tmp_path):
    make_project(tmp_path, preset={"laptop": "some-directory\n--include-bundles base\n"})

    args = parse(tmp_path, ["apply", str(tmp_path), "--preset", "laptop"])

    assert args.include_bundles == ["base"]
    assert args.project_dir == str(tmp_path)


@pytest.mark.parametrize("line", ["--preset other", "--preset=other", "-p other", "-p=other"])
def test_nested_preset_options_in_preset_are_ignored(tmp_path, line):
    make_project(tmp_path, preset={"laptop": f"{line}\n--include-bundles base\n"})

    args = parse(tmp_path, ["apply", str(tmp_path), "--preset", "laptop"])

    assert args.include_bundles == ["base"]
    assert args.preset == ["laptop"]


@pytest.mark.parametrize("line", ["--c other", "--cm other", "-nm other", "--include-bundl base"])
def test_unknown_option_forms_in_preset_are_ignored(tmp_path, line):
    make_project(tmp_path, preset={"laptop": f"{line}\n--include-bundles base\n"})

    args = parse(tmp_path, ["apply", str(tmp_path), "--preset", "laptop"])

    assert args.include_bundles == ["base"]


def test_options_from_another_command_are_ignored(tmp_path):
    make_project(tmp_path, preset={"laptop": "--dry-run\n--include-bundles base\n"})

    args = parse(tmp_path, ["list", str(tmp_path), "--preset", "laptop"])

    assert args.include_bundles == ["base"]
    assert not hasattr(args, "dry_run")


def test_invalid_quoting_is_an_error(tmp_path):
    make_project(tmp_path, preset={"laptop": '--include-bundles "base\n'})

    with pytest.raises(PresetError, match=r"invalid quoting in preset.*laptop:1"):
        parse(tmp_path, ["apply", str(tmp_path), "--preset", "laptop"])


def test_blank_and_comment_only_preset_is_empty(tmp_path):
    write(tmp_path / "laptop", "# nothing here\n\n")

    assert read_preset_tokens(tmp_path / "laptop") == []


def test_unreadable_preset_is_an_error(tmp_path):
    (tmp_path / "laptop").write_bytes(b"\xff\xfe")

    with pytest.raises(PresetError, match="cannot read preset"):
        read_preset_tokens(tmp_path / "laptop")
