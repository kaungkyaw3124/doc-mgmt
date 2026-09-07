import uuid
from datetime import datetime

from sqlalchemy import Column, String, DateTime, Boolean, ForeignKey, UniqueConstraint, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.db import Base


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = Column(String(100), unique=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    is_superuser = Column(Boolean, default=False, nullable=False)
    is_approved = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)  # disabling ≠ un-approving — a quick suspend/unsuspend
    requested_group_id = Column(UUID(as_uuid=True), ForeignKey("groups.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    group_memberships = relationship("UserGroup", back_populates="user", cascade="all, delete-orphan")
    role_assignments = relationship("UserRole", back_populates="user", cascade="all, delete-orphan")


class Group(Base):
    __tablename__ = "groups"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), unique=True, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    memberships = relationship("UserGroup", back_populates="group", cascade="all, delete-orphan")
    roles = relationship("Role", back_populates="group", cascade="all, delete-orphan")
    project_access = relationship("GroupProjectAccess", back_populates="group", cascade="all, delete-orphan")


class UserGroup(Base):
    """A user's membership in a group, with an optional group-admin flag."""
    __tablename__ = "user_groups"
    __table_args__ = (UniqueConstraint("user_id", "group_id", name="uq_user_group"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    group_id = Column(UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False)
    is_group_admin = Column(Boolean, default=False, nullable=False)

    user = relationship("User", back_populates="group_memberships")
    group = relationship("Group", back_populates="memberships")


class Role(Base):
    """A role belongs to exactly one group and grants access to a set of services."""
    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("name", "group_id", name="uq_role_name_per_group"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)
    group_id = Column(UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    group = relationship("Group", back_populates="roles")
    access_grants = relationship("RoleAccess", back_populates="role", cascade="all, delete-orphan")
    project_grants = relationship("RoleProjectAccess", back_populates="role", cascade="all, delete-orphan")
    user_assignments = relationship("UserRole", back_populates="role", cascade="all, delete-orphan")


class RoleAccess(Base):
    """One row = this role grants access to one whole service (e.g. 'documents'),
    at a given permission level. 'view' = read-only, 'edit' = full read/write
    (create, upload, edit, change status). 'products' and 'search' don't
    currently distinguish levels — only 'documents' enforces this — but the
    column applies uniformly for simplicity."""
    __tablename__ = "role_access"
    __table_args__ = (UniqueConstraint("role_id", "service_name", name="uq_role_service"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    role_id = Column(UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), nullable=False)
    service_name = Column(String(50), nullable=False)  # "documents" | "products" | "search" | "audit-log"
    access_level = Column(String(10), nullable=False, default="edit")  # "view" | "edit"

    role = relationship("Role", back_populates="access_grants")


class RoleProjectAccess(Base):
    """
    One row = this role grants visibility into one project's documents.
    project_id is a plain UUID reference into document-service's own Project
    table (a different service/database) — not an enforced foreign key here,
    same loose-coupling pattern as RoleAccess.service_name being a plain string.
    If a role has NO rows here at all, its users see documents from every
    project (unrestricted) — restrictions are opt-in, not a new default lockdown.
    """
    __tablename__ = "role_project_access"
    __table_args__ = (UniqueConstraint("role_id", "project_id", name="uq_role_project"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    role_id = Column(UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), nullable=False)
    project_id = Column(UUID(as_uuid=True), nullable=False)

    role = relationship("Role", back_populates="project_grants")


class GroupProjectAccess(Base):
    """
    One row = a superuser has granted this whole group visibility into one
    project. This is the "pool" a group admin draws from when granting
    individual roles project access (see RoleProjectAccess) — a group
    admin can only grant a role a project that's already in their group's
    pool here; only a superuser can add to the pool itself.
    """
    __tablename__ = "group_project_access"
    __table_args__ = (UniqueConstraint("group_id", "project_id", name="uq_group_project"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    group_id = Column(UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False)
    project_id = Column(UUID(as_uuid=True), nullable=False)

    group = relationship("Group", back_populates="project_access")


class UserProjectAccess(Base):
    """
    One row = this specific user can see this specific project's documents.
    Replaces the earlier role-based project access — roles now carry only
    service permissions (documents/products/search/etc.); project access
    is granted directly to a user, still drawn from their group's pool
    (see GroupProjectAccess) unless the granter is a superuser.
    If a user has NO rows here at all, they see every project (unrestricted)
    — same opt-in-restriction philosophy as before.
    """
    __tablename__ = "user_project_access"
    __table_args__ = (UniqueConstraint("user_id", "project_id", name="uq_user_project"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    project_id = Column(UUID(as_uuid=True), nullable=False)


class UserRole(Base):
    """A user has been assigned a role (and thereby gets that role's service access)."""
    __tablename__ = "user_roles"
    __table_args__ = (UniqueConstraint("user_id", "role_id", name="uq_user_role"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role_id = Column(UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), nullable=False)

    user = relationship("User", back_populates="role_assignments")
    role = relationship("Role", back_populates="user_assignments")


class LoginAttempt(Base):
    """
    One row per FAILED login attempt, used to rate-limit /login. Keyed by
    client IP and lowercased username (not a user_id FK — the username may
    not correspond to a real account, e.g. during enumeration attempts).
    Successful logins are not recorded here, so the table only grows under
    failed-attempt load and old rows are opportunistically pruned on write.
    """
    __tablename__ = "login_attempts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ip = Column(String(64), nullable=False)
    username = Column(String(100), nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_login_attempts_ip_created", "ip", "created_at"),
        Index("ix_login_attempts_ip_username_created", "ip", "username", "created_at"),
        Index("ix_login_attempts_username_created", "username", "created_at"),
    )
