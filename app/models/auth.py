from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class CCUser(Base):
    __tablename__ = "cc_users"

    __table_args__ = (
        UniqueConstraint(
            "email",
            name="uq_cc_users_email",
        ),
        Index(
            "ix_cc_users_email",
            "email",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    email: Mapped[str] = mapped_column(
    String(320),
    nullable=False,
    )

    first_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    last_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    phone: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
    )

    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )

    is_email_verified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )

    must_change_password: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )

    failed_login_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    password_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "cc_users.id",
            name="fk_cc_users_created_by_user_id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    updated_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "cc_users.id",
            name="fk_cc_users_updated_by_user_id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    roles: Mapped[list[CCUserRole]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="CCUserRole.user_id",
    )

    clinic_access: Mapped[list[CCUserClinicAccess]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="CCUserClinicAccess.user_id",
    )

    sessions: Mapped[list[CCSession]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="CCSession.user_id",
    )

    password_reset_tokens: Mapped[list[CCPasswordResetToken]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="CCPasswordResetToken.user_id",
    )


class CCRole(Base):
    __tablename__ = "cc_roles"

    __table_args__ = (
        UniqueConstraint(
            "code",
            name="uq_cc_roles_code",
        ),
        Index(
            "ix_cc_roles_code",
            "code",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    code: Mapped[str] = mapped_column(
    String(50),
    nullable=False,
    )

    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    is_system_role: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    users: Mapped[list[CCUserRole]] = relationship(
        back_populates="role",
        cascade="all, delete-orphan",
    )

    permissions: Mapped[list[CCRolePermission]] = relationship(
        back_populates="role",
        cascade="all, delete-orphan",
    )


class CCPermission(Base):
    __tablename__ = "cc_permissions"

    __table_args__ = (
        UniqueConstraint(
            "code",
            name="uq_cc_permissions_code",
        ),
        Index(
            "ix_cc_permissions_code",
            "code",
            unique=True,
        ),
        Index(
            "ix_cc_permissions_module",
            "module",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    code: Mapped[str] = mapped_column(
    String(100),
    nullable=False,
    )

    module: Mapped[str] = mapped_column(
    String(50),
    nullable=False,
    )

    name: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    roles: Mapped[list[CCRolePermission]] = relationship(
        back_populates="permission",
        cascade="all, delete-orphan",
    )


class CCUserRole(Base):
    __tablename__ = "cc_user_roles"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "role_id",
            name="uq_cc_user_roles_user_role",
        ),
        Index(
            "ix_cc_user_roles_user_id",
            "user_id",
        ),
        Index(
            "ix_cc_user_roles_role_id",
            "role_id",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "cc_users.id",
            name="fk_cc_user_roles_user_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    role_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "cc_roles.id",
            name="fk_cc_user_roles_role_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    assigned_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "cc_users.id",
            name="fk_cc_user_roles_assigned_by_user_id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    user: Mapped[CCUser] = relationship(
        back_populates="roles",
        foreign_keys=[user_id],
    )

    role: Mapped[CCRole] = relationship(
        back_populates="users",
    )


class CCRolePermission(Base):
    __tablename__ = "cc_role_permissions"
    __table_args__ = (
        UniqueConstraint(
            "role_id",
            "permission_id",
            name="uq_cc_role_permissions_role_permission",
        ),
        Index(
            "ix_cc_role_permissions_role_id",
            "role_id",
        ),
        Index(
            "ix_cc_role_permissions_permission_id",
            "permission_id",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    role_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "cc_roles.id",
            name="fk_cc_role_permissions_role_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    permission_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "cc_permissions.id",
            name="fk_cc_role_permissions_permission_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    role: Mapped[CCRole] = relationship(
        back_populates="permissions",
    )

    permission: Mapped[CCPermission] = relationship(
        back_populates="roles",
    )


class CCUserClinicAccess(Base):
    __tablename__ = "cc_user_clinic_access"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "clinic_id",
            name="uq_cc_user_clinic_access_user_clinic",
        ),
        Index(
            "ix_cc_user_clinic_access_user_id",
            "user_id",
        ),
        Index(
            "ix_cc_user_clinic_access_clinic_id",
            "clinic_id",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "cc_users.id",
            name="fk_cc_user_clinic_access_user_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    clinic_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    is_primary: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )

    assigned_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "cc_users.id",
            name="fk_cc_user_clinic_access_assigned_by_user_id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    user: Mapped[CCUser] = relationship(
        back_populates="clinic_access",
        foreign_keys=[user_id],
    )


class CCSession(Base):
    __tablename__ = "cc_sessions"
    __table_args__ = (
        Index(
            "ix_cc_sessions_user_id",
            "user_id",
        ),
        Index(
            "ix_cc_sessions_refresh_token_hash",
            "refresh_token_hash",
        ),
        Index(
            "ix_cc_sessions_expires_at",
            "expires_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "cc_users.id",
            name="fk_cc_sessions_user_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    refresh_token_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
    )

    user_agent: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    ip_address: Mapped[str | None] = mapped_column(
        INET,
        nullable=True,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    user: Mapped[CCUser] = relationship(
        back_populates="sessions",
        foreign_keys=[user_id],
    )


class CCPasswordResetToken(Base):
    __tablename__ = "cc_password_reset_tokens"
    __table_args__ = (
        Index(
            "ix_cc_password_reset_tokens_user_id",
            "user_id",
        ),
        Index(
            "ix_cc_password_reset_tokens_token_hash",
            "token_hash",
        ),
        Index(
            "ix_cc_password_reset_tokens_expires_at",
            "expires_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "cc_users.id",
            name="fk_cc_password_reset_tokens_user_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    token_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    user: Mapped[CCUser] = relationship(
        back_populates="password_reset_tokens",
        foreign_keys=[user_id],
    )


class CCLoginAudit(Base):
    __tablename__ = "cc_login_audit"
    __table_args__ = (
        Index(
            "ix_cc_login_audit_user_id",
            "user_id",
        ),
        Index(
            "ix_cc_login_audit_email",
            "email",
        ),
        Index(
            "ix_cc_login_audit_created_at",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "cc_users.id",
            name="fk_cc_login_audit_user_id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    email: Mapped[str | None] = mapped_column(
        String(320),
        nullable=True,
    )

    success: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
    )

    failure_reason: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    ip_address: Mapped[str | None] = mapped_column(
        INET,
        nullable=True,
    )

    user_agent: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class CCUserActivityAudit(Base):
    __tablename__ = "cc_user_activity_audit"
    __table_args__ = (
        Index(
            "ix_cc_user_activity_audit_actor_user_id",
            "actor_user_id",
        ),
        Index(
            "ix_cc_user_activity_audit_target_user_id",
            "target_user_id",
        ),
        Index(
            "ix_cc_user_activity_audit_action",
            "action",
        ),
        Index(
            "ix_cc_user_activity_audit_created_at",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )

    actor_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "cc_users.id",
            name="fk_cc_user_activity_audit_actor_user_id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    target_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "cc_users.id",
            name="fk_cc_user_activity_audit_target_user_id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    action: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    resource_type: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    resource_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    clinic_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )

    details: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    ip_address: Mapped[str | None] = mapped_column(
        INET,
        nullable=True,
    )

    user_agent: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
