"""Government Verification Screenshot Evidence ORM model."""
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from app.db.database import Base

class GovernmentVerificationScreenshot(Base):
    __tablename__ = "government_verification_screenshots"

    id = Column(Integer, primary_key=True, index=True)
    application_id = Column(Integer, ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True)
    verification_type = Column(String(50), nullable=False)  # 'uidai_aadhaar', 'pan_aadhaar_link', 'tax_receipt'
    government_portal = Column(String(100), nullable=False)  # e.g., 'UIDAI', 'Income Tax e-Filing'
    verification_reference = Column(String(100), nullable=True)
    verification_status = Column(String(50), nullable=True)  # 'VERIFIED', 'FAILED', 'PENDING'
    screenshot_path = Column(String(1000), nullable=False)  # disk reference, never raw binary
    captured_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    source_url = Column(String(1000), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    application = relationship("Application", back_populates="gov_screenshots")
