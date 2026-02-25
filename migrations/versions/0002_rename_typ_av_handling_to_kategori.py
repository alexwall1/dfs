"""Rename typer_av_handling to kategorier, handling_typ to handling_kategori

Revision ID: 0002
Revises: 0001
Create Date: 2026-02-25

"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.rename_table("typer_av_handling", "kategorier")
    op.rename_table("handling_typ", "handling_kategori")
    op.alter_column("handling_kategori", "typ_id", new_column_name="kategori_id")


def downgrade():
    op.alter_column("handling_kategori", "kategori_id", new_column_name="typ_id")
    op.rename_table("handling_kategori", "handling_typ")
    op.rename_table("kategorier", "typer_av_handling")
