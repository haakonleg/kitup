from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from kitup.errors import TaskFailed, VarsError
from kitup.project import Project
from kitup.registry import Bundle, side_effect
from kitup.results import CallResult, Status, TaskResult
from kitup.vars import Vars

from .helpers import FakeCommandRunner, FakeSudo, add_task, make_context, response


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
    context.begin_task(task)
    result = TaskResult(task=task, status=Status.CHANGED, value="done")
    context.store_result(task, result)

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
    context.begin_task(task)
    result = TaskResult(task=task, status=Status.OK)
    context.store_result(task, result)

    snapshot = context.results
    assert snapshot == {task.id: result}
    snapshot.clear()
    assert context.result(task.func) is result

    context.begin_task(task_for("Other"))
    assert context.result(task.func) is result


def test_result_and_results_are_empty_without_a_current_bundle(tmp_path):
    context = make_context(tmp_path)
    task = task_for("Check")
    result = TaskResult(task=task, status=Status.OK)
    context.store_result(task, result)

    assert context.results == {}
    with pytest.raises(TaskFailed, match="no current bundle"):
        context.result(task.func)


def test_finish_run_clears_the_current_bundle(tmp_path):
    context = make_context(tmp_path)
    context.begin_task(task_for("Check"))
    assert context.run_state.current is not None

    context.finish_run()

    assert context.run_state.current is None
    assert context.results == {}


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


def test_ensure_sudo_prompts_only_when_credentials_are_invalid(tmp_path):
    sudo = FakeSudo()
    context = make_context(tmp_path, sudo=sudo)
    context.begin_task(task_for("Install packages"))
    expected = 'task "Install packages" requires sudo privileges'

    context.ensure_sudo()
    context.ensure_sudo()
    assert sudo.refresh_count == 1
    assert sudo.reasons == [expected]

    sudo.valid = False
    context.ensure_sudo()
    assert sudo.refresh_count == 2
    assert sudo.reasons == [expected, expected]


def test_run_requests_sudo_even_in_dry_run(tmp_path):
    command = FakeCommandRunner()
    context = make_context(tmp_path, dry_run=True, command=command)
    context.begin_task(task_for())

    context.run(["install"], sudo=True)

    assert command.calls[0][0] == ["install"]
    assert command.calls[0][1]["sudo"] is True


def test_sudo_auth_failure_reprompts_and_retries(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(1, stderr="sudo: a password is required\n"),
        response(0),
    ]
    sudo = FakeSudo()
    context = make_context(tmp_path, command=command, sudo=sudo)
    context.begin_task(task_for("Install packages"))

    result = context.run(["dnf5", "install", "-y", "git"], sudo=True)

    assert result.returncode == 0
    assert len(command.calls) == 2
    assert sudo.refresh_count == 1
    assert sudo.reasons == ['task "Install packages" requires sudo privileges']


def test_sudo_auth_failure_retries_probes(tmp_path):
    command = FakeCommandRunner()
    command.responses = [
        response(1, stderr="sudo: a password is required\n"),
        response(0, stdout="enabled\n"),
    ]
    sudo = FakeSudo()
    context = make_context(tmp_path, command=command, sudo=sudo)

    result = context.probe(["systemctl", "is-enabled", "foo.service"], sudo=True)

    assert result.returncode == 0
    assert result.stdout == "enabled\n"
    assert len(command.calls) == 2


def test_sudo_command_failure_is_not_retried(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1, stderr="boom\n")]
    sudo = FakeSudo()
    context = make_context(tmp_path, command=command, sudo=sudo)

    with pytest.raises(TaskFailed, match="exit code 1"):
        context.run(["dnf5", "install"], sudo=True)

    assert len(command.calls) == 1
    assert sudo.refresh_count == 0


def test_sudo_error_with_valid_credentials_is_not_retried(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1, stderr="sudo: unknown command\n")]
    sudo = FakeSudo()
    sudo.valid = True
    context = make_context(tmp_path, command=command, sudo=sudo)

    with pytest.raises(TaskFailed, match="exit code 1"):
        context.run(["dnf5", "install"], sudo=True)

    assert len(command.calls) == 1
    assert sudo.refresh_count == 0


