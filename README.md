# GitHub Profile Analyzer

An evidence-first profile intelligence tool for GitHub accounts.

The project is intended primarily for **fresh, repeatable insights about a GitHub profile** and secondarily for **screening/filtering people**, for example during hiring. GitHub is the primary evidence source; LinkedIn and resumes are supplementary sources.

## Core principle

The analyzer should not treat a person's declared skills as proof.

Instead, it reads the repositories it can legitimately access and builds an evidence trail from things such as:

- source files across the repository tree
- imports and dependency manifests
- programming languages and file types
- GitHub topics
- technology/framework signals
- README/documentation signals
- repository metadata
- recent repository activity
- Git history / commits attributable to the target account where available

A detected skill is therefore returned together with concrete evidence such as repository, file path, line number, source type, and observed text. Repository ownership by itself is explicitly **not** treated as proof of authorship.

## Repository access

Without a token, the analyzer scans the target user's publicly accessible repositories.

With a GitHub access token, the analyzer uses the repositories accessible to that token and includes private repositories owned by the target account when the token has access to them. GitHub's authenticated repository endpoint can expose repositories the authenticated user owns, collaborates on, or can access through organizations; this project filters that accessible set to the requested target account. citeturn0search5turn0search7

The token is never written into a clone URL or analysis result. Git receives it through an environment-backed HTTP authorization header.

The token must have the repository permissions necessary to read the private repositories you want analyzed. GitHub's Git tree/content APIs likewise require appropriate contents read access for private repositories. citeturn0search0turn0search1

## Entire repository scanning

The analyzer clones repositories normally rather than only reading a small set of top-level files. It recursively walks the checked-out repository and scans text/source files throughout the repository.

It also recursively initializes Git submodules where possible.

Binary/generated/vendor artifacts are counted where useful but are not interpreted as source-code evidence. This avoids treating `node_modules`, build outputs, `.git`, caches, compiled binaries, etc. as the candidate's skills.

## Architecture

The application now has one core class:

```text
GitHubProfileAnalyzer
│
├── discover_repositories()
├── analyze()
├── _analyze_repository()
├── _clone_repository()
├── _iter_files()
├── _scan_source_text()
├── _extract_imports()
├── _parse_dependency_file()
├── _score_domains()
├── _build_skill_insights()
├── _build_general_insights()
├── analyze_resume()
└── analyze_linkedin()
```

`analyzer.py` contains the reusable analysis engine.

`main.py` is intentionally thin: it loads spreadsheet records, instantiates the analyzer for each GitHub account, attaches supplementary resume/LinkedIn analysis, applies screening decisions, and sends results to the existing Excel exporter.

A backward-compatible `analyze_candidate()` function remains available for scripts that used the previous API.

## Output model

The core analysis returns structured data containing:

- GitHub profile metadata
- repositories discovered
- private repositories analyzed
- repository metadata
- files/source files/lines scanned
- languages
- libraries/dependencies
- domain scores
- inferred skills
- confidence levels
- evidence records
- recent/general profile insights
- optional resume evidence
- optional LinkedIn evidence

The evidence record has the conceptual form:

```text
skill
repository
source_type
path
line
observed evidence
strength
```

This makes the system extensible: future insight types can be added without redesigning the basic profile model.

## Batch usage

```bash
python main.py path\\to\\candidates.xlsx --output qintern_results.xlsx
```

With a GitHub token:

```powershell
$env:GITHUB_TOKEN = "your_token_here"
python main.py path\\to\\candidates.xlsx --output qintern_results.xlsx
```

Or:

```bash
python main.py path\\to\\candidates.xlsx --token YOUR_TOKEN
```

To exclude forks:

```bash
python main.py path\\to\\candidates.xlsx --exclude-forks
```

## Single-profile usage

```bash
python analyzer.py kittupriyatham --output profile_analysis.json
```

With private repository access and supplementary sources:

```bash
python analyzer.py kittupriyatham \
  --token YOUR_TOKEN \
  --resume "https://example.com/resume.pdf" \
  --linkedin "https://www.linkedin.com/in/example/" \
  --output profile_analysis.json
```

## Setup

```bash
python -m venv .venv
.venv\\Scripts\\activate
pip install -r requirements.txt
```

Git must also be installed and available on `PATH`, because full repository analysis uses Git itself.

## Hiring/screening vs profile intelligence

The same evidence model serves both use cases:

### Profile intelligence

The important output is the detailed evidence and changing profile signals. This is useful for repeatedly analyzing one account and comparing how its repository portfolio evolves.

### Screening

The batch pipeline can turn the same evidence into deterministic buckets such as `SHORTLIST`, `REVIEW`, and `REJECT`. These decisions are deliberately secondary to the underlying evidence and should not replace human review.

## LinkedIn and resume

LinkedIn and resumes are treated as **supplementary evidence**. A statement in a resume or LinkedIn profile does not receive the same evidentiary weight as a technology observed in repository code or dependency files.

LinkedIn extraction can fail because of access restrictions or anti-scraping mechanisms. Resume extraction currently supports directly downloadable text/PDF content.

## Future extension points

The `profile_analysis` structure is deliberately open-ended so additional insights can later be added, for example:

- contribution/activity trends
- technology evolution over time
- project complexity indicators
- testing/CI/CD practices
- documentation quality
- architecture patterns
- security practices
- deployment/infrastructure evidence
- issue/PR activity
- ownership vs authorship attribution
- cross-repository consistency
- profile change history

These should continue to be represented as observations with evidence rather than unsupported skill labels.
