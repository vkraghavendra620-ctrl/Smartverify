"""
SmartVerify Multi-Agent Definitions
─────────────────────────────────────
Defines the specialised CrewAI agents for Milestone 2:

    1. Document Analyst
    2. Data Extraction Specialist
    3. Loan Verification Officer
    4. Government Verification Agent
    5. Compliance Reporter
"""

import logging
from crewai import Agent, LLM

from app.core.config import settings
from app.agents.tools import MemoryTool

logger = logging.getLogger(__name__)


def get_llm() -> LLM:
    return LLM(
        model=settings.CREWAI_MODEL,
        temperature=settings.CREWAI_TEMPERATURE,
    )


def build_agents(db_session, shared_context=None) -> dict:
    llm = get_llm()
    memory_tool = MemoryTool(shared_context=shared_context)

    document_analyst = Agent(
        role="Document Analyst",
        goal=(
            "Review the provided OCR text and Structured JSON to verify document quality, "
            "detect missing documents, validate document types, and flag unreadable documents."
        ),
        backstory=(
            "You are a meticulous document processing specialist. You never run OCR yourself; "
            "you only read the already-extracted OCR Text and Structured JSON to assess "
            "the quality and completeness of the submitted documents."
        ),
        tools=[],  # Consumes data from Task context directly
        llm=llm,
        verbose=True,
        allow_delegation=False,
    )

    extraction_specialist = Agent(
        role="Data Extraction Specialist",
        goal=(
            "Consume ONLY the Structured JSON provided. Validate extracted values, "
            "normalize names, addresses, and dates, detect missing mandatory fields, "
            "and produce a cleaned applicant profile."
        ),
        backstory=(
            "You are an expert in data normalization. You do NOT perform OCR. You trust "
            "the Structured JSON provided to you, but you clean and normalize its contents "
            "to ensure consistency and flag any missing required fields."
        ),
        tools=[],  # Consumes Structured JSON from Task context directly
        llm=llm,
        verbose=True,
        allow_delegation=False,
    )

    verification_officer = Agent(
        role="Loan Verification Officer",
        goal=(
            "Check eligibility, validate income and loan amount against the cleaned applicant "
            "profile and loan details, and produce a preliminary recommendation."
        ),
        backstory=(
            "You are a senior loan verification officer. You strictly use the provided "
            "Applicant Profile and Loan Details to determine eligibility and recommend "
            "approval, manual review, or rejection. You do NOT use RAG yet."
        ),
        tools=[memory_tool],
        llm=llm,
        verbose=True,
        allow_delegation=False,
    )

    gov_verification_agent = Agent(
        role="Government Verification Agent",
        goal=(
            "Consume Aadhaar and PAN numbers, read the simulated/existing verification status, "
            "compare OCR values with verification status, flag mismatches or missing verifications, "
            "and produce a verification summary."
        ),
        backstory=(
            "You are a government ID verification specialist. You check Aadhaar and PAN "
            "validity statuses. Since you cannot connect directly to UIDAI/IT portals yet, "
            "you rely on the provided mock verification status to assess ID validity."
        ),
        tools=[memory_tool],
        llm=llm,
        verbose=True,
        allow_delegation=False,
    )

    compliance_reporter = Agent(
        role="Compliance Reporter",
        goal=(
            "Consume outputs from all previous agents and generate the Final AI Output JSON, "
            "including the Final Recommendation, Human-in-the-loop Recommendation, and "
            "Consolidated AI Summary."
        ),
        backstory=(
            "You are responsible for the final audit and synthesis. You review the findings "
            "of the Document Analyst, Data Extraction Specialist, Verification Officer, and "
            "Government Verification Agent. You compile these into a final, structured decision."
        ),
        tools=[memory_tool],
        llm=llm,
        verbose=True,
        allow_delegation=False,
    )

    return {
        "document_analyst": document_analyst,
        "extraction_specialist": extraction_specialist,
        "verification_officer": verification_officer,
        "gov_verification_agent": gov_verification_agent,
        "compliance_reporter": compliance_reporter,
    }
