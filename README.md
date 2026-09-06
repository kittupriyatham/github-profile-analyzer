# GitHub Profile Analyzer

A Python pipeline for screening QIntern candidate profiles using GitHub repositories, resume links, LinkedIn profile text, and a configurable domain taxonomy.

The main batch workflow reads candidate data from a CSV or Excel spreadsheet, analyzes available profile links, computes weighted domain scores, applies shortlisting rules, and exports a formatted Excel workbook.

## What It Analyzes

The analyzer looks for signals across these domains:

- ML/DL/DS/AI
- Quantum Technology
- Software Engineering
- Cloud & DevOps
- Mathematics & Foundations

Domain libraries, keywords, and scoring thresholds are configured in `domains.json`.

## Project Layout

```text
main.py                      Batch pipeline entry point
analyzer.py                  Core profile, repository, resume, and LinkedIn analysis
domains.json                 Domain taxonomy and scoring configuration
requirements.txt             Python dependencies

loaders/
  spreadsheet_loader.py      CSV/XLSX candidate loader

scoring/
  scorer.py                  Weighted domain scoring
  shortlist.py               Shortlist/review/reject decision logic

exporters/
  excel_exporter.py          Excel workbook export

analyzers/                   Placeholder modules for future analyzer split-out
config/                      Configuration helpers/placeholders

evaluator.py                 Separate notebook assessment evaluator
interview_guide.md           Interview guide/reference notes

send_*.py                    Operational email scripts
schedule_interviews.py       Operational calendar scheduling script
update_buckets.py            Utility for updating bucket labels in output workbook
print_interns.py             Utility for printing selected intern contact info
patch_analyzer.py            One-time patching helper retained for history/reference
```

Generated Python bytecode folders named `__pycache__/` are not part of the source logic and can be safely removed.

## Setup

Create and activate a virtual environment, then install dependencies:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

For higher GitHub API limits, set a GitHub personal access token:

```bash
set GITHUB_TOKEN=your_token_here
```

On PowerShell:

```powershell
$env:GITHUB_TOKEN = "your_token_here"
```

A GitHub token is an optional access token from your GitHub account. The analyzer uses it when calling the GitHub API so it can make more requests before hitting rate limits. It is not required for small runs or public-only testing, but larger batches are more reliable with one.

## Run The Batch Analyzer

```bash
python main.py path\to\candidates.xlsx --output qintern_results.xlsx
```

Optional arguments:

```bash
python main.py path\to\candidates.csv --config domains.json --token your_token_here --exclude-forks --output results.xlsx
```

The batch pipeline expects a candidate spreadsheet with the column layout currently encoded in `loaders/spreadsheet_loader.py`.

## Run A Single Profile Analysis

`analyzer.py` can also be used directly:

```bash
python analyzer.py <username> --resume "https://drive.google.com/..." --linkedin "https://www.linkedin.com/in/example/" --output report.txt
```

Here, `<username>` is the GitHub username or GitHub profile URL to analyze.

## Outputs

The main pipeline writes an Excel workbook with:

- Rankings
- Detailed Scores
- Contact Info
- Pipeline Log

The standalone analyzer writes a text report.

## Notes

- Resume extraction supports Google Drive links and attempts PDF text extraction when a PDF is detected.
- GitHub repositories may be cloned temporarily during analysis.
- LinkedIn extraction depends on public page accessibility and may fail when anti-scraping gates are active.
- Operational scripts that send emails or create calendar events contain hardcoded campaign data; review them carefully before running.
