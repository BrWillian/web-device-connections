"""Print the token to provision on a device.

    python -m tools.mint_device_token device-01
    python -m tools.mint_device_token device-01 device-02 --env

Tokens are derived from DEVICE_MASTER_SECRET, so nothing is stored server-side and
rotating the master secret invalidates the whole fleet at once.
"""

import argparse
import sys

from app.core.config import settings
from app.core.security import DeviceAuthUnavailable, mint_device_token


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("device_ids", nargs="+", help="one or more device ids")
    parser.add_argument(
        "--env",
        action="store_true",
        help="print as .env lines ready to paste on the device",
    )
    args = parser.parse_args()

    if not settings.device_master_secret:
        print(
            "DEVICE_MASTER_SECRET is not set.\n"
            "Generate one with:  python -c \"import secrets; print(secrets.token_urlsafe(32))\"\n"
            "then put it in server/.env before minting tokens.",
            file=sys.stderr,
        )
        return 1

    for device_id in args.device_ids:
        try:
            token = mint_device_token(device_id)
        except DeviceAuthUnavailable as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if args.env:
            print(f"# {device_id}")
            print(f"DEVICE_ID={device_id}")
            print(f"DEVICE_TOKEN={token}")
            print()
        else:
            print(f"{device_id}\t{token}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
