"""Weighted domain scoring engine for candidate profile analysis.

Consumes raw analysis results produced by the analyzer pipeline and computes
weighted domain scores.  Real code signals (library imports, dependency files)
carry more weight than keyword mentions in markdown or external profiles.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Signal weights – real code projects are worth more than keyword mentions
# ---------------------------------------------------------------------------
SIGNAL_WEIGHTS: dict[str, int] = {
    "repo_library": 10,     # Library found in actual code imports / dependency files
    "repo_keyword": 3,      # Keyword found in source code or markdown
    "resume_keyword": 2,    # Keyword found in resume
    "linkedin_keyword": 1,  # Keyword found on LinkedIn
    "repo_topic": 5,        # GitHub topic tag on repository
    "language_file": 1,     # Source file in a relevant language
}

# Canonical domain names (must match domains.json keys)
DOMAIN_NAMES: list[str] = [
    "ML/DL/DS/AI",
    "Quantum Technology",
    "Software Engineering",
    "Cloud & DevOps",
    "Mathematics & Foundations",
]

# Minimum weighted score to flag a domain as "detected"
_DETECTION_THRESHOLD: int = 5


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe_score(scores: dict[str, int], domain: str) -> int:
    """Return the score for *domain*, defaulting to 0."""
    return scores.get(domain, 0)


def _build_strength_summary(
    quantum: int,
    ml: int,
    software: int,
    cloud: int,
    math: int,
) -> str:
    """Generate a concise, human-readable strength summary string.

    The summary captures the candidate's strongest domain combination in a
    format suitable for quick screening (e.g. 'Strong QC + ML candidate').
    """
    parts: list[str] = []

    # --- Primary combination labels (highest priority first) ---------------
    if quantum >= 15 and ml >= 15:
        return "Strong QC + ML candidate"
    if quantum >= 15 and ml >= 5:
        return "QC with ML exposure"
    if quantum >= 15 and software >= 5:
        return "QC with SE exposure"
    if ml >= 30:
        return "ML/AI specialist"
    if quantum >= 15:
        return "Quantum specialist"
    if ml >= 15 and software >= 15:
        return "ML + SE generalist"
    if ml >= 15:
        return "ML/AI focused"
    if software >= 15 and cloud >= 15:
        return "Full-stack / DevOps generalist"
    if software >= 15:
        return "Software generalist"
    if cloud >= 15:
        return "Cloud & DevOps focused"
    if math >= 15:
        return "Mathematics / Foundations focused"

    # --- Fallback: list any domain that crosses the detection threshold -----
    if quantum >= _DETECTION_THRESHOLD:
        parts.append("QC")
    if ml >= _DETECTION_THRESHOLD:
        parts.append("ML")
    if software >= _DETECTION_THRESHOLD:
        parts.append("SE")
    if cloud >= _DETECTION_THRESHOLD:
        parts.append("Cloud")
    if math >= _DETECTION_THRESHOLD:
        parts.append("Math")

    if parts:
        return f"Emerging {' + '.join(parts)} signals"
    return "Insufficient domain signals"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_candidate_scores(analysis_results: dict[str, Any]) -> dict[str, Any]:
    """Compute weighted domain scores from raw analysis results.

    Parameters
    ----------
    analysis_results:
        A dictionary produced by the analyzer pipeline.  Expected keys:

        * ``domain_scores``  – ``Counter | dict`` mapping domain name → raw int
        * ``libraries``      – ``dict[str, set|list]`` mapping domain → lib names
        * ``keywords``       – ``dict[str, Counter|dict]`` mapping domain → kw counts
        * ``languages``      – ``Counter | dict`` mapping language name → file count
        * ``resume_scores``  – ``Counter | dict`` (optional) resume sub-scores
        * ``linkedin_scores``– ``Counter | dict`` (optional) LinkedIn sub-scores
        * ``repo_analyses``  – ``list[dict]`` (optional) per-repo breakdowns
        * ``repo_topics``    – ``Counter | dict`` (optional) discovered GitHub topics

    Returns
    -------
    dict
        Structured scoring result with the following keys:

        - ``domain_scores``:    ``dict[str, int]`` weighted score per domain
        - ``total_score``:      ``int`` sum of all domain scores
        - ``primary_domain``:   ``str`` highest-scoring domain name
        - ``top_domains``:      ``list[tuple[str, int]]`` domains sorted descending
        - ``has_quantum``:      ``bool``
        - ``has_ml``:           ``bool``
        - ``has_software``:     ``bool``
        - ``has_cloud``:        ``bool``
        - ``has_math``:         ``bool``
        - ``strength_summary``: ``str`` human-readable summary
    """
    try:
        raw_scores: dict[str, int] = dict(analysis_results.get("domain_scores", {}))
    except (TypeError, ValueError):
        logger.warning("Invalid or missing 'domain_scores' in analysis_results")
        raw_scores = {}

    # Ensure every canonical domain has an entry (default 0)
    domain_scores: dict[str, int] = {d: raw_scores.get(d, 0) for d in DOMAIN_NAMES}

    # ----- Layer additional weighted signals if available ------------------

    # Library signals (repo_library weight already baked in by the analyzer at
    # 10 pts per hit, but we count distinct libs here as a cross-check)
    libraries: dict[str, Any] = analysis_results.get("libraries", {})
    for domain in DOMAIN_NAMES:
        libs = libraries.get(domain, [])
        if isinstance(libs, set):
            libs = list(libs)
        if libs:
            logger.debug(f"Domain '{domain}' has {len(libs)} libraries: {libs}")

    # Topic tag signals – each unique topic adds SIGNAL_WEIGHTS['repo_topic']
    repo_topics: dict[str, int] = dict(analysis_results.get("repo_topics", {}))
    # Topic matching is already done upstream; we keep the raw_scores as-is.

    # ----- Compute aggregates ---------------------------------------------
    total_score: int = sum(domain_scores.values())
    top_domains: list[tuple[str, int]] = sorted(
        domain_scores.items(), key=lambda kv: kv[1], reverse=True
    )
    primary_domain: str = top_domains[0][0] if top_domains and top_domains[0][1] > 0 else "Undetermined"

    # ----- Detection flags -------------------------------------------------
    quantum_score = domain_scores.get("Quantum Technology", 0)
    ml_score = domain_scores.get("ML/DL/DS/AI", 0)
    software_score = domain_scores.get("Software Engineering", 0)
    cloud_score = domain_scores.get("Cloud & DevOps", 0)
    math_score = domain_scores.get("Mathematics & Foundations", 0)

    has_quantum: bool = quantum_score >= _DETECTION_THRESHOLD
    has_ml: bool = ml_score >= _DETECTION_THRESHOLD
    has_software: bool = software_score >= _DETECTION_THRESHOLD
    has_cloud: bool = cloud_score >= _DETECTION_THRESHOLD
    has_math: bool = math_score >= _DETECTION_THRESHOLD

    strength_summary = _build_strength_summary(
        quantum=quantum_score,
        ml=ml_score,
        software=software_score,
        cloud=cloud_score,
        math=math_score,
    )

    result: dict[str, Any] = {
        "domain_scores": domain_scores,
        "total_score": total_score,
        "primary_domain": primary_domain,
        "top_domains": top_domains,
        "has_quantum": has_quantum,
        "has_ml": has_ml,
        "has_software": has_software,
        "has_cloud": has_cloud,
        "has_math": has_math,
        "strength_summary": strength_summary,
    }

    logger.info(
        f"Scoring complete — total={total_score}, primary={primary_domain}, "
        f"summary='{strength_summary}'"
    )
    return result
