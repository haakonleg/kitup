from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from kitup.errors import TaskFailed
from kitup.registry import Bundle, side_effect
from kitup.results import CallResult, Status, TaskResult

from .helpers import FakeCommandRunner, FakeSudo, add_task, make_context


def _noop(ctx):
    return None


def task_for(name: str = "test"):
    bundle = Bundle("test")
    add_task(bundle, _noop, name=name)
    return bundle.tasks[0]


def test_changed_records_calls_notes_and_task_scope(tmp_path):
    context = make_context(tmp_path)
    result = CallResult(action="test.task", changed=True, summary="+git", added=["git"])

    assert context.changed(result) is result
    context.note("note")
    assert context.calls == [result]
    assert context.changes == [result]
    assert context.notes == ["note"]

    context.begin_task(task_for())
    assert context.calls == []
    assert context.changes == []
    assert context.notes == []


def test_changed_builds_calls_and_defaults_action(tmp_path):
    context = make_context(tmp_path)

    result = context.changed(action="test.method", summary="+pkg", package="pkg")
    assert isinstance(result, CallResult)
    assert result.action == "test.method"
    assert result.changed is True
    assert result.summary == "+pkg"
    assert result.package == "pkg"
    assert context.calls == [result]
    assert context.changes == [result]

    context.begin_task(task_for())
    assert context.changed(summary="+pkg").action == "task._noop"

    no_change = context.changed(changed=False, summary="ensure pkg", package="pkg")
    assert no_change.changed is False
    assert context.calls[-1] is no_change
    assert no_change not in context.changes


def test_call_result_requires_a_non_empty_action(tmp_path):
    with pytest.raises(TypeError):
        CallResult(changed=True, summary="+pkg")  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="action"):
        CallResult(action="", changed=True, summary="+pkg")

    context = make_context(tmp_path)
    with pytest.raises(ValueError, match="action"):
        context.changed(action="", summary="+pkg")


def test_memo_and_input_share_a_store_across_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt: "provided")
    context = make_context(tmp_path)
    calls = []

    def load():
        calls.append(1)
        return "loaded"

    assert context.memo("key", load) == "loaded"
    context.begin_task(task_for())
    assert context.memo("key", load) == "loaded"
    assert len(calls) == 1

    context.input("provided_key")
    context.begin_task(task_for("Other"))
    assert context.memo("provided_key", load) == "provided"
    assert context.get("provided_key") == "provided"
    assert len(calls) == 1


def test_input_enters_interactive_output_and_clears_prompt(tmp_path, monkeypatch):
    calls = []
    prompts = []
    cleared = []

    @contextmanager
    def interactive():
        calls.append("begin")
        try:
            yield
        finally:
            calls.append("end")

    def fake_input(prompt):
        prompts.append(prompt)
        return "provided"

    def clear_input(prompt, value):
        cleared.append((prompt, value))

    monkeypatch.setattr("builtins.input", fake_input)
    output = SimpleNamespace(interactive=interactive, clear_input=clear_input)
    context = make_context(tmp_path, output=output)

    assert context.input(prompt="Value: ") == "provided"
    assert prompts == ["Value: "]
    assert cleared == [("Value: ", "provided")]
    assert calls == ["begin", "end"]


def test_get_returns_default_and_raises_for_missing_key(tmp_path):
    context = make_context(tmp_path)

    assert context.get("missing", "fallback") == "fallback"

    with pytest.raises(TaskFailed, match="no stored value for key: missing"):
        context.get("missing")


def test_result_returns_stored_results_and_raises_for_unrun_tasks(tmp_path):
    context = make_context(tmp_path)
    task = task_for("Check")
    result = TaskResult(task=task, status=Status.CHANGED, value="done")
    context._store_result(task, result)

    assert context.result(task.func) is result

    def other(ctx):
        return None

    other_bundle = Bundle("other")
    add_task(other_bundle, other, name="Other")
    with pytest.raises(TaskFailed, match="no result for task:"):
        context.result(other)


def test_results_returns_a_snapshot_that_survives_begin_task(tmp_path):
    context = make_context(tmp_path)
    task = task_for("Check")
    result = TaskResult(task=task, status=Status.OK)
    context._store_result(task, result)

    snapshot = context.results
    assert snapshot == {task.func: result}
    snapshot.clear()
    assert context.result(task.func) is result

    context.begin_task(task_for("Other"))
    assert context.result(task.func) is result


