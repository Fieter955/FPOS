"""Initial lifetime-license schema."""

import sqlalchemy as sa
from alembic import op


revision = "20260902_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "licenses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("license_key", sa.String(64), nullable=False),
        sa.Column("owner_name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("active_installation_id", sa.Integer(), nullable=True),
        sa.Column("conflict_open", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("license_key"),
    )
    op.create_index("ix_licenses_license_key", "licenses", ["license_key"], unique=True)
    op.create_table(
        "installations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("installation_id", sa.String(64), nullable=False),
        sa.Column("license_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("hardware_fingerprint", sa.String(128), nullable=False),
        sa.Column("hostname", sa.String(128), nullable=False),
        sa.Column("fpos_url", sa.String(500), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("blocked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["license_id"], ["licenses.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("installation_id"),
    )
    op.create_index(
        "ix_installations_installation_id", "installations", ["installation_id"], unique=True
    )
    op.create_index(
        "ix_installations_license_status", "installations", ["license_id", "status"]
    )
    op.create_table(
        "license_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("license_id", sa.Integer(), nullable=True),
        sa.Column("installation_id", sa.String(64), nullable=True),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("details", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["license_id"], ["licenses.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_license_events_license_id", "license_events", ["license_id"])
    op.create_index("ix_license_events_event_type", "license_events", ["event_type"])


def downgrade() -> None:
    op.drop_table("license_events")
    op.drop_table("installations")
    op.drop_table("licenses")
