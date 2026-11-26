"""Behavior of the terminal output formatter."""

import os
from io import StringIO

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
    warnings: tuple[str, ...] = (),
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
        warnings=list(warnings),
        diffs=list(diffs),
        detail=detail,
        duration=duration,
        error=error,
    )


def task_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith(("  [", "  ↳"))]


def test_changed_tasks_hide_unchanged_calls_unless_verbose():
    stream = StringIO()
    output = Output(stream, no_color=True)
    calls = (
        CallResult(action="dnf.packages", changed=True, summary="+neovim"),
        CallResult(action="files.file", changed=False, summary="ensure /b"),
        CallResult(action="systemd.unit", changed=True, summary="enable nginx.service"),
    )

    output.task(make_result(status=Status.CHANGED, calls=calls, duration=1.24))

    lines = stream.getvalue().splitlines()
    assert lines[0].startswith("  Task")
    assert "changed" in lines[0]
    assert "×2" in lines[0]  # noqa: RUF001 - rendered change count
    assert lines[1] == "    + dnf.packages: +neovim"
    assert lines[2] == "    + systemd.unit: enable nginx.service"
    assert "ensure /b" not in stream.getvalue()

    stream = StringIO()
    output = Output(stream, no_color=True, verbose=True)
    calls = (
        CallResult(action="files.file", changed=True, summary="write /a"),
        CallResult(action="files.file", changed=False, summary="ensure /b"),
    )

    output.task(make_result(status=Status.CHANGED, calls=calls, notes=("probed sshd config",)))

    text = stream.getvalue()
    assert "    + files.file: write /a" in text
    assert "    · files.file: ensure /b" in text
    assert "probed sshd config" in text


def test_warnings_render_prominently_below_the_task():
    stream = StringIO()
    output = Output(stream, no_color=True)
    output.task(make_result(status=Status.OK, warnings=("unexpected ownership",)))

    assert "    warning: unexpected ownership" in stream.getvalue()

    stream = TtyStream()
    Output(stream).task(make_result(status=Status.OK, warnings=("unexpected ownership",)))

    assert "\033[1;33mwarning:\033[0m \033[33munexpected ownership\033[0m" in stream.getvalue()


def test_dry_run_glyphs_and_color_modes():
    stream = TtyStream()
    output = Output(stream, dry_run=True)
    calls = (CallResult(action="files.file", changed=True, summary="write /a"),)

    output.dry_run_banner()
    output.task(make_result(status=Status.CHANGED, calls=calls))

    text = stream.getvalue()
    assert "\033[35mdry run: no changes will be applied\033[0m" in text
    assert "▲ would" in text
    assert "\033[35m+\033[0m" in text

    interactive = TtyStream()
    Output(interactive, no_color=True).task(make_result(status=Status.OK))

    piped = StringIO()
    Output(piped, no_color=True).task(make_result(status=Status.OK))

    assert "✓ ok" in interactive.getvalue()
    assert "✓" not in piped.getvalue()
    assert "ok" in piped.getvalue()

    stream = TtyStream()
    stderr = TtyStream()
    output = Output(stream, stderr=stderr)
    calls = (CallResult(action="files.file", changed=True, summary="write /a"),)
    result = make_result(status=Status.CHANGED, calls=calls, diffs=("--- a", "+++ b", "@@ -1 +1 @@", "-old", "+new"))

    output.task(result)
    output.summary({Status.CHANGED: 1}, 1.0)
    output.warning("careful")
    output.error("broken")

    text = stream.getvalue()
    assert "\033[1m--- a\033[0m" in text
    assert "\033[31m-old\033[0m" in text
    assert "\033[32m+new\033[0m" in text
    assert "\033[33mwarning: careful\033[0m" in stderr.getvalue()
    assert "\033[1;31merror: broken\033[0m" in stderr.getvalue()

    plain = TtyStream()
    no_color = Output(plain, no_color=True, stderr=plain)
    no_color.task(result)
    no_color.summary({Status.CHANGED: 1}, 1.0)
    no_color.warning("careful")
    no_color.error("broken")
    assert "\033[" not in plain.getvalue()


def test_progress_columns_align_and_terminal_width_truncates(monkeypatch):
    monkeypatch.setattr(os, "get_terminal_size", lambda fd: os.terminal_size((60, 24)))
    stream = TtyStream()
    output = Output(stream, no_color=True)
    long_name = "A very long task name that will not fit"
    other_name = "Another very long task name that will not fit"
    many = tuple(CallResult(action="files.file", changed=True, summary=f"write /{i}") for i in range(12))

    output.task(make_result(name=long_name, status=Status.OK, duration=0.03), progress=(1, 10))
    output.task(make_result(name=other_name, status=Status.CHANGED, calls=many, duration=1.24), progress=(10, 10))
    output.task(make_result(name="Reload"), side_effect=True, progress=(1, 3))

    lines = task_lines(stream.getvalue())
    assert lines[0].startswith("  [1/10]  ")
    assert lines[1].startswith("  [10/10] ")
    assert "…" in lines[0] and long_name not in lines[0]
    assert "…" in lines[1] and other_name not in lines[1]
    assert lines[0].index("ok") == lines[1].index("changed")
    assert lines[2].startswith("  ↳     Reload")

    piped = StringIO()
    Output(piped, no_color=True).task(make_result(name=long_name, status=Status.OK), progress=(1, 3))
    assert long_name in piped.getvalue()
    assert "…" not in piped.getvalue()


def test_bundle_headers_and_rollups():
    stream = StringIO()
    output = Output(stream, no_color=True)
    bundle = make_bundle()

    output.bundle_header("Base system (base)")
    output.bundle_plan_header(bundle, verbose=True)
    output.bundle_rollup("base", {Status.CHANGED: 2, Status.OK: 1, Status.SKIPPED: 3}, 1.6)

    text = stream.getvalue()
    assert "bundle: Base system (base)" in text
    assert "  name: Base system" in text
    assert "  description: Core packages" in text
    assert "↳ base: 2 changed, 1 ok, 3 skipped — 1.6 s" in text

    dry_run = StringIO()
    Output(dry_run, no_color=True, dry_run=True).bundle_rollup("base", {Status.CHANGED: 2, Status.OK: 1}, 1.6)
    assert "↳ base: 2 would change, 1 ok — 1.6 s" in dry_run.getvalue()


def test_summary_and_failure_blocks():
    plain = StringIO()
    Output(plain, no_color=True).summary({Status.OK: 2}, 1.2)
    assert "Summary: 2 ok, nothing changed — 1.2 s" in plain.getvalue()

    dry_run = StringIO()
    Output(dry_run, no_color=True, dry_run=True).summary({Status.CHANGED: 2, Status.OK: 1}, 1.6)
    assert "Summary: 2 would change, 1 ok — 1.6 s" in dry_run.getvalue()

    stream = StringIO()
    output = Output(stream, no_color=True)
    error = TaskFailed("boom", command=["tool", "--flag"], stdout="\n".join(f"line {i}" for i in range(25)))
    result = make_result(status=Status.FAILED, detail="boom", error=error)

    output.task(result)
    output.failure(result)

    text = stream.getvalue()
    assert "boom" not in text.splitlines()[0]
    assert "error: boom" in text
    assert "command: tool --flag" in text
    assert "… (5 earlier lines omitted)" in text
    assert "line 0" not in text
    assert "line 24" in text

    verbose = StringIO()
    Output(verbose, no_color=True, verbose=True).failure(result)
    assert "line 0" in verbose.getvalue()
    assert "line 24" in verbose.getvalue()
    assert "omitted" not in verbose.getvalue()
