from kitup import task, vars
from kitup.modules import flatpak


@task(tags={"flatpak"})
def add_flathub():
    """Add the Flathub remote."""
    flatpak.remote(vars.FLATPAK_REMOTE, vars.FLATPAK_URL)


@task(tags={"flatpak"})
def install_flatpak_apps():
    """Install the configured Flatpak applications."""
    flatpak.packages(
        vars.FLATPAK_APPLICATIONS,
        remote=vars.FLATPAK_REMOTE,
    )
