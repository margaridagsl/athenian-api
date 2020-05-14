import ctypes
from datetime import datetime, timezone
import json

from sqlalchemy import BigInteger, Boolean, Column, ForeignKey, func, Integer, JSON, \
    SmallInteger, String, TIMESTAMP, UniqueConstraint
import xxhash

from athenian.api.models import always_unequal, create_base


Base = create_base()


class CollectionMixin():
    """Mixin for collection-alike tables."""

    def count_items(ctx):
        """Return the number of items in the collection."""
        return len(ctx.get_current_parameters()["items"])

    def calc_items_checksum(ctx):
        """Calculate the checksum of the items in the collection."""
        return ctypes.c_longlong(xxhash.xxh64_intdigest(json.dumps(
            ctx.get_current_parameters()["items"]))).value

    items = Column(always_unequal(JSON()), nullable=False)
    items_count = Column(Integer(), nullable=False, default=count_items, onupdate=count_items)
    items_checksum = Column(always_unequal(BigInteger()), nullable=False,
                            default=calc_items_checksum, onupdate=calc_items_checksum)

    count_items = staticmethod(count_items)
    calc_items_checksum = staticmethod(calc_items_checksum)


def create_time_mixin(created_at: bool = False, updated_at: bool = False):
    """Create the mixin accorinding to the required columns."""
    cols = {}
    if created_at:
        cols["created_at"] = Column(TIMESTAMP(timezone=True), nullable=False,
                                    default=lambda: datetime.now(timezone.utc),
                                    server_default=func.now())
    if updated_at:
        cols["updated_at"] = Column(TIMESTAMP(timezone=True), nullable=False,
                                    default=lambda: datetime.now(timezone.utc),
                                    server_default=func.now(),
                                    onupdate=lambda ctx: datetime.now(timezone.utc))
    return type("TimeMixin", (), cols)


class RepositorySet(Base):
    """A group of repositories identified by an integer."""

    __tablename__ = "repository_sets"
    __table_args__ = (UniqueConstraint("owner", "items_checksum", name="uc_owner_items"),
                      {"sqlite_autoincrement": True})

    def count_items(ctx):
        """Return the number of repositories in a set."""
        return len(ctx.get_current_parameters()["items"])

    def calc_items_checksum(ctx):
        """Calculate the checksum of the reposet items."""
        return ctypes.c_longlong(xxhash.xxh64_intdigest(json.dumps(
            ctx.get_current_parameters()["items"]))).value

    id = Column(Integer(), primary_key=True)
    owner = Column(Integer(), ForeignKey("accounts.id", name="fk_reposet_owner"), nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False,
                        default=lambda: datetime.now(timezone.utc),
                        server_default=func.now(),
                        onupdate=lambda ctx: datetime.now(timezone.utc))
    created_at = Column(TIMESTAMP(timezone=True), nullable=False,
                        default=lambda: datetime.now(timezone.utc),
                        server_default=func.now())
    updates_count = Column(always_unequal(Integer()), nullable=False, default=1,
                           onupdate=lambda ctx: ctx.get_current_parameters()["updates_count"] + 1)
    items = Column(always_unequal(JSON()), nullable=False)
    items_count = Column(Integer(), nullable=False, default=count_items, onupdate=count_items)
    items_checksum = Column(always_unequal(BigInteger()), nullable=False,
                            default=calc_items_checksum, onupdate=calc_items_checksum)

    count_items = staticmethod(count_items)
    calc_items_checksum = staticmethod(calc_items_checksum)


class UserAccount(Base):
    """User<>account many-to-many relations."""

    __tablename__ = "user_accounts"

    user_id = Column(String(256), primary_key=True)
    account_id = Column(Integer(), ForeignKey("accounts.id", name="fk_user_account"),
                        nullable=False, primary_key=True)
    is_admin = Column(Boolean(), nullable=False, default=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False,
                        default=lambda: datetime.now(timezone.utc),
                        server_default=func.now())


class Account(Base):
    """Group of users, some are admins and some are regular."""

    __tablename__ = "accounts"
    __table_args__ = {"sqlite_autoincrement": True}

    id = Column(Integer(), primary_key=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False,
                        default=lambda: datetime.now(timezone.utc),
                        server_default=func.now())


class Installation(Base):
    """Mapping account -> installation_id, one-to-many."""

    __tablename__ = "installations"

    id = Column(BigInteger(), primary_key=True, autoincrement=False)
    account_id = Column(Integer(), ForeignKey("accounts.id", name="fk_installation_id_owner"),
                        nullable=False)


class Invitation(Base):
    """Account invitations, each maps to a URL that invitees should click."""

    __tablename__ = "invitations"
    __table_args__ = {"sqlite_autoincrement": True}

    id = Column(Integer(), primary_key=True)
    salt = Column(Integer(), nullable=False)
    account_id = Column(Integer(), ForeignKey(
        "accounts.id", name="fk_invitation_account"), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    accepted = Column(Integer(), nullable=False, default=0)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False,
                        default=lambda: datetime.now(timezone.utc),
                        server_default=func.now())
    created_by = Column(String(256))


class God(Base):
    """Secret user mappings for chosen ones."""

    __tablename__ = "gods"

    user_id = Column(String(256), primary_key=True)
    mapped_id = Column(String(256), nullable=True)
    updated_at = Column(always_unequal(TIMESTAMP(timezone=True)), nullable=False,
                        default=lambda: datetime.now(timezone.utc),
                        server_default=func.now(),
                        onupdate=lambda ctx: datetime.now(timezone.utc))


class ReleaseSetting(Base):
    """Release matching rules per repo."""

    __tablename__ = "release_settings"

    repository = Column(String(512), primary_key=True)
    account_id = Column(Integer(), primary_key=True)
    branches = Column(String(1024))
    tags = Column(String(1024))
    match = Column(SmallInteger())
    updated_at = Column(always_unequal(TIMESTAMP(timezone=True)), nullable=False,
                        default=lambda: datetime.now(timezone.utc),
                        server_default=func.now(),
                        onupdate=lambda ctx: datetime.now(timezone.utc))
