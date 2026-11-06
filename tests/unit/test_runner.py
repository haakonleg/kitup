from io import StringIO

from kitup.errors import PrivilegeError, TaskFailed
from kitup.modules import command
from kitup.output import Output
from kitup.registry import Bundle, side_effect
from kitup.resolver import resolve
from kitup.results import Status, TaskResult
from kitup.runner import Runner

from .helpers import FakeCommandRunner, FakeSudo, add_task, make_context


def make_output() -> Output:
    return Output(StringIO(), no_color=True)


def run_plan(tmp_path, bundles, *, tags=(), skip_tags=(), dry_run=False):
    context = make_context(
        tmp_path,
        dry_run=dry_run,
        command=FakeCommandRunner(),
        sudo=FakeSudo(),
    )
    output = make_output()
    runner = Runner(
        resolve(bundles),
        context,
        output,
        tags=None if not tags else frozenset(tags),
        skip_tags=None if not skip_tags else frozenset(skip_tags),
    )
    code = runner.run()
    return code, output.stream.getvalue(), context


def test_dependency_order_and_shared_dependency_once(tmp_path):
    calls = []
    base = Bundle("base")
    repos = Bundle("repos", depends_on=[base])
    desktop = Bundle("desktop", depends_on=[base, repos])

    add_task(base, lambda ctx: calls.append("base"))
    add_task(repos, lambda ctx: calls.append("repos"))
    add_task(desktop, lambda ctx: calls.append("desktop"))

    code, text, _ = run_plan(tmp_path, [desktop, repos, base])

    assert code == 0
    assert calls == ["base", "repos", "desktop"]
    assert text.index("bundle: base") < text.index("bundle: repos") < text.index("bundle: desktop")
    assert text.count("bundle: base") == 1


def test_status_inference_and_summary(tmp_path):
    ok = Bundle("ok")
    changed = Bundle("changed")
    skipped = Bundle("skipped")

    add_task(ok, lambda ctx: None)
    add_task(changed, lambda ctx: ctx.changed(summary="+x", x=1))
    add_task(skipped, lambda ctx: ctx.skip("not needed"))

    code, text, _ = run_plan(tmp_path, [ok, changed, skipped])

    assert code == 0
    assert "ok" in text
    assert "changed  +x" in text
    assert "skipped  not needed" in text
    assert "Summary: 1 changed, 1 ok, 1 skipped, 0 failed, 0 not run" in text


def test_no_change_call_is_recorded_but_keeps_the_task_ok(tmp_path):
    bundle = Bundle("base")

    def check(ctx):
        ctx.changed(changed=False, summary="ensure thing")

    add_task(bundle, check, name="Check")

    code, _, context = run_plan(tmp_path, [bundle])

    result = context.result(check)
    assert code == 0
    assert result.status is Status.OK
    assert len(result.calls) == 1
    assert result.calls[0].changed is False
    assert result.changes == []


def test_calls_recorded_before_skip_are_retained(tmp_path):
    bundle = Bundle("base")

    def check(ctx):
        ctx.changed(summary="prepared")
        ctx.skip("done")

    add_task(bundle, check, name="Check")

    code, _, context = run_plan(tmp_path, [bundle])

    result = context.result(check)
    assert code == 0
    assert result.status is Status.SKIPPED
    assert [call.summary for call in result.calls] == ["prepared"]
    assert [call.summary for call in result.changes] == ["prepared"]


def test_later_task_reads_previous_result(tmp_path):
    seen = []
    bundle = Bundle("base")

    def check(ctx):
        ctx.changed(summary="+updates", available=True)
        return "release-1"

    def apply(ctx):
        result = ctx.result(check)
        seen.append((result.changed, result.value, result.status))
        if not result.changed:
            ctx.skip("no updates")

    add_task(bundle, check, name="Check")
    add_task(bundle, apply, name="Apply")

    code, _, _ = run_plan(tmp_path, [bundle])

    assert code == 0
    assert seen == [(True, "release-1", Status.CHANGED)]


def test_skipped_task_result_is_available(tmp_path):
    seen = []
    bundle = Bundle("base")

    def check(ctx):
        ctx.skip("nothing to do")

    def apply(ctx):
        result = ctx.result(check)
        seen.append((result.skipped, result.value))

    add_task(bundle, check, name="Check")
    add_task(bundle, apply, name="Apply")

    code, _, _ = run_plan(tmp_path, [bundle])

    assert code == 0
    assert seen == [(True, None)]


