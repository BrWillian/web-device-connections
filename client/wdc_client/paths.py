"""Where the relay is allowed to read and write on this device.

The relay is trusted to route bytes, not to choose filesystem locations. Before
this module every transfer took the path verbatim: a `target_path` of
`/etc/cron.d/x`, or `../../.ssh/authorized_keys`, landed exactly there with the
agent's own privileges, and a download could ask for any file the user could
read. Both directions now have to resolve inside one of the configured roots.
"""

import os


class PathNotAllowed(Exception):
    """Raised when a resolved path escapes every allowed root."""


def _is_within(path: str, root: str) -> bool:
    """True when `path` is `root` itself or sits underneath it.

    A plain `startswith` would accept `/home/device-backup` for a root of
    `/home/device`, so the separator is part of the comparison.
    """
    if path == root:
        return True
    return path.startswith(root.rstrip(os.sep) + os.sep)


def check_allowed(path: str, roots) -> str:
    """Return the fully resolved `path`, or raise if it is outside `roots`.

    `realpath` is what makes this hold up: it collapses `..`, expands symlinks
    and normalises the result, so neither traversal nor a symlink planted in an
    allowed directory can point the transfer somewhere else. It works on paths
    that do not exist yet, which is the usual case for an upload destination.
    """
    resolved = os.path.realpath(os.path.abspath(os.path.expanduser(path)))
    roots = list(roots)
    if not roots:
        # No roots configured is a misconfiguration, not a permission grant.
        raise PathNotAllowed(f"{resolved}: no allowed roots are configured")
    if any(_is_within(resolved, root) for root in roots):
        return resolved
    raise PathNotAllowed(f"{resolved}: outside the allowed roots")


def safe_filename(filename: str) -> str:
    """Reduce a relay-supplied name to a single harmless path component.

    The name arrives from the browser and is never a directory: `../../x` and
    `/etc/passwd` both collapse to `x` and `passwd`. The root check still runs
    afterwards; this only stops the name itself from steering the destination.
    """
    base = os.path.basename((filename or "").strip().replace("\\", "/").rstrip("/"))
    if not base or base in (".", ".."):
        return "received.bin"
    return base


def resolve_upload_destination(filename: str, target_path: str, roots, home=None) -> str:
    """Absolute path to write an incoming file to.

    `target_path` may be a directory (trailing separator, or one that already
    exists) or a full destination path. `~` is expanded on both sides: without
    it a target of `~/Downloads/` — exactly what the dashboard suggests — used
    to create a directory literally named `~` next to the agent.
    """
    name = safe_filename(filename)
    home = home or os.path.expanduser("~")

    if target_path:
        target = os.path.expanduser(target_path.strip())
        if target.endswith(os.sep) or os.path.isdir(target):
            dest = os.path.join(target, name)
        else:
            dest = target
    else:
        dest = os.path.join(home, name)

    return check_allowed(dest, roots)


def resolve_download_source(path: str, roots) -> str:
    """Absolute path to read an outgoing file from, checked against `roots`."""
    return check_allowed(path, roots)
