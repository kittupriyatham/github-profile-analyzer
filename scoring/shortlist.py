"""Shortlisting engine for QIntern candidate screening.

Assigns candidates to buckets (``QC/ML``, ``QC/SE``, ``ML/AI/DS``,
``Full Stack``, ``Systems``) and statuses (``SHORTLIST``, ``REVIEW``,
``REJECT``) based on weighted domain scores produced by the scorer module.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ShortlistResult:
    """Immutable outcome of the shortlisting decision for a single candidate."""

    status: str           # 'SHORTLIST', 'REVIEW', 'REJECT'
    assigned_bucket: str  # 'QC/ML', 'QC/SE', 'ML/AI/DS', 'Full Stack', 'Systems'
    reason: str           # Human-readable explanation
    priority: int         # 1 = highest, 5 = lowest within status
    quantum_score: int
    ml_score: int
    software_score: int
    cloud_score: int
    math_score: int
    total_score: int


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _determine_priority(
    has_quantum: bool,
    has_ml: bool,
    has_software: bool,
    has_cloud: bool,
    quantum_score: int,
    ml_score: int,
    software_score: int,
) -> int:
    """Assign a priority rank (1–5) within the candidate's status tier.

    1 – Quantum + ML with strong scores
    2 – Quantum + SE with strong scores
    3 – Strong ML/AI
    4 – Strong SE
    5 – Everything else
    """
    if has_quantum and has_ml and (quantum_score >= 15 or ml_score >= 15):
        return 1
    if has_quantum and has_software and (quantum_score >= 15 or software_score >= 15):
        return 2
    if has_ml and ml_score >= 15:
        return 3
    if has_software and software_score >= 15:
        return 4
    return 5


def _determine_bucket(
    has_quantum: bool,
    has_ml: bool,
    has_software: bool,
    has_cloud: bool,
    has_math: bool,
    software_score: int,
    cloud_score: int,
) -> str:
    """Map domain flags to the best-fit candidate bucket.

    Priority order:
      Quantum + ML  → 'QC/ML'
      Quantum + SE  → 'QC/SE'
      Quantum + Math→ 'QC/ML'  (math foundations support QC/ML track)
      ML (no QC)    → 'ML/AI/DS'
      SE + Cloud    → 'Full Stack'
      SE heavy, low-level indicators → 'Systems'
      Fallback      → 'ML/AI/DS' (default review bucket)
    """
    if has_quantum and has_ml:
        return "QC/ML"
    if has_quantum and has_math:
        return "QC/ML"
    if has_quantum and has_software:
        return "QC/SE"
    if has_quantum:
        return "QC/SE"
    if has_ml:
        return "ML/AI/DS"
    if has_software and has_cloud and software_score >= 15 and cloud_score >= 15:
        return "Full Stack"
    if has_software and software_score >= 20 and not has_cloud:
        return "Systems"
    if has_software:
        return "Full Stack"
    return "ML/AI/DS"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def determine_shortlist(
    scores: dict[str, int],
    has_quantum: bool,
    has_ml: bool,
    has_software: bool,
    has_cloud: bool,
    has_math: bool,
    repos_scanned: int = 0,
    resume_evaluated: bool = False,
    linkedin_screened: bool = False,
) -> ShortlistResult:
    """Apply QIntern shortlisting rules and return a ``ShortlistResult``.

    Parameters
    ----------
    scores:
        Domain score mapping, e.g.
        ``{"Quantum Technology": 25, "ML/DL/DS/AI": 18, ...}``.
    has_quantum, has_ml, has_software, has_cloud, has_math:
        Boolean detection flags (True when the domain score ≥ threshold).
    repos_scanned:
        Number of GitHub repositories that were analyzed.
    resume_evaluated:
        Whether a resume was supplied and parsed.
    linkedin_screened:
        Whether a LinkedIn profile was supplied and parsed.

    Returns
    -------
    ShortlistResult
        The shortlisting decision with bucket, status, priority, and reason.
    """
    quantum_score: int = scores.get("Quantum Technology", 0)
    ml_score: int = scores.get("ML/DL/DS/AI", 0)
    software_score: int = scores.get("Software Engineering", 0)
    cloud_score: int = scores.get("Cloud & DevOps", 0)
    math_score: int = scores.get("Mathematics & Foundations", 0)
    total_score: int = sum(scores.values())

    # --- Determine bucket --------------------------------------------------
    bucket = _determine_bucket(
        has_quantum=has_quantum,
        has_ml=has_ml,
        has_software=has_software,
        has_cloud=has_cloud,
        has_math=has_math,
        software_score=software_score,
        cloud_score=cloud_score,
    )

    # --- Determine priority ------------------------------------------------
    priority = _determine_priority(
        has_quantum=has_quantum,
        has_ml=has_ml,
        has_software=has_software,
        has_cloud=has_cloud,
        quantum_score=quantum_score,
        ml_score=ml_score,
        software_score=software_score,
    )

    # --- Apply shortlisting rules (most specific first) --------------------

    # REJECT: insufficient data
    if total_score < 10:
        data_sources: list[str] = []
        if repos_scanned:
            data_sources.append(f"{repos_scanned} repos")
        if resume_evaluated:
            data_sources.append("resume")
        if linkedin_screened:
            data_sources.append("LinkedIn")
        sources_note = f" (scanned: {', '.join(data_sources)})" if data_sources else ""
        return ShortlistResult(
            status="REJECT",
            assigned_bucket=bucket,
            reason=f"Insufficient profile data — total score {total_score}{sources_note}",
            priority=5,
            quantum_score=quantum_score,
            ml_score=ml_score,
            software_score=software_score,
            cloud_score=cloud_score,
            math_score=math_score,
            total_score=total_score,
        )

    # REJECT: no relevant domain signals
    if not has_quantum and not has_ml:
        return ShortlistResult(
            status="REJECT",
            assigned_bucket=bucket,
            reason="No relevant domain signals (neither Quantum nor ML/AI detected)",
            priority=5,
            quantum_score=quantum_score,
            ml_score=ml_score,
            software_score=software_score,
            cloud_score=cloud_score,
            math_score=math_score,
            total_score=total_score,
        )

    # SHORTLIST: Quantum + ML or SE
    if has_quantum and (has_ml or has_software):
        reason_parts: list[str] = [f"Quantum ({quantum_score})"]
        if has_ml:
            reason_parts.append(f"ML/AI ({ml_score})")
        if has_software:
            reason_parts.append(f"SE ({software_score})")
        return ShortlistResult(
            status="SHORTLIST",
            assigned_bucket=bucket,
            reason=f"Multi-domain fit: {' + '.join(reason_parts)}",
            priority=priority,
            quantum_score=quantum_score,
            ml_score=ml_score,
            software_score=software_score,
            cloud_score=cloud_score,
            math_score=math_score,
            total_score=total_score,
        )

    # SHORTLIST: Quantum + Math foundations
    if has_quantum and has_math:
        return ShortlistResult(
            status="SHORTLIST",
            assigned_bucket="QC/ML",
            reason=f"Quantum ({quantum_score}) + Math foundations ({math_score})",
            priority=priority,
            quantum_score=quantum_score,
            ml_score=ml_score,
            software_score=software_score,
            cloud_score=cloud_score,
            math_score=math_score,
            total_score=total_score,
        )

    # REVIEW: Quantum only (no ML/SE)
    if has_quantum and not has_ml and not has_software:
        return ShortlistResult(
            status="REVIEW",
            assigned_bucket="QC/SE",
            reason="Quantum only, needs ML/SE assessment",
            priority=priority,
            quantum_score=quantum_score,
            ml_score=ml_score,
            software_score=software_score,
            cloud_score=cloud_score,
            math_score=math_score,
            total_score=total_score,
        )

    # REVIEW: ML + SE (no quantum)
    if has_ml and has_software and not has_quantum:
        return ShortlistResult(
            status="REVIEW",
            assigned_bucket="ML/AI/DS",
            reason="Strong ML+SE, assess quantum interest",
            priority=priority,
            quantum_score=quantum_score,
            ml_score=ml_score,
            software_score=software_score,
            cloud_score=cloud_score,
            math_score=math_score,
            total_score=total_score,
        )

    # REVIEW: Strong ML specialist (no quantum)
    if has_ml and ml_score >= 30 and not has_quantum:
        return ShortlistResult(
            status="REVIEW",
            assigned_bucket="ML/AI/DS",
            reason=f"Strong ML specialist (score {ml_score})",
            priority=priority,
            quantum_score=quantum_score,
            ml_score=ml_score,
            software_score=software_score,
            cloud_score=cloud_score,
            math_score=math_score,
            total_score=total_score,
        )

    # REVIEW: ML present but below specialist threshold (no quantum)
    if has_ml and not has_quantum:
        return ShortlistResult(
            status="REVIEW",
            assigned_bucket="ML/AI/DS",
            reason=f"ML signals detected (score {ml_score}), assess quantum interest",
            priority=priority,
            quantum_score=quantum_score,
            ml_score=ml_score,
            software_score=software_score,
            cloud_score=cloud_score,
            math_score=math_score,
            total_score=total_score,
        )

    # Fallback REJECT (should rarely be reached)
    logger.warning(
        f"Shortlisting fallback reached — scores: Q={quantum_score} ML={ml_score} "
        f"SE={software_score} Cloud={cloud_score} Math={math_score}"
    )
    return ShortlistResult(
        status="REJECT",
        assigned_bucket=bucket,
        reason="No qualifying domain combination for QIntern tracks",
        priority=5,
        quantum_score=quantum_score,
        ml_score=ml_score,
        software_score=software_score,
        cloud_score=cloud_score,
        math_score=math_score,
        total_score=total_score,
    )
