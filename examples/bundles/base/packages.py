from kitup import task, vars
from kitup.modules import dnf


@task(tags={"packages"})
def install_base_packages():
    """Install the base package set."""
    dnf.packages(vars.PACKAGES)
