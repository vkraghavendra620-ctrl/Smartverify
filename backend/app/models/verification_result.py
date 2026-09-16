"""Granular Verification Result ORM model."""
from sqlalchemy import Column, Integer, Float, String, DateTime, ForeignKey, JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from datetime import datetime
from app.db.database import Base

JSON_TYPE = JSON().with_variant(JSONB, "postgresql")

class VerificationResult(Base):
    __tablename__ = "verification_results"

    id = Column(Integer, primary_key=True, index=True)
    application_id = Column(Integer, ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True)
    check_type = Column(String(50), nullable=False)  # 'aadhaar', 'pan', 'name_match', 'dob_match', 'kyc', 'income', 'fraud'
    status = Column(String(50), nullable=False)      # 'PASS', 'FAIL', 'WARNING', 'MANUAL_REVIEW'
    confidence_score = Column(Float, default=0.0)
    details = Column(JSON_TYPE, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    application = relationship("Application", back_populates="verification_results")
