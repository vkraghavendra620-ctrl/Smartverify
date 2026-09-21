"""Reverification Report ORM model for SmartVerify Report Generation System."""
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, JSON, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from datetime import datetime
from app.db.database import Base

# Cross-dialect JSONB support: PostgreSQL uses JSONB, fallback to JSON for SQLite
JSON_TYPE = JSON().with_variant(JSONB, "postgresql")


class ReverificationReport(Base):
    __tablename__ = "reverification_reports"
    __table_args__ = (
        UniqueConstraint("application_id", "version", name="uq_reverification_reports_application_version"),
    )

    id = Column(Integer, primary_key=True, index=True)
    application_id = Column(Integer, ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True)
    version = Column(Integer, default=1, nullable=False)
    template_key = Column(String(100), default="standard_reverification", nullable=False)
    template_version = Column(String(50), default="v1.0", nullable=False)
    status = Column(String(50), default="draft", nullable=False)  # draft, generated, reviewed, approved, finalized
    content = Column(JSON_TYPE, nullable=True)
    snapshot = Column(JSON_TYPE, nullable=True)
    row_version = Column(Integer, default=1, nullable=False)
    pdf_path = Column(String(500), nullable=True)
    pdf_sha256 = Column(String(64), nullable=True)
    prepared_by = Column(String(255), nullable=True)
    finalized_by = Column(String(255), nullable=True)
    finalized_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    application = relationship("Application", back_populates="reverification_reports")