def test_dry_run_result_is_available(tmp_path):
    seen = []
    bundle = Bundle("base")

    def check(ctx):
        ctx.changed(summary="would change", would="change")

    def apply(ctx):
        result = ctx.result(check)
        seen.append((result.changed, result.status))

    add_task(bundle, check, name="Check")
    add_task(bundle, apply, name="Apply")

    code, _, _ = run_plan(tmp_path, [bundle], dry_run=True)

    assert code == 0
    assert seen == [(True, Status.CHANGED)]


def test_failed_task_result_is_available(tmp_path):
    bundle = Bundle("base")

    def fail(ctx):
        raise TaskFailed("boom")

    add_task(bundle, fail, name="Fail")

    code, _, context = run_plan(tmp_path, [bundle])

    assert code == 1
    result = context.result(fail)
    assert result.failed
    assert result.value is None


def test_result_for_task_that_did_not_run_raises(tmp_path):
    bundle = Bundle("base")

    def check(ctx):
        pass

    def apply(ctx):
        ctx.result(check)

    add_task(bundle, check, name="Check", tags={"skip"})
    add_task(bundle, apply, name="Apply")

    code, text, _ = run_plan(tmp_path, [bundle], skip_tags={"skip"})

    assert code == 1
    assert "no result for task: check" in text


def test_result_is_available_across_bundles(tmp_path):
    seen = []
    base = Bundle("base")
    desktop = Bundle("desktop", depends_on=[base])

    def check(ctx):
        return "ready"

    def apply(ctx):
        seen.append(ctx.result(check).value)

    add_task(base, check, name="Check")
    add_task(desktop, apply, name="Apply")

    code, _, _ = run_plan(tmp_path, [desktop])

    assert code == 0
    assert seen == ["ready"]


def test_task_result_status_helpers():
    bundle = Bundle("base")
    add_task(bundle, lambda ctx: None)
    task = bundle.tasks[0]

    assert TaskResult(task, Status.OK).ok
    assert TaskResult(task, Status.CHANGED).changed
    assert TaskResult(task, Status.CHANGED).ok
    assert TaskResult(task, Status.SKIPPED).skipped
    assert TaskResult(task, Status.FAILED).failed


def test_fail_fast_and_remaining_count(tmp_path):
    bundle = Bundle("base")
    add_task(bundle, lambda ctx: None, name="First")
    add_task(bundle, lambda ctx: (_ for _ in ()).throw(TaskFailed("boom")), name="Second")
    add_task(bundle, lambda ctx: None, name="Third")

    code, text, _ = run_plan(tmp_path, [bundle])

    assert code == 1
    assert "Second" in text and "failed" in text
    assert "error: boom" in text
    assert "Summary: 0 changed, 1 ok, 1 failed, 1 not run" in text
    assert 'Aborted after failure in "Second". 1 task(s) not run.' in text


def test_privilege_error_preserves_changes_and_notes(tmp_path):
    bundle = Bundle("base")

    def fail(ctx):
        ctx.changed(summary="prepared", step="prepared")
        ctx.note("a note")
        raise PrivilegeError("no credentials")

    add_task(bundle, fail, name="Fail")
    context = make_context(tmp_path, command=FakeCommandRunner(), sudo=FakeSudo())
    runner = Runner(resolve([bundle]), context, make_output())

    result = runner._run_task(bundle.tasks[0])

    assert result.status is Status.FAILED
    assert [change.summary for change in result.changes] == ["prepared"]
    assert result.notes == ["a note"]


def test_unexpected_exception_fails_task(tmp_path):
    bundle = Bundle("base")

    def explode(ctx):
        raise RuntimeError("unexpected")

    add_task(bundle, explode, name="Explode")

    code, text, _ = run_plan(tmp_path, [bundle])

    assert code == 1
    assert "RuntimeError: unexpected" in text


def test_keyboard_interrupt_returns_130(tmp_path):
    bundle = Bundle("base")
    add_task(bundle, lambda ctx: None, name="First")

    def interrupt(ctx):
        raise KeyboardInterrupt

    add_task(bundle, interrupt, name="Second")

    code, text, _ = run_plan(tmp_path, [bundle])

    assert code == 130
    assert "Summary: 0 changed, 1 ok, 0 failed, 1 not run" in text
    assert "Interrupted. 1 task(s) not run." in text


def test_tag_filtering_and_skip_tags(tmp_path):
    calls = []
    bundle = Bundle("base")
    add_task(bundle, lambda ctx: calls.append("packages"), tags={"packages"})
    add_task(bundle, lambda ctx: calls.append("config"), tags={"config"})
    add_task(bundle, lambda ctx: calls.append("both"), tags={"packages", "config"})

    code, text, _ = run_plan(tmp_path, [bundle], tags={"packages"}, skip_tags={"config"})

    assert code == 0
    assert calls == ["packages"]
    assert "Summary: 0 changed, 1 ok, 0 failed, 0 not run" in text


