"""Loaders package — ingest candidate data from spreadsheets and other sources."""

from .spreadsheet_loader import load_candidates, CandidateRecord

__all__ = ["load_candidates", "CandidateRecord"]
