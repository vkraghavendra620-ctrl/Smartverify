"""add chat_messages, application_findings, finding_documents, government_verification_screenshots, verification_results

Revision ID: 0003_chat_findings_gov_shots
Revises: 0002_add_applicant_fields
Create Date: 2026-09-15
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003_chat_findings_gov_shots"
down_revision = "0002_add_applicant_fields"
branch_labels = None
depends_on = None

# JSON type with JSONB variant on postgres
JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade():
    # 1. chat_messages table
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("application_id", sa.Integer(), sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_id", sa.String(length=100), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    op.create_index("ix_chat_messages_id", "chat_messages", ["id"])
    op.create_index("ix_chat_messages_application_id", "chat_messages", ["application_id"])
    op.create_index("ix_chat_messages_session_id", "chat_messages", ["session_id"])

    # 2. application_findings table
    op.create_table(
        "application_findings",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("application_id", sa.Integer(), sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("particular_id", sa.String(length=100), nullable=True),
        sa.Column("question", sa.Text(), nullable=True),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("finding_type", sa.String(length=50), nullable=True),
        sa.Column("status", sa.String(length=30), server_default="REVIEW", nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("evidence", JSON_TYPE, nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    op.create_index("ix_application_findings_id", "application_findings", ["id"])
    op.create_index("ix_application_findings_application_id", "application_findings", ["application_id"])

    # 3. finding_documents association table
    op.create_table(
        "finding_documents",
        sa.Column("finding_id", sa.Integer(), sa.ForeignKey("application_findings.id", ondelete="CASCADE"), primary_key=True, nullable=False),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )

    # 4. government_verification_screenshots table
    op.create_table(
        "government_verification_screenshots",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("application_id", sa.Integer(), sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("verification_type", sa.String(length=50), nullable=False),
        sa.Column("government_portal", sa.String(length=100), nullable=False),
        sa.Column("verification_reference", sa.String(length=100), nullable=True),
        sa.Column("verification_status", sa.String(length=50), nullable=True),
        sa.Column("screenshot_path", sa.String(length=1000), nullable=False),
        sa.Column("captured_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("source_url", sa.String(length=1000), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    op.create_index("ix_gov_screenshots_id", "government_verification_screenshots", ["id"])
    op.create_index("ix_gov_screenshots_application_id", "government_verification_screenshots", ["application_id"])

    # 5. verification_results table
    op.create_table(
        "verification_results",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("application_id", sa.Integer(), sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("check_type", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("confidence_score", sa.Float(), server_default="0.0", nullable=True),
        sa.Column("details", JSON_TYPE, nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    op.create_index("ix_verification_results_id", "verification_results", ["id"])
    op.create_index("ix_verification_results_application_id", "verification_results", ["application_id"])

    # 6. multi-agent intelligence columns on verification_reports
    op.add_column("verification_reports", sa.Column("confidence_score", sa.Float(), server_default="0.0", nullable=True))
    op.add_column("verification_reports", sa.Column("execution_timeline", JSON_TYPE, nullable=True))
    op.add_column("verification_reports", sa.Column("agent_memory", JSON_TYPE, nullable=True))
    op.add_column("verification_reports", sa.Column("explainable_ai", JSON_TYPE, nullable=True))


def downgrade():
    op.drop_column("verification_reports", "explainable_ai")
    op.drop_column("verification_reports", "agent_memory")
    op.drop_column("verification_reports", "execution_timeline")
    op.drop_column("verification_reports", "confidence_score")
    op.drop_table("verification_results")
    op.drop_table("government_verification_screenshots")
    op.drop_table("finding_documents")
    op.drop_table("application_findings")
    op.drop_table("chat_messages")
