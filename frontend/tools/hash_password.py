"""Generate the bcrypt hash to put in ADMIN_PASSWORD_HASH.

    python -m tools.hash_password
    python -m tools.hash_password --password 'segredo'   # non-interactive

Reads from a prompt by default so the password does not land in shell history.
"""

import argparse
import getpass
import sys

from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--password",
        help="password to hash; omit to be prompted (preferred)",
    )
    args = parser.parse_args()

    if args.password:
        password = args.password
    else:
        password = getpass.getpass("Senha: ")
        if password != getpass.getpass("Confirme: "):
            print("As senhas não conferem.", file=sys.stderr)
            return 1

    if not password:
        print("Senha vazia.", file=sys.stderr)
        return 1

    print()
    print(f"ADMIN_PASSWORD_HASH={pwd_context.hash(password)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