def test_sudo_is_requested_at_first_sudo_operation(tmp_path):
    bundle = Bundle("base")

    def install(ctx):
        ctx.run(["install"], sudo=True)

    add_task(bundle, install, name="Install packages")

    code, _, context = run_plan(tmp_path, [bundle])

    assert code == 0
    assert context.sudo.refresh_count == 1
    assert context.sudo.reasons == ['task "Install packages" requires sudo privileges']


def test_changed_task_triggers_one_shot_side_effect_at_end_of_bundle(tmp_path):
    calls = []
    bundle = Bundle("base")
    effect = side_effect("Refresh")(lambda ctx: calls.append("refresh"))

    def install(ctx):
        calls.append("install")
        ctx.changed(summary="+pkg")
        ctx.register_side_effect(effect)

    add_task(bundle, install, name="Install")
    add_task(bundle, lambda ctx: calls.append("last"), name="Last")

    code, text, _ = run_plan(tmp_path, [bundle])

    assert code == 0
    assert calls == ["install", "last", "refresh"]
    assert "↳ Refresh" in text


def test_changed_task_triggers_immediate_side_effect_before_next_task(tmp_path):
    calls = []
    bundle = Bundle("base")
    effect = side_effect("Reload", one_shot=False)(lambda ctx: calls.append("reload"))

    def configure(ctx):
        calls.append("configure")
        ctx.changed(summary="+conf")
        ctx.register_side_effect(effect)

    add_task(bundle, configure, name="Configure")
    add_task(bundle, lambda ctx: calls.append("next"), name="Next")

    code, _, _ = run_plan(tmp_path, [bundle])

    assert code == 0
    assert calls == ["configure", "reload", "next"]


def test_unchanged_task_does_not_trigger_side_effect(tmp_path):
    calls = []
    bundle = Bundle("base")
    effect = side_effect("Refresh")(lambda ctx: calls.append("refresh"))

    def install(ctx):
        ctx.register_side_effect(effect)

    add_task(bundle, install, name="Install")

    code, _, _ = run_plan(tmp_path, [bundle])

    assert code == 0
    assert calls == []


def test_skipped_task_does_not_trigger_side_effect(tmp_path):
    calls = []
    bundle = Bundle("base")
    effect = side_effect("Refresh")(lambda ctx: calls.append("refresh"))

    def install(ctx):
        ctx.register_side_effect(effect)
        ctx.skip("nothing to do")

    add_task(bundle, install, name="Install")

    code, _, _ = run_plan(tmp_path, [bundle])

    assert code == 0
    assert calls == []


def test_failed_task_does_not_run_its_side_effects(tmp_path):
    calls = []
    bundle = Bundle("base")
    immediate = side_effect("Reload", one_shot=False)(lambda ctx: calls.append("reload"))
    deferred = side_effect("Refresh")(lambda ctx: calls.append("refresh"))

    def install(ctx):
        ctx.register_side_effect(immediate)
        ctx.register_side_effect(deferred)
        raise TaskFailed("boom")

    add_task(bundle, install, name="Install")

    code, _, _ = run_plan(tmp_path, [bundle])

    assert code == 1
    assert calls == []


def test_deferred_side_effects_run_before_the_next_bundle(tmp_path):
    calls = []
    base = Bundle("base")
    other = Bundle("other", depends_on=[base])
    effect = side_effect("Refresh")(lambda ctx: calls.append("refresh"))

    def install(ctx):
        calls.append("install")
        ctx.changed(summary="+pkg")
        ctx.register_side_effect(effect)

    add_task(base, install, name="Install")
    add_task(other, lambda ctx: calls.append("other"), name="Other")

    code, text, _ = run_plan(tmp_path, [other])

    assert code == 0
    assert calls == ["install", "refresh", "other"]
    assert text.index("↳ Refresh") < text.index("bundle: other")


def test_immediate_side_effects_can_affect_later_tasks(tmp_path):
    seen = []
    bundle = Bundle("base")

    def reload(ctx):
        ctx.set("ready", "yes")

    effect = side_effect("Reload", one_shot=False)(reload)

    def configure(ctx):
        ctx.changed(summary="+conf")
        ctx.register_side_effect(effect)

    def check(ctx):
        seen.append(ctx.get("ready", "no"))

    add_task(bundle, configure, name="Configure")
    add_task(bundle, check, name="Check")

    code, _, _ = run_plan(tmp_path, [bundle])

    assert code == 0
    assert seen == ["yes"]


