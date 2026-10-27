from kitup.modules import gsettings
from kitup.registry import task


@task("Use the dark GNOME theme", tags={"desktop"})
def use_dark_theme(ctx):
    gsettings.key(
        ctx,
        "org.gnome.desktop.interface",
        "color-scheme",
        ctx.config.GNOME_COLOR_SCHEME,
    )
