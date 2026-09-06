import os
import re
import csv
import logging
from typing import Optional
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)

@dataclass
class CandidateRecord:
    """Represents a single candidate application record."""

    row_number: int = 0
    timestamp: str = ""
    candidate_id: str = ""
    full_name: str = ""
    email: str = ""
    phone: str = ""
    discord: str = ""
    
    classical_github: str = ""
    classical_deployed: str = ""
    quantum_github: str = ""
    quantum_deployed: str = ""
    
    linkedin_url: str = ""
    resume_url: str = ""
    skill_bucket: str = ""
    pre_selected: str = ""  # From the spreadsheet 'Selected' column

    # Analysis results (populated later by analyzers)
    analysis_complete: bool = False
    analysis_error: str = ""
    domain_scores: dict = field(default_factory=dict)
    total_score: int = 0
    primary_domain: str = ""
    languages: dict = field(default_factory=dict)
    libraries: dict = field(default_factory=dict)
    resume_scores: dict = field(default_factory=dict)
    linkedin_scores: dict = field(default_factory=dict)
    repos_scanned: int = 0

    # Shortlisting results (populated later by scoring)
    computed_status: str = ""  # SHORTLIST / REVIEW / REJECT
    status_reason: str = ""
    assigned_bucket: str = ""  # Computed skill bucket

    def to_dict(self) -> dict:
        """Serialize the record to a plain dictionary."""
        return asdict(self)


def _row_to_record(row_values: list[str], row_number: int) -> CandidateRecord:
    def safe_get(idx):
        if idx < len(row_values):
            return str(row_values[idx]).strip()
        return ""

    # Hardcoded to the user's specific Google Sheet column layout
    # C(2) = ID
    # H(7) = Classical Github
    # I(8) = Classical Deployed
    # J(9) = Quantum Github
    # K(10) = Quantum Deployed
    # L(11) = Linkedin
    # M(12) = Resume/CV
    return CandidateRecord(
        row_number=row_number,
        timestamp=safe_get(0),
        email=safe_get(1) or safe_get(4),
        candidate_id=safe_get(2),
        full_name=safe_get(3),
        phone=safe_get(5),
        discord=safe_get(6),
        classical_github=safe_get(7),
        classical_deployed=safe_get(8),
        quantum_github=safe_get(9),
        quantum_deployed=safe_get(10),
        linkedin_url=safe_get(11),
        resume_url=safe_get(12),
        skill_bucket=safe_get(13),
        pre_selected=safe_get(14),
    )


def load_from_csv(file_path: str) -> list[CandidateRecord]:
    file_path = os.path.abspath(file_path)
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"CSV file not found: {file_path}")

    records: list[CandidateRecord] = []
    try:
        with open(file_path, "r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.reader(fh)
            next(reader, None) # skip header
            for row_idx, row in enumerate(reader, start=2):
                if not any(cell.strip() for cell in row): continue
                records.append(_row_to_record(row, row_number=row_idx))
    except csv.Error as exc:
        logger.error(f"CSV parsing error in {file_path}: {exc}")
        raise
    return records


def load_from_excel(file_path: str) -> list[CandidateRecord]:
    file_path = os.path.abspath(file_path)
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"Excel file not found: {file_path}")

    try:
        import openpyxl
    except ImportError:
        logger.error("openpyxl is required to load Excel files.")
        raise

    records: list[CandidateRecord] = []
    try:
        wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
        ws = wb.active
        rows_iter = ws.iter_rows(values_only=True)
        next(rows_iter, None) # skip header

        for row_idx, row in enumerate(rows_iter, start=2):
            values = [str(v) if v is not None else "" for v in row]
            if not any(cell.strip() for cell in values): continue
            records.append(_row_to_record(values, row_number=row_idx))
        wb.close()
    except Exception as exc:
        logger.error(f"Error reading Excel file {file_path}: {exc}")
        raise

    logger.info(f"Loaded {len(records)} candidate(s) from Excel: {file_path}")
    return records


def load_candidates(file_path: str) -> list[CandidateRecord]:
    file_path = os.path.abspath(file_path)
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".csv": return load_from_csv(file_path)
    elif ext in (".xlsx", ".xls"): return load_from_excel(file_path)
    else: raise ValueError(f"Unsupported file extension '{ext}'. Expected .csv or .xlsx")
