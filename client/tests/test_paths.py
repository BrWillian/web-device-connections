"""The device decides where the relay may read and write, not the other way round."""

import os

import pytest

from wdc_client.paths import (
    PathNotAllowed,
    resolve_download_source,
    resolve_upload_destination,
    safe_filename,
)


def test_plain_filename_lands_in_the_home_dir(sandbox, roots):
    dest = resolve_upload_destination("report.txt", None, roots, home=str(sandbox))
    assert dest == os.path.join(os.path.realpath(str(sandbox)), "report.txt")


def test_directory_target_keeps_the_filename(sandbox, roots):
    (sandbox / "Downloads").mkdir()
    dest = resolve_upload_destination("a.bin", str(sandbox / "Downloads"), roots)
    assert dest.endswith(os.path.join("Downloads", "a.bin"))


def test_tilde_target_is_expanded(sandbox, roots, monkeypatch):
    """A literal "~" directory next to the agent was the old behaviour."""
    monkeypatch.setenv("HOME", str(sandbox))
    dest = resolve_upload_destination("a.bin", "~/", roots)
    assert dest == os.path.join(os.path.realpath(str(sandbox)), "a.bin")
    assert not os.path.exists("~")


def test_traversal_in_the_target_is_refused(sandbox, roots):
    with pytest.raises(PathNotAllowed):
        resolve_upload_destination("a.bin", str(sandbox / ".." / ".." / "etc"), roots)


def test_absolute_target_outside_the_roots_is_refused(roots):
    with pytest.raises(PathNotAllowed):
        resolve_upload_destination("evil", "/etc/cron.d/evil", roots)


def test_traversal_in_the_filename_is_flattened(sandbox, roots):
    dest = resolve_upload_destination("../../../etc/passwd", None, roots, home=str(sandbox))
    assert dest == os.path.join(os.path.realpath(str(sandbox)), "passwd")


def test_symlink_out_of_the_root_is_refused(sandbox, roots, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (sandbox / "escape").symlink_to(outside)
    with pytest.raises(PathNotAllowed):
        resolve_upload_destination("a.bin", str(sandbox / "escape") + os.sep, roots)


def test_a_sibling_prefix_is_not_inside_the_root(tmp_path):
    """/home/device-backup must not pass a root check for /home/device."""
    root = tmp_path / "device"
    root.mkdir()
    (tmp_path / "device-backup").mkdir()
    with pytest.raises(PathNotAllowed):
        resolve_download_source(str(tmp_path / "device-backup" / "x"), [os.path.realpath(str(root))])


def test_download_inside_the_root_is_allowed(sandbox, roots):
    target = sandbox / "log.txt"
    target.write_text("hi")
    assert resolve_download_source(str(target), roots) == os.path.realpath(str(target))


def test_no_roots_means_nothing_is_allowed(sandbox):
    """An empty ALLOWED_ROOTS is a misconfiguration, never a blanket grant."""
    with pytest.raises(PathNotAllowed):
        resolve_download_source(str(sandbox / "x"), [])


def test_root_itself_is_allowed_when_configured(sandbox):
    assert resolve_download_source("/etc/hostname", ["/"]) == os.path.realpath("/etc/hostname")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("a.txt", "a.txt"),
        ("../a.txt", "a.txt"),
        ("/etc/passwd", "passwd"),
        ("C:\\Windows\\evil.exe", "evil.exe"),
        ("", "received.bin"),
        ("..", "received.bin"),
        ("dir/", "dir"),
    ],
)
def test_safe_filename(raw, expected):
    assert safe_filename(raw) == expected
