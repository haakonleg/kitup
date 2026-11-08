from kitup import task
from kitup.modules import gsettings


@task(tags={"desktop"})
def use_dark_theme(ctx):
    """Use the dark GNOME color scheme."""
    gsettings.key(
        ctx,
        "org.gnome.desktop.interface",
        "color-scheme",
        ctx.vars.GNOME_COLOR_SCHEME,
    )
