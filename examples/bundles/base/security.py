from kitup.modules import files
from kitup.registry import task


@task("Harden SSH", tags={"security"})
def harden_ssh(ctx):
    content = ctx.bundle.read_resource("files/sshd.conf").format(
        port=ctx.config.SSH_PORT
    )
    files.file(
        ctx,
        "/etc/ssh/sshd_config.d/99-kitup.conf",
        content=content,
        mode=0o644,
        sudo=True,
    )
