from kitup import bundle

bundle(
    "Workstation",
    "Flatpak applications and desktop settings",
    depends_on=["base"],
)