def test_non_sudo_failure_is_not_retried(tmp_path):
    command = FakeCommandRunner()
    command.responses = [response(1, stderr="sudo: a password is required\n")]
    context = make_context(tmp_path, command=command)

    with pytest.raises(TaskFailed, match="exit code 1"):
        context.run(["false"])

    assert len(command.calls) == 1


def test_bundle_is_the_owning_bundle_for_the_current_task(tmp_path):
    bundle = Bundle("base", path=tmp_path)
    add_task(bundle, lambda ctx: None, name="Task")
    context = make_context(tmp_path)

    context.begin_task(bundle.tasks[0])

    assert context.bundle is bundle
    assert context.bundle_path == tmp_path


def test_context_exposes_root_project_and_vars(tmp_path):
    project = Project(root=tmp_path)
    values = Vars(VALUE=1)
    context = make_context(tmp_path, project=project, vars=values)

    assert context.root == tmp_path
    assert context.project is project
    assert context.vars.VALUE == 1
    assert context.vars.get("MISSING", "fallback") == "fallback"

    with pytest.raises(VarsError, match="missing var value: MISSING"):
        context.vars.require("MISSING")


def test_begin_task_switches_to_bundle_scoped_vars(tmp_path):
    project_values = Vars(VALUE="project")
    base = Bundle("base")
    other = Bundle("other")
    add_task(base, _noop, name="Base")
    add_task(other, _noop, name="Other")
    context = make_context(
        tmp_path,
        vars=project_values,
        bundle_vars={base: Vars(VALUE="base")},
    )

    context.begin_task(base.tasks[0])
    assert context.vars.VALUE == "base"

    context.begin_task(other.tasks[0])
    assert context.vars.VALUE == "project"


def test_side_effect_queues_for_the_current_task(tmp_path):
    context = make_context(tmp_path)
    context.begin_task(task_for())
    effect = side_effect("Refresh")(lambda ctx: None)

    context.side_effect(effect)

    assert context.take_side_effects() == [effect]


def test_side_effect_rejects_invalid_contexts(tmp_path):
    context = make_context(tmp_path)
    effect = side_effect("Refresh")(lambda ctx: None)

    with pytest.raises(TaskFailed, match="side effects can only be registered from a task"):
        context.side_effect(effect)

    context.begin_task(task_for(), is_side_effect=True)
    with pytest.raises(TaskFailed, match="side effects cannot register other side effects"):
        context.side_effect(effect)

    context.begin_task(task_for())
    with pytest.raises(TaskFailed, match="side_effect expects a SideEffect"):
        context.side_effect(lambda ctx: None)  # type: ignore[arg-type]


def test_side_effect_queue_lifecycle(tmp_path):
    context = make_context(tmp_path)
    context.begin_task(task_for())

    def first_effect(ctx):
        pass

    def second_effect(ctx):
        pass

    first = side_effect("First")(first_effect)
    second = side_effect("Second", one_shot=False)(second_effect)
    context.side_effect(first)
    context.side_effect(second)

    assert context.take_side_effects() == [first, second]
    assert context.take_side_effects() == []

    context.side_effect(first)
    context.begin_task(task_for("Other"))
    assert context.take_side_effects() == []


def test_side_effect_rejects_duplicate_function_names(tmp_path):
    context = make_context(tmp_path)
    context.begin_task(task_for())

    def refresh(ctx):
        pass

    first = side_effect("Refresh")(refresh)
    second = side_effect("Refresh again")(refresh)

    context.side_effect(first)
    context.side_effect(first)

    with pytest.raises(TaskFailed, match="duplicate side effect function name in bundle 'test': refresh"):
        context.side_effect(second)


def test_side_effect_rejects_task_function_name_collisions(tmp_path):
    bundle = Bundle("test")

    def shared(ctx):
        pass

    add_task(bundle, shared, name="Shared")
    context = make_context(tmp_path)
    context.begin_task(bundle.tasks[0])
    effect = side_effect("Shared effect")(shared)

    with pytest.raises(TaskFailed, match="collides with a task in bundle 'test'"):
        context.side_effect(effect)
