from kitup import task, vars
from kitup.modules import gsettings


@task(tags={"desktop"})
def use_dark_theme():
    """Use the dark GNOME color scheme."""
    gsettings.key(
        "org.gnome.desktop.interface",
        "color-scheme",
        vars.GNOME_COLOR_SCHEME,
    )
