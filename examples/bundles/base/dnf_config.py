from kitup.modules import files

from . import bundle


@bundle.task("Configure DNF", needs_sudo=True, tags={"config"})
def configure_dnf(ctx):
    files.file(
        ctx,
        ctx.config.DNF_CONF,
        content=bundle.read_resource("resources/dnf.conf"),
        mode=0o644,
    )
