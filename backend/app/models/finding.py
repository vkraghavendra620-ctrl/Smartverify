"""Application Finding and Finding-Documents Association ORM models."""
from sqlalchemy import Column, Integer, Float, String, Text, DateTime, ForeignKey, Table, JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from datetime import datetime
import enum
from app.db.database import Base

# Cross-dialect JSONB support: PostgreSQL uses JSONB, fallback to JSON for SQLite
JSON_TYPE = JSON().with_variant(JSONB, "postgresql")

class FindingStatus(str, enum.Enum):
    VERIFIED = "VERIFIED"
    NOT_VERIFIED = "NOT_VERIFIED"
    REVIEW = "REVIEW"
    NOT_AVAILABLE = "NOT_AVAILABLE"

# Many-to-Many association table linking application findings to source documents
finding_documents = Table(
    "finding_documents",
    Base.metadata,
    Column("finding_id", Integer, ForeignKey("application_findings.id", ondelete="CASCADE"), primary_key=True),
    Column("document_id", Integer, ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
    Column("created_at", DateTime, default=datetime.utcnow, nullable=False),
)

class ApplicationFinding(Base):
    __tablename__ = "application_findings"

    id = Column(Integer, primary_key=True, index=True)
    application_id = Column(Integer, ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True)
    particular_id = Column(String(100), nullable=True)
    question = Column(Text, nullable=True)
    answer = Column(Text, nullable=True)
    finding_type = Column(String(50), nullable=True)  # 'income', 'identity', 'employment', 'collateral'
    status = Column(String(30), default=FindingStatus.REVIEW.value, nullable=False)
    confidence = Column(Float, nullable=True)
    evidence = Column(JSON_TYPE, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    application = relationship("Application", back_populates="findings")
    documents = relationship("Document", secondary=finding_documents, back_populates="findings")
