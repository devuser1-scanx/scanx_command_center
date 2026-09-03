from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.auth import CCRole, CCUser, CCUserRole


def normalize_email(email: str) -> str:
    return email.strip().lower()


def get_existing_user(db: Session, email: str) -> CCUser | None:
    return db.scalar(select(CCUser).where(CCUser.email == email))


def get_admin_role(db: Session) -> CCRole:
    role = db.scalar(select(CCRole).where(CCRole.code == "admin"))

    if role is None:
        raise RuntimeError("Admin role not found. Run 'alembic upgrade head' first.")

    return role


def user_has_admin_role(
    db: Session,
    *,
    user_id: int,
    role_id: int,
) -> bool:
    mapping = db.scalar(
        select(CCUserRole).where(
            CCUserRole.user_id == user_id,
            CCUserRole.role_id == role_id,
        )
    )

    return mapping is not None


# def validate_password(password: str) -> None:
#     if len(password) < 12:
#         raise ValueError(
#             "Password must contain at least 12 characters."
#         )

#     if not any(character.isupper() for character in password):
#         raise ValueError(
#             "Password must contain at least one uppercase letter."
#         )

#     if not any(character.islower() for character in password):
#         raise ValueError(
#             "Password must contain at least one lowercase letter."
#         )

#     if not any(character.isdigit() for character in password):
#         raise ValueError(
#             "Password must contain at least one number."
#         )

#     if not any(
#         not character.isalnum()
#         for character in password
#     ):
#         raise ValueError(
#             "Password must contain at least one special character."
#         )


def bootstrap_admin(
    *,
    first_name: str,
    last_name: str,
    email: str,
    phone: str | None,
    password: str,
) -> None:
    normalized_email = normalize_email(email)
    # validate_password(password) # As now hash_password() can validate the passowrd.

    with SessionLocal() as db:
        admin_role = get_admin_role(db)
        existing_user = get_existing_user(db, normalized_email)

        if existing_user is not None:
            if user_has_admin_role(
                db,
                user_id=existing_user.id,
                role_id=admin_role.id,
            ):
                raise RuntimeError(
                    f"User '{normalized_email}' already exists and already has the admin role."
                )

            existing_user.password_hash = hash_password(password)
            existing_user.is_active = True
            existing_user.must_change_password = True
            existing_user.failed_login_attempts = 0
            existing_user.locked_until = None

            db.add(
                CCUserRole(
                    user_id=existing_user.id,
                    role_id=admin_role.id,
                    assigned_by_user_id=None,
                )
            )

            db.commit()

            print(f"Existing user '{normalized_email}' was updated and assigned the admin role.")
            return

        user = CCUser(
            first_name=first_name.strip(),
            last_name=last_name.strip(),
            email=normalized_email,
            phone=phone.strip() if phone else None,
            password_hash=hash_password(password),
            is_active=True,
            must_change_password=True,
            failed_login_attempts=0,
        )

        db.add(user)
        db.flush()

        db.add(
            CCUserRole(
                user_id=user.id,
                role_id=admin_role.id,
                assigned_by_user_id=None,
            )
        )

        db.commit()

        print(f"Admin user '{normalized_email}' created successfully.")
        print(f"User ID: {user.id}")
        print("The user must change the password after first login.")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create the first ScanX Command Center admin user."
    )

    parser.add_argument(
        "--first-name",
        required=True,
        help="Admin first name.",
    )

    parser.add_argument(
        "--last-name",
        required=True,
        help="Admin last name.",
    )

    parser.add_argument(
        "--email",
        required=True,
        help="Admin email address.",
    )

    parser.add_argument(
        "--phone",
        required=False,
        default=None,
        help="Optional admin phone number.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_arguments()

    password = getpass.getpass("Enter initial admin password: ")

    password_confirmation = getpass.getpass("Confirm initial admin password: ")

    if password != password_confirmation:
        print(
            "Passwords do not match.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    try:
        bootstrap_admin(
            first_name=args.first_name,
            last_name=args.last_name,
            email=args.email,
            phone=args.phone,
            password=password,
        )
    except (ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
