from kitup.context import current_context
from kitup.errors import PrivilegeError, TaskFailed
from kitup.modules import command
from kitup.registry import Bundle, side_effect
from kitup.resolver import resolve
from kitup.results import Status
from kitup.runner import Runner
from kitup.variables import Vars

from .helpers import FakeCommandRunner, FakeSudo, add_task, make_context, plain_output, response


def run_plan(
    tmp_path, bundles, *, tags=(), skip_tags=(), dry_run=False, vars=None, bundle_vars=None, command=None, sudo=None
):
    context = make_context(
        tmp_path,
        dry_run=dry_run,
        command=command or FakeCommandRunner(),
        sudo=sudo or FakeSudo(),
        vars=vars,
        bundle_vars=bundle_vars,
    )
    output = plain_output()
    runner = Runner(
        resolve(bundles),
        context,
        output,
        tags=None if not tags else frozenset(tags),
        skip_tags=None if not skip_tags else frozenset(skip_tags),
    )
    code = runner.run()
    return code, output.stream.getvalue(), context


def stored_result(context, task):
    """Read a stored result after the run cleared the current bundle."""
    return context.run_state.bundles[task.bundle.id].results[task.id]


def test_dependency_order_and_shared_dependency_once(tmp_path):
    calls = []
    base = Bundle("base")
    repos = Bundle("repos", depends_on=[base])
    desktop = Bundle("desktop", depends_on=[base, repos])

    add_task(base, lambda: calls.append("base"))
    add_task(repos, lambda: calls.append("repos"))
    add_task(desktop, lambda: calls.append("desktop"))

    code, text, _ = run_plan(tmp_path, [desktop, repos, base])

    assert code == 0
    assert calls == ["base", "repos", "desktop"]
    assert text.index("bundle: base") < text.index("bundle: repos") < text.index("bundle: desktop")
    assert text.count("bundle: base") == 1


def test_status_inference_and_summary(tmp_path):
    ok = Bundle("ok")
    changed = Bundle("changed")
    skipped = Bundle("skipped")

    add_task(ok, lambda: None)
    add_task(changed, lambda: current_context().changed(summary="+x", x=1))
    add_task(skipped, lambda: current_context().skip("not needed"))

    code, text, _ = run_plan(tmp_path, [ok, changed, skipped])

    assert code == 0
    assert "ok" in text
    assert "changed" in text
    assert "+ task.<lambda>: +x" in text
    assert "not needed" in text
    assert "Summary: 1 changed, 1 ok, 1 skipped" in text


def test_no_change_call_is_recorded_but_keeps_the_task_ok(tmp_path):
    bundle = Bundle("base")

    def check():
        ctx = current_context()
        ctx.changed(changed=False, summary="ensure thing")

    add_task(bundle, check, name="Check")

    code, _, context = run_plan(tmp_path, [bundle])

    result = stored_result(context, bundle.tasks[0])
    assert code == 0
    assert result.status is Status.OK
    assert len(result.calls) == 1
    assert result.calls[0].changed is False
    assert result.changes == []


def test_calls_recorded_before_skip_are_retained(tmp_path):
    bundle = Bundle("base")

    def check():
        ctx = current_context()
        ctx.changed(summary="prepared")
        ctx.skip("done")

    add_task(bundle, check, name="Check")

    code, _, context = run_plan(tmp_path, [bundle])

    result = stored_result(context, bundle.tasks[0])
    assert code == 0
    assert result.status is Status.SKIPPED
    assert [call.summary for call in result.calls] == ["prepared"]
    assert [call.summary for call in result.changes] == ["prepared"]


