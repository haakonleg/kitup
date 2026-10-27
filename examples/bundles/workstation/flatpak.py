from kitup.modules import flatpak
from kitup.registry import task


@task("Add Flathub", tags={"flatpak"})
def add_flathub(ctx):
    flatpak.remote(ctx, ctx.config.FLATPAK_REMOTE, ctx.config.FLATPAK_URL)


@task("Install Flatpak applications", tags={"flatpak"})
def install_flatpak_apps(ctx):
    flatpak.packages(
        ctx,
        ctx.config.FLATPAK_APPLICATIONS,
        remote=ctx.config.FLATPAK_REMOTE,
    )
