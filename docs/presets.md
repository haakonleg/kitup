# Presets

A preset is a plain-text file containing command-line options. Presets let you
save a reusable option set for a machine, such as a package selection or a tag
filter, and apply it with one flag.

Presets are available for `apply`, `list`, and `check`.

## Selecting a preset

Use `--preset` or `-p`:

```bash
kitup apply my-machine --preset laptop
kitup list my-machine -p laptop
kitup check my-machine --preset laptop
```

Relative names resolve under the project's `presets/` directory:

```text
my-machine/presets/laptop
my-machine/presets/web/laptop
```

```bash
kitup apply my-machine --preset laptop
kitup apply my-machine --preset web/laptop
```

Absolute paths and `~` are accepted for presets stored outside the project:

```bash
kitup apply my-machine --preset ~/.config/kitup/work
```

`--preset` is repeatable. Multiple presets apply in order; see
[Precedence](#precedence).

## Preset format

A preset contains command-line options, one or more per line. Blank lines and
lines beginning with `#` are ignored. Elsewhere, `#` is part of the value.
Values are split with shell-style quoting, so quote a value that contains
spaces.

```text
# my-machine/presets/laptop
--include-bundles base,workstation
--include-vars laptop
-t packages,security
```

Options can share a line:

```text
--include-bundles base,workstation -t packages
```

The option names are the same as on the command line. They must be written
exactly as documented; abbreviated long options and combined short options such
as `-nm` are not recognized. An option that no command recognizes is ignored
with a warning naming the preset and the option, so typos do not pass silently.
Options that are valid for another command are ignored without a warning, which
lets a preset be shared between `apply`, `list`, and `check`.

Presets can set the same options as the command line, including
`--bundle-path` and `--var-file`:

```text
# my-machine/presets/shared
--bundle-path shared/bundles
--var-file secrets.py
```

Paths in preset values resolve against the project root, not the preset file:

```bash
kitup apply my-machine --preset shared
# reads my-machine/shared/bundles and my-machine/secrets.py
```

The project directory is never set by a preset; it comes from the command line
or defaults to the current directory. A preset cannot select another preset.

## Precedence

Presets are applied in order, and later presets override earlier ones. The
command line always wins over every preset:

```text
# my-machine/presets/laptop
--include-bundles base
```

```bash
kitup apply my-machine --preset laptop --include-bundles workstation
# result: only workstation and its dependencies
```

List options replace the previous value; they are not merged. This applies both
between presets and between a preset and the command line:

```text
# my-machine/presets/laptop
-t packages
-t docs
```

```bash
kitup apply my-machine --preset laptop -t docs
# result: only tasks tagged docs
```

## Errors

A missing or unreadable preset, a directory instead of a file, a relative name
that leaves `presets/`, invalid quoting, or an invalid option value is reported
as a preset error naming the file. The command exits with code `2`.
