# CLI

kitup has five commands: `apply`, `list`, `list-modules`, `check`, and `init`.
Every command takes an optional project directory as its positional argument. It
defaults to the current directory and is the root containing `bundles/`,
`modules/`, `files/`, `vars/`, and `presets/`. See [Projects](project.md) for the
project layout.

## Synopsis

```text
kitup apply        [PROJECT_DIR] [OPTIONS]
kitup list         [PROJECT_DIR] [OPTIONS]
kitup list-modules [PROJECT_DIR] [OPTIONS]
kitup check        [PROJECT_DIR] [OPTIONS]
kitup init         [PROJECT_DIR] [--force] [OPTIONS]
```

## Commands

### `apply`

Load the project, select bundles and tasks, and apply the plan. Tasks run in
dependency order and stop at the first failure. By default `apply` changes the
system; use `--dry-run` to preview the result first.

### `list`

Show the active project, vars, bundles, and tasks without running anything.
Bundle ids and display names are shown. Add `--verbose` to include descriptions,
dependencies, bundle var files, task ids, and tags.

### `list-modules`

List registered module names and descriptions. Add `--verbose` to include public
functions.

### `check`

Load and validate the project, bundle dependencies, selections, project vars,
and the selected bundles' own vars, then print the plan without running tasks.
`check` is useful before `apply`, and it warns when selected bundles contain no
tasks.

`apply` and `check` require at least one bundle, either under `bundles/` or
supplied through `--bundle-path`.

### `init`

Create a new project with an example bundle, variable files, and a commented
`presets/laptop`. Refuses to overwrite existing files unless `--force` is
passed.

## Options

### Project sources

- `-p PRESET`, `--preset PRESET` — read options from a preset file. Relative
  names resolve under `presets/`; absolute paths are used as-is. Repeatable:
  later files win. See [Presets](presets.md).
- `--bundle-path DIR` — scan an extra directory for bundle folders, in addition
  to `<root>/bundles`. Relative values resolve against the project root;
  absolute paths and `~` are accepted. Repeatable; values are not comma-split.
- `--var-file PATH` — load an extra project-level Python var file after the
  project's `vars/*.py` files. Relative values resolve against the project root;
  absolute paths and `~` are accepted. Repeatable; values are not comma-split.
  The file must exist and end in `.py`.

### Selection

- `-b LIST`, `--include-bundles LIST` — keep only these bundle ids and the
  bundles they depend on.
- `-B LIST`, `--exclude-bundles LIST` — remove these bundles. Excluding a
  required dependency is an error.
- `-i LIST`, `--include-vars LIST` — keep only these project var ids. Bundle
  vars are not filtered.
- `-E LIST`, `--exclude-vars LIST` — remove these project var ids. Bundle vars
  are not filtered.
- `-t TAGS`, `--tags TAGS` — run only tasks matching these tags.
- `-x TAGS`, `--skip-tags TAGS` — skip tasks matching these tags.

Selection values accept comma-separated entries or repeated flags:

```bash
kitup apply my-machine -b base,workstation
kitup apply my-machine -b base -b workstation
```

Unknown bundle or var ids are reported with the available ids. Tag filters apply
to tasks only; a selected bundle whose tasks are all filtered out still appears
in the plan but runs nothing.

### Run behavior

- `-n`, `--dry-run` — report what would change without changing anything.
- `-d`, `--diff` — show unified diffs for file changes.
- `-s`, `--sudo` — validate sudo credentials before starting.

### Output

- `-v`, `--verbose` — show project, vars, and task details.
- `-c`, `--no-color` — disable colors.
- `-V`, `--version` — print the version.

Option names must be written exactly as shown. Abbreviated long options such as
`--include-bundl` and combined short options such as `-nm` are rejected.

## Presets and precedence

Options can come from the command line and from one or more preset files.
Values from the command line win over preset values. Among multiple presets,
later files win. List options replace the previous value rather than merging it.

`--bundle-path` and `--var-file` may also be set from a preset. Their relative
values resolve against the project root, not the preset file.

## Examples

```bash
kitup init my-machine
kitup check my-machine
kitup list my-machine -v
kitup list-modules my-machine -v
kitup apply my-machine -n
kitup apply my-machine --preset laptop
kitup apply my-machine -t packages,docs
kitup apply my-machine -b base,workstation -n
kitup apply my-machine -E laptop
kitup apply my-machine --bundle-path ~/shared/kitup-bundles
kitup apply my-machine --var-file ~/.config/kitup/secrets.py
```

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Success. |
| `1` | A task failed. |
| `2` | Usage, project, var loading, preset, bundle, dependency, or privilege error. |
| `130` | Interrupted. |
