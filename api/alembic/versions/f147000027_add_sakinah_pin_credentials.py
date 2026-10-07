"""add organization-scoped Sakinah PIN credentials

Revision ID: f147000027
Revises: f147000004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f147000027"
down_revision: str | None = "f147000004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("memories", sa.Column("fact_key", sa.String(length=128), nullable=True))
    op.add_column("memories", sa.Column("fact_category", sa.String(length=64), nullable=True))
    op.add_column("memories", sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "memories",
        sa.Column("source_count", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )
    op.add_column(
        "memories",
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'active'")),
    )
    op.add_column("memories", sa.Column("superseded_by", sa.String(length=36), nullable=True))
    op.create_foreign_key(
        "fk_memories_superseded_by",
        "memories",
        "memories",
        ["superseded_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_memories_service_user_fact_key", "memories", ["service_user_id", "fact_key"])
    op.create_index("ix_memories_status", "memories", ["status"])

    op.create_table(
        "service_user_credentials",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("service_user_id", sa.String(length=36), nullable=False),
        sa.Column("credential_type", sa.String(length=32), nullable=False),
        sa.Column("pin_hash", sa.String(length=255), nullable=False),
        sa.Column("pin_salt", sa.String(length=64), nullable=True),
        sa.Column(
            "hash_algorithm",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'bcrypt_v1'"),
        ),
        sa.Column(
            "status", sa.String(length=32), nullable=False, server_default=sa.text("'active'"),
        ),
        sa.Column(
            "failed_attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0"),
        ),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["service_user_id"], ["service_users.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "service_user_id",
            "credential_type",
            name="uq_service_user_credentials_org_user_type",
        ),
    )
    op.create_index(
        "ix_service_user_credentials_lookup",
        "service_user_credentials",
        ["organization_id", "service_user_id", "credential_type"],
    )
    op.create_index(
        "ix_service_user_credentials_status",
        "service_user_credentials",
        ["status", "locked_until"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_service_user_credentials_status", table_name="service_user_credentials"
    )
    op.drop_index(
        "ix_service_user_credentials_lookup", table_name="service_user_credentials"
    )
    op.drop_table("service_user_credentials")
    op.drop_index("ix_memories_status", table_name="memories")
    op.drop_index("ix_memories_service_user_fact_key", table_name="memories")
    op.drop_constraint("fk_memories_superseded_by", "memories", type_="foreignkey")
    for name in (
        "superseded_by",
        "status",
        "source_count",
        "first_observed_at",
        "fact_category",
        "fact_key",
    ):
        op.drop_column("memories", name)
