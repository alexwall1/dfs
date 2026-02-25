"""Initial schema

Revision ID: 0001
Revises:
Create Date: 2026-02-25

"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("username", sa.String(80), unique=True, nullable=False),
        sa.Column("password_hash", sa.String(256), nullable=False),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(200)),
        sa.Column("role", sa.String(20), nullable=False, server_default="handlaggare"),
        sa.Column("active", sa.Boolean, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime),
        sa.Column("misslyckade_inloggningar", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_locked_until", sa.DateTime),
        sa.Column("maste_byta_losenord", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("deleted", sa.Boolean, nullable=False, server_default=sa.false()),
    )

    op.create_table(
        "arenden",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("diarienummer", sa.String(30), unique=True, nullable=False),
        sa.Column("arende_mening", sa.String(500), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="oppnat"),
        sa.Column("sekretess", sa.Boolean, server_default=sa.false()),
        sa.Column("sekretess_grund", sa.String(500)),
        sa.Column("skapad_av", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("handlaggare_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("skapad_datum", sa.DateTime),
        sa.Column("andrad_datum", sa.DateTime),
        sa.Column("deleted", sa.Boolean, server_default=sa.false()),
    )

    op.create_table(
        "typer_av_handling",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("namn", sa.String(100), unique=True, nullable=False),
    )

    op.create_table(
        "handlingar",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("arende_id", sa.Integer, sa.ForeignKey("arenden.id"), nullable=False),
        sa.Column("typ", sa.String(20), nullable=False),
        sa.Column("datum_inkom", sa.Date),
        sa.Column("avsandare", sa.String(300)),
        sa.Column("mottagare", sa.String(300)),
        sa.Column("beskrivning", sa.String(500), nullable=False),
        sa.Column("sekretess", sa.Boolean, server_default=sa.false()),
        sa.Column("skapad_av", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("skapad_datum", sa.DateTime),
        sa.Column("deleted", sa.Boolean, server_default=sa.false()),
    )

    op.create_table(
        "handling_typ",
        sa.Column("handling_id", sa.Integer, sa.ForeignKey("handlingar.id"), primary_key=True),
        sa.Column("typ_id", sa.Integer, sa.ForeignKey("typer_av_handling.id"), primary_key=True),
    )

    op.create_table(
        "document_versions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("handling_id", sa.Integer, sa.ForeignKey("handlingar.id"), nullable=False),
        sa.Column("version_nr", sa.Integer, nullable=False, server_default="1"),
        sa.Column("filnamn", sa.String(300), nullable=False),
        sa.Column("fildata", sa.LargeBinary, nullable=False),
        sa.Column("mime_type", sa.String(100)),
        sa.Column("kommentar", sa.String(500)),
        sa.Column("skapad_av", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("skapad_datum", sa.DateTime),
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("target_type", sa.String(50)),
        sa.Column("target_id", sa.Integer),
        sa.Column("details", sa.JSON),
        sa.Column("timestamp", sa.DateTime),
        sa.Column("ip_address", sa.String(45)),
    )

    op.create_table(
        "nummerserier",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("prefix", sa.String(20), nullable=False),
        sa.Column("year", sa.Integer, nullable=False),
        sa.Column("current_number", sa.Integer, nullable=False, server_default="0"),
        sa.UniqueConstraint("prefix", "year", name="uq_prefix_year"),
    )


def downgrade():
    op.drop_table("nummerserier")
    op.drop_table("audit_log")
    op.drop_table("document_versions")
    op.drop_table("handling_typ")
    op.drop_table("handlingar")
    op.drop_table("typer_av_handling")
    op.drop_table("arenden")
    op.drop_table("users")
