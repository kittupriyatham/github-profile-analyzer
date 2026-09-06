"""Batch entry point for GitHub Profile Analyzer.

The application logic lives in ``GitHubProfileAnalyzer``.  This file is only
responsible for command-line parsing, spreadsheet loading, batch orchestration,
and export.  Keeping the entry point thin makes the same analyzer usable from
Python code, notebooks, web services, or future APIs.
"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime
from typing import Any

from analyzer import GitHubProfileAnalyzer
from exporters import export_results
from loaders import load_candidates

logger = logging.getLogger(__name__)


class ProfileAnalysisApplication:
    """Batch application that attaches spreadsheet records to the core analyzer."""

    def __init__(
        self,
        token: str | None = None,
        config_path: str | None = None,
        exclude_forks: bool = False,
    ) -> None:
        self.token = token or os.getenv("GITHUB_TOKEN")
        self.config_path = config_path
        self.exclude_forks = exclude_forks

    def analyze_record(self, candidate: Any) -> Any:
        """Analyze one spreadsheet candidate using the unified core class."""
        github_url = self._choose_github(candidate)
        if not github_url and not getattr(candidate, "resume_url", "") and not getattr(candidate, "linkedin_url", ""):
            candidate.analysis_error = "No trackable profiles provided"
            candidate.analysis_complete = True
            candidate.computed_status = "REJECT"
            candidate.status_reason = "No trackable profiles provided"
            return candidate

        username = self._github_username(github_url)
        if not username:
            # Resume/LinkedIn-only records remain supported.
            candidate.analysis_error = "No usable GitHub profile URL; only supplementary sources were supplied"
            candidate.analysis_complete = True
            candidate.computed_status = "REVIEW"
            candidate.status_reason = "No GitHub profile; supplementary sources require manual review"
            return candidate

        analyzer = GitHubProfileAnalyzer(
            username=username,
            token=self.token,
            config_path=self.config_path,
            exclude_forks=self.exclude_forks,
        )
        result = analyzer.analyze()

        if getattr(candidate, "resume_url", ""):
            result["resume"] = analyzer.analyze_resume(candidate.resume_url)
        if getattr(candidate, "linkedin_url", ""):
            result["linkedin"] = analyzer.analyze_linkedin(candidate.linkedin_url)

        self._attach_result(candidate, result)
        self._apply_screening_decision(candidate)
        candidate.analysis_complete = True
        return candidate

    def analyze_batch(self, candidates: list[Any]) -> tuple[list[Any], dict[str, Any]]:
        """Analyze all records and return records plus run metadata."""
        started = datetime.now()
        errors = 0
        for candidate in candidates:
            try:
                self.analyze_record(candidate)
                if getattr(candidate, "analysis_error", ""):
                    errors += 1
            except Exception as exc:
                errors += 1
                candidate.analysis_error = str(exc)
                candidate.analysis_complete = True
                candidate.computed_status = "REVIEW"
                candidate.status_reason = f"Analysis error: {exc}"
                logger.exception("Failed to analyze %s", getattr(candidate, "full_name", "candidate"))

        elapsed = (datetime.now() - started).total_seconds()
        metadata = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_processed": len(candidates),
            "shortlisted": sum(getattr(c, "computed_status", "") == "SHORTLIST" for c in candidates),
            "review": sum(getattr(c, "computed_status", "") == "REVIEW" for c in candidates),
            "rejected": sum(getattr(c, "computed_status", "") == "REJECT" for c in candidates),
            "errors": errors,
            "processing_time": f"{elapsed:.1f}s",
            "config_used": self.config_path or "domains.json",
        }
        return candidates, metadata

    @staticmethod
    def _choose_github(candidate: Any) -> str:
        """Prefer classical GitHub, then quantum GitHub, preserving old sheets."""
        return (
            getattr(candidate, "classical_github", "")
            or getattr(candidate, "quantum_github", "")
            or getattr(candidate, "github_url", "")
            or ""
        ).strip()

    @staticmethod
    def _github_username(value: str) -> str:
        value = value.strip().rstrip("/")
        if value.startswith("git@github.com:"):
            value = value.split(":", 1)[1]
        value = value.replace(".git", "")
        if "github.com/" in value.lower():
            value = value.split("github.com/", 1)[1]
        value = value.split("/")[0]
        return value.strip()

    @staticmethod
    def _attach_result(candidate: Any, result: dict[str, Any]) -> None:
        candidate.languages = result.get("languages", {})
        candidate.libraries = result.get("libraries", {})
        candidate.domain_scores = result.get("domain_scores", {})
        candidate.total_score = result.get("total_score", 0)
        candidate.primary_domain = result.get("primary_domain", "")
        candidate.repos_scanned = result.get("repositories_discovered", 0)

        # New evidence-first fields. The loader is backward-compatible, but
        # these fields can also be added dynamically to old CandidateRecord objects.
        candidate.skills = result.get("skills", [])
        candidate.evidence = result.get("evidence", [])
        candidate.insights = result.get("insights", {})
        candidate.repository_insights = result.get("repository_insights", [])
        candidate.profile_analysis = result

        resume = result.get("resume", {})
        linkedin = result.get("linkedin", {})
        candidate.resume_scores = resume.get("scores", {})
        candidate.linkedin_scores = linkedin.get("scores", {})
        candidate.resume_evaluated = bool(resume)
        candidate.linkedin_screened = bool(linkedin)

    @staticmethod
    def _apply_screening_decision(candidate: Any) -> None:
        """Apply intentionally conservative hiring buckets to evidence scores."""
        scores = candidate.domain_scores or {}
        quantum = int(scores.get("Quantum Technology", 0))
        ml = int(scores.get("ML/DL/DS/AI", 0))
        software = int(scores.get("Software Engineering", 0))
        cloud = int(scores.get("Cloud & DevOps", 0))
        math = int(scores.get("Mathematics & Foundations", 0))
        total = int(sum(scores.values()))

        has_q = quantum >= 5
        has_ml = ml >= 5
        has_se = software >= 5
        has_cloud = cloud >= 5
        has_math = math >= 5

        if total < 10:
            status, bucket, reason = "REJECT", "ML/AI/DS", f"Insufficient evidence score ({total})"
        elif has_q and (has_ml or has_se or has_math):
            status, bucket = "SHORTLIST", "QC/ML" if (has_ml or has_math) else "QC/SE"
            reason = f"Evidence-backed multi-domain fit: Q={quantum}, ML={ml}, SE={software}, Math={math}"
        elif has_ml:
            status, bucket, reason = "REVIEW", "ML/AI/DS", f"ML/AI evidence detected ({ml}); assess broader fit manually"
        elif has_q:
            status, bucket, reason = "REVIEW", "QC/SE", f"Quantum evidence detected ({quantum}); assess implementation breadth manually"
        elif has_se and has_cloud:
            status, bucket, reason = "REVIEW", "Full Stack", f"Software + cloud evidence ({software} + {cloud})"
        elif has_se:
            status, bucket, reason = "REVIEW", "Systems", f"Software evidence detected ({software})"
        else:
            status, bucket, reason = "REJECT", "ML/AI/DS", "No qualifying domain evidence"

        candidate.computed_status = status
        candidate.assigned_bucket = bucket
        candidate.status_reason = reason


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evidence-backed GitHub profile analyzer")
    parser.add_argument("input_spreadsheet", help="Path to candidate CSV/XLSX")
    parser.add_argument("--output", default="qintern_results.xlsx", help="Output Excel filename")
    parser.add_argument("--config", default=None, help="Path to domains.json")
    parser.add_argument("--token", default=None, help="GitHub access token (or GITHUB_TOKEN)")
    parser.add_argument("--exclude-forks", action="store_true", help="Exclude forked repositories")
    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = build_parser().parse_args()

    config_path = args.config or os.path.join(os.path.dirname(os.path.abspath(__file__)), "domains.json")
    logger.info("Loading candidates from %s", args.input_spreadsheet)
    candidates = load_candidates(args.input_spreadsheet)
    logger.info("Loaded %d candidates", len(candidates))

    app = ProfileAnalysisApplication(
        token=args.token,
        config_path=config_path,
        exclude_forks=args.exclude_forks,
    )
    candidates, metadata = app.analyze_batch(candidates)

    output_path = export_results(candidates, args.output, run_metadata=metadata)
    logger.info("Analysis complete: %s", output_path)


if __name__ == "__main__":
    main()
