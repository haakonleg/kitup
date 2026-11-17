from kitup import task
from kitup.modules import files


@task(tags={"security"})
def harden_ssh():
    """Write the hardened SSH daemon configuration."""
    files.template(
        "sshd.conf.j2",
        "/etc/ssh/sshd_config.d/99-kitup.conf",
        mode=0o644,
        sudo=True,
    )
