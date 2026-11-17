"""Behavior of the terminal output formatter."""

import os
from io import StringIO

import pytest

from kitup.errors import TaskFailed
from kitup.output import Output
from kitup.registry import Bundle
from kitup.results import CallResult, Status, TaskResult

from .helpers import add_task


class TtyStream(StringIO):
    """In-memory stream that reports itself as an interactive terminal."""

    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        return 1


def make_bundle(*, bundle_id: str = "base", name: str = "Base system", description: str = "Core packages") -> Bundle:
    bundle = Bundle(name, description)
    bundle._finalize(id=bundle_id, name=name, var_paths=())
    return bundle


def make_task(name: str = "Task"):
    bundle = Bundle("base")
    add_task(bundle, lambda: None, name=name)
    return bundle.tasks[0]


def make_result(
    *,
    status: Status = Status.OK,
    calls: tuple[CallResult, ...] = (),
    notes: tuple[str, ...] = (),
    diffs: tuple[str, ...] = (),
    detail: str = "",
    name: str = "Task",
    duration: float = 0.0,
    error: BaseException | None = None,
) -> TaskResult:
    return TaskResult(
        task=make_task(name),
        status=status,
        calls=list(calls),
        notes=list(notes),
        diffs=list(diffs),
        detail=detail,
        duration=duration,
        error=error,
    )


def task_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith(("  [", "  ↳"))]


def test_changed_task_renders_one_sub_line_per_change():
    stream = StringIO()
    output = Output(stream, no_color=True)
    calls = (
        CallResult(action="dnf.packages", changed=True, summary="+neovim"),
        CallResult(action="systemd.unit", changed=True, summary="enable nginx.service"),
    )

    output.task(make_result(status=Status.CHANGED, calls=calls, duration=1.24))

    lines = stream.getvalue().splitlines()
    assert lines[0].startswith("  Task")
    assert "changed" in lines[0]
    assert "×2" in lines[0]  # noqa: RUF001 - rendered change count
    assert lines[1] == "    + dnf.packages: +neovim"
    assert lines[2] == "    + systemd.unit: enable nginx.service"


def test_non_verbose_hides_unchanged_calls():
    stream = StringIO()
    output = Output(stream, no_color=True)
    calls = (
        CallResult(action="files.file", changed=True, summary="write /a"),
        CallResult(action="files.file", changed=False, summary="ensure /b"),
    )

    output.task(make_result(status=Status.CHANGED, calls=calls))

    text = stream.getvalue()
    assert "+ files.file: write /a" in text
    assert "ensure /b" not in text


def test_verbose_renders_unchanged_calls_as_dim_lines():
    stream = StringIO()
    output = Output(stream, no_color=True, verbose=True)
    calls = (
        CallResult(action="files.file", changed=True, summary="write /a"),
        CallResult(action="files.file", changed=False, summary="ensure /b"),
    )

    output.task(make_result(status=Status.CHANGED, calls=calls))

    text = stream.getvalue()
    assert "    + files.file: write /a" in text
    assert "    · files.file: ensure /b" in text


def test_notes_render_as_cyan_sub_lines():
    stream = TtyStream()
    output = Output(stream)

    output.task(make_result(notes=("probed sshd config",)))

    assert "\033[36m›\033[0m \033[36mprobed sshd config\033[0m" in stream.getvalue()  # noqa: RUF001


def test_side_effect_sub_lines_are_indented_one_level_deeper():
    stream = StringIO()
    output = Output(stream, no_color=True)
    calls = (CallResult(action="systemd.daemon_reload", changed=True, summary="reload systemd"),)

    output.task(make_result(status=Status.CHANGED, calls=calls), side_effect=True, progress=(1, 3))

    lines = stream.getvalue().splitlines()
    assert lines[0].startswith("  ↳     ")
    assert lines[1] == "      + systemd.daemon_reload: reload systemd"


def test_dry_run_uses_would_and_magenta_markers():
    stream = TtyStream()
    output = Output(stream, dry_run=True)
    calls = (CallResult(action="files.file", changed=True, summary="write /a"),)

    output.task(make_result(status=Status.CHANGED, calls=calls))

    text = stream.getvalue()
    assert "▲ would" in text
    assert "\033[35m" in text
    assert "\033[35m+\033[0m" in text


def test_glyphs_render_only_on_interactive_streams():
    interactive = TtyStream()
    Output(interactive, no_color=True).task(make_result(status=Status.OK))

    piped = StringIO()
    Output(piped, no_color=True).task(make_result(status=Status.OK))

    assert "✓ ok" in interactive.getvalue()
    assert "✓" not in piped.getvalue()
    assert "ok" in piped.getvalue()


@pytest.mark.parametrize(
    ("status", "glyph", "color"),
    [
        (Status.OK, "✓", "\033[32m"),
        (Status.CHANGED, "▲", "\033[33m"),
        (Status.SKIPPED, "–", "\033[36m"),  # noqa: RUF001 - rendered glyph
        (Status.FAILED, "✗", "\033[1;31m"),
    ],
)
def test_status_glyphs_and_colors(status, glyph, color):
    stream = TtyStream()
    output = Output(stream)

    output.task(make_result(status=status))

    text = stream.getvalue()
    assert f"{glyph} {status.value}" in text
    assert color in text