def test_later_tasks_read_changed_skipped_dry_run_and_failed_results(tmp_path):
    seen = []
    bundle = Bundle("base")

    def check():
        ctx = current_context()
        ctx.changed(summary="+updates", available=True)
        return "release-1"

    def apply():
        ctx = current_context()
        result = ctx.result(check)
        seen.append((result.changed, result.value, result.status))

    add_task(bundle, check, name="Check")
    add_task(bundle, apply, name="Apply")

    code, _, _ = run_plan(tmp_path, [bundle])

    assert code == 0
    assert seen == [(True, "release-1", Status.CHANGED)]

    seen = []
    bundle = Bundle("base")

    def skip_check():
        ctx = current_context()
        ctx.skip("nothing to do")

    def read_skipped():
        ctx = current_context()
        result = ctx.result(skip_check)
        seen.append((result.skipped, result.value))

    add_task(bundle, skip_check, name="Check")
    add_task(bundle, read_skipped, name="Apply")
    code, _, _ = run_plan(tmp_path, [bundle])
    assert code == 0
    assert seen == [(True, None)]

    seen = []
    bundle = Bundle("base")

    def dry_check():
        ctx = current_context()
        ctx.changed(summary="would change", would="change")

    def read_dry():
        ctx = current_context()
        result = ctx.result(dry_check)
        seen.append((result.changed, result.status))

    add_task(bundle, dry_check, name="Check")
    add_task(bundle, read_dry, name="Apply")
    code, _, _ = run_plan(tmp_path, [bundle], dry_run=True)
    assert code == 0
    assert seen == [(True, Status.CHANGED)]

    bundle = Bundle("base")

    def fail():
        raise TaskFailed("boom")

    add_task(bundle, fail, name="Fail")
    code, _, context = run_plan(tmp_path, [bundle])
    assert code == 1
    result = stored_result(context, bundle.tasks[0])
    assert result.failed
    assert result.value is None


def test_result_for_task_that_did_not_run_raises(tmp_path):
    bundle = Bundle("base")

    def check():
        pass

    def apply():
        ctx = current_context()
        ctx.result(check)

    add_task(bundle, check, name="Check", tags={"skip"})
    add_task(bundle, apply, name="Apply")

    code, text, _ = run_plan(tmp_path, [bundle], skip_tags={"skip"})

    assert code == 1
    assert "no result for task: check" in text


def test_task_results_are_stored_and_looked_up_per_bundle(tmp_path):
    seen = []
    one = Bundle("one")
    two = Bundle("two")

    def check():
        ctx = current_context()
        return ctx.bundle.id

    def apply():
        ctx = current_context()
        seen.append(ctx.result(check).value)

    add_task(one, check, name="Check")
    add_task(one, apply, name="Apply")
    add_task(two, check, name="Check")
    add_task(two, apply, name="Apply")

    code, _, context = run_plan(tmp_path, [one, two])

    assert code == 0
    assert seen == ["one", "two"]
    assert context.run_state.bundles["one"].results["one.check"].value == "one"
    assert context.run_state.bundles["two"].results["two.check"].value == "two"


def test_fail_fast_and_remaining_count(tmp_path):
    bundle = Bundle("base")

    def first():
        pass

    def second():
        raise TaskFailed("boom")

    def third():
        pass

    add_task(bundle, first, name="First")
    add_task(bundle, second, name="Second")
    add_task(bundle, third, name="Third")

    code, text, _ = run_plan(tmp_path, [bundle])

    assert code == 1
    assert "Second" in text and "failed" in text
    assert "error: boom" in text
    assert "Summary: 1 ok, 1 failed, 1 not run" in text
    assert 'Aborted after failure in "Second". 1 task(s) not run.' in text


def test_failures_print_diagnostics_and_unexpected_exceptions(tmp_path):
    bundle = Bundle("base")

    def fail():
        raise TaskFailed("boom", command=["tool"], stdout="diagnostic output\n")

    add_task(bundle, fail, name="Fail")

    code, text, _ = run_plan(tmp_path, [bundle])

    assert code == 1
    assert "stdout:" in text
    assert "diagnostic output" in text

    bundle = Bundle("base")

    def explode():
        raise RuntimeError("unexpected")

    add_task(bundle, explode, name="Explode")

    code, text, _ = run_plan(tmp_path, [bundle])

    assert code == 1
    assert "RuntimeError: unexpected" in text