def test_one_shot_side_effect_runs_at_most_once_across_bundles(tmp_path):
    calls = []
    base = Bundle("base")
    other = Bundle("other", depends_on=[base])
    effect = side_effect("Refresh")(lambda ctx: calls.append("refresh"))

    def register(ctx):
        ctx.changed(summary="+pkg")
        ctx.register_side_effect(effect)

    add_task(base, register, name="Base install")
    add_task(other, register, name="Other install")

    code, _, _ = run_plan(tmp_path, [other])

    assert code == 0
    assert calls == ["refresh"]


def test_skipped_one_shot_side_effect_can_run_again_later(tmp_path):
    calls = []
    base = Bundle("base")
    other = Bundle("other", depends_on=[base])

    def refresh(ctx):
        calls.append("refresh")
        ctx.skip("not now")

    effect = side_effect("Refresh")(refresh)

    def register(ctx):
        ctx.changed(summary="+pkg")
        ctx.register_side_effect(effect)

    add_task(base, register, name="Base install")
    add_task(other, register, name="Other install")

    code, _, _ = run_plan(tmp_path, [other])

    assert code == 0
    assert calls == ["refresh", "refresh"]


def test_immediate_side_effect_runs_for_every_registration(tmp_path):
    calls = []
    bundle = Bundle("base")
    effect = side_effect("Reload", one_shot=False)(lambda ctx: calls.append("reload"))

    def configure(ctx):
        ctx.changed(summary="+conf")
        ctx.register_side_effect(effect)
        ctx.register_side_effect(effect)

    add_task(bundle, configure, name="Configure")

    code, _, _ = run_plan(tmp_path, [bundle])

    assert code == 0
    assert calls == ["reload", "reload"]


def test_immediate_side_effect_failure_aborts_immediately(tmp_path):
    calls = []
    bundle = Bundle("base")

    def fail(ctx):
        raise TaskFailed("reload failed")

    effect = side_effect("Reload", one_shot=False)(fail)

    def configure(ctx):
        ctx.changed(summary="+conf")
        ctx.register_side_effect(effect)

    add_task(bundle, configure, name="Configure")
    add_task(bundle, lambda ctx: calls.append("next"), name="Next")

    code, text, _ = run_plan(tmp_path, [bundle])

    assert code == 1
    assert calls == []
    assert "error: reload failed" in text
    assert 'Aborted after failure in "Reload". 1 task(s) not run.' in text


def test_deferred_side_effect_failure_aborts_at_end_of_bundle(tmp_path):
    calls = []
    base = Bundle("base")
    other = Bundle("other", depends_on=[base])

    def fail(ctx):
        raise TaskFailed("refresh failed")

    effect = side_effect("Refresh")(fail)

    def register(ctx):
        ctx.changed(summary="+pkg")
        ctx.register_side_effect(effect)

    add_task(base, register, name="Install")
    add_task(base, lambda ctx: calls.append("last"), name="Last")
    add_task(other, lambda ctx: calls.append("other"), name="Other")

    code, text, _ = run_plan(tmp_path, [other])

    assert code == 1
    assert calls == ["last"]
    assert "error: refresh failed" in text
    assert 'Aborted after failure in "Refresh". 1 task(s) not run.' in text


def test_later_task_failure_keeps_immediate_side_effect(tmp_path):
    calls = []
    bundle = Bundle("base")
    effect = side_effect("Reload", one_shot=False)(lambda ctx: calls.append("reload"))

    def configure(ctx):
        ctx.changed(summary="+conf")
        ctx.register_side_effect(effect)

    add_task(bundle, configure, name="Configure")
    add_task(bundle, lambda ctx: (_ for _ in ()).throw(TaskFailed("boom")), name="Fail")

    code, _, _ = run_plan(tmp_path, [bundle])

    assert code == 1
    assert calls == ["reload"]


def test_deferred_side_effects_are_discarded_when_bundle_fails(tmp_path):
    calls = []
    bundle = Bundle("base")
    effect = side_effect("Refresh")(lambda ctx: calls.append("refresh"))

    def configure(ctx):
        ctx.changed(summary="+conf")
        ctx.register_side_effect(effect)

    add_task(bundle, configure, name="Configure")
    add_task(bundle, lambda ctx: (_ for _ in ()).throw(TaskFailed("boom")), name="Fail")

    code, _, _ = run_plan(tmp_path, [bundle])

    assert code == 1
    assert calls == []