def test_change_count_is_omitted_when_nothing_changed():
    stream = StringIO()
    output = Output(stream, no_color=True)

    output.task(make_result(status=Status.OK))

    assert "×" not in stream.getvalue()  # noqa: RUF001 - rendered change count


def test_failure_message_is_not_inline_on_the_task_line():
    stream = StringIO()
    output = Output(stream, no_color=True)
    result = make_result(status=Status.FAILED, detail="boom", error=TaskFailed("boom"))

    output.task(result)
    output.failure(result)

    lines = stream.getvalue().splitlines()
    assert "boom" not in lines[0]
    assert lines[1] == "error: boom"


def test_progress_prefix_and_columns_align_across_tasks():
    stream = StringIO()
    output = Output(stream, no_color=True)
    many = tuple(CallResult(action="files.file", changed=True, summary=f"write /{i}") for i in range(12))

    output.task(make_result(name="First", status=Status.OK, duration=0.03), progress=(1, 10))
    output.task(make_result(name="Second", status=Status.CHANGED, calls=many, duration=1.24), progress=(10, 10))

    lines = task_lines(stream.getvalue())
    assert lines[0].startswith("  [1/10]  First")
    assert lines[1].startswith("  [10/10] Second")
    assert lines[0].index("ok") == lines[1].index("changed")
    assert lines[0].index("(0.03 s)") == lines[1].index("(1.24 s)")


def test_side_effect_prefix_pads_the_arrow_to_the_progress_width():
    stream = StringIO()
    output = Output(stream, no_color=True)

    output.task(make_result(name="Reload"), side_effect=True, progress=(1, 3))

    assert task_lines(stream.getvalue())[0].startswith("  ↳     Reload")


def test_terminal_width_truncates_long_names_and_keeps_columns_aligned(monkeypatch):
    monkeypatch.setattr(os, "get_terminal_size", lambda fd: os.terminal_size((60, 24)))
    stream = TtyStream()
    output = Output(stream, no_color=True)
    long_name = "A very long task name that will not fit"
    other_name = "Another very long task name that will not fit"

    output.task(make_result(name=long_name, status=Status.OK, duration=0.03), progress=(1, 3))
    output.task(make_result(name=other_name, status=Status.OK, duration=0.04), progress=(2, 3))

    lines = task_lines(stream.getvalue())
    assert "…" in lines[0] and "…" in lines[1]
    assert long_name not in lines[0]
    assert other_name not in lines[1]
    assert lines[0].index("ok") == lines[1].index("ok")
    assert lines[0].index("(0.03 s)") == lines[1].index("(0.04 s)")


def test_non_interactive_streams_never_truncate_names(monkeypatch):
    monkeypatch.setattr(os, "get_terminal_size", lambda fd: os.terminal_size((60, 24)))
    stream = StringIO()
    output = Output(stream, no_color=True)
    long_name = "A very long task name that will not fit"

    output.task(make_result(name=long_name, status=Status.OK), progress=(1, 3))

    text = stream.getvalue()
    assert long_name in text
    assert "…" not in text


def test_fit_task_names_widens_with_a_four_space_gap_and_never_shrinks():
    output = Output(StringIO(), no_color=True)
    output.fit_task_names(["short"])
    assert output.name_width == 44

    output.fit_task_names(["x" * 50])
    assert output.name_width == 54

    output.fit_task_names(["tiny"])
    assert output.name_width == 54


def test_bundle_header_is_bold():
    stream = TtyStream()
    output = Output(stream)

    output.bundle_header("Base system (base)")

    assert "\033[1mbundle: Base system (base)\033[0m" in stream.getvalue()


def test_bundle_plan_header_dims_description_in_non_verbose():
    stream = TtyStream()
    output = Output(stream)
    bundle = make_bundle()

    output.bundle_plan_header(bundle, verbose=False)

    text = stream.getvalue()
    assert "\033[1mbundle: Base system (base)\033[0m" in text
    assert "\033[2m  Core packages\033[0m" in text


def test_bundle_plan_header_lists_id_name_and_description_when_verbose():
    stream = StringIO()
    output = Output(stream, no_color=True)
    bundle = make_bundle()

    output.bundle_plan_header(bundle, verbose=True)

    assert stream.getvalue().splitlines() == [
        "",
        "bundle: base",
        "  name: Base system",
        "  description: Core packages",
    ]


def test_bundle_rollup_lists_changed_ok_and_skipped():
    stream = StringIO()
    output = Output(stream, no_color=True)

    output.bundle_rollup("base", {Status.CHANGED: 2, Status.OK: 1, Status.SKIPPED: 3}, 1.6)

    assert "↳ base: 2 changed, 1 ok, 3 skipped — 1.6 s" in stream.getvalue()


