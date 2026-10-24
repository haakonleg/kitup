from __future__ import annotations

from io import StringIO

from kitup.output import Output
from kitup.registry import Bundle
from kitup.results import Change, Status, TaskResult


class TTYStringIO(StringIO):
    def isatty(self) -> bool:
        return True


def make_result(
    status: Status = Status.OK,
    *,
    detail: str = "",
    changes=(),
    duration: float = 0.0,
    description: str = "",
) -> TaskResult:
    bundle = Bundle("base")

    def task(ctx):
        pass

    bundle.task("Task name", description=description)(task)
    return TaskResult(
        task=bundle.tasks[0],
        status=status,
        changes=list(changes),
        detail=detail,
        duration=duration,
    )


def test_status_lines_are_stable_without_color():
    stream = StringIO()
    output = Output(stream, no_color=True)

    output.bundle_header("base")
    output.task(
        make_result(
            Status.CHANGED,
            duration=1.25,
            changes=[Change({"added": ["git"]}, "+git")],
        )
    )
    output.task(make_result(Status.OK))

    assert stream.getvalue() == (
        "\nbundle: base\n"
        "  Task name                                   changed  +git  (1.25 s)\n"
        "  Task name                                   ok  (0.00 s)\n"
    )


def test_long_task_name_keeps_separator_before_status():
    stream = StringIO()
    output = Output(stream, no_color=True)
    bundle = Bundle("base")
    name = "Long task name that goes well past the forty-four column boundary"
    bundle.task(name)(lambda ctx: None)
    result = TaskResult(task=bundle.tasks[0], status=Status.OK)

    output.task(result)

    assert stream.getvalue() == f"  {name} ok  (0.00 s)\n"


def test_empty_no_color_does_not_disable_color(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "")
    stream = TTYStringIO()
    output = Output(stream)

    output.task(make_result(Status.OK))

    assert "\033[32m" in stream.getvalue()


def test_verbose_renders_change_dicts():
    stream = StringIO()
    output = Output(stream, no_color=True, verbose=True)

    output.task(make_result(Status.CHANGED, changes=[Change({"added": ["git"]}, "+git")]))

    assert "{'added': ['git']}" in stream.getvalue()
    assert "+git" not in stream.getvalue()


def test_verbose_renders_task_description():
    stream = StringIO()
    output = Output(stream, no_color=True, verbose=True)

    output.task(make_result(Status.OK, description="Install the base packages"))

    assert "Install the base packages" in stream.getvalue()


def test_non_verbose_hides_task_description():
    stream = StringIO()
    output = Output(stream, no_color=True)

    output.task(make_result(Status.OK, description="Install the base packages"))

    assert "Install the base packages" not in stream.getvalue()


def test_notes_are_appended_to_detail():
    stream = StringIO()
    output = Output(stream, no_color=True)
    result = make_result(Status.OK, detail="first")
    result.notes = ["second"]

    output.task(result)

    assert "first; second" in stream.getvalue()


def test_color_is_emitted_for_a_tty(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    stream = TTYStringIO()
    output = Output(stream)

    output.task(make_result(Status.OK))

    assert "\033[32m" in stream.getvalue()


def test_no_color_wins_over_tty(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    stream = TTYStringIO()
    output = Output(stream, no_color=True)

    output.task(make_result(Status.OK))

    assert "\033[" not in stream.getvalue()


def test_banner_uses_pretty_name(monkeypatch):
    monkeypatch.delenv("XDG_CURRENT_DESKTOP", raising=False)
    stream = StringIO()
    output = Output(stream, no_color=True)

    output.banner({"os_release": {"PRETTY_NAME": "Test Linux 1 (Workstation Edition)"}})

    assert stream.getvalue().strip() == "kitup 0.1.0  ·  Test Linux 1 (Workstation Edition)"


def test_banner_falls_back_to_name_version_and_variant(monkeypatch):
    monkeypatch.delenv("XDG_CURRENT_DESKTOP", raising=False)
    stream = StringIO()
    output = Output(stream, no_color=True)

    output.banner(
        {
            "distro_id": "testos",
            "version_id": "1",
            "os_release": {"NAME": "Test Linux", "VARIANT": "Workstation Edition"},
        }
    )

    assert stream.getvalue().strip() == "kitup 0.1.0  ·  Test Linux 1 (Workstation Edition)"


def test_summary_format():
    stream = StringIO()
    output = Output(stream, no_color=True)

    output.summary(
        {Status.CHANGED: 2, Status.OK: 1, Status.FAILED: 1, Status.NOT_RUN: 0},
        12.4,
    )

    assert "Summary: 2 changed, 1 ok, 1 failed, 0 not run — 12.4 s" in stream.getvalue()
