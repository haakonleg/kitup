from kitup import task
from kitup.modules import systemd


@task(tags={"services"})
def enable_firewall(ctx):
    """Enable and start firewalld."""
    systemd.unit(ctx, "firewalld.service", enabled=True, state="started")


@task(tags={"services"})
def enable_time_sync(ctx):
    """Enable and start time synchronization."""
    systemd.unit(ctx, "chronyd.service", enabled=True, state="started")
