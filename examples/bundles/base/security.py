from kitup import task
from kitup.modules import template


@task(tags={"security"})
def harden_ssh(ctx):
    """Write the hardened SSH daemon configuration."""
    template.file(
        ctx,
        "sshd.conf.j2",
        "/etc/ssh/sshd_config.d/99-kitup.conf",
        mode=0o644,
        sudo=True,
    )
