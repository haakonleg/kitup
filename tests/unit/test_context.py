from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from kitup import ctx as ambient_ctx
from kitup import facts as ambient_facts
from kitup import memo
from kitup import vars as ambient_vars
from kitup.context import current_context, set_current_context
from kitup.errors import SkipTask, TaskFailed, VarsError
from kitup.modules import files
from kitup.project import Project
from kitup.registry import Bundle, side_effect
from kitup.results import CallResult, Status, TaskResult
from kitup.variables import Vars

from .helpers import FakeCommandRunner, FakeSudo, add_task, make_context, response


def _noop():
    return None


def task_for(name: str = "test"):
    bundle = Bundle("test")
    add_task(bundle, _noop, name=name)
    return bundle.tasks[0]


def test_module_function_outside_a_task_fails_clearly(tmp_path):
    with pytest.raises(TaskFailed, match="no active task context"):
        files.file(tmp_path / "file", content="value\n")


def test_ambient_proxies_resolve_the_current_context(tmp_path):
    set_current_context(None)
    with pytest.raises(AttributeError, match="no active task context"):
        _ = ambient_ctx.vars

    context = make_context(tmp_path, vars=Vars(VALUE="base"), facts={"os": "fedora"})

    assert current_context() is context
    assert ambient_ctx.vars is context.vars
    assert ambient_vars.VALUE == "base"
    assert ambient_facts["os"] == "fedora"


def test_changed_records_calls_notes_task_scope_and_action_inference(tmp_path):
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

    assert context.changed(summary="+pkg").action == "task._noop"

    no_change = context.changed(changed=False, summary="ensure pkg", package="pkg")
    assert no_change.changed is False
    assert context.calls[-1] is no_change
    assert no_change not in context.changes

    explicit = CallResult(action="test.method", changed=True, summary="+pkg", package="pkg")
    assert context.changed(explicit).action == "test.method"

    empty = CallResult(changed=True, summary="+pkg")
    assert empty.action == ""
    assert make_context(tmp_path).changed(empty).action == "task"
    inferred = context.changed(CallResult(changed=True, summary="+pkg"))
    assert inferred.action == "task._noop"

    with pytest.raises(TypeError, match="action"):
        CallResult(action=1, changed=True, summary="+pkg")  # type: ignore[arg-type]


def test_memo_and_input_share_a_store_across_tasks(tmp_path, monkeypatch):
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
    loads = []

    def load():
        loads.append(1)
        return "loaded"

    assert context.memo("key", load) == "loaded"
    context.begin_task(task_for())
    assert context.memo("key", load) == "loaded"
    assert len(loads) == 1

    assert context.input(prompt="Value: ") == "provided"
    assert prompts == ["Value: "]
    assert cleared == [("Value: ", "provided")]
    assert calls == ["begin", "end"]

    context.begin_task(task_for("Other"))
    assert context.memo("Value: ", load) == "loaded"
    assert context.input("provided_key") == "provided"
    assert context.memo("provided_key", load) == "provided"
    assert context.get("provided_key") == "provided"
    assert cleared[-1] == ("", "provided")
    assert calls == ["begin", "end", "begin", "end"]
    assert len(loads) == 2


def test_memo_decorator_caches_by_function_and_arguments(tmp_path):
    context = make_context(tmp_path)
    calls = []

    @memo
    def load(name, *, mode="fast"):
        calls.append((name, mode))
        return f"{name}:{mode}"

    assert load("a") == "a:fast"
    assert load("a") == "a:fast"
    assert load("a", mode="slow") == "a:slow"
    assert load("b") == "b:fast"
    context.begin_task(task_for())
    assert load("a") == "a:fast"
    assert calls == [("a", "fast"), ("a", "slow"), ("b", "fast")]
    assert load.__name__ == "load"


def test_memo_decorator_distinguishes_functions(tmp_path):
    make_context(tmp_path)
    calls = []

    @memo
    def first():
        calls.append("first")
        return "first value"

    @memo
    def second():
        calls.append("second")
        return "second value"

    assert first() == "first value"
    assert second() == "second value"
    assert first() == "first value"
    assert calls == ["first", "second"]


def test_memo_decorator_caches_failures(tmp_path):
    make_context(tmp_path)
    attempts = []

    @memo
    def load():
        attempts.append(1)
        raise TaskFailed("boom")

    with pytest.raises(TaskFailed, match="boom"):
        load()
    with pytest.raises(TaskFailed, match="boom"):
        load()
    assert attempts == [1]


def test_memo_decorator_caches_skips_across_tasks(tmp_path):
    context = make_context(tmp_path)
    calls = []

    @memo
    def load(name):
        calls.append(name)
        context.skip(f"missing {name}")

    with pytest.raises(SkipTask, match="missing a"):
        load("a")
    context.begin_task(task_for())
    with pytest.raises(SkipTask, match="missing a"):
        load("a")
    assert calls == ["a"]


def test_memo_decorator_caches_outcomes_per_arguments(tmp_path):
    context = make_context(tmp_path)
    calls = []

    @memo
    def load(name):
        calls.append(name)
        if name == "skip":
            context.skip("skip it")
        if name == "fail":
            context.fail("fail it")
        return f"value {name}"

    assert load("ok") == "value ok"
    with pytest.raises(SkipTask, match="skip it"):
        load("skip")
    with pytest.raises(TaskFailed, match="fail it"):
        load("fail")

    assert load("ok") == "value ok"
    with pytest.raises(SkipTask, match="skip it"):
        load("skip")
    with pytest.raises(TaskFailed, match="fail it"):
        load("fail")
    assert calls == ["ok", "skip", "fail"]


