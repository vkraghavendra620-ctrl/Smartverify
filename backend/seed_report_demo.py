"""
SmartVerify - Report Generation Live Demo Seeder

Creates ONE isolated synthetic application for demonstrating
the Report Generation pipeline.

This script does NOT run:
- OCR
- Gemini Vision
- RAG
- Fraud Detection
- Government APIs

It only creates already-verified DEMO inputs for the
Report Generation module.
"""

import json
from datetime import datetime

from app.db.database import SessionLocal
from app.models.application import (
    Application,
    ApplicationStatus,
    JointApplicant,
    SiteVerification,
    GovVerification,
    PropertyDetails,
)
from app.models.document import Document, DocumentType
from app.models.finding import ApplicationFinding, FindingStatus
from app.models.government_screenshot import GovernmentVerificationScreenshot
from app.models.user import User


DEMO_TAG = "SMARTVERIFY_REPORT_DEMO"


def get_demo_user(db):
    """Use an existing user if available; otherwise create a demo user."""

    user = db.query(User).first()

    if user:
        return user

    # This is only a fallback for an empty database.
    user = User(
        username="report_demo_user",
        email="report.demo@smartverify.local",
    )

    # Some versions of the User model may have additional required fields.
    # If this fails, the database already contains users and this fallback
    # will not be needed.

    db.add(user)
    db.flush()

    return user


def add_document(
    db,
    application_id,
    document_type,
    original_name,
    structured_data,
):
    document = Document(
        application_id=application_id,
        document_type=document_type,
        file_path=f"/demo-evidence/{original_name}",
        original_name=original_name,
        extracted_text="Synthetic verified demo document. Not a real document.",
        structured_data=json.dumps(structured_data),
        processed=2,
    )

    db.add(document)
    db.flush()

    return document


def add_finding(
    db,
    application_id,
    particular_id,
    question,
    answer,
    finding_type,
    documents=None,
    evidence=None,
):
    finding = ApplicationFinding(
        application_id=application_id,
        particular_id=particular_id,
        question=question,
        answer=answer,
        finding_type=finding_type,
        status=FindingStatus.VERIFIED.value,
        confidence=0.98,
        evidence=evidence or {},
    )

    if documents:
        finding.documents.extend(documents)

    db.add(finding)
    db.flush()

    return finding


