from kitup.modules import dnf

from . import bundle


@bundle.task("Install base packages", tags={"packages"})
def install_base_packages(ctx):
    dnf.packages(ctx, ctx.config.PACKAGES)
