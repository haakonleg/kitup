import pytest

from kitup import cli
from kitup.errors import PresetError
from kitup.presets import parse_with_presets, read_preset_tokens

from ..helpers import make_project, write


def parse(tmp_path, argv, *, warned=None):
    on_warning = warned.append if warned is not None else None
    args, _ = parse_with_presets(cli.build_parser(), argv, root=tmp_path, on_warning=on_warning)
    return args


def parse_with_paths(tmp_path, argv):
    return parse_with_presets(cli.build_parser(), argv, root=tmp_path)


def test_read_preset_tokens(tmp_path):
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

    # Only a full-line hash starts a comment; a hash inside a token is kept.
    write(tmp_path / "presets" / "laptop", "# full-line comment\n--tags a#b\n")

    assert read_preset_tokens(tmp_path / "presets" / "laptop") == ["--tags", "a#b"]


def test_help_and_version_in_preset_are_ignored(tmp_path):
    for option in ["-h", "--help", "-V", "--version"]:
        make_project(tmp_path, preset={"laptop": f"{option}\n--include-bundles base\n"})

        args = parse(tmp_path, ["apply", str(tmp_path), "--preset", "laptop"])

        assert args.include_bundles == ["base"], option


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


def test_absolute_preset_paths_are_used_as_is(tmp_path):
    path = tmp_path / "elsewhere" / "laptop"
    write(path, "--include-bundles base\n")

    args = parse(tmp_path, ["apply", str(tmp_path), "--preset", str(path)])

    assert args.include_bundles == ["base"]


def test_relative_and_subdirectory_presets_resolve_under_presets(tmp_path):
    relative_root = tmp_path / "relative"
    make_project(relative_root, preset={"web": "--include-bundles base\n"})

    args, preset_paths = parse_with_paths(relative_root, ["apply", str(relative_root), "--preset", "web"])

    assert args.include_bundles == ["base"]
    assert preset_paths == (relative_root / "presets" / "web",)

    nested_root = tmp_path / "nested"
    make_project(nested_root, preset={"web/laptop": "--include-bundles base\n"})

    args, preset_paths = parse_with_paths(nested_root, ["apply", str(nested_root), "--preset", "web/laptop"])

    assert args.include_bundles == ["base"]
    assert preset_paths == (nested_root / "presets" / "web" / "laptop",)


def test_invalid_preset_paths_are_rejected(tmp_path):
    with pytest.raises(PresetError, match="preset path must stay inside presets/"):
        parse(tmp_path, ["apply", str(tmp_path), "--preset", "../laptop"])

    with pytest.raises(PresetError, match=r"preset not found:.*presets.*missing"):
        parse(tmp_path, ["apply", str(tmp_path), "--preset", "missing"])


def test_positional_tokens_in_preset_are_ignored(tmp_path):
    make_project(tmp_path, preset={"laptop": "some-directory\n--include-bundles base\n"})

    args = parse(tmp_path, ["apply", str(tmp_path), "--preset", "laptop"])

    assert args.include_bundles == ["base"]
    assert args.project_dir == str(tmp_path)


def test_nested_preset_options_in_preset_are_ignored(tmp_path):
    for line in ["--preset other", "--preset=other", "-p other", "-p=other"]:
        make_project(tmp_path, preset={"laptop": f"{line}\n--include-bundles base\n"})

        args = parse(tmp_path, ["apply", str(tmp_path), "--preset", "laptop"])

        assert args.include_bundles == ["base"], line
        assert args.preset == ["laptop"], line


def test_unknown_options_in_preset_warn_and_are_ignored(tmp_path):
    for line in ["--c other", "--cm other", "-nm other", "--include-bundl base"]:
        make_project(tmp_path, preset={"laptop": f"{line}\n--include-bundles base\n"})

        warned = []
        args = parse(tmp_path, ["apply", str(tmp_path), "--preset", "laptop"], warned=warned)

        assert args.include_bundles == ["base"], line
        assert len(warned) == 1, line
        assert line.split()[0] in warned[0], line

    # Without a warning callback, warnings go through the warnings module.
    make_project(tmp_path, preset={"laptop": "--dry-runn\n--include-bundles base\n"})

    with pytest.warns(UserWarning, match="ignoring unknown option '--dry-runn'"):
        args = parse(tmp_path, ["apply", str(tmp_path), "--preset", "laptop"])

    assert args.include_bundles == ["base"]

    # Options that are valid for another command are ignored silently.
    make_project(tmp_path, preset={"laptop": "--dry-run\n--include-bundles base\n"})
    warned = []

    args = parse(tmp_path, ["list", str(tmp_path), "--preset", "laptop"], warned=warned)

    assert args.include_bundles == ["base"]
    assert not hasattr(args, "dry_run")
    assert warned == []


def test_invalid_quoting_is_an_error(tmp_path):
    make_project(tmp_path, preset={"laptop": '--include-bundles "base\n'})

    with pytest.raises(PresetError, match=r"invalid quoting in preset.*laptop:1"):
        parse(tmp_path, ["apply", str(tmp_path), "--preset", "laptop"])


def test_missing_option_value_in_preset_is_an_error(tmp_path):
    make_project(tmp_path, preset={"laptop": "--include-bundles\n"})

    with pytest.raises(PresetError, match="expected one argument"):
        parse(tmp_path, ["apply", str(tmp_path), "--preset", "laptop"])


def test_unreadable_preset_is_an_error(tmp_path):
    (tmp_path / "laptop").write_bytes(b"\xff\xfe")

    with pytest.raises(PresetError, match="cannot read preset"):
        read_preset_tokens(tmp_path / "laptop")