def test_privilege_error_preserves_changes_and_notes(tmp_path):
    bundle = Bundle("base")

    def fail():
        ctx = current_context()
        ctx.changed(summary="prepared", step="prepared")
        ctx.note("a note")
        raise PrivilegeError("no credentials")

    add_task(bundle, fail, name="Fail")
    context = make_context(tmp_path, command=FakeCommandRunner(), sudo=FakeSudo())
    runner = Runner(resolve([bundle]), context, plain_output())

    result = runner._run_task(bundle.tasks[0])

    assert result.status is Status.FAILED
    assert [change.summary for change in result.changes] == ["prepared"]
    assert result.notes == ["a note"]


def test_keyboard_interrupt_returns_130(tmp_path):
    bundle = Bundle("base")
    add_task(bundle, lambda: None, name="First")

    def interrupt():
        raise KeyboardInterrupt

    add_task(bundle, interrupt, name="Second")

    code, text, _ = run_plan(tmp_path, [bundle])

    assert code == 130
    assert "Summary: 1 ok, 1 not run" in text
    assert "Interrupted. 1 task(s) not run." in text


def test_tag_filtering_and_skip_tags(tmp_path):
    calls = []
    bundle = Bundle("base")

    def packages():
        calls.append("packages")

    def config():
        calls.append("config")

    def both():
        calls.append("both")

    add_task(bundle, packages, tags={"packages"})
    add_task(bundle, config, tags={"config"})
    add_task(bundle, both, tags={"packages", "config"})

    code, text, _ = run_plan(tmp_path, [bundle], tags={"packages"}, skip_tags={"config"})

    assert code == 0
    assert calls == ["packages"]
    assert "Summary: 1 ok, nothing changed" in text


def test_sudo_is_requested_at_first_sudo_operation(tmp_path):
    bundle = Bundle("base")

    def install():
        ctx = current_context()
        ctx.run("install", sudo=True)

    add_task(bundle, install, name="Install packages")

    command = FakeCommandRunner()
    command.responses = [
        response(1, stderr="sudo: a password is required\n"),
        response(0),
    ]
    sudo = FakeSudo()

    code, _, _ = run_plan(tmp_path, [bundle], command=command, sudo=sudo)

    assert code == 0
    assert sudo.refresh_count == 1
    assert sudo.reasons == ['task "Install packages" requires sudo privileges']


def test_deferred_side_effect_runs_at_end_of_bundle_and_before_next_bundle(tmp_path):
    calls = []
    bundle = Bundle("base")

    def refresh():
        calls.append("refresh")

    effect = side_effect("Refresh")(refresh)

    def install():
        ctx = current_context()
        calls.append("install")
        ctx.changed(summary="+pkg")
        ctx.side_effect(effect)

    add_task(bundle, install, name="Install")
    add_task(bundle, lambda: calls.append("last"), name="Last")

    code, text, _ = run_plan(tmp_path, [bundle])
    assert code == 0
    assert calls == ["install", "last", "refresh"]
    assert "Refresh" in text
    assert "↳" in text

    calls.clear()
    base = Bundle("base")
    other = Bundle("other", depends_on=[base])

    def refresh_again():
        calls.append("refresh")

    effect = side_effect("Refresh")(refresh_again)

    def install():
        ctx = current_context()
        calls.append("install")
        ctx.changed(summary="+pkg")
        ctx.side_effect(effect)

    add_task(base, install, name="Install")
    add_task(other, lambda: calls.append("other"), name="Other")

    code, text, _ = run_plan(tmp_path, [other])
    assert code == 0
    assert calls == ["install", "refresh", "other"]
    assert text.index("Refresh") < text.index("bundle: other")


def test_immediate_side_effect_runs_before_and_affects_next_task(tmp_path):
    calls = []
    seen = []
    bundle = Bundle("base")

    def reload():
        ctx = current_context()
        calls.append("reload")
        ctx.set("ready", "yes")

    effect = side_effect("Reload", one_shot=False)(reload)

    def configure():
        ctx = current_context()
        calls.append("configure")
        ctx.changed(summary="+conf")
        ctx.side_effect(effect)

    def check():
        ctx = current_context()
        calls.append("next")
        seen.append(ctx.get("ready", "no"))

    add_task(bundle, configure, name="Configure")
    add_task(bundle, check, name="Next")

    code, _, _ = run_plan(tmp_path, [bundle])
    assert code == 0
    assert calls == ["configure", "reload", "next"]
    assert seen == ["yes"]


