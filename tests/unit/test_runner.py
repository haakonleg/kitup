from __future__ import annotations

from io import StringIO

from kitup.errors import PrivilegeError, TaskFailed
from kitup.output import Output
from kitup.registry import Bundle
from kitup.resolver import resolve
from kitup.results import Status
from kitup.runner import Runner

from .helpers import FakeCommandRunner, FakeSudo, add_task, make_context


def make_output() -> Output:
    return Output(StringIO(), no_color=True)

def run_plan(tmp_path, bundles, *, tags=(), skip_tags=()):
    context = make_context(
        tmp_path,
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
    add_task(changed, lambda ctx: ctx.changed({"x": 1}, "+x"))
    add_task(skipped, lambda ctx: ctx.skip("not needed"))

    code, text, _ = run_plan(tmp_path, [ok, changed, skipped])

    assert code == 0
    assert "ok" in text
    assert "changed  +x" in text
    assert "skipped  not needed" in text
    assert "Summary: 1 changed, 1 ok, 1 skipped, 0 failed, 0 not run" in text

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
        ctx.changed({"step": "prepared"}, "prepared")
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

