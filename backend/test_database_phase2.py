"""
SmartVerify Phase 2 - Comprehensive Database & Schema Test Suite
Validates all PostgreSQL / SQLAlchemy models, Alembic migrations,
relationships, foreign-key integrity, and cascade delete behaviors.
"""
import sys
import os
from pathlib import Path
from datetime import datetime

# Ensure backend root is on sys.path
backend_dir = Path(__file__).resolve().parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from alembic.config import Config
from alembic import command
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
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
from app.models.chat_message import ChatMessage
from app.models.finding import ApplicationFinding, FindingStatus, finding_documents
from app.models.government_screenshot import GovernmentVerificationScreenshot
from app.models.verification_result import VerificationResult
from app.models.verification_report import VerificationReport


def run_all_tests(db_url: str = None):
    target_url = db_url or settings.DATABASE_URL
    os.environ["DATABASE_URL"] = target_url
    print("\n" + "=" * 70)
    print(f"SMARTVERIFY DATABASE VALIDATION TEST SUITE")
    print(f"Target Database URL: {target_url}")
    print("=" * 70)

    results = {}

    # ─────────────────────────────────────────────────────────────────
    # Test 1: Database Connection
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 1] Testing Database Connection...")
    try:
        connect_args = {"check_same_thread": False} if target_url.startswith("sqlite") else {}
        engine = create_engine(target_url, pool_pre_ping=True, connect_args=connect_args)
        with engine.connect() as conn:
            val = conn.execute(text("SELECT 1")).scalar()
            assert val == 1
        print("  -> PASSED: Engine connected and executed SELECT 1 successfully.")
        results["1_connection"] = "PASSED"
    except Exception as e:
        print(f"  -> FAILED: Connection error: {e}")
        results["1_connection"] = f"FAILED: {e}"
        return results

    # ─────────────────────────────────────────────────────────────────
    # Test 2: Alembic Upgrade to Head
    # ─────────────────────────────────────────────────────────────────
    print("\n[Test 2] Testing Alembic Upgrade to Head...")
    try:
        alembic_cfg = Config(str(backend_dir / "alembic.ini"))
        alembic_cfg.set_main_option("script_location", str(backend_dir / "alembic"))
        alembic_cfg.set_main_option("sqlalchemy.url", target_url)
        command.upgrade(alembic_cfg, "head")

        with engine.connect() as conn:
            current_rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            assert current_rev == "0003_chat_findings_gov_shots"
        print(f"  -> PASSED: Alembic upgraded to head revision: {current_rev}")
        results["2_alembic_upgrade"] = "PASSED"
    except Exception as e:
        print(f"  -> FAILED: Alembic upgrade error: {e}")
        results["2_alembic_upgrade"] = f"FAILED: {e}"
        return results

    SessionTest = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionTest()

    try:
        # ─────────────────────────────────────────────────────────────
        # Setup: Create a clean test user
        # ─────────────────────────────────────────────────────────────
        test_email = f"test_officer_{int(datetime.utcnow().timestamp())}@smartverify.com"
        user = User(
            name="Test Verification Officer",
            email=test_email,
            password="hashed_secure_password",
            role=UserRole.loan_officer,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        # ─────────────────────────────────────────────────────────────
        # Test 3 & 4: Application Creation and Retrieval
        # ─────────────────────────────────────────────────────────────
        print("\n[Test 3 & 4] Testing Application Creation and Retrieval...")
        app = Application(
            user_id=user.id,
            applicant_name="Vikramaditya Verma",
            branch="Indiranagar Branch",
            loan_type="Home Loan",
            loan_amount=7500000.0,
            loan_tenure=240,
            interest_rate=8.5,
            aadhaar_number="987654321098",
            pan_number="ABCDE1234F",
            dob="1988-11-23",
            gender="Male",
            address="Flat 402, Royal Palms, Indiranagar, Bengaluru, KA 560038",
            father_name="Rameshwar Verma",
            status=ApplicationStatus.pending,
        )
        db.add(app)
        db.commit()
        db.refresh(app)

        fetched_app = db.query(Application).filter(Application.id == app.id).first()
        assert fetched_app is not None
        assert fetched_app.applicant_name == "Vikramaditya Verma"
        assert fetched_app.aadhaar_number == "987654321098"
        assert fetched_app.pan_number == "ABCDE1234F"
        assert fetched_app.loan_amount == 7500000.0
        assert fetched_app.user.email == test_email
        print(f"  -> PASSED: Application ID {fetched_app.id} created and retrieved with all KYC fields.")
        results["3_4_application_crud"] = "PASSED"

        # ─────────────────────────────────────────────────────────────
        # Test 5 & 6: Document Creation & Application Relationship
        # ─────────────────────────────────────────────────────────────
        print("\n[Test 5 & 6] Testing Document Creation and Application Relationship...")
        doc1 = Document(
            application_id=app.id,
            document_type=DocumentType.aadhaar,
            file_path="uploads/test_aadhaar_card.png",
            original_name="Aadhaar_Front_Back.png",
            extracted_text="Government of India Vikramaditya Verma DOB 23/11/1988 9876 5432 1098",
            structured_data='{"name": "Vikramaditya Verma", "dob": "1988-11-23", "aadhaar": "987654321098"}',
            processed=2,
        )
        doc2 = Document(
            application_id=app.id,
            document_type=DocumentType.salary_slip,
            file_path="uploads/test_payslip_jul2026.pdf",
            original_name="PaySlip_July_2026.pdf",
            extracted_text="Pay Slip July 2026 Net Pay Rs 1,45,000",
            structured_data='{"net_income": 145000, "employer": "Apex Tech Solutions"}',
            processed=2,
        )
        # Chatbot uploaded document
        doc3 = Document(
            application_id=app.id,
            document_type=DocumentType.chatbot_upload,
            file_path="uploads/chat_uploads/rent_agreement.pdf",
            original_name="Rental_Agreement_Indiranagar.pdf",
            extracted_text="Registered Rental Agreement Flat 402 Royal Palms Indiranagar",
            structured_data='{"type": "rental_agreement", "address": "Flat 402 Royal Palms Indiranagar"}',
            processed=1,
        )
        db.add_all([doc1, doc2, doc3])
        db.commit()
        db.refresh(app)

        assert len(app.documents) == 3
        doc_types = {d.document_type.value for d in app.documents}
        assert "aadhaar" in doc_types
        assert "salary_slip" in doc_types
        assert "chatbot_upload" in doc_types
        print(f"  -> PASSED: 3 documents (including chatbot upload) attached to application {app.id}.")
        results["5_6_documents"] = "PASSED"

        # ─────────────────────────────────────────────────────────────
        # Test 7 & 8: Application Findings & Finding-Documents M:N
        # ─────────────────────────────────────────────────────────────
        print("\n[Test 7 & 8] Testing Application Findings & Finding-Document Association...")
        finding1 = ApplicationFinding(
            application_id=app.id,
            particular_id="PARTICULAR-INC-01",
            question="Verify monthly in-hand salary against payslip and bank statement",
            answer="Applicant receives ₹1,45,000 net monthly salary as verified by July 2026 payslip.",
            finding_type="income",
            status=FindingStatus.VERIFIED.value,
            confidence=98.0,
            evidence={"source_clause": "Net Pay Rs 1,45,000", "verified_by": "chat_agent"},
        )
        # Attach the payslip document to this finding
        finding1.documents.append(doc2)
        finding1.documents.append(doc3)
        db.add(finding1)
        db.commit()
        db.refresh(finding1)

        assert len(finding1.documents) == 2
        assert doc2 in finding1.documents
        assert doc3 in finding1.documents
        assert finding1 in doc2.findings
        print(f"  -> PASSED: Finding ID {finding1.id} created with 2 linked evidence documents via finding_documents.")
        results["7_8_findings_and_evidence"] = "PASSED"

        # ─────────────────────────────────────────────────────────────
        # Test 9: Chat Messages Scoped to Application
        # ─────────────────────────────────────────────────────────────
        print("\n[Test 9] Testing Chat Messages Scoped to Application...")
        session_id = f"sess_{int(datetime.utcnow().timestamp())}"
        msg1 = ChatMessage(
            application_id=app.id,
            session_id=session_id,
            role="user",
            message="Can you confirm if my address matches my rent agreement?",
        )
        msg2 = ChatMessage(
            application_id=app.id,
            session_id=session_id,
            role="assistant",
            message="Yes Vikramaditya, your address at Flat 402 Royal Palms matches both your application and uploaded agreement.",
        )
        db.add_all([msg1, msg2])
        db.commit()
        db.refresh(app)

        chat_history = (
            db.query(ChatMessage)
            .filter(ChatMessage.application_id == app.id, ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at.asc())
            .all()
        )
        assert len(chat_history) == 2
        assert chat_history[0].role == "user"
        assert chat_history[1].role == "assistant"
        print(f"  -> PASSED: Chat messages stored and retrieved successfully for session {session_id}.")
        results["9_chat_messages"] = "PASSED"

        # ─────────────────────────────────────────────────────────────
        # Test 10: Government Verification Screenshots
        # ─────────────────────────────────────────────────────────────
        print("\n[Test 10] Testing Government Verification Screenshots...")
        gov_shot = GovernmentVerificationScreenshot(
            application_id=app.id,
            verification_type="uidai_aadhaar",
            government_portal="UIDAI (myaadhaar.uidai.gov.in)",
            verification_reference="UIDAI-AUDIT-2026-9876",
            verification_status="VERIFIED",
            screenshot_path="uploads/gov_evidence/uidai_verify_987654321098.png",
            source_url="https://myaadhaar.uidai.gov.in/verify-aadhaar",
            notes="Aadhaar status confirmed active with age band 30-40 and mobile number verified.",
        )
        db.add(gov_shot)
        db.commit()
        db.refresh(app)

        assert len(app.gov_screenshots) == 1
        assert app.gov_screenshots[0].government_portal == "UIDAI (myaadhaar.uidai.gov.in)"
        print(f"  -> PASSED: Government verification screenshot evidence logged for UIDAI.")
        results["10_gov_screenshots"] = "PASSED"

        # ─────────────────────────────────────────────────────────────
        # Test 11: Granular Verification Results
        # ─────────────────────────────────────────────────────────────
        print("\n[Test 11] Testing Granular Verification Results...")
        vr1 = VerificationResult(
            application_id=app.id,
            check_type="name_match",
            status="PASS",
            confidence_score=99.2,
            details={"pan_name": "VIKRAMADITYA VERMA", "aadhaar_name": "Vikramaditya Verma", "similarity": 1.0},
        )
        vr2 = VerificationResult(
            application_id=app.id,
            check_type="income_eligibility",
            status="PASS",
            confidence_score=94.5,
            details={"declared_income": 145000, "foir_calculated": 0.38, "max_allowed_foir": 0.60},
        )
        db.add_all([vr1, vr2])
        db.commit()
        db.refresh(app)

        assert len(app.verification_results) == 2
        res_checks = {r.check_type for r in app.verification_results}
        assert "name_match" in res_checks
        assert "income_eligibility" in res_checks
        print(f"  -> PASSED: Granular verification results stored for name matching and income.")
        results["11_verification_results"] = "PASSED"

        # ─────────────────────────────────────────────────────────────
        # Test 12: Verification Report Dossier Creation
        # ─────────────────────────────────────────────────────────────
        print("\n[Test 12] Testing Verification Report Dossier Creation...")
        report = VerificationReport(
            application_id=app.id,
            verification_score=92.5,
            risk_score=15.0,
            fraud_flag=False,
            status="approved",
            extracted_info={"applicant_name": "Vikramaditya Verma", "loan_amount": 7500000.0},
            fraud_analysis={"risk_level": "LOW", "signals": []},
            verification_details={"kyc": "PASSED", "income": "PASSED"},
            pdf_path="reports/report_vikramaditya_verma.pdf",
            verification_mode="agentic",
            confidence_score=95.0,
            agent_summary="High quality applicant with excellent credit metrics and verified collateral.",
        )
        db.add(report)
        db.commit()
        db.refresh(app)

        assert app.report is not None
        assert app.report.verification_score == 92.5
        assert app.report.verification_mode == "agentic"
        print(f"  -> PASSED: Compiled verification dossier report created and linked 1:1.")
        results["12_report_dossier"] = "PASSED"

        # ─────────────────────────────────────────────────────────────
        # Test 13: Foreign-Key Referential Integrity
        # ─────────────────────────────────────────────────────────────
        print("\n[Test 13] Testing Foreign-Key Referential Integrity...")
        try:
            orphan_doc = Document(
                application_id=99999999,  # Non-existent application ID
                document_type=DocumentType.pan,
                file_path="uploads/orphan.png",
            )
            db.add(orphan_doc)
            db.commit()
            print("  -> FAILED: Orphan record was inserted without foreign key violation!")
            results["13_fk_integrity"] = "FAILED: Foreign key was not enforced"
        except (IntegrityError, Exception) as e:
            db.rollback()
            print("  -> PASSED: Foreign key constraint successfully prevented orphaned document creation.")
            results["13_fk_integrity"] = "PASSED"

        # ─────────────────────────────────────────────────────────────
        # Test 14: Cascade Delete Behavior
        # ─────────────────────────────────────────────────────────────
        print("\n[Test 14] Testing Cascade Delete Behavior...")
        app_id_to_delete = app.id
        db.delete(app)
        db.commit()

        # Verify child records are all cleaned up
        remaining_docs = db.query(Document).filter(Document.application_id == app_id_to_delete).count()
        remaining_chats = db.query(ChatMessage).filter(ChatMessage.application_id == app_id_to_delete).count()
        remaining_findings = db.query(ApplicationFinding).filter(ApplicationFinding.application_id == app_id_to_delete).count()
        remaining_shots = db.query(GovernmentVerificationScreenshot).filter(GovernmentVerificationScreenshot.application_id == app_id_to_delete).count()
        remaining_vrs = db.query(VerificationResult).filter(VerificationResult.application_id == app_id_to_delete).count()
        remaining_reports = db.query(VerificationReport).filter(VerificationReport.application_id == app_id_to_delete).count()

        assert remaining_docs == 0, f"Expected 0 docs, got {remaining_docs}"
        assert remaining_chats == 0, f"Expected 0 chats, got {remaining_chats}"
        assert remaining_findings == 0, f"Expected 0 findings, got {remaining_findings}"
        assert remaining_shots == 0, f"Expected 0 gov shots, got {remaining_shots}"
        assert remaining_vrs == 0, f"Expected 0 verification results, got {remaining_vrs}"
        assert remaining_reports == 0, f"Expected 0 reports, got {remaining_reports}"

        # Verify user was NOT deleted
        persisted_user = db.query(User).filter(User.id == user.id).first()
        assert persisted_user is not None
        print(f"  -> PASSED: Deleting Application {app_id_to_delete} cleanly cascaded to all child tables, and User {user.id} was preserved.")
        results["14_cascade_delete"] = "PASSED"

        # Cleanup test user
        db.delete(persisted_user)
        db.commit()

    finally:
        db.close()

    print("\n" + "=" * 70)
    print("FINAL TEST RESULTS SUMMARY:")
    all_passed = True
    for test_name, status in sorted(results.items()):
        print(f"  {test_name:30}: {status}")
        if "PASSED" not in status:
            all_passed = False
    print("=" * 70)
    return results


if __name__ == "__main__":
    url_arg = sys.argv[1] if len(sys.argv) > 1 else None
    run_all_tests(url_arg)