def test_unchanged_or_skipped_task_does_not_trigger_side_effect(tmp_path):
    for skip in (False, True):
        assert _run_with_optional_skip(tmp_path, skip=skip) == []


def _run_with_optional_skip(tmp_path, *, skip: bool) -> list[str]:
    calls: list[str] = []
    bundle = Bundle("base")

    def refresh():
        calls.append("refresh")

    effect = side_effect("Refresh")(refresh)

    def install():
        ctx = current_context()
        ctx.side_effect(effect)
        if skip:
            ctx.skip("nothing to do")

    add_task(bundle, install, name="Install")
    code, _, _ = run_plan(tmp_path, [bundle])
    assert code == 0
    return calls


def test_failures_and_side_effects(tmp_path):
    # A task that fails before returning runs none of its side effects.
    calls = []
    bundle = Bundle("base")

    def reload():
        calls.append("reload")

    def refresh():
        calls.append("refresh")

    immediate = side_effect("Reload", one_shot=False)(reload)
    deferred = side_effect("Refresh")(refresh)

    def install():
        ctx = current_context()
        ctx.side_effect(immediate)
        ctx.side_effect(deferred)
        raise TaskFailed("boom")

    add_task(bundle, install, name="Install")
    code, _, _ = run_plan(tmp_path, [bundle])
    assert code == 1
    assert calls == []

    # An immediate effect that already ran survives a later failure; deferred ones are discarded.
    calls = []
    bundle = Bundle("base")

    def reload_again():
        calls.append("reload")

    def refresh_again():
        calls.append("refresh")

    immediate = side_effect("Reload", one_shot=False)(reload_again)
    deferred = side_effect("Refresh")(refresh_again)

    def configure():
        ctx = current_context()
        ctx.changed(summary="+conf")
        ctx.side_effect(immediate)
        ctx.side_effect(deferred)

    add_task(bundle, configure, name="Configure")
    add_task(bundle, lambda: (_ for _ in ()).throw(TaskFailed("boom")), name="Fail")
    code, _, _ = run_plan(tmp_path, [bundle])
    assert code == 1
    assert calls == ["reload"]


def test_one_shot_side_effects_run_once_per_bundle_but_can_retry_after_skip(tmp_path):
    calls = []
    base = Bundle("base")
    other = Bundle("other", depends_on=[base])

    def refresh():
        calls.append("refresh")

    effect = side_effect("Refresh")(refresh)

    def register():
        ctx = current_context()
        ctx.changed(summary="+pkg")
        ctx.side_effect(effect)

    add_task(base, register, name="Base install")
    add_task(other, register, name="Other install")

    code, _, _ = run_plan(tmp_path, [other])
    assert code == 0
    assert calls == ["refresh", "refresh"]

    calls = []
    bundle = Bundle("base")

    def register_twice():
        ctx = current_context()
        ctx.changed(summary="+pkg")
        ctx.side_effect(effect)
        ctx.side_effect(effect)

    add_task(bundle, register_twice, name="Install")

    code, _, _ = run_plan(tmp_path, [bundle])
    assert code == 0
    assert calls == ["refresh"]

    calls = []
    bundle = Bundle("base")

    def refresh_or_skip():
        ctx = current_context()
        calls.append("refresh")
        if len(calls) == 1:
            ctx.skip("not now")

    retry_effect = side_effect("Refresh")(refresh_or_skip)

    def first():
        ctx = current_context()
        ctx.changed(summary="+first")
        ctx.side_effect(retry_effect)

    def second():
        ctx = current_context()
        ctx.changed(summary="+second")
        ctx.side_effect(retry_effect)

    add_task(bundle, first, name="First")
    add_task(bundle, second, name="Second")

    code, _, _ = run_plan(tmp_path, [bundle])
    assert code == 0
    assert calls == ["refresh", "refresh"]


