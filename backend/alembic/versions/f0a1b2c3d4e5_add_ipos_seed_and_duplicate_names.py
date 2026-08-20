"""add ipos seed status and allow duplicate item names

Revision ID: f0a1b2c3d4e5
Revises: e8f9a0b1c2d3
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "f0a1b2c3d4e5"
down_revision = "e8f9a0b1c2d3"
branch_labels = None
depends_on = None

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def _item_name_unique(inspector) -> dict | None:
    for constraint in inspector.get_unique_constraints("items"):
        if constraint.get("column_names") == ["name"]:
            return constraint
    return None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "data_seed_runs" not in inspector.get_table_names():
        op.create_table(
            "data_seed_runs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("seed_key", sa.String(length=100), nullable=False),
            sa.Column("version", sa.String(length=50), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False),
            sa.Column("counts_json", sa.Text(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("seed_key"),
        )
        op.create_index(op.f("ix_data_seed_runs_id"), "data_seed_runs", ["id"], unique=False)

    inspector = sa.inspect(bind)
    constraint = _item_name_unique(inspector)
    if constraint:
        # SQLite memberi nama NULL pada UNIQUE inline. Naming convention membuat
        # nama refleksi stabil sehingga batch recreate dapat menjatuhkannya.
        constraint_name = constraint.get("name") or "uq_items_name"
        with op.batch_alter_table(
            "items",
            recreate="always",
            naming_convention=NAMING_CONVENTION,
        ) as batch_op:
            batch_op.drop_constraint(constraint_name, type_="unique")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not _item_name_unique(inspector):
        with op.batch_alter_table(
            "items",
            recreate="always",
            naming_convention=NAMING_CONVENTION,
        ) as batch_op:
            batch_op.create_unique_constraint("uq_items_name", ["name"])

    inspector = sa.inspect(bind)
    if "data_seed_runs" in inspector.get_table_names():
        op.drop_index(op.f("ix_data_seed_runs_id"), table_name="data_seed_runs")
        op.drop_table("data_seed_runs")
