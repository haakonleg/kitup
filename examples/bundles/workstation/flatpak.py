from kitup import task
from kitup.modules import flatpak


@task(tags={"flatpak"})
def add_flathub(ctx):
    """Add the Flathub remote."""
    flatpak.remote(ctx, ctx.vars.FLATPAK_REMOTE, ctx.vars.FLATPAK_URL)


@task(tags={"flatpak"})
def install_flatpak_apps(ctx):
    """Install the configured Flatpak applications."""
    flatpak.packages(
        ctx,
        ctx.vars.FLATPAK_APPLICATIONS,
        remote=ctx.vars.FLATPAK_REMOTE,
    )
