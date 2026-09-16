"""baseline core schema for smartverify

Revision ID: 0000_baseline_core_schema
Revises:
Create Date: 2026-09-15
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0000_baseline_core_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # 1. users table
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False, unique=True),
        sa.Column("password", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=50), server_default="loan_officer", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
    )
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_id", "users", ["id"])

    # 2. applications table (base fields before 0002 migration)
    op.create_table(
        "applications",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("applicant_name", sa.String(length=255), nullable=True),
        sa.Column("branch", sa.String(length=255), nullable=True),
        sa.Column("loan_type", sa.String(length=255), nullable=True),
        sa.Column("loan_amount", sa.Float(), nullable=False),
        sa.Column("loan_tenure", sa.Integer(), nullable=True),
        sa.Column("interest_rate", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=50), server_default="pending", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
    )
    op.create_index("ix_applications_id", "applications", ["id"])

    # 3. documents table
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("application_id", sa.Integer(), sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_type", sa.String(length=50), nullable=False),
        sa.Column("file_path", sa.String(length=500), nullable=False),
        sa.Column("original_name", sa.String(length=255), nullable=True),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("structured_data", sa.Text(), nullable=True),
        sa.Column("processed", sa.Integer(), server_default="0", nullable=True),
        sa.Column("joint_applicant_index", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
    )
    op.create_index("ix_documents_id", "documents", ["id"])
    op.create_index("ix_documents_application_id", "documents", ["application_id"])

    # 4. site_verifications table
    op.create_table(
        "site_verifications",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("application_id", sa.Integer(), sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("gps_coordinates", sa.String(length=255), nullable=True),
        sa.Column("officer_name", sa.String(length=100), nullable=True),
        sa.Column("date", sa.String(length=50), nullable=True),
        sa.Column("time", sa.String(length=50), nullable=True),
        sa.Column("property_condition", sa.String(length=100), nullable=True),
        sa.Column("construction_quality", sa.String(length=100), nullable=True),
        sa.Column("boundary_present", sa.String(length=50), nullable=True),
        sa.Column("road_access", sa.String(length=50), nullable=True),
        sa.Column("utilities_available", sa.String(length=500), nullable=True),
        sa.Column("remarks", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
    )
    op.create_index("ix_site_verifications_id", "site_verifications", ["id"])

    # 5. joint_applicants table
    op.create_table(
        "joint_applicants",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("application_id", sa.Integer(), sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("index", sa.Integer(), nullable=False),
        sa.Column("relationship_type", sa.String(length=100), nullable=True),
        sa.Column("mobile", sa.String(length=20), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("remarks", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
    )
    op.create_index("ix_joint_applicants_id", "joint_applicants", ["id"])

    # 6. property_details table
    op.create_table(
        "property_details",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("application_id", sa.Integer(), sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("property_type", sa.String(length=100), nullable=True),
        sa.Column("address", sa.String(length=500), nullable=True),
        sa.Column("village_city", sa.String(length=100), nullable=True),
        sa.Column("taluk", sa.String(length=100), nullable=True),
        sa.Column("district", sa.String(length=100), nullable=True),
        sa.Column("state", sa.String(length=100), nullable=True),
        sa.Column("pin_code", sa.String(length=20), nullable=True),
        sa.Column("survey_number", sa.String(length=100), nullable=True),
        sa.Column("khata_number", sa.String(length=100), nullable=True),
        sa.Column("property_area", sa.String(length=100), nullable=True),
        sa.Column("market_value", sa.Float(), nullable=True),
        sa.Column("loan_security_value", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
    )
    op.create_index("ix_property_details_id", "property_details", ["id"])

    # 7. gov_verifications table
    op.create_table(
        "gov_verifications",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("application_id", sa.Integer(), sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("pan_aadhaar_link_status", sa.String(length=50), nullable=True),
        sa.Column("tax_receipt_status", sa.String(length=50), nullable=True),
        sa.Column("aadhaar_validity_status", sa.String(length=50), nullable=True),
        sa.Column("aadhaar_screenshot_path", sa.String(length=1000), nullable=True),
        sa.Column("officer_name", sa.String(length=100), nullable=True),
        sa.Column("timestamp", sa.String(length=100), nullable=True),
        sa.Column("remarks", sa.String(length=1000), nullable=True),
        sa.Column("screenshot_path", sa.String(length=1000), nullable=True),
        sa.Column("verification_screenshots", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
    )
    op.create_index("ix_gov_verifications_id", "gov_verifications", ["id"])

    # 8. verification_reports table (base fields before 0001 migration)
    op.create_table(
        "verification_reports",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("application_id", sa.Integer(), sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("verification_score", sa.Float(), server_default="0.0", nullable=True),
        sa.Column("risk_score", sa.Float(), server_default="0.0", nullable=True),
        sa.Column("fraud_flag", sa.Boolean(), server_default="false", nullable=True),
        sa.Column("status", sa.String(length=50), server_default="pending", nullable=True),
        sa.Column("extracted_info", sa.JSON(), nullable=True),
        sa.Column("fraud_analysis", sa.JSON(), nullable=True),
        sa.Column("verification_details", sa.JSON(), nullable=True),
        sa.Column("pdf_path", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
    )
    op.create_index("ix_verification_reports_id", "verification_reports", ["id"])


def downgrade():
    op.drop_table("verification_reports")
    op.drop_table("gov_verifications")
    op.drop_table("property_details")
    op.drop_table("joint_applicants")
    op.drop_table("site_verifications")
    op.drop_table("documents")
    op.drop_table("applications")
    op.drop_table("users")
