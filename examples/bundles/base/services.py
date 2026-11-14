from kitup import task
from kitup.modules import systemd


@task(tags={"services"})
def enable_firewall():
    """Enable and start firewalld."""
    systemd.unit("firewalld.service", enabled=True, state="started")


@task(tags={"services"})
def enable_time_sync():
    """Enable and start time synchronization."""
    systemd.unit("chronyd.service", enabled=True, state="started")
