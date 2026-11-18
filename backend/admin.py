"""Create or rotate a user using a prompted password."""

import argparse
import getpass

from pydantic import TypeAdapter
from sqlalchemy.dialects.postgresql import insert

from backend.auth import PASSWORDS, ROLES
from backend.database import make_sessions
from backend.models import User
from backend.schemas import Identifier


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("username")
    parser.add_argument("role", choices=ROLES)
    args = parser.parse_args()
    username = TypeAdapter(Identifier).validate_python(args.username)
    password = getpass.getpass("Password (minimum 12 characters): ")
    if len(password) < 12 or len(password) > 256 or password != getpass.getpass("Repeat password: "):
        parser.error("Password length or confirmation is invalid.")
    digest = PASSWORDS.hash(password)
    with make_sessions().begin() as db:
        db.execute(insert(User).values(username=username, role=args.role, password_hash=digest)
                   .on_conflict_do_update(index_elements=[User.username],
                                          set_={"role": args.role, "password_hash": digest}))
    print(f"Saved user {username} with role {args.role}.")


if __name__ == "__main__":
    main()
