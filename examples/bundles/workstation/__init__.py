from bundles import base

from kitup.registry import Bundle

from . import desktop, flatpak

Bundle(
    "Workstation",
    "Flatpak applications and desktop settings",
    depends_on=[base],
    modules=[desktop, flatpak],
)
