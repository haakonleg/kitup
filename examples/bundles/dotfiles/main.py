from kitup import bundle

bundle(
    "Dotfiles",
    "Shell and editor configuration",
    depends_on=["base"],
)
