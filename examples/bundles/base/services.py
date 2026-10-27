from kitup.modules import systemd
from kitup.registry import task


@task("Enable and start firewalld", tags={"services"})
def enable_firewall(ctx):
    systemd.unit(ctx, "firewalld.service", enabled=True, state="started")


@task("Enable and start time synchronization", tags={"services"})
def enable_time_sync(ctx):
    systemd.unit(ctx, "chronyd.service", enabled=True, state="started")