def test_dry_run_rollup_says_would_change():
    stream = StringIO()
    output = Output(stream, no_color=True, dry_run=True)

    output.bundle_rollup("base", {Status.CHANGED: 2, Status.OK: 1}, 1.6)

    assert "↳ base: 2 would change, 1 ok — 1.6 s" in stream.getvalue()


def test_summary_reports_nothing_changed_and_dry_run_would_change():
    plain = StringIO()
    Output(plain, no_color=True).summary({Status.OK: 2}, 1.2)
    assert "Summary: 2 ok, nothing changed — 1.2 s" in plain.getvalue()

    changed = StringIO()
    Output(changed, no_color=True).summary({Status.CHANGED: 2, Status.OK: 1}, 1.6)
    assert "Summary: 2 changed, 1 ok — 1.6 s" in changed.getvalue()

    dry_run = StringIO()
    Output(dry_run, no_color=True, dry_run=True).summary({Status.CHANGED: 2, Status.OK: 1}, 1.6)
    assert "Summary: 2 would change, 1 ok — 1.6 s" in dry_run.getvalue()


def test_summary_colors_counts_per_status():
    stream = TtyStream()
    output = Output(stream)

    output.summary({Status.CHANGED: 2, Status.OK: 1, Status.SKIPPED: 1, Status.FAILED: 1, Status.NOT_RUN: 1}, 1.6)

    text = stream.getvalue()
    assert "\033[1mSummary:\033[0m" in text
    assert "\033[33m2 changed\033[0m" in text
    assert "\033[32m1 ok\033[0m" in text
    assert "\033[36m1 skipped\033[0m" in text
    assert "\033[1;31m1 failed\033[0m" in text


def test_aborted_and_interrupted_messages_are_red():
    stream = TtyStream()
    output = Output(stream)

    output.aborted("Explode", 1)
    output.interrupted(2)

    text = stream.getvalue()
    assert '\033[31mAborted after failure in "Explode". 1 task(s) not run.\033[0m' in text
    assert "\033[31mInterrupted. 2 task(s) not run.\033[0m" in text


def test_failure_block_trims_captured_output_and_reports_omitted_lines():
    stream = StringIO()
    output = Output(stream, no_color=True)
    error = TaskFailed("boom", command=["tool", "--flag"], stdout="\n".join(f"line {i}" for i in range(25)))

    output.failure(make_result(status=Status.FAILED, detail="boom", error=error))

    text = stream.getvalue()
    assert "error: boom" in text
    assert "command: tool --flag" in text
    assert "… (5 earlier lines omitted)" in text
    assert "line 0" not in text
    assert "line 5" in text
    assert "line 24" in text


def test_verbose_failure_block_shows_every_line():
    stream = StringIO()
    output = Output(stream, no_color=True, verbose=True)
    error = TaskFailed("boom", stdout="\n".join(f"line {i}" for i in range(25)))

    output.failure(make_result(status=Status.FAILED, detail="boom", error=error))

    text = stream.getvalue()
    assert "line 0" in text
    assert "line 24" in text
    assert "omitted" not in text


def test_diff_lines_are_indented_and_colorized():
    stream = TtyStream()
    output = Output(stream)
    calls = (CallResult(action="files.file", changed=True, summary="write /a"),)
    diffs = ("--- a", "+++ b", "@@ -1 +1 @@", "-old", "+new", " context")

    output.task(make_result(status=Status.CHANGED, calls=calls, diffs=diffs))

    text = stream.getvalue()
    assert "\033[1m--- a\033[0m" in text
    assert "\033[1m+++ b\033[0m" in text
    assert "\033[36m@@ -1 +1 @@\033[0m" in text
    assert "\033[31m-old\033[0m" in text
    assert "\033[32m+new\033[0m" in text
    assert next(line for line in text.splitlines() if "old" in line).startswith("      ")


def test_dry_run_banner_is_magenta():
    stream = TtyStream()
    output = Output(stream, dry_run=True)

    output.dry_run_banner()

    assert "\033[35mdry run: no changes will be applied\033[0m" in stream.getvalue()


def test_warning_and_error_use_stderr_colors():
    stdout = TtyStream()
    stderr = TtyStream()
    output = Output(stdout, stderr=stderr)

    output.warning("careful")
    output.error("broken")

    text = stderr.getvalue()
    assert "\033[33mwarning: careful\033[0m" in text
    assert "\033[1;31merror: broken\033[0m" in text
    assert stdout.getvalue() == ""


def test_no_color_suppresses_every_escape_sequence():
    stream = TtyStream()
    output = Output(stream, no_color=True, stderr=stream)
    calls = (CallResult(action="files.file", changed=True, summary="write /a"),)

    output.task(make_result(status=Status.CHANGED, calls=calls, notes=("note",), diffs=("+new",)))
    output.summary({Status.CHANGED: 1}, 1.0)
    output.warning("careful")
    output.error("broken")

    assert "\033[" not in stream.getvalue()
