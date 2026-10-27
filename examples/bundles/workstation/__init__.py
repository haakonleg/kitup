from bundles import base

from kitup.registry import Bundle

Bundle("Workstation", "Flatpak applications and desktop settings", depends_on=[base])

from . import desktop, flatpak  # noqa: E402,F401