def test_immediate_side_effects_run_for_every_registration(tmp_path):
    calls = []
    bundle = Bundle("base")

    def reload():
        calls.append("reload")

    effect = side_effect("Reload", one_shot=False)(reload)

    def configure():
        ctx = current_context()
        ctx.changed(summary="+conf")
        ctx.side_effect(effect)
        ctx.side_effect(effect)

    add_task(bundle, configure, name="Configure")

    code, _, _ = run_plan(tmp_path, [bundle])

    assert code == 0
    assert calls == ["reload", "reload"]


def test_side_effect_failures_abort(tmp_path):
    # An immediate failure aborts before the next task.
    calls = []
    bundle = Bundle("base")

    def fail():
        raise TaskFailed("reload failed")

    effect = side_effect("Reload", one_shot=False)(fail)

    def configure():
        ctx = current_context()
        ctx.changed(summary="+conf")
        ctx.side_effect(effect)

    add_task(bundle, configure, name="Configure")
    add_task(bundle, lambda: calls.append("next"), name="Next")

    code, text, _ = run_plan(tmp_path, [bundle])
    assert code == 1
    assert calls == []
    assert "error: reload failed" in text
    assert 'Aborted after failure in "Reload". 1 task(s) not run.' in text

    # A deferred failure aborts at the end of the bundle, after later tasks run.
    calls = []
    base = Bundle("base")
    other = Bundle("other", depends_on=[base])

    def fail():
        raise TaskFailed("refresh failed")

    effect = side_effect("Refresh")(fail)

    def register():
        ctx = current_context()
        ctx.changed(summary="+pkg")
        ctx.side_effect(effect)

    add_task(base, register, name="Install")
    add_task(base, lambda: calls.append("last"), name="Last")
    add_task(other, lambda: calls.append("other"), name="Other")

    code, text, _ = run_plan(tmp_path, [other])
    assert code == 1
    assert calls == ["last"]
    assert "error: refresh failed" in text
    assert 'Aborted after failure in "Refresh". 1 task(s) not run.' in text


def test_side_effects_run_in_dry_run_and_record_would_be_changes(tmp_path):
    bundle = Bundle("base")

    def reload():
        command.run("systemctl", "reload", "nginx")

    def refresh():
        command.run("dnf5", "makecache")

    immediate = side_effect("Reload", one_shot=False)(reload)
    deferred = side_effect("Refresh")(refresh)

    def configure():
        ctx = current_context()
        ctx.changed(summary="+conf")
        ctx.side_effect(immediate)
        ctx.side_effect(deferred)

    add_task(bundle, configure, name="Configure")

    code, text, _ = run_plan(tmp_path, [bundle], dry_run=True)

    assert code == 0
    assert "would run: systemctl reload nginx" in text
    assert "would run: dnf5 makecache" in text


def test_tag_filtering_selects_the_registering_task_and_its_side_effect(tmp_path):
    calls = []
    bundle = Bundle("base")

    def refresh_packages():
        calls.append("packages")

    def reload_config():
        calls.append("config")

    packages_effect = side_effect("Refresh")(refresh_packages)
    config_effect = side_effect("Reload", one_shot=False)(reload_config)

    def packages():
        ctx = current_context()
        ctx.changed(summary="+pkg")
        ctx.side_effect(packages_effect)

    def configure():
        ctx = current_context()
        ctx.changed(summary="+conf")
        ctx.side_effect(config_effect)

    add_task(bundle, packages, name="Packages", tags={"packages"})
    add_task(bundle, configure, name="Configure", tags={"config"})

    code, _, _ = run_plan(tmp_path, [bundle], tags={"packages"})

    assert code == 0
    assert calls == ["packages"]


