from kitup.modules import template
from kitup.registry import task


@task("Harden SSH", tags={"security"})
def harden_ssh(ctx):
    template.file(
        ctx,
        "sshd.conf.j2",
        "/etc/ssh/sshd_config.d/99-kitup.conf",
        mode=0o644,
        sudo=True,
    )
