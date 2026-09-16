"""Verification Report ORM model."""
from sqlalchemy import Column, Integer, Float, String, DateTime, ForeignKey, Boolean, JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from datetime import datetime
from app.db.database import Base

JSON_TYPE = JSON().with_variant(JSONB, "postgresql")

class VerificationReport(Base):
    __tablename__ = "verification_reports"
    id                   = Column(Integer, primary_key=True, index=True)
    application_id       = Column(Integer, ForeignKey("applications.id", ondelete="CASCADE"), unique=True, nullable=False)
    verification_score   = Column(Float, default=0.0)
    risk_score           = Column(Float, default=0.0)
    fraud_flag           = Column(Boolean, default=False)
    status               = Column(String(50), default="pending")
    extracted_info       = Column(JSON_TYPE)
    fraud_analysis       = Column(JSON_TYPE)
    verification_details = Column(JSON_TYPE)
    pdf_path             = Column(String(500))
    # ── Multi-agent (CrewAI) fields ───────────────────────────────────────
    verification_mode    = Column(String(20), default="rule_based")  # "rule_based" | "agentic"
    agent_summary        = Column(String(2000), nullable=True)
    agent_trace          = Column(JSON_TYPE, nullable=True)
    
    # ── Multi-agent Intelligence fields (Phase 2 Upgrade) ────────────────
    confidence_score     = Column(Float, default=0.0)
    execution_timeline   = Column(JSON_TYPE, nullable=True)
    agent_memory         = Column(JSON_TYPE, nullable=True)
    explainable_ai       = Column(JSON_TYPE, nullable=True)
    
    created_at           = Column(DateTime, default=datetime.utcnow)
    application = relationship("Application", back_populates="report")

