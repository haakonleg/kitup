from pathlib import Path

from kitup.modules import files

from . import bundle


@bundle.task("Install dotfiles", tags={"dotfiles"})
def install_dotfiles(ctx):
    _copy_dotfiles(ctx, bundle.resource_path("resources/dotfiles"), Path.home())


def _copy_dotfiles(ctx, source: Path, target: Path) -> None:
    for entry in sorted(source.iterdir(), key=lambda item: item.name):
        if entry.name == "README.md" or entry.suffix == ".example":
            continue

        destination = target / entry.name
        if entry.is_symlink():
            files.symlink(ctx, entry.readlink(), destination)
        elif entry.is_dir():
            files.directory(ctx, destination)
            _copy_dotfiles(ctx, entry, destination)
        else:
            files.copy(ctx, entry, destination)
