from pathlib import Path

import base_profile
from bundles import dotfiles, workstation

from kitup import profile

profile.extend(
    base_profile,
    bundles=[workstation, dotfiles],
    config=[Path("workstation.py")],
    description="Fedora workstation",
)
