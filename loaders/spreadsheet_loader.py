import os
import csv
import logging
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)


@dataclass
class CandidateRecord:
    """Input record plus extensible evidence-backed analysis results."""

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
    pre_selected: str = ""

    # Core analysis
    analysis_complete: bool = False
    analysis_error: str = ""
    domain_scores: dict = field(default_factory=dict)
    total_score: int = 0
    primary_domain: str = ""
    languages: dict = field(default_factory=dict)
    libraries: dict = field(default_factory=dict)
    repos_scanned: int = 0

    # Evidence-first profile intelligence
    skills: list = field(default_factory=list)
    evidence: list = field(default_factory=list)
    insights: dict = field(default_factory=dict)
    repository_insights: list = field(default_factory=list)
    profile_analysis: dict = field(default_factory=dict)

    # Supplementary sources
    resume_scores: dict = field(default_factory=dict)
    linkedin_scores: dict = field(default_factory=dict)
    resume_evaluated: bool = False
    linkedin_screened: bool = False

    # Hiring/screening output
    computed_status: str = ""
    status_reason: str = ""
    assigned_bucket: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _row_to_record(row_values: list[str], row_number: int) -> CandidateRecord:
    def safe_get(idx: int) -> str:
        if idx < len(row_values):
            return str(row_values[idx]).strip()
        return ""

    # Existing QIntern sheet layout is preserved for backwards compatibility.
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
    with open(file_path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        next(reader, None)
        for row_idx, row in enumerate(reader, start=2):
            if any(cell.strip() for cell in row):
                records.append(_row_to_record(row, row_idx))
    return records


def load_from_excel(file_path: str) -> list[CandidateRecord]:
    file_path = os.path.abspath(file_path)
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"Excel file not found: {file_path}")

    import openpyxl

    records: list[CandidateRecord] = []
    wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
    try:
        ws = wb.active
        rows_iter = ws.iter_rows(values_only=True)
        next(rows_iter, None)
        for row_idx, row in enumerate(rows_iter, start=2):
            values = [str(v) if v is not None else "" for v in row]
            if any(cell.strip() for cell in values):
                records.append(_row_to_record(values, row_idx))
    finally:
        wb.close()
    logger.info("Loaded %d candidate(s) from Excel: %s", len(records), file_path)
    return records


def load_candidates(file_path: str) -> list[CandidateRecord]:
    file_path = os.path.abspath(file_path)
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".csv":
        return load_from_csv(file_path)
    if ext in {".xlsx", ".xls"}:
        return load_from_excel(file_path)
    raise ValueError(f"Unsupported file extension '{ext}'. Expected .csv or .xlsx")
