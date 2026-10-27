from pathlib import Path

from bundles import base

from kitup import profile

profile(
    bundles=[base],
    config=[Path("config.py")],
    description="Fedora base system",
)
