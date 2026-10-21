"""Resolve and persist Admin username/password credentials."""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from acquisition.auth.admin import hash_password, verify_password
from acquisition.config import Settings, get_settings
from acquisition.models.admin_auth import AdminAuthSettings

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9._-]{3,64}$")


def normalize_username(username: str) -> str:
    return (username or "").strip()


def validate_username(username: str) -> str | None:
    cleaned = normalize_username(username)
    if not cleaned:
        return "Username is required"
    if not _USERNAME_RE.match(cleaned):
        return "Username must be 3–64 characters (letters, numbers, . _ -)"
    return None


def validate_new_password(password: str) -> str | None:
    if not password or len(password) < 8:
        return "Password must be at least 8 characters"
    if len(password) > 128:
        return "Password is too long"
    return None


def get_db_credentials(db: Session) -> AdminAuthSettings | None:
    return db.get(AdminAuthSettings, 1)


def credentials_configured(db: Session, *, settings: Settings | None = None) -> bool:
    cfg = settings or get_settings()
    if (cfg.admin_password_hash or "").strip() and (cfg.admin_username or "").strip():
        return True
    row = get_db_credentials(db)
    return bool(row and row.username and row.password_hash)


def verify_admin_login(
    db: Session,
    *,
    username: str,
    password: str,
    settings: Settings | None = None,
) -> bool:
    cfg = settings or get_settings()
    user = normalize_username(username)
    if not user or not password:
        return False

    env_user = normalize_username(cfg.admin_username)
    env_hash = (cfg.admin_password_hash or "").strip()
    if env_user and env_hash:
        return user.lower() == env_user.lower() and verify_password(password, env_hash)

    row = get_db_credentials(db)
    if row is None:
        return False
    return user.lower() == row.username.lower() and verify_password(password, row.password_hash)


def verify_acquisition_owner_login(
    *,
    username: str,
    password: str,
    settings: Settings | None = None,
) -> bool:
    """Env-provisioned acquisition owner (read-only). No hardcoded client secrets."""
    cfg = settings or get_settings()
    user = normalize_username(username)
    owner_user = normalize_username(cfg.acquisition_owner_username)
    owner_hash = (cfg.acquisition_owner_password_hash or "").strip()
    if not user or not password or not owner_user or not owner_hash:
        return False
    return user.lower() == owner_user.lower() and verify_password(password, owner_hash)


def resolve_login_role(
    db: Session,
    *,
    username: str,
    password: str,
    settings: Settings | None = None,
) -> str | None:
    """Return 'admin', 'acquisition_owner', or None. Admin credentials win on conflict."""
    cfg = settings or get_settings()
    if verify_admin_login(db, username=username, password=password, settings=cfg):
        return "admin"
    if verify_acquisition_owner_login(username=username, password=password, settings=cfg):
        return "acquisition_owner"
    return None


def create_admin_account(
    db: Session,
    *,
    username: str,
    password: str,
) -> AdminAuthSettings:
    """First-time Admin bootstrap. Raises ValueError on validation / already configured."""
    if credentials_configured(db):
        raise ValueError("Admin account already exists")
    user_err = validate_username(username)
    if user_err:
        raise ValueError(user_err)
    pass_err = validate_new_password(password)
    if pass_err:
        raise ValueError(pass_err)

    row = AdminAuthSettings(
        id=1,
        username=normalize_username(username),
        password_hash=hash_password(password),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
