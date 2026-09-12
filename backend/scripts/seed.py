"""Seed database with default users. Run: python scripts/seed.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.db.database import SessionLocal
from app.models.user import User, UserRole
from app.models.application import Application
from app.core.security import hash_password

def seed():
    db = SessionLocal()
    try:
        admin_user = db.query(User).filter(User.email == "admin@smartverify.com").first()
        if not admin_user:
            admin_user = User(name="Admin User", email="admin@smartverify.com",
                              password=hash_password("admin123"), role=UserRole.admin)
            db.add(admin_user)
            db.flush()

        if not db.query(User).filter(User.email == "officer@smartverify.com").first():
            db.add(User(name="Loan Officer", email="officer@smartverify.com",
                        password=hash_password("officer123"), role=UserRole.loan_officer))

        if db.query(Application).count() == 0:
            sample_app = Application(
                user_id=admin_user.id,
                applicant_name="Rajesh Sharma",
                loan_amount=500000.0,
                loan_type="Home Loan",
                branch="Main Branch",
                status="pending",
            )
            db.add(sample_app)

        db.commit()
        print("Seed complete.")
        print("  Admin:   admin@smartverify.com / admin123")
        print("  Officer: officer@smartverify.com / officer123")
    finally:
        db.close()

if __name__ == "__main__":
    seed()