def test_run_executes_and_forwards_options(tmp_path):
    command = FakeCommandRunner()
    context = make_context(tmp_path, command=command)

    result = context.run(["dnf5", "install", "-y", "git"])
    assert result.returncode == 0
    assert command.calls[0][0] == ["dnf5", "install", "-y", "git"]
    assert context.calls == []

    context.run(["probe"], check=False)
    assert command.calls[1][1]["check"] is False

    context.run(["login"], stdin="secret\n")
    assert command.calls[2][1]["stdin"] == "secret\n"


def test_run_does_not_handle_guards_or_dry_run(tmp_path):
    command = FakeCommandRunner()
    context = make_context(tmp_path, dry_run=True, command=command)

    context.run(["touch", "file"])
    assert command.calls[0][0] == ["touch", "file"]
    assert context.calls == []

    with pytest.raises(TypeError):
        context.run(["true"], creates="marker")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        context.run(["true"], unless=["true"])  # type: ignore[call-arg]


def test_probe_returns_nonzero_result_instead_of_failing(tmp_path):
    command = FakeCommandRunner(returncode=1)
    context = make_context(tmp_path, command=command)

    result = context.probe(["rpm", "-q", "git"])
    assert result.returncode == 1
    assert command.calls[0][1]["check"] is False


def test_sudo_is_refreshed_once_and_revalidated_when_expired(tmp_path):
    command = FakeCommandRunner()
    sudo = FakeSudo()
    context = make_context(tmp_path, command=command, sudo=sudo)
    context.begin_task(task_for("Install packages"))
    expected = 'task "Install packages" requires sudo privileges'

    context.run(["first"], sudo=True)
    context.run(["second"], sudo=True)
    assert sudo.refresh_count == 1
    assert sudo.reasons == [expected]
    assert command.calls[0][1]["sudo"] is True
    assert command.calls[1][1]["sudo"] is True

    sudo.valid = False
    context.run(["third"], sudo=True)
    assert sudo.refresh_count == 2
    assert sudo.reasons == [expected, expected]


def test_run_requests_sudo_even_in_dry_run(tmp_path):
    command = FakeCommandRunner()
    sudo = FakeSudo()
    context = make_context(tmp_path, dry_run=True, command=command, sudo=sudo)
    context.begin_task(task_for())

    context.run(["install"], sudo=True)

    assert sudo.refresh_count == 1
    assert command.calls[0][0] == ["install"]


def test_bundle_is_the_owning_bundle_for_the_current_task(tmp_path):
    bundle = Bundle("base", path=tmp_path)
    add_task(bundle, lambda ctx: None, name="Task")
    context = make_context(tmp_path)

    context.begin_task(bundle.tasks[0])

    assert context.bundle is bundle
    assert context.bundle_path == tmp_path


def test_register_side_effect_queues_for_the_current_task(tmp_path):
    context = make_context(tmp_path)
    context.begin_task(task_for())
    effect = side_effect("Refresh")(lambda ctx: None)

    context.register_side_effect(effect)

    assert context._take_side_effects() == [effect]


def test_register_side_effect_rejects_invalid_contexts(tmp_path):
    context = make_context(tmp_path)
    effect = side_effect("Refresh")(lambda ctx: None)

    with pytest.raises(TaskFailed, match="side effects can only be registered from a task"):
        context.register_side_effect(effect)

    context.begin_task(task_for(), is_side_effect=True)
    with pytest.raises(TaskFailed, match="side effects cannot register other side effects"):
        context.register_side_effect(effect)

    context.begin_task(task_for())
    with pytest.raises(TaskFailed, match="register_side_effect expects a SideEffect"):
        context.register_side_effect(lambda ctx: None)  # type: ignore[arg-type]


def test_side_effect_queue_lifecycle(tmp_path):
    context = make_context(tmp_path)
    context.begin_task(task_for())
    first = side_effect("First")(lambda ctx: None)
    second = side_effect("Second", one_shot=False)(lambda ctx: None)
    context.register_side_effect(first)
    context.register_side_effect(second)

    assert context._take_side_effects() == [first, second]
    assert context._take_side_effects() == []

    context.register_side_effect(first)
    context.begin_task(task_for("Other"))
    assert context._take_side_effects() == []
