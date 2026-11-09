from kitup.errors import PrivilegeError, TaskFailed
from kitup.modules import command
from kitup.registry import Bundle, side_effect
from kitup.resolver import resolve
from kitup.results import Status
from kitup.runner import Runner
from kitup.vars import Vars

from .helpers import FakeCommandRunner, FakeSudo, add_task, make_context, plain_output


def run_plan(tmp_path, bundles, *, tags=(), skip_tags=(), dry_run=False, vars=None, bundle_vars=None):
    context = make_context(
        tmp_path,
        dry_run=dry_run,
        command=FakeCommandRunner(),
        sudo=FakeSudo(),
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

    result = stored_result(context, bundle.tasks[0])
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

    result = stored_result(context, bundle.tasks[0])
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


def test_task_result_statuses_are_available(tmp_path):
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

    bundle = Bundle("base")

    def fail(ctx):
        raise TaskFailed("boom")

    add_task(bundle, fail, name="Fail")
    code, _, context = run_plan(tmp_path, [bundle])
    assert code == 1
    result = stored_result(context, bundle.tasks[0])
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


def test_result_lookup_does_not_cross_bundles(tmp_path):
    seen = []
    base = Bundle("base")
    desktop = Bundle("desktop", depends_on=[base])

    def check(ctx):
        return "ready"

    def apply(ctx):
        try:
            ctx.result(check)
        except TaskFailed as exc:
            seen.append(str(exc))

    add_task(base, check, name="Check")
    add_task(desktop, apply, name="Apply")

    code, _, _ = run_plan(tmp_path, [desktop])

    assert code == 0
    assert seen == ["no result for task: check"]


def test_task_results_are_stored_per_bundle(tmp_path):
    base = Bundle("base")
    desktop = Bundle("desktop", depends_on=[base])

    def shared(ctx):
        return ctx.bundle.id

    add_task(base, shared, name="Shared")
    add_task(desktop, shared, name="Shared")

    code, _, context = run_plan(tmp_path, [desktop])

    assert code == 0
    assert context.run_state.bundles["base"].results["base.shared"].value == "base"
    assert context.run_state.bundles["desktop"].results["desktop.shared"].value == "desktop"


def test_result_lookup_reads_the_current_bundle_only(tmp_path):
    seen = []
    one = Bundle("one")
    two = Bundle("two")

    def check(ctx):
        return ctx.bundle.id

    def apply(ctx):
        seen.append(ctx.result(check).value)

    add_task(one, check, name="Check")
    add_task(one, apply, name="Apply")
    add_task(two, check, name="Check")
    add_task(two, apply, name="Apply")

    code, _, _ = run_plan(tmp_path, [one, two])

    assert code == 0
    assert seen == ["one", "two"]


def test_fail_fast_and_remaining_count(tmp_path):
    bundle = Bundle("base")

    def first(ctx):
        pass

    def second(ctx):
        raise TaskFailed("boom")

    def third(ctx):
        pass

    add_task(bundle, first, name="First")
    add_task(bundle, second, name="Second")
    add_task(bundle, third, name="Third")

    code, text, _ = run_plan(tmp_path, [bundle])

    assert code == 1
    assert "Second" in text and "failed" in text
    assert "error: boom" in text
    assert "Summary: 0 changed, 1 ok, 1 failed, 1 not run" in text
    assert 'Aborted after failure in "Second". 1 task(s) not run.' in text


def test_failure_prints_captured_stdout(tmp_path):
    bundle = Bundle("base")

    def fail(ctx):
        raise TaskFailed("boom", command=["tool"], stdout="diagnostic output\n")

    add_task(bundle, fail, name="Fail")

    code, text, _ = run_plan(tmp_path, [bundle])

    assert code == 1
    assert "stdout:" in text
    assert "diagnostic output" in text


def test_privilege_error_preserves_changes_and_notes(tmp_path):
    bundle = Bundle("base")

    def fail(ctx):
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

    def packages(ctx):
        calls.append("packages")

    def config(ctx):
        calls.append("config")

    def both(ctx):
        calls.append("both")

    add_task(bundle, packages, tags={"packages"})
    add_task(bundle, config, tags={"config"})
    add_task(bundle, both, tags={"packages", "config"})

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


def test_deferred_side_effect_runs_at_end_of_bundle_and_before_next_bundle(tmp_path):
    calls = []
    bundle = Bundle("base")

    def refresh(ctx):
        calls.append("refresh")

    effect = side_effect("Refresh")(refresh)

    def install(ctx):
        calls.append("install")
        ctx.changed(summary="+pkg")
        ctx.side_effect(effect)

    add_task(bundle, install, name="Install")
    add_task(bundle, lambda ctx: calls.append("last"), name="Last")

    code, text, _ = run_plan(tmp_path, [bundle])
    assert code == 0
    assert calls == ["install", "last", "refresh"]
    assert "↳ Refresh" in text

    calls.clear()
    base = Bundle("base")
    other = Bundle("other", depends_on=[base])

    def refresh_again(ctx):
        calls.append("refresh")

    effect = side_effect("Refresh")(refresh_again)

    def install(ctx):
        calls.append("install")
        ctx.changed(summary="+pkg")
        ctx.side_effect(effect)

    add_task(base, install, name="Install")
    add_task(other, lambda ctx: calls.append("other"), name="Other")

    code, text, _ = run_plan(tmp_path, [other])
    assert code == 0
    assert calls == ["install", "refresh", "other"]
    assert text.index("↳ Refresh") < text.index("bundle: other")


def test_immediate_side_effect_runs_before_and_affects_next_task(tmp_path):
    calls = []
    seen = []
    bundle = Bundle("base")

    def reload(ctx):
        calls.append("reload")
        ctx.set("ready", "yes")

    effect = side_effect("Reload", one_shot=False)(reload)

    def configure(ctx):
        calls.append("configure")
        ctx.changed(summary="+conf")
        ctx.side_effect(effect)

    def check(ctx):
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
        calls = []
        bundle = Bundle("base")

        def refresh(ctx, calls=calls):
            calls.append("refresh")

        effect = side_effect("Refresh")(refresh)

        def install(ctx, skip=skip, effect=effect):
            ctx.side_effect(effect)
            if skip:
                ctx.skip("nothing to do")

        add_task(bundle, install, name="Install")
        code, _, _ = run_plan(tmp_path, [bundle])
        assert code == 0
        assert calls == []


def test_failures_and_side_effects(tmp_path):
    # A task that fails before returning runs none of its side effects.
    calls = []
    bundle = Bundle("base")

    def reload(ctx):
        calls.append("reload")

    def refresh(ctx):
        calls.append("refresh")

    immediate = side_effect("Reload", one_shot=False)(reload)
    deferred = side_effect("Refresh")(refresh)

    def install(ctx):
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

    def reload_again(ctx):
        calls.append("reload")

    def refresh_again(ctx):
        calls.append("refresh")

    immediate = side_effect("Reload", one_shot=False)(reload_again)
    deferred = side_effect("Refresh")(refresh_again)

    def configure(ctx):
        ctx.changed(summary="+conf")
        ctx.side_effect(immediate)
        ctx.side_effect(deferred)

    add_task(bundle, configure, name="Configure")
    add_task(bundle, lambda ctx: (_ for _ in ()).throw(TaskFailed("boom")), name="Fail")
    code, _, _ = run_plan(tmp_path, [bundle])
    assert code == 1
    assert calls == ["reload"]


def test_one_shot_side_effect_runs_once_per_bundle(tmp_path):
    calls = []
    base = Bundle("base")
    other = Bundle("other", depends_on=[base])

    def refresh(ctx):
        calls.append("refresh")

    effect = side_effect("Refresh")(refresh)

    def register(ctx):
        ctx.changed(summary="+pkg")
        ctx.side_effect(effect)

    add_task(base, register, name="Base install")
    add_task(other, register, name="Other install")

    code, _, _ = run_plan(tmp_path, [other])
    assert code == 0
    assert calls == ["refresh", "refresh"]


def test_one_shot_side_effect_runs_once_per_bundle_registration(tmp_path):
    calls = []
    bundle = Bundle("base")

    def refresh(ctx):
        calls.append("refresh")

    effect = side_effect("Refresh")(refresh)

    def install(ctx):
        ctx.changed(summary="+pkg")
        ctx.side_effect(effect)
        ctx.side_effect(effect)

    add_task(bundle, install, name="Install")

    code, _, _ = run_plan(tmp_path, [bundle])
    assert code == 0
    assert calls == ["refresh"]


def test_skipped_one_shot_side_effect_can_run_again_in_the_same_bundle(tmp_path):
    calls = []
    bundle = Bundle("base")

    def refresh(ctx):
        calls.append("refresh")
        if len(calls) == 1:
            ctx.skip("not now")

    effect = side_effect("Refresh")(refresh)

    def first(ctx):
        ctx.changed(summary="+first")
        ctx.side_effect(effect)

    def second(ctx):
        ctx.changed(summary="+second")
        ctx.side_effect(effect)

    add_task(bundle, first, name="First")
    add_task(bundle, second, name="Second")

    code, _, _ = run_plan(tmp_path, [bundle])
    assert code == 0
    assert calls == ["refresh", "refresh"]


def test_immediate_side_effect_runs_per_registration_across_bundles(tmp_path):
    calls = []
    base = Bundle("base")
    other = Bundle("other", depends_on=[base])

    def reload(ctx):
        calls.append("reload")

    effect = side_effect("Reload", one_shot=False)(reload)

    def register(ctx):
        ctx.changed(summary="+pkg")
        ctx.side_effect(effect)

    add_task(base, register, name="Base install")
    add_task(other, register, name="Other install")

    code, _, _ = run_plan(tmp_path, [other])
    assert code == 0
    assert calls == ["reload", "reload"]


def test_immediate_side_effect_runs_for_every_registration(tmp_path):
    calls = []
    bundle = Bundle("base")

    def reload(ctx):
        calls.append("reload")

    effect = side_effect("Reload", one_shot=False)(reload)

    def configure(ctx):
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

    def fail(ctx):
        raise TaskFailed("reload failed")

    effect = side_effect("Reload", one_shot=False)(fail)

    def configure(ctx):
        ctx.changed(summary="+conf")
        ctx.side_effect(effect)

    add_task(bundle, configure, name="Configure")
    add_task(bundle, lambda ctx: calls.append("next"), name="Next")

    code, text, _ = run_plan(tmp_path, [bundle])
    assert code == 1
    assert calls == []
    assert "error: reload failed" in text
    assert 'Aborted after failure in "Reload". 1 task(s) not run.' in text

    # A deferred failure aborts at the end of the bundle, after later tasks run.
    calls = []
    base = Bundle("base")
    other = Bundle("other", depends_on=[base])

    def fail(ctx):
        raise TaskFailed("refresh failed")

    effect = side_effect("Refresh")(fail)

    def register(ctx):
        ctx.changed(summary="+pkg")
        ctx.side_effect(effect)

    add_task(base, register, name="Install")
    add_task(base, lambda ctx: calls.append("last"), name="Last")
    add_task(other, lambda ctx: calls.append("other"), name="Other")

    code, text, _ = run_plan(tmp_path, [other])
    assert code == 1
    assert calls == ["last"]
    assert "error: refresh failed" in text
    assert 'Aborted after failure in "Refresh". 1 task(s) not run.' in text


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

    def refresh_packages(ctx):
        calls.append("packages")

    def reload_config(ctx):
        calls.append("config")

    packages_effect = side_effect("Refresh")(refresh_packages)
    config_effect = side_effect("Reload", one_shot=False)(reload_config)

    def packages(ctx):
        ctx.changed(summary="+pkg")
        ctx.side_effect(packages_effect)

    def configure(ctx):
        ctx.changed(summary="+conf")
        ctx.side_effect(config_effect)

    add_task(bundle, packages, name="Packages", tags={"packages"})
    add_task(bundle, configure, name="Configure", tags={"config"})

    code, _, _ = run_plan(tmp_path, [bundle], tags={"packages"})

    assert code == 0
    assert calls == ["packages"]


def test_side_effects_use_the_registering_bundle_context(tmp_path):
    seen = []
    bundle = Bundle("base", path=tmp_path)

    def refresh(ctx):
        seen.append((ctx.bundle.name, ctx.bundle_path, ctx.resolve_source("marker")))

    effect = side_effect("Refresh")(refresh)

    def install(ctx):
        ctx.changed(summary="+pkg")
        ctx.side_effect(effect)

    add_task(bundle, install, name="Install")

    code, _, _ = run_plan(tmp_path, [bundle])

    assert code == 0
    assert seen == [("base", tmp_path, tmp_path / "files" / "marker")]


def test_bundle_vars_are_scoped_per_bundle(tmp_path):
    seen = []
    base = Bundle("base")
    other = Bundle("other", depends_on=[base])

    def show_base(ctx):
        seen.append(("base", ctx.vars.VALUE, ctx.vars.get("BASE_ONLY")))

    def show_other(ctx):
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


def test_side_effects_use_bundle_scoped_vars(tmp_path):
    seen = []
    bundle = Bundle("base", path=tmp_path)

    def refresh(ctx):
        seen.append(ctx.vars.VALUE)

    effect = side_effect("Refresh")(refresh)

    def install(ctx):
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
    assert seen == ["base"]


def test_side_effect_results_are_accessible_and_stored(tmp_path):
    seen = []
    bundle = Bundle("base")

    def refresh(ctx):
        seen.append(ctx.result(check).value)
        return "done"

    effect = side_effect("Refresh")(refresh)

    def check(ctx):
        return "release-1"

    def install(ctx):
        ctx.changed(summary="+pkg")
        ctx.side_effect(effect)

    add_task(bundle, check, name="Check")
    add_task(bundle, install, name="Install")

    code, _, context = run_plan(tmp_path, [bundle])
    assert code == 0
    assert seen == ["release-1"]
    assert context.run_state.bundles["base"].results["base.refresh"].value == "done"


def test_side_effect_results_are_bundle_local(tmp_path):
    seen = []
    base = Bundle("base")
    other = Bundle("other", depends_on=[base])

    def refresh(ctx):
        return "base"

    effect = side_effect("Refresh")(refresh)

    def register(ctx):
        ctx.changed(summary="+pkg")
        ctx.side_effect(effect)

    def check(ctx):
        try:
            ctx.result(refresh)
        except TaskFailed as exc:
            seen.append(str(exc))

    add_task(base, register, name="Install")
    add_task(other, check, name="Check")

    code, _, _ = run_plan(tmp_path, [other])

    assert code == 0
    assert seen == ["no result for task: refresh"]


def test_nested_side_effect_registration_fails(tmp_path):
    bundle = Bundle("base")
    inner = side_effect("Inner")(lambda ctx: None)

    def outer(ctx):
        ctx.side_effect(inner)

    outer_effect = side_effect("Outer", one_shot=False)(outer)

    def install(ctx):
        ctx.changed(summary="+pkg")
        ctx.side_effect(outer_effect)

    add_task(bundle, install, name="Install")

    code, text, _ = run_plan(tmp_path, [bundle])

    assert code == 1
    assert "side effects cannot register other side effects" in text