def test_side_effects_use_the_registering_bundle_context_and_vars(tmp_path):
    seen = []
    (tmp_path / "files").mkdir()
    bundle = Bundle("base", path=tmp_path)

    def refresh():
        ctx = current_context()
        seen.append((ctx.bundle.name, ctx.bundle_path, ctx.resolve_source("marker"), ctx.vars.VALUE))

    effect = side_effect("Refresh")(refresh)

    def install():
        ctx = current_context()
        ctx.changed(summary="+pkg")
        ctx.side_effect(effect)

    add_task(bundle, install, name="Install")

    code, _, _ = run_plan(
        tmp_path,
        [bundle],
        vars=Vars(VALUE="project"),
        bundle_vars={bundle: Vars(VALUE="base")},
    )

    assert code == 0
    assert seen == [("base", tmp_path, tmp_path / "files" / "marker", "base")]


def test_bundle_vars_are_scoped_per_bundle(tmp_path):
    seen = []
    base = Bundle("base")
    other = Bundle("other", depends_on=[base])

    def show_base():
        ctx = current_context()
        seen.append(("base", ctx.vars.VALUE, ctx.vars.get("BASE_ONLY")))

    def show_other():
        ctx = current_context()
        seen.append(("other", ctx.vars.VALUE, ctx.vars.get("BASE_ONLY")))

    add_task(base, show_base, name="Show base")
    add_task(other, show_other, name="Show other")

    code, _, _ = run_plan(
        tmp_path,
        [other],
        vars=Vars(VALUE="project"),
        bundle_vars={base: Vars(VALUE="base", BASE_ONLY=1)},
    )

    assert code == 0
    assert seen == [("base", "base", 1), ("other", "project", None)]


def test_side_effect_results_are_accessible_stored_and_bundle_local(tmp_path):
    seen = []
    bundle = Bundle("base")

    def refresh():
        ctx = current_context()
        seen.append(ctx.result(check).value)
        return "done"

    effect = side_effect("Refresh")(refresh)

    def check():
        return "release-1"

    def install():
        ctx = current_context()
        ctx.changed(summary="+pkg")
        ctx.side_effect(effect)

    add_task(bundle, check, name="Check")
    add_task(bundle, install, name="Install")

    code, _, context = run_plan(tmp_path, [bundle])
    assert code == 0
    assert seen == ["release-1"]
    assert context.run_state.bundles["base"].results["base.refresh"].value == "done"

    seen = []
    base = Bundle("base")
    other = Bundle("other", depends_on=[base])

    def local_refresh():
        return "base"

    local_effect = side_effect("Refresh")(local_refresh)

    def register():
        ctx = current_context()
        ctx.changed(summary="+pkg")
        ctx.side_effect(local_effect)

    def check_local():
        ctx = current_context()
        try:
            ctx.result(local_refresh)
        except TaskFailed as exc:
            seen.append(str(exc))

    add_task(base, register, name="Install")
    add_task(other, check_local, name="Check")

    code, _, _ = run_plan(tmp_path, [other])

    assert code == 0
    assert seen == ["no result for task: local_refresh"]


def test_nested_side_effect_registration_fails(tmp_path):
    bundle = Bundle("base")
    inner = side_effect("Inner")(lambda: None)

    def outer():
        ctx = current_context()
        ctx.side_effect(inner)

    outer_effect = side_effect("Outer", one_shot=False)(outer)

    def install():
        ctx = current_context()
        ctx.changed(summary="+pkg")
        ctx.side_effect(outer_effect)

    add_task(bundle, install, name="Install")

    code, text, _ = run_plan(tmp_path, [bundle])

    assert code == 1
    assert "side effects cannot register other side effects" in text


def test_runner_renders_progress_rollups_and_diffs(tmp_path):
    base = Bundle("base")
    other = Bundle("other", depends_on=[base])

    def first():
        current_context().add_diff("+new")

    add_task(base, first, name="First")
    add_task(other, lambda: None, name="Second")

    code, text, context = run_plan(tmp_path, [other])

    assert code == 0
    assert "  [1/2] First" in text
    assert "  [2/2] Second" in text
    assert "      +new" in text
    assert stored_result(context, base.tasks[0]).diffs == ["+new"]
    assert "↳ base: 0 changed, 1 ok" in text
    assert "↳ other: 0 changed, 1 ok" in text
