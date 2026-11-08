from kitup import task
from kitup.modules import dnf


@task(tags={"packages"})
def install_base_packages(ctx):
    """Install the base package set."""
    dnf.packages(ctx, ctx.vars.PACKAGES)