def main():
    db = SessionLocal()

    try:
        # ---------------------------------------------------------
        # 1. Prevent accidental duplicate demo applications
        # ---------------------------------------------------------
        existing = (
            db.query(Application)
            .filter(Application.applicant_name == "Sunil Kumar")
            .filter(Application.applicant_email == "sunil.kumar.demo@smartverify.local")
            .first()
        )

        if existing:
            print()
            print("Demo application already exists.")
            print(f"APPLICATION_ID={existing.id}")
            print()
            return

        # ---------------------------------------------------------
        # 2. User
        # ---------------------------------------------------------
        user = get_demo_user(db)

        # ---------------------------------------------------------
        # 3. Application
        # ---------------------------------------------------------
        application = Application(
            user_id=user.id,
            applicant_name="Sunil Kumar",
            branch="Mangalore Main Branch",
            loan_type="Vehicle Loan",
            loan_amount=850000.0,
            loan_tenure=60,
            interest_rate=9.25,

            # Synthetic identity data
            aadhaar_number="999999999999",
            pan_number="ABCDE1234F",
            dob="15-08-1999",
            gender="Male",

            address=(
                "24 MG Road, Hampankatta, "
                "Mangalore, Karnataka - 575001"
            ),
            father_name="Kumar Kumar",
            applicant_mobile="9000000001",
            applicant_email="sunil.kumar.demo@smartverify.local",

            status=ApplicationStatus.pending,
        )

        db.add(application)
        db.flush()

        app_id = application.id

        print()
        print("=" * 70)
        print("SMARTVERIFY REPORT GENERATION DEMO")
        print("=" * 70)
        print(f"Created application: {app_id}")
        print()

        # ---------------------------------------------------------
        # 4. Guarantor
        # ---------------------------------------------------------
        guarantor = JointApplicant(
            application_id=app_id,
            index=1,
            name="Arun Kumar",
            applicant_type="guarantor",
            relationship_type="Brother",
            pan_number="FGHIJ5678K",
            aadhaar_number="888888888888",
            dob="22-03-1995",
            address=(
                "18 K S Rao Road, "
                "Mangalore, Karnataka - 575001"
            ),
            mobile="9000000002",
            email="arun.kumar.demo@smartverify.local",
            income=65000.0,
            occupation="Senior Technician",
            father_name="Kumar Kumar",
            years_in_occupation=7,
            remarks="Synthetic demo guarantor record.",
        )

        db.add(guarantor)
        db.flush()

        # ---------------------------------------------------------
        # 5. Site / Residence verification
        # ---------------------------------------------------------
        site = SiteVerification(
            application_id=app_id,
            gps_coordinates="12.8698, 74.8422",
            officer_name="Demo Verification Officer",
            date="20-09-2026",
            time="11:30 AM",
            property_condition="Good",
            construction_quality="Good",
            boundary_present="Yes",
            road_access="Available",
            utilities_available="Electricity, water and road access available",
            remarks=(
                "Residence physically visited during the synthetic "
                "verification scenario. Occupancy and address were "
                "reported as satisfactory."
            ),
        )

        db.add(site)

        # ---------------------------------------------------------
        # 6. Property / collateral information
        # ---------------------------------------------------------
        property_details = PropertyDetails(
            application_id=app_id,
            property_type="Residential",
            address=(
                "24 MG Road, Hampankatta, "
                "Mangalore, Karnataka - 575001"
            ),
            village_city="Mangalore",
            taluk="Mangalore",
            district="Dakshina Kannada",
            state="Karnataka",
            pin_code="575001",
            survey_number="DEMO-SURVEY-102",
            khata_number="DEMO-KHATA-204",
            property_area="1200 sq.ft",
            market_value=4500000.0,
            loan_security_value=3000000.0,
        )

        db.add(property_details)

        # ---------------------------------------------------------
        # 7. Documents
        # ---------------------------------------------------------
        aadhaar_doc = add_document(
            db,
            app_id,
            DocumentType.aadhaar,
            "demo_aadhaar_sunil.pdf",
            {
                "name": "Sunil Kumar",
                "verification_status": "VERIFIED",
                "document_scope": "synthetic_demo",
            },
        )

        pan_doc = add_document(
            db,
            app_id,
            DocumentType.pan,
            "demo_pan_sunil.pdf",
            {
                "name": "Sunil Kumar",
                "pan": "ABCDE1234F",
                "verification_status": "VERIFIED",
                "document_scope": "synthetic_demo",
            },
        )

        salary_doc = add_document(
            db,
            app_id,
            DocumentType.salary_slip,
            "demo_salary_slip_aug_2026.pdf",
            {
                "employer": "Tech Solutions India Pvt Ltd",
                "designation": "Software Engineer",
                "monthly_income": 75000,
                "verification_status": "VERIFIED",
                "document_scope": "synthetic_demo",
            },
        )

        form16_doc = add_document(
            db,
            app_id,
            DocumentType.form_16,
            "demo_form16_2025_26.pdf",
            {
                "financial_year": "2025-26",
                "employer": "Tech Solutions India Pvt Ltd",
                "verification_status": "VERIFIED",
                "document_scope": "synthetic_demo",
            },
        )

        bank_doc = add_document(
            db,
            app_id,
            DocumentType.bank_statement,
            "demo_bank_statement.pdf",
            {
                "account_holder": "Sunil Kumar",
                "period": "April 2026 - September 2026",
                "regular_credits": True,
                "verification_status": "VERIFIED",
                "document_scope": "synthetic_demo",
            },
        )

        residence_doc = add_document(
            db,
            app_id,
            DocumentType.residence_proof,
            "demo_residence_proof.pdf",
            {
                "address_verified": True,
                "verification_status": "VERIFIED",
                "document_scope": "synthetic_demo",
            },
        )

        vehicle_doc = add_document(
            db,
            app_id,
            DocumentType.vehicle_document,
            "demo_vehicle_rc.pdf",
            {
                "vehicle_type": "Passenger Car",
                "registration_status": "VERIFIED",
                "document_scope": "synthetic_demo",
            },
        )

        invoice_doc = add_document(
            db,
            app_id,
            DocumentType.invoice,
            "demo_vehicle_invoice.pdf",
            {
                "dealer": "Demo Motors Pvt Ltd",
                "invoice_status": "VERIFIED",
                "document_scope": "synthetic_demo",
            },
        )

        # ---------------------------------------------------------
        # 8. Government verification
        # ---------------------------------------------------------
        gov = GovVerification(
            application_id=app_id,
            pan_aadhaar_link_status="VERIFIED",
            tax_receipt_status="VERIFIED",
            aadhaar_validity_status="VERIFIED",
            aadhaar_screenshot_path="/demo-evidence/gov/aadhaar.png",
            officer_name="Demo Government Verification Officer",
            timestamp="20-09-2026 12:15 PM",
            remarks=(
                "Synthetic government verification result created "
                "for academic demonstration only."
            ),
            screenshot_path="/demo-evidence/gov/aadhaar.png",
            verification_screenshots="/demo-evidence/gov/aadhaar.png",
        )

        db.add(gov)
        db.flush()

        # ---------------------------------------------------------
        # 9. Government screenshot evidence
        # ---------------------------------------------------------
        gov_aadhaar = GovernmentVerificationScreenshot(
            application_id=app_id,
            verification_type="uidai_aadhaar",
            government_portal="UIDAI",
            verification_reference="DEMO-UIDAI-001",
            verification_status="VERIFIED",
            screenshot_path="/demo-evidence/gov/aadhaar.png",
            captured_at=datetime.utcnow(),
            source_url="https://demo.smartverify.local/verification",
            notes="Synthetic demo evidence. Not an actual government record.",
        )

        gov_pan = GovernmentVerificationScreenshot(
            application_id=app_id,
            verification_type="pan_aadhaar_link",
            government_portal="Income Tax e-Filing",
            verification_reference="DEMO-PAN-001",
            verification_status="VERIFIED",
            screenshot_path="/demo-evidence/gov/pan-aadhaar.png",
            captured_at=datetime.utcnow(),
            source_url="https://demo.smartverify.local/verification",
            notes="Synthetic demo evidence. Not an actual government record.",
        )

        db.add_all([gov_aadhaar, gov_pan])

        # ---------------------------------------------------------
        # 10. Findings mapped to report Particulars
        # ---------------------------------------------------------

        add_finding(
            db,
            app_id,
            "P1",
            "Name and address of applicant",
            (
                "Sunil Kumar resides at 24 MG Road, Hampankatta, "
                "Mangalore, Karnataka - 575001."
            ),
            "identity",
            [aadhaar_doc, residence_doc],
        )

        add_finding(
            db,
            app_id,
            "P2A",
            "Physical verification of residence",
            (
                "Residence visit completed on 20-09-2026. "
                "Property condition and construction quality were "
                "reported as good, with road access and utilities available."
            ),
            "residence",
            [residence_doc],
        )

        add_finding(
            db,
            app_id,
            "P2D",
            "Residence verification status",
            "Residence details verified and found satisfactory.",
            "residence",
            [residence_doc],
        )

        add_finding(
            db,
            app_id,
            "P2E",
            "Aadhaar verification",
            "Aadhaar identity details verified in the upstream verification output.",
            "identity",
            [aadhaar_doc],
        )

        add_finding(
            db,
            app_id,
            "P3",
            "KYC re-verification",
            "KYC identity documents were verified.",
            "identity",
            [aadhaar_doc, pan_doc],
        )

        add_finding(
            db,
            app_id,
            "P4A",
            "Income supporting documents",
            "Salary slip and Form 16 were available and verified.",
            "income",
            [salary_doc, form16_doc],
        )

        add_finding(
            db,
            app_id,
            "P4B",
            "Employment and income",
            (
                "Applicant is employed as a Software Engineer with "
                "Tech Solutions India Pvt Ltd. Reported monthly income "
                "is ₹75,000 with approximately five years of experience."
            ),
            "employment",
            [salary_doc, form16_doc],
        )

        add_finding(
            db,
            app_id,
            "P5",
            "Bank statement",
            (
                "Bank statement for April 2026 to September 2026 was "
                "available and marked verified in the upstream findings."
            ),
            "income",
            [bank_doc],
        )

        add_finding(
            db,
            app_id,
            "P6",
            "Track record / adverse information",
            "No adverse finding was recorded in the supplied verification results.",
            "track_record",
            [bank_doc],
        )

        add_finding(
            db,
            app_id,
            "P7A",
            "New vehicle documentation",
            (
                "Vehicle invoice and vehicle registration documentation "
                "were available and marked verified."
            ),
            "collateral",
            [invoice_doc, vehicle_doc],
        )

        add_finding(
            db,
            app_id,
            "P8",
            "General information and verification opinion",
            (
                "The supplied verification inputs are internally consistent "
                "for the synthetic demonstration case."
            ),
            "overall",
            [aadhaar_doc, salary_doc, bank_doc, vehicle_doc],
        )

        # ---------------------------------------------------------
        # Commit
        # ---------------------------------------------------------
        db.commit()

        print()
        print("=" * 70)
        print("DEMO DATA CREATED SUCCESSFULLY")
        print("=" * 70)
        print(f"APPLICATION_ID={app_id}")
        print("Applicant: Sunil Kumar")
        print("Loan: Vehicle Loan")
        print("Amount: Rs. 8,50,000")
        print("Guarantor: Arun Kumar")
        print("Findings: P1-P8")
        print("Government verification: synthetic VERIFIED inputs")
        print()
        print("Next URL:")
        print(f"http://localhost:3000/report/{app_id}/review")
        print("=" * 70)

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


if __name__ == "__main__":
    main()