from types import SimpleNamespace

import pytest

from kitup.host_facts import collect_facts, collect_mounts, parse_mountinfo, parse_os_release


def test_parse_os_release_handles_quotes_comments_and_blanks():
    text = """
    # a comment
    NAME="Test Linux"
    ID=testos
    VERSION_ID="1"
    EMPTY=
    BAD LINE
    """

    values = parse_os_release(text)

    assert values == {
        "NAME": "Test Linux",
        "ID": "testos",
        "VERSION_ID": "1",
        "EMPTY": "",
    }


def test_parse_mountinfo_and_collect_mounts(tmp_path):
    text = (
        "36 35 98:0 / / rw,relatime shared:1 - ext4 /dev/nvme0n1p1 rw,seclabel\n"
        "37 35 0:32 / /run/user/1000 rw,nosuid,nodev shared:2 "
        "- tmpfs tmpfs rw,size=806544k\n"
        "38 35 0:40 / /media/My\\040Disk ro,nosuid shared:3 - btrfs "
        "/dev/sdb1 ro,compress=zstd:1,subvolid=257,subvol=/data\n"
    )

    mounts = parse_mountinfo(text)

    assert mounts[0] == {
        "mount": "/",
        "device": "/dev/nvme0n1p1",
        "fstype": "ext4",
        "options": ("rw", "relatime", "seclabel"),
    }
    assert mounts[1]["options"] == ("rw", "nosuid", "nodev", "size=806544k")
    assert mounts[2] == {
        "mount": "/media/My Disk",
        "device": "/dev/sdb1",
        "fstype": "btrfs",
        "options": ("ro", "nosuid", "compress=zstd:1", "subvolid=257", "subvol=/data"),
    }

    path = tmp_path / "mountinfo"
    path.write_text(
        "36 35 98:0 / / rw,relatime - ext4 /dev/nvme0n1p1 rw\n37 35 0:32 / /run rw,nosuid - tmpfs tmpfs rw\n",
        encoding="utf-8",
    )
    sizes = {
        "/": SimpleNamespace(
            f_frsize=4096,
            f_bsize=4096,
            f_blocks=1000,
            f_bfree=250,
        )
    }

    def fake_statvfs(mountpoint):
        if mountpoint not in sizes:
            raise FileNotFoundError(mountpoint)
        return sizes[mountpoint]

    collected = collect_mounts(path, statvfs=fake_statvfs)

    assert len(collected) == 1
    assert collected[0]["mount"] == "/"
    assert collected[0].mount == "/"
    assert collected[0]["device"] == "/dev/nvme0n1p1"
    assert collected[0].device == "/dev/nvme0n1p1"
    assert collected[0]["fstype"] == "ext4"
    assert collected[0].fstype == "ext4"
    assert collected[0]["options"] == ("rw", "relatime")
    assert collected[0].options == ("rw", "relatime")
    assert collected[0]["bytes_total"] == 4096 * 1000
    assert collected[0].bytes_total == 4096 * 1000
    assert collected[0]["bytes_free"] == 4096 * 250
    assert collected[0].bytes_free == 4096 * 250
    assert collected[0]["bytes_used"] == 4096 * 750
    assert collected[0].bytes_used == 4096 * 750
    assert collected[0]["bytes_total"] == collected[0]["bytes_used"] + collected[0]["bytes_free"]

    with pytest.raises(TypeError):
        collected[0]["bytes_free"] = 0


def test_collect_facts_uses_process_user(tmp_path, monkeypatch):
    monkeypatch.setattr("kitup.host_facts.os.getuid", lambda: 1000)
    monkeypatch.setattr("kitup.host_facts.pwd.getpwuid", lambda uid: SimpleNamespace(pw_name="alice"))

    facts = collect_facts(tmp_path / "missing-os-release")

    assert facts["username"] == "alice"
    assert facts.username == "alice"


def test_collect_facts_omits_unresolvable_username(tmp_path, monkeypatch):
    def missing(uid):
        raise KeyError(uid)

    monkeypatch.setattr("kitup.host_facts.pwd.getpwuid", missing)

    facts = collect_facts(tmp_path / "missing-os-release")

    assert "username" not in facts


def test_collect_facts_are_read_only_and_missing_sources_are_omitted(tmp_path):
    path = tmp_path / "os-release"
    path.write_text(
        'NAME="Test Linux"\nID=testos\nVERSION_ID=1\nPRETTY_NAME="Test Linux 1 (Workstation Edition)"\n',
        encoding="utf-8",
    )

    facts = collect_facts(path)

    assert facts["distro_id"] == "testos"
    assert facts.distro_id == "testos"
    assert facts["version_id"] == "1"
    assert facts.version_id == "1"
    assert facts["os_release"]["PRETTY_NAME"] == "Test Linux 1 (Workstation Edition)"
    assert facts.os_release.PRETTY_NAME == "Test Linux 1 (Workstation Edition)"
    assert facts["os_release"].ID == "testos"
    assert facts["hostname"]
    assert facts.hostname
    assert facts["arch"]
    assert facts.arch
    assert facts["kernel"]
    assert facts.kernel

    with pytest.raises(AttributeError):
        facts.hostname = "other"
    with pytest.raises(TypeError):
        facts["hostname"] = "other"
    with pytest.raises(TypeError):
        del facts["hostname"]

    assert collect_mounts(tmp_path / "missing-mountinfo") == ()

    missing = collect_facts(tmp_path / "missing-os-release")
    assert "os_release" not in missing
    assert "distro_id" not in missing
    assert "version_id" not in missing
    assert "hostname" in missing
