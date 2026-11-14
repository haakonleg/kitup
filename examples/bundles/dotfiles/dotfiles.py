from pathlib import Path

from kitup import ctx, task
from kitup.modules import files


@task(tags={"dotfiles"})
def install_dotfiles():
    """Install shell and editor dotfiles."""
    _copy_dotfiles(ctx.bundle.resource_path("files/dotfiles"), Path.home())


def _copy_dotfiles(source: Path, target: Path) -> None:
    for entry in sorted(source.iterdir(), key=lambda item: item.name):
        if entry.name == "README.md" or entry.suffix == ".example":
            continue

        destination = target / entry.name
        if entry.is_symlink():
            files.symlink(entry.readlink(), destination)
        elif entry.is_dir():
            files.directory(destination)
            _copy_dotfiles(entry, destination)
        else:
            files.copy(entry, destination)