def test_memo_method_caches_skip_and_failure_outcomes(tmp_path):
    context = make_context(tmp_path)
    attempts = {"skip": 0, "fail": 0}

    def skip_loader():
        attempts["skip"] += 1
        context.skip("skip it")

    def fail_loader():
        attempts["fail"] += 1
        raise TaskFailed("fail it")

    with pytest.raises(SkipTask, match="skip it"):
        context.memo("skip", skip_loader)
    with pytest.raises(TaskFailed, match="fail it"):
        context.memo("fail", fail_loader)
    with pytest.raises(SkipTask, match="skip it"):
        context.memo("skip", skip_loader)
    with pytest.raises(TaskFailed, match="fail it"):
        context.memo("fail", fail_loader)
    assert attempts == {"skip": 1, "fail": 1}


def test_memo_decorator_rejects_unhashable_arguments(tmp_path):
    make_context(tmp_path)

    @memo
    def load(value):
        return value

    with pytest.raises(TaskFailed, match="memo arguments must be hashable"):
        load([1, 2])


def test_memo_decorator_does_not_cache_unexpected_errors(tmp_path):
    make_context(tmp_path)
    attempts = []

    @memo
    def load():
        attempts.append(1)
        raise TypeError("inner failure")

    with pytest.raises(TypeError, match="inner failure"):
        load()
    with pytest.raises(TypeError, match="inner failure"):
        load()
    assert attempts == [1, 1]


def test_memo_decorator_requires_an_active_context():
    set_current_context(None)

    @memo
    def load():
        return 1

    with pytest.raises(TaskFailed, match="no active task context"):
        load()


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

    def other():
        return None

    other_bundle = Bundle("other")
    add_task(other_bundle, other, name="Other")
    with pytest.raises(TaskFailed, match="no result for task:"):
        context.result(other)

    context = make_context(tmp_path)
    context.store_result(task, result)

    assert context.results == {}
    with pytest.raises(TaskFailed, match="no current bundle"):
        context.result(task.func)


def test_run_executes_and_probe_returns_nonzero_instead_of_failing(tmp_path):
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

    failing = FakeCommandRunner(returncode=1)
    context = make_context(tmp_path, command=failing)

    result = context.probe(["rpm", "-q", "git"])
    assert result.returncode == 1
    assert failing.calls[0][1]["check"] is False


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


def test_sudo_auth_failure_retries_runs_and_probes_but_other_failures_do_not(tmp_path):
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

    command = FakeCommandRunner()
    command.responses = [
        response(1, stderr="sudo: a password is required\n"),
        response(0, stdout="enabled\n"),
    ]
    context = make_context(tmp_path, command=command, sudo=FakeSudo())

    result = context.probe(["systemctl", "is-enabled", "foo.service"], sudo=True)

    assert result.returncode == 0
    assert result.stdout == "enabled\n"
    assert len(command.calls) == 2

    command = FakeCommandRunner()
    command.responses = [response(1, stderr="boom\n")]
    context = make_context(tmp_path, command=command)
    with pytest.raises(TaskFailed, match="exit code 1"):
        context.run(["dnf5", "install"], sudo=True)
    assert len(command.calls) == 1

    command = FakeCommandRunner()
    command.responses = [response(1, stderr="sudo: a password is required\n")]
    context = make_context(tmp_path, command=command)
    with pytest.raises(TaskFailed, match="exit code 1"):
        context.run(["false"])
    assert len(command.calls) == 1


def test_context_exposes_root_project_vars_and_owning_bundle(tmp_path):
    project = Project(root=tmp_path)
    values = Vars(VALUE=1)
    bundle = Bundle("base", path=tmp_path)
    add_task(bundle, lambda: None, name="Task")
    context = make_context(tmp_path, project=project, vars=values)

    assert context.root == tmp_path
    assert context.project is project
    assert context.vars.VALUE == 1
    assert context.vars.get("MISSING", "fallback") == "fallback"

    context.begin_task(bundle.tasks[0])

    assert context.bundle is bundle
    assert context.bundle_path == tmp_path

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


def test_side_effect_queue_lifecycle(tmp_path):
    context = make_context(tmp_path)
    context.begin_task(task_for())

    def first_effect():
        pass

    def second_effect():
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


def test_side_effect_validation(tmp_path):
    context = make_context(tmp_path)
    assert context.take_side_effects() == []
    effect = side_effect("Refresh")(lambda: None)

    with pytest.raises(TaskFailed, match="side effects can only be registered from a task"):
        context.side_effect(effect)

    context.begin_task(task_for(), is_side_effect=True)
    with pytest.raises(TaskFailed, match="side effects cannot register other side effects"):
        context.side_effect(effect)

    context.begin_task(task_for())
    with pytest.raises(TaskFailed, match="side_effect expects a SideEffect"):
        context.side_effect(lambda: None)  # type: ignore[arg-type]

    def refresh():
        pass

    first = side_effect("Refresh")(refresh)
    second = side_effect("Refresh again")(refresh)
    context.side_effect(first)
    context.side_effect(first)
    with pytest.raises(TaskFailed, match="duplicate side effect function name in bundle 'test': refresh"):
        context.side_effect(second)

    bundle = Bundle("test")

    def shared():
        pass

    add_task(bundle, shared, name="Shared")
    context = make_context(tmp_path)
    context.begin_task(bundle.tasks[0])
    colliding = side_effect("Shared effect")(shared)
    with pytest.raises(TaskFailed, match="collides with a task in bundle 'test'"):
        context.side_effect(colliding)
