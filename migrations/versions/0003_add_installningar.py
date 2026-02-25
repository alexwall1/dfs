"""Add installningar table for system settings

Revision ID: 0003
Revises: 0002
Create Date: 2026-02-25

"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "installningar",
        sa.Column("key", sa.String(50), primary_key=True),
        sa.Column("value", sa.String(200), nullable=False),
    )


def downgrade():
    op.drop_table("installningar")
