"""add reverification_reports and applicant/guarantor fields

Revision ID: 0004_reverification_reports_and_applicant_fields
Revises: 0003_chat_findings_gov_shots
Create Date: 2026-09-20
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004_reverification_reports_and_applicant_fields"
down_revision = "0003_chat_findings_gov_shots"
branch_labels = None
depends_on = None

# Cross-dialect JSONB support: PostgreSQL uses JSONB, fallback to JSON for SQLite
JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade():
    # Enlarge alembic_version.version_num on PostgreSQL if needed (revision ID is 48 chars > default 32)
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(64)")

    # 1. Add applicant_mobile and applicant_email to applications
    op.add_column("applications", sa.Column("applicant_mobile", sa.String(length=20), nullable=True))
    op.add_column("applications", sa.Column("applicant_email", sa.String(length=255), nullable=True))

    # 2. Add identity, KYC, and financial fields to joint_applicants
    op.add_column("joint_applicants", sa.Column("name", sa.String(length=255), nullable=True))
    op.add_column("joint_applicants", sa.Column("applicant_type", sa.String(length=50), server_default="co_applicant", nullable=False))
    op.add_column("joint_applicants", sa.Column("pan_number", sa.String(length=20), nullable=True))
    op.add_column("joint_applicants", sa.Column("aadhaar_number", sa.String(length=20), nullable=True))
    op.add_column("joint_applicants", sa.Column("dob", sa.String(length=50), nullable=True))
    op.add_column("joint_applicants", sa.Column("address", sa.String(length=500), nullable=True))
    op.add_column("joint_applicants", sa.Column("income", sa.Float(), nullable=True))
    op.add_column("joint_applicants", sa.Column("occupation", sa.String(length=255), nullable=True))
    op.add_column("joint_applicants", sa.Column("father_name", sa.String(length=255), nullable=True))
    op.add_column("joint_applicants", sa.Column("years_in_occupation", sa.Integer(), nullable=True))

    # 3. Create dedicated reverification_reports table for Report Generation System
    op.create_table(
        "reverification_reports",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("application_id", sa.Integer(), sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("template_key", sa.String(length=100), server_default="standard_reverification", nullable=False),
        sa.Column("template_version", sa.String(length=50), server_default="v1.0", nullable=False),
        sa.Column("status", sa.String(length=50), server_default="draft", nullable=False),
        sa.Column("content", JSON_TYPE, nullable=True),
        sa.Column("snapshot", JSON_TYPE, nullable=True),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("pdf_path", sa.String(length=500), nullable=True),
        sa.Column("pdf_sha256", sa.String(length=64), nullable=True),
        sa.Column("prepared_by", sa.String(length=255), nullable=True),
        sa.Column("finalized_by", sa.String(length=255), nullable=True),
        sa.Column("finalized_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.UniqueConstraint("application_id", "version", name="uq_reverification_reports_application_version"),
    )
    op.create_index("ix_reverification_reports_id", "reverification_reports", ["id"])
    op.create_index("ix_reverification_reports_application_id", "reverification_reports", ["application_id"])


def downgrade():
    op.drop_index("ix_reverification_reports_application_id", table_name="reverification_reports")
    op.drop_index("ix_reverification_reports_id", table_name="reverification_reports")
    op.drop_table("reverification_reports")

    op.drop_column("joint_applicants", "years_in_occupation")
    op.drop_column("joint_applicants", "father_name")
    op.drop_column("joint_applicants", "occupation")
    op.drop_column("joint_applicants", "income")
    op.drop_column("joint_applicants", "address")
    op.drop_column("joint_applicants", "dob")
    op.drop_column("joint_applicants", "aadhaar_number")
    op.drop_column("joint_applicants", "pan_number")
    op.drop_column("joint_applicants", "applicant_type")
    op.drop_column("joint_applicants", "name")

    op.drop_column("applications", "applicant_email")
    op.drop_column("applications", "applicant_mobile")
