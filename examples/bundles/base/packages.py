from kitup.modules import dnf
from kitup.registry import task


@task("Install base packages", tags={"packages"})
def install_base_packages(ctx):
    dnf.packages(ctx, ctx.config.PACKAGES)