def test_side_effects_run_in_dry_run_and_record_would_be_changes(tmp_path):
    bundle = Bundle("base")

    def reload(ctx):
        command.run(ctx, ["systemctl", "reload", "nginx"])

    def refresh(ctx):
        command.run(ctx, ["dnf5", "makecache"])

    immediate = side_effect("Reload", one_shot=False)(reload)
    deferred = side_effect("Refresh")(refresh)

    def configure(ctx):
        ctx.changed(summary="+conf")
        ctx.register_side_effect(immediate)
        ctx.register_side_effect(deferred)

    add_task(bundle, configure, name="Configure")

    code, text, _ = run_plan(tmp_path, [bundle], dry_run=True)

    assert code == 0
    assert "would run: systemctl reload nginx" in text
    assert "would run: dnf5 makecache" in text


def test_tag_filtering_selects_the_registering_task_and_its_side_effect(tmp_path):
    calls = []
    bundle = Bundle("base")
    packages_effect = side_effect("Refresh")(lambda ctx: calls.append("packages"))
    config_effect = side_effect("Reload", one_shot=False)(lambda ctx: calls.append("config"))

    def packages(ctx):
        ctx.changed(summary="+pkg")
        ctx.register_side_effect(packages_effect)

    def configure(ctx):
        ctx.changed(summary="+conf")
        ctx.register_side_effect(config_effect)

    add_task(bundle, packages, name="Packages", tags={"packages"})
    add_task(bundle, configure, name="Configure", tags={"config"})

    code, _, _ = run_plan(tmp_path, [bundle], tags={"packages"})

    assert code == 0
    assert calls == ["packages"]


def test_side_effects_use_the_registering_bundle_context(tmp_path):
    seen = []
    bundle = Bundle("base", path=tmp_path)
    effect = side_effect("Refresh")(
        lambda ctx: seen.append((ctx.bundle.name, ctx.bundle_path, ctx.resolve_source("marker")))
    )

    def install(ctx):
        ctx.changed(summary="+pkg")
        ctx.register_side_effect(effect)

    add_task(bundle, install, name="Install")

    code, _, _ = run_plan(tmp_path, [bundle])

    assert code == 0
    assert seen == [("base", tmp_path, tmp_path / "files" / "marker")]


def test_side_effects_can_inspect_earlier_task_results(tmp_path):
    seen = []
    bundle = Bundle("base")
    effect = side_effect("Refresh")(lambda ctx: seen.append(ctx.result(check).value))

    def check(ctx):
        return "release-1"

    def install(ctx):
        ctx.changed(summary="+pkg")
        ctx.register_side_effect(effect)

    add_task(bundle, check, name="Check")
    add_task(bundle, install, name="Install")

    code, _, _ = run_plan(tmp_path, [bundle])

    assert code == 0
    assert seen == ["release-1"]


def test_side_effect_results_are_stored_like_task_results(tmp_path):
    bundle = Bundle("base")

    def refresh(ctx):
        return "done"

    effect = side_effect("Refresh")(refresh)

    def install(ctx):
        ctx.changed(summary="+pkg")
        ctx.register_side_effect(effect)

    add_task(bundle, install, name="Install")

    code, _, context = run_plan(tmp_path, [bundle])

    assert code == 0
    assert context.result(effect.func).value == "done"


def test_nested_side_effect_registration_fails(tmp_path):
    bundle = Bundle("base")
    inner = side_effect("Inner")(lambda ctx: None)

    def outer(ctx):
        ctx.register_side_effect(inner)

    outer_effect = side_effect("Outer", one_shot=False)(outer)

    def install(ctx):
        ctx.changed(summary="+pkg")
        ctx.register_side_effect(outer_effect)

    add_task(bundle, install, name="Install")

    code, text, _ = run_plan(tmp_path, [bundle])

    assert code == 1
    assert "side effects cannot register other side effects" in text


def test_apply_output_prefixes_side_effect_lines(tmp_path):
    bundle = Bundle("base")
    effect = side_effect("Reload", one_shot=False)(lambda ctx: ctx.changed(summary="+reloaded"))

    def configure(ctx):
        ctx.changed(summary="+conf")
        ctx.register_side_effect(effect)

    add_task(bundle, configure, name="Configure")

    code, text, _ = run_plan(tmp_path, [bundle])

    assert code == 0
    lines = text.splitlines()
    assert any(line.startswith("    Configure") for line in lines)
    assert any(line.startswith("  ↳ Reload") for line in lines)
    assert not any(line.startswith("  ↳ Configure") for line in lines)
