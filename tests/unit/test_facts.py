from __future__ import annotations

from types import SimpleNamespace

from kitup.facts import collect_facts, collect_mounts, parse_mountinfo, parse_os_release


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


def test_parse_mountinfo_extracts_unescaped_mount_attributes():
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


def test_collect_mounts_adds_capacity_and_skips_unreadable_mounts(tmp_path):
    path = tmp_path / "mountinfo"
    path.write_text(
        "36 35 98:0 / / rw,relatime - ext4 /dev/nvme0n1p1 rw\n"
        "37 35 0:32 / /run rw,nosuid - tmpfs tmpfs rw\n",
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

    mounts = collect_mounts(path, statvfs=fake_statvfs)

    assert len(mounts) == 1
    assert mounts[0]["mount"] == "/"
    assert mounts[0]["device"] == "/dev/nvme0n1p1"
    assert mounts[0]["fstype"] == "ext4"
    assert mounts[0]["bytes_total"] == 4096 * 1000
    assert mounts[0]["bytes_free"] == 4096 * 250
    assert mounts[0]["bytes_used"] == 4096 * 750
    assert mounts[0]["bytes_total"] == mounts[0]["bytes_used"] + mounts[0]["bytes_free"]

    try:
        mounts[0]["bytes_free"] = 0
    except TypeError:
        pass
    else:
        raise AssertionError("mounts should be read-only")


def test_missing_mountinfo_omits_mounts(tmp_path):
    assert collect_mounts(tmp_path / "missing") == ()


def test_collect_facts_from_os_release(tmp_path):
    path = tmp_path / "os-release"
    path.write_text(
        'NAME="Test Linux"\nID=testos\nVERSION_ID=1\n'
        'PRETTY_NAME="Test Linux 1 (Workstation Edition)"\n',
        encoding="utf-8",
    )

    facts = collect_facts(path)

    assert facts["distro_id"] == "testos"
    assert facts["version_id"] == "1"
    assert facts["os_release"]["PRETTY_NAME"] == "Test Linux 1 (Workstation Edition)"
    assert facts["hostname"]
    assert facts["arch"]
    assert facts["kernel"]


def test_missing_os_release_omits_optional_facts(tmp_path):
    facts = collect_facts(tmp_path / "missing")

    assert "os_release" not in facts
    assert "distro_id" not in facts
    assert "version_id" not in facts
    assert "hostname" in facts


def test_facts_are_read_only(tmp_path):
    path = tmp_path / "os-release"
    path.write_text("ID=testos\nVERSION_ID=1\n", encoding="utf-8")
    facts = collect_facts(path)

    try:
        facts["hostname"] = "other"  # type: ignore[index]
    except TypeError:
        pass
    else:
        raise AssertionError("facts should be read-only")

    try:
        facts["os_release"]["ID"] = "other"  # type: ignore[index]
    except TypeError:
        pass
    else:
        raise AssertionError("nested os_release should be read-only")
