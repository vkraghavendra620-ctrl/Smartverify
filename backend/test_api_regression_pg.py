import os
import sys
from pathlib import Path

# Ensure PostgreSQL URL is set for application startup test
os.environ["DATABASE_URL"] = "postgresql+psycopg2://postgres@127.0.0.1:5432/smartverify"
os.environ["SECRET_KEY"] = "smartverify-super-secret-key-for-local-run"

backend_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(backend_dir))

from fastapi.testclient import TestClient
from app.main import app
from app.db.database import SessionLocal
from app.models.user import User, UserRole
from app.models.application import (
    Application,
    SiteVerification,
    JointApplicant,
    PropertyDetails,
    GovVerification,
)
from app.models.document import Document, DocumentType
from app.models.verification_report import VerificationReport
from app.core.security import hash_password

client = TestClient(app)

print("Testing /health endpoint...")
resp = client.get("/health")
print("  /health status:", resp.status_code, resp.json())
assert resp.status_code == 200
assert resp.json() == {"status": "healthy"}

print("\nSeeding admin user for API regression testing...")
db = SessionLocal()
admin_user = db.query(User).filter(User.email == "admin@smartverify.com").first()
if not admin_user:
    admin_user = User(
        name="Admin User",
        email="admin@smartverify.com",
        password=hash_password("admin123"),
        role=UserRole.admin,
    )
    db.add(admin_user)
    db.commit()
    db.refresh(admin_user)
print("  Admin user ready:", admin_user.email, f"(ID: {admin_user.id})")
db.close()

print("\nTesting /auth/login endpoint...")
login_resp = client.post("/auth/login", json={"email": "admin@smartverify.com", "password": "admin123"})
print("  /auth/login status:", login_resp.status_code)
assert login_resp.status_code == 200
token = login_resp.json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}

print("\nTesting /auth/me endpoint...")
me_resp = client.get("/auth/me", headers=headers)
print("  /auth/me status:", me_resp.status_code, me_resp.json()["email"])
assert me_resp.status_code == 200
assert me_resp.json()["email"] == "admin@smartverify.com"

print("\nTesting /applications/ endpoint (POST)...")
app_payload = {
    "applicant_name": "Suresh Kumar",
    "branch": "Koramangala Branch",
    "loan_type": "Personal Loan",
    "loan_amount": 350000.0,
    "loan_tenure": 36,
    "interest_rate": 11.5,
    "aadhaar_number": "112233445566",
    "pan_number": "PQRST5678G",
    "dob": "1992-05-14",
    "gender": "Male",
    "address": "7th Block, Koramangala, Bangalore",
    "father_name": "Ramesh Kumar",
}
create_app_resp = client.post("/applications/", json=app_payload, headers=headers)
print("  POST /applications status:", create_app_resp.status_code)
assert create_app_resp.status_code == 201
created_app_id = create_app_resp.json()["id"]

try:
    print(f"\nTesting /applications/{created_app_id} (GET)...")
    get_app_resp = client.get(f"/applications/{created_app_id}", headers=headers)
    print("  GET /applications/{id} status:", get_app_resp.status_code, get_app_resp.json()["applicant_name"])
    assert get_app_resp.status_code == 200
    assert get_app_resp.json()["applicant_name"] == "Suresh Kumar"
    assert get_app_resp.json()["aadhaar_number"] == "112233445566"
    assert get_app_resp.json()["pan_number"] == "PQRST5678G"

    print("\nTesting Document Creation and Retrieval...")
    db = SessionLocal()
    test_doc = Document(
        application_id=created_app_id,
        document_type=DocumentType.pan,
        file_path="uploads/test_pan_sample.png",
        original_name="PAN_Card_Sample.png",
        extracted_text="INCOME TAX DEPARTMENT PQRST5678G SURESH KUMAR",
        processed=2,
    )
    db.add(test_doc)
    db.commit()
    db.refresh(test_doc)
    print(f"  Document created in DB (ID: {test_doc.id})")
    db.close()

    docs_resp = client.get(f"/documents/{created_app_id}", headers=headers)
    print("  GET /documents/{id} status:", docs_resp.status_code, len(docs_resp.json()), "documents")
    assert docs_resp.status_code == 200
    assert len(docs_resp.json()) == 1
    assert docs_resp.json()[0]["document_type"] == "pan"

    print("\nTesting Existing Model Relationships (JointApplicant, PropertyDetails, SiteVerification, GovVerification)...")
    db = SessionLocal()
    app_record = db.query(Application).filter(Application.id == created_app_id).first()

    joint_app = JointApplicant(
        application_id=created_app_id,
        index=1,
        relationship_type="Spouse",
        mobile="9876543210",
        email="spouse@example.com",
    )
    prop_details = PropertyDetails(
        application_id=created_app_id,
        property_type="Residential Apartment",
        address="Flat 101, Sunshine Heights, Bangalore",
        market_value=5000000.0,
    )
    site_verif = SiteVerification(
        application_id=created_app_id,
        gps_coordinates="12.9352,77.6245",
        officer_name="Verification Officer John",
        property_condition="Good",
    )
    gov_verif = GovVerification(
        application_id=created_app_id,
        pan_aadhaar_link_status="LINKED",
        aadhaar_validity_status="VALID",
        officer_name="Gov Officer Jane",
    )
    db.add_all([joint_app, prop_details, site_verif, gov_verif])
    db.commit()
    db.refresh(app_record)

    assert len(app_record.joint_applicants) == 1
    assert app_record.joint_applicants[0].relationship_type == "Spouse"
    assert app_record.property_details is not None
    assert app_record.property_details.property_type == "Residential Apartment"
    assert app_record.site_verification is not None
    assert app_record.site_verification.property_condition == "Good"
    assert app_record.gov_verification is not None
    assert app_record.gov_verification.pan_aadhaar_link_status == "LINKED"
    print("  -> PASSED: JointApplicant, PropertyDetails, SiteVerification, and GovVerification relationships verified.")
    db.close()

    print("\nTesting Verification Report Model Access...")
    db = SessionLocal()
    report = VerificationReport(
        application_id=created_app_id,
        verification_score=88.5,
        risk_score=12.0,
        status="approved",
        verification_mode="agentic",
        extracted_info={"applicant_name": "Suresh Kumar"},
        agent_summary="Verification completed cleanly across all checks.",
    )
    db.add(report)
    db.commit()

    app_record = db.query(Application).filter(Application.id == created_app_id).first()
    assert app_record.report is not None
    assert app_record.report.verification_score == 88.5
    assert app_record.report.verification_mode == "agentic"
    print("  -> PASSED: VerificationReport model access and 1:1 relationship verified.")
    db.close()

    print("\nTesting /dashboard/stats (GET)...")
    stats_resp = client.get("/dashboard/stats", headers=headers)
    print("  GET /dashboard/stats status:", stats_resp.status_code, stats_resp.json())
    assert stats_resp.status_code == 200

finally:
    # Clean up test application and all cascading relations
    print("\nCleaning up test application...")
    db = SessionLocal()
    app_to_delete = db.query(Application).filter(Application.id == created_app_id).first()
    if app_to_delete:
        db.delete(app_to_delete)
        db.commit()
        print(f"  Test application {created_app_id} and all cascaded children cleanly removed.")
    db.close()

print("\nALL EXISTING API AND RELATIONSHIP REGRESSION TESTS PASSED ON POSTGRESQL!")
