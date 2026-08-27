"""Add hash columns to audit_log

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-26

"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("audit_log", sa.Column("prev_hash", sa.String(64), nullable=True))
    op.add_column("audit_log", sa.Column("entry_hash", sa.String(64), nullable=True))
    # Backfill: sätt entry_hash="" för existerande rader (bakåtkompatibelt;
    # verify_chain() flaggar dem som "obestyrkta" men inte som manipulerade).
    op.execute("UPDATE audit_log SET entry_hash = '' WHERE entry_hash IS NULL")


def downgrade():
    op.drop_column("audit_log", "entry_hash")
    op.drop_column("audit_log", "prev_hash")