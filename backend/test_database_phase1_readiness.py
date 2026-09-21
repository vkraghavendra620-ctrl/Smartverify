"""
SmartVerify Phase 1: Report Generation Data Readiness Test Suite
Tests Alembic migration 0004_reverification_reports_and_applicant_fields,
schema upgrades/downgrades, Application additions, JointApplicant/Guarantor additions,
ReverificationReport CRUD and cascades, and guarantees zero regressions on VerificationReport.
Operates on an isolated test SQLite database without touching the production/live PostgreSQL database.
"""
import sys
import os
import tempfile
from pathlib import Path
from datetime import datetime

# Ensure backend directory is in python path
backend_dir = Path(__file__).resolve().parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from alembic.config import Config
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError

from app.db.database import Base
from app.models.user import User, UserRole
from app.models.application import (
    Application,
    ApplicationStatus,
    SiteVerification,
    JointApplicant,
    PropertyDetails,
    GovVerification,
)
from app.models.document import Document, DocumentType
from app.models.finding import ApplicationFinding, FindingStatus, finding_documents
from app.models.government_screenshot import GovernmentVerificationScreenshot
from app.models.verification_result import VerificationResult
from app.models.verification_report import VerificationReport
from app.models.reverification_report import ReverificationReport


def run_phase1_readiness_tests():
    # Create an isolated temporary SQLite database file for testing
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        temp_db_path = tmp.name

    test_db_url = f"sqlite:///{temp_db_path.replace(os.sep, '/')}"
    os.environ["DATABASE_URL"] = test_db_url
    print("\n" + "=" * 75)
    print("SMARTVERIFY PHASE 1: DATA READINESS & MIGRATION TEST SUITE")
    print(f"Isolated Test Database URL: {test_db_url}")
    print("=" * 75)

    results = {}

    try:
        engine = create_engine(test_db_url, connect_args={"check_same_thread": False})

        # ─────────────────────────────────────────────────────────────────
        # Test 1: Alembic Upgrade to Head (0004)
        # ─────────────────────────────────────────────────────────────────
        print("\n[Test 1] Testing Alembic Upgrade from 0000 -> 0004_reverification_reports_and_applicant_fields...")
        alembic_cfg = Config(str(backend_dir / "alembic.ini"))
        alembic_cfg.set_main_option("script_location", str(backend_dir / "alembic"))
        alembic_cfg.set_main_option("sqlalchemy.url", test_db_url)
        command.upgrade(alembic_cfg, "head")

        with engine.connect() as conn:
            current_rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            assert current_rev == "0004_reverification_reports_and_applicant_fields", (
                f"Expected 0004_reverification_reports_and_applicant_fields, got {current_rev}"
            )
        print(f"  -> PASSED: Successfully upgraded to head: {current_rev}")
        results["1_alembic_upgrade_head"] = "PASSED"

        # ─────────────────────────────────────────────────────────────────
        # Test 2: Alembic Downgrade to 0003 & Re-Upgrade to Head
        # ─────────────────────────────────────────────────────────────────
        print("\n[Test 2] Testing Alembic Downgrade to 0003 and Re-Upgrade to 0004...")
        command.downgrade(alembic_cfg, "0003_chat_findings_gov_shots")
        with engine.connect() as conn:
            downgraded_rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            assert downgraded_rev == "0003_chat_findings_gov_shots", (
                f"Expected 0003_chat_findings_gov_shots, got {downgraded_rev}"
            )
        print(f"  -> PASSED: Cleanly downgraded to {downgraded_rev}")

        command.upgrade(alembic_cfg, "head")
        with engine.connect() as conn:
            reupgraded_rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            assert reupgraded_rev == "0004_reverification_reports_and_applicant_fields"
        print(f"  -> PASSED: Cleanly re-upgraded to {reupgraded_rev}")
        results["2_alembic_downgrade_reupgrade"] = "PASSED"

        SessionTest = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        db = SessionTest()

        # ─────────────────────────────────────────────────────────────────
        # Setup: Create Test User
        # ─────────────────────────────────────────────────────────────────
        test_user = User(
            name="Loan Verification Officer",
            email="officer.report@smartverify.com",
            password="hashed_secure_password",
            role=UserRole.loan_officer,
        )
        db.add(test_user)
        db.commit()
        db.refresh(test_user)

        # ─────────────────────────────────────────────────────────────────
        # Test 3: Application Creation with applicant_mobile & applicant_email
        # ─────────────────────────────────────────────────────────────────
        print("\n[Test 3] Testing Application Creation with additive contact fields...")
        app = Application(
            user_id=test_user.id,
            applicant_name="Siddharth Malhotra",
            branch="Indiranagar Branch",
            loan_type="Home Loan",
            loan_amount=6500000.0,
            loan_tenure=240,
            interest_rate=8.75,
            aadhaar_number="456789123456",
            pan_number="ABCDE9999F",
            dob="1987-04-15",
            gender="Male",
            address="Flat 101, Prestige Palms, Bengaluru, KA 560008",
            father_name="Devraj Malhotra",
            applicant_mobile="+91-9876543210",
            applicant_email="siddharth.m@example.com",
            status=ApplicationStatus.pending,
        )
        db.add(app)
        db.commit()
        db.refresh(app)

        fetched_app = db.query(Application).filter(Application.id == app.id).first()
        assert fetched_app is not None
        assert fetched_app.applicant_mobile == "+91-9876543210"
        assert fetched_app.applicant_email == "siddharth.m@example.com"
        assert fetched_app.applicant_name == "Siddharth Malhotra"
        print(f"  -> PASSED: Application created with applicant_mobile={fetched_app.applicant_mobile}, email={fetched_app.applicant_email}")
        results["3_application_additive_fields"] = "PASSED"

        # ─────────────────────────────────────────────────────────────────
        # Test 4: JointApplicant & Guarantor KYC Additions
        # ─────────────────────────────────────────────────────────────────
        print("\n[Test 4] Testing JointApplicant Creation (Co-Applicant & Guarantor)...")
        # 4a: Co-Applicant
        co_applicant = JointApplicant(
            application_id=app.id,
            index=0,
            name="Kiara Advani Malhotra",
            applicant_type="co_applicant",
            relationship_type="Spouse",
            pan_number="FGHIJ5678K",
            aadhaar_number="987612345678",
            dob="1990-07-31",
            address="Flat 101, Prestige Palms, Bengaluru, KA 560008",
            mobile="+91-9123456789",
            email="kiara.m@example.com",
            income=150000.0,
            occupation="Salaried Professional",
            father_name="Mukesh Advani",
            years_in_occupation=7,
            remarks="Employed with multinational tech firm",
        )
        # 4b: Guarantor
        guarantor = JointApplicant(
            application_id=app.id,
            index=1,
            name="Rameshwar Advani",
            applicant_type="guarantor",
            relationship_type="Father-in-law",
            pan_number="KLMNO1234P",
            aadhaar_number="555566667777",
            dob="1960-03-22",
            address="Plot 45, Defense Colony, New Delhi 110024",
            mobile="+91-9811122233",
            email="rameshwar.a@example.com",
            income=300000.0,
            occupation="Business Owner",
            father_name="Lalchand Advani",
            years_in_occupation=35,
            remarks="Guarantor with commercial property assets",
        )
        db.add_all([co_applicant, guarantor])
        db.commit()

        fetched_jas = db.query(JointApplicant).filter(JointApplicant.application_id == app.id).order_by(JointApplicant.index).all()
        assert len(fetched_jas) == 2
        assert fetched_jas[0].applicant_type == "co_applicant"
        assert fetched_jas[0].name == "Kiara Advani Malhotra"
        assert fetched_jas[0].income == 150000.0
        assert fetched_jas[0].father_name == "Mukesh Advani"
        assert fetched_jas[0].years_in_occupation == 7
        assert fetched_jas[1].applicant_type == "guarantor"
        assert fetched_jas[1].name == "Rameshwar Advani"
        assert fetched_jas[1].occupation == "Business Owner"
        assert fetched_jas[1].father_name == "Lalchand Advani"
        assert fetched_jas[1].years_in_occupation == 35
        print(f"  -> PASSED: Created Co-Applicant '{fetched_jas[0].name}' (father: {fetched_jas[0].father_name}, {fetched_jas[0].years_in_occupation} yrs) and Guarantor '{fetched_jas[1].name}' (father: {fetched_jas[1].father_name}, {fetched_jas[1].years_in_occupation} yrs) with full KYC.")
        results["4_joint_applicants_and_guarantor"] = "PASSED"

        # ─────────────────────────────────────────────────────────────────
        # Test 5: ReverificationReport Creation, Content/Snapshot, and Lifecycle
        # ─────────────────────────────────────────────────────────────────
        print("\n[Test 5] Testing ReverificationReport CRUD, JSON Content & Lifecycle...")
        sample_content = {
            "particulars": [
                {
                    "particular_id": "PART-01-IDENTITY",
                    "title": "Borrower Identity & KYC",
                    "status": "VERIFIED",
                    "details": "Identity verified via UIDAI Aadhaar portal check and IT PAN-Aadhaar linking."
                },
                {
                    "particular_id": "PART-02-INCOME",
                    "title": "Income Substantiation",
                    "status": "VERIFIED",
                    "details": "Verified with 6-month bank statements and ITR V acknowledgment."
                }
            ],
            "opinion": "Borrower and co-borrower profiles confirm verified identity and stable repayment capacity."
        }
        sample_snapshot = {
            "borrower_name": "Siddharth Malhotra",
            "loan_amount": 6500000.0,
            "pan_number": "ABCDE9999F",
            "co_borrower": "Kiara Advani Malhotra",
            "guarantor": "Rameshwar Advani"
        }

        rev_report = ReverificationReport(
            application_id=app.id,
            version=1,
            template_key="standard_reverification",
            template_version="v1.0",
            status="draft",
            content=sample_content,
            snapshot=sample_snapshot,
            row_version=1,
            pdf_path=None,
            pdf_sha256=None,
            prepared_by="Officer John Doe",
            finalized_by=None,
            finalized_at=None,
        )
        db.add(rev_report)
        db.commit()
        db.refresh(rev_report)

        assert rev_report.id is not None
        assert rev_report.status == "draft"
        assert rev_report.content["particulars"][0]["particular_id"] == "PART-01-IDENTITY"
        assert rev_report.snapshot["borrower_name"] == "Siddharth Malhotra"
        assert rev_report.application_id == app.id
        print(f"  -> PASSED: Created ReverificationReport ID={rev_report.id}, status='draft'")

        # Update report to finalized
        now = datetime.utcnow()
        rev_report.status = "finalized"
        rev_report.pdf_path = "reports/SmartVerify_Reverification_APP-000001.pdf"
        rev_report.pdf_sha256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        rev_report.finalized_by = "Manager Alice Smith"
        rev_report.finalized_at = now
        rev_report.row_version = 2
        db.commit()
        db.refresh(rev_report)

        assert rev_report.status == "finalized"
        assert rev_report.finalized_by == "Manager Alice Smith"
        assert rev_report.pdf_sha256 is not None
        assert rev_report.row_version == 2
        print(f"  -> PASSED: Updated ReverificationReport to 'finalized' with PDF SHA-256 and row_version=2")
        results["5_reverification_report_crud"] = "PASSED"

        # ─────────────────────────────────────────────────────────────────
        # Test 6: Legacy VerificationReport Zero Regression Guarantee
        # ─────────────────────────────────────────────────────────────────
        print("\n[Test 6] Testing existing VerificationReport to guarantee zero regression...")
        legacy_report = VerificationReport(
            application_id=app.id,
            verification_score=88.5,
            risk_score=15.0,
            fraud_flag=False,
            status="approved",
            extracted_info={"applicant_name": "Siddharth Malhotra", "pan": "ABCDE9999F"},
            verification_details={"rules_passed": 5, "rules_failed": 0},
            fraud_analysis={"risk_level": "LOW"},
            verification_mode="rule_based",
            agent_summary="Legacy V6 report summary",
            confidence_score=92.0,
            execution_timeline={"pipeline_ms": 420},
        )
        db.add(legacy_report)
        db.commit()
        db.refresh(legacy_report)

        assert legacy_report.id is not None
        assert legacy_report.verification_score == 88.5
        assert legacy_report.verification_mode == "rule_based"
        print("  -> PASSED: Existing VerificationReport operates completely unchanged.")
        results["6_legacy_verification_report_untouched"] = "PASSED"

        # ─────────────────────────────────────────────────────────────────
        # Test 7: Cascade Deletion of ReverificationReport and JointApplicants
        # ─────────────────────────────────────────────────────────────────
        print("\n[Test 7] Testing Cascade Deletion when Application is deleted...")
        db.delete(app)
        db.commit()

        dangling_rep = db.query(ReverificationReport).filter(ReverificationReport.application_id == app.id).first()
        dangling_ja = db.query(JointApplicant).filter(JointApplicant.application_id == app.id).all()
        dangling_legacy = db.query(VerificationReport).filter(VerificationReport.application_id == app.id).first()

        assert dangling_rep is None, "ReverificationReport should be cascade deleted!"
        assert len(dangling_ja) == 0, "JointApplicants should be cascade deleted!"
        assert dangling_legacy is None, "VerificationReport should be cascade deleted!"
        print("  -> PASSED: Cascade delete successfully cleaned up ReverificationReport and related records.")
        results["7_cascade_deletion"] = "PASSED"

        # ─────────────────────────────────────────────────────────────────
        # Test 8: Uniqueness of (application_id, version) on ReverificationReport
        # ─────────────────────────────────────────────────────────────────
        print("\n[Test 8] Testing Uniqueness of (application_id, version) constraint...")
        app2 = Application(
            user_id=test_user.id,
            applicant_name="Vikram Seth",
            loan_amount=5000000.0,
            status=ApplicationStatus.pending,
        )
        db.add(app2)
        db.commit()
        db.refresh(app2)

        # Create report version 1
        rep_v1 = ReverificationReport(
            application_id=app2.id,
            version=1,
            template_key="standard_reverification",
            template_version="v1.0",
            status="draft",
        )
        db.add(rep_v1)
        db.commit()
        print(f"  -> PASSED: Created ReverificationReport (application_id={app2.id}, version=1)")

        # Create report version 2 (allowed)
        rep_v2 = ReverificationReport(
            application_id=app2.id,
            version=2,
            template_key="standard_reverification",
            template_version="v1.0",
            status="draft",
        )
        db.add(rep_v2)
        db.commit()
        print(f"  -> PASSED: Created ReverificationReport (application_id={app2.id}, version=2) for same application")

        # Attempt to create duplicate (application_id, version=1) -> must fail!
        duplicate_rep = ReverificationReport(
            application_id=app2.id,
            version=1,
            template_key="standard_reverification",
            template_version="v1.0",
            status="draft",
        )
        db.add(duplicate_rep)
        try:
            db.commit()
            raise AssertionError("Duplicate (application_id, version) was incorrectly accepted!")
        except IntegrityError:
            db.rollback()
            print("  -> PASSED: Duplicate (application_id, version) was rejected with IntegrityError as expected.")
            results["8_version_uniqueness_constraint"] = "PASSED"

        db.delete(app2)
        db.commit()

        print("\n" + "=" * 75)
        print("PHASE 1 DATA READINESS TESTS: ALL PASSED (8/8)")
        print("=" * 75)

    finally:
        # Cleanup isolated test database file
        try:
            if os.path.exists(temp_db_path):
                os.remove(temp_db_path)
        except Exception:
            pass

    return results


if __name__ == "__main__":
    test_results = run_phase1_readiness_tests()
    all_passed = all(v == "PASSED" for v in test_results.values())
    sys.exit(0 if all_passed else 1)
