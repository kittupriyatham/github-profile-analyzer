"""Core GitHub Profile Analyzer.

The analyzer is intentionally centered around one class: ``GitHubProfileAnalyzer``.
It is designed for two primary uses:

1. Personal profile intelligence: repeatedly analyze an account and get fresh,
   evidence-backed insights from the repositories currently accessible to it.
2. Screening/recruiting: analyze another person's public GitHub profile and,
   when the operator supplies a token that legitimately has access, include
   private repositories visible to that token.

Important design principle:
    A skill is an inference, not a self-declared fact.  The result therefore
    keeps the evidence that caused a skill/domain to be detected: repository,
    file, line, dependency, topic, README text, etc.

The class deliberately has no dependency on the spreadsheet pipeline.  The
batch ``main.py`` adapts spreadsheet records to this class, while the class
can also be imported and used directly.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable

import requests

logger = logging.getLogger(__name__)

DEFAULT_API = "https://api.github.com"
DEFAULT_USER_AGENT = "github-profile-analyzer"

# Files that are normally binary or generated artifacts.  They are still
# counted as repository files, but their bytes are not interpreted as source.
BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".ico", ".webp",
    ".mp3", ".wav", ".ogg", ".flac", ".mp4", ".avi", ".mkv", ".mov", ".webm",
    ".zip", ".tar", ".gz", ".tgz", ".rar", ".7z", ".bz2", ".xz",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".pyc", ".o", ".a", ".lib",
    ".pt", ".pth", ".onnx", ".pb", ".h5", ".weights", ".tflite", ".model",
    ".pkl", ".pickle", ".joblib", ".npy", ".npz", ".class", ".jar", ".wasm",
    ".woff", ".woff2", ".ttf", ".otf", ".db", ".sqlite", ".sqlite3",
}

# Generated/vendor directories are not evidence of the candidate's own work.
SKIP_DIRECTORIES = {
    ".git", ".hg", ".svn", "node_modules", "vendor", "venv", ".venv",
    "env", ".env", "__pycache__", ".mypy_cache", ".pytest_cache", ".tox",
    "dist", "build", "target", "coverage", ".next", ".nuxt", ".gradle",
}

LANGUAGE_BY_EXTENSION = {
    ".py": "Python", ".pyw": "Python", ".js": "JavaScript", ".jsx": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".java": "Java", ".kt": "Kotlin",
    ".kts": "Kotlin", ".c": "C", ".h": "C/C++", ".cc": "C++", ".cpp": "C++",
    ".cxx": "C++", ".hpp": "C++", ".cs": "C#", ".go": "Go", ".rs": "Rust",
    ".rb": "Ruby", ".php": "PHP", ".swift": "Swift", ".scala": "Scala",
    ".r": "R", ".R": "R", ".jl": "Julia", ".dart": "Dart", ".lua": "Lua",
    ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell", ".ps1": "PowerShell",
    ".sql": "SQL", ".html": "HTML", ".css": "CSS", ".scss": "SCSS",
    ".vue": "Vue", ".svelte": "Svelte", ".sol": "Solidity", ".zig": "Zig",
    ".ex": "Elixir", ".exs": "Elixir", ".erl": "Erlang", ".fs": "F#",
    ".fsx": "F#", ".m": "Objective-C", ".mm": "Objective-C++",
}

DEPENDENCY_FILES = {
    "requirements.txt", "pyproject.toml", "poetry.lock", "Pipfile", "Pipfile.lock",
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "Cargo.toml", "Cargo.lock", "go.mod", "go.sum", "pom.xml", "build.gradle",
    "build.gradle.kts", "Gemfile", "composer.json", "pubspec.yaml", "mix.exs",
}

@dataclass
class Evidence:
    """A concrete observation supporting an inferred skill or signal."""

    skill: str
    repository: str
    source_type: str
    path: str = ""
    line: int | None = None
    evidence: str = ""
    strength: str = "medium"


@dataclass
class RepositoryInsight:
    """Repository-level facts and evidence."""

    name: str
    full_name: str
    url: str
    private: bool
    fork: bool
    archived: bool
    default_branch: str
    description: str = ""
    topics: list[str] = field(default_factory=list)
    stars: int = 0
    forks: int = 0
    size_kb: int = 0
    created_at: str = ""
    updated_at: str = ""
    pushed_at: str = ""
    files_scanned: int = 0
    source_files_scanned: int = 0
    lines_scanned: int = 0
    languages: dict[str, int] = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    author_commit_count: int = 0
    clone_error: str = ""


class GitHubProfileAnalyzer:
    """Analyze a GitHub account and produce evidence-backed profile insights.

    ``token`` is optional. Without one, only publicly accessible repositories
    are considered. With one, the analyzer asks GitHub for repositories
    accessible to that token and includes private repositories owned by the
    target account when the token has access to them.

    The analyzer never sends the token to the target account, stores it in the
    report, or puts it in a clone URL. Git receives the token through an
    environment-backed HTTP header.
    """

    def __init__(
        self,
        username: str,
        token: str | None = None,
        config_path: str | None = None,
        workdir: str | None = None,
        keep_clones: bool = False,
        exclude_forks: bool = False,
        scan_history: bool = True,
        max_file_bytes: int = 5 * 1024 * 1024,
    ) -> None:
        self.username = self._normalise_username(username)
        self.token = token or os.getenv("GITHUB_TOKEN")
        self.config_path = config_path
        self.workdir = Path(workdir) if workdir else None
        self.keep_clones = keep_clones
        self.exclude_forks = exclude_forks
        self.scan_history = scan_history
        self.max_file_bytes = max_file_bytes

        self.domains: dict[str, dict[str, set[str]]] = {}
        self.domain_regexes: dict[str, list[tuple[str, re.Pattern[str]]]] = {}
        self.evidence: list[Evidence] = []
        self.repositories: list[RepositoryInsight] = []
        self.profile: dict[str, Any] = {}
        self._load_domain_config(config_path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(self) -> dict[str, Any]:
        """Run the complete profile analysis and return a serialisable report."""
        user = self._api_get(f"/users/{self.username}")
        if user is None:
            raise RuntimeError(f"GitHub user not found or inaccessible: {self.username}")

        repos = self.discover_repositories()
        logger.info("Discovered %d repositories for %s", len(repos), self.username)

        for repo in repos:
            try:
                self._analyze_repository(repo)
            except Exception as exc:  # one broken repo must not abort a profile
                logger.exception("Repository analysis failed for %s", repo.get("full_name"))
                self.repositories.append(
                    RepositoryInsight(
                        name=repo.get("name", ""),
                        full_name=repo.get("full_name", ""),
                        url=repo.get("html_url", ""),
                        private=bool(repo.get("private")),
                        fork=bool(repo.get("fork")),
                        archived=bool(repo.get("archived")),
                        default_branch=repo.get("default_branch", ""),
                        description=repo.get("description") or "",
                        topics=repo.get("topics") or [],
                        stars=int(repo.get("stargazers_count") or 0),
                        forks=int(repo.get("forks_count") or 0),
                        size_kb=int(repo.get("size") or 0),
                        created_at=repo.get("created_at") or "",
                        updated_at=repo.get("updated_at") or "",
                        pushed_at=repo.get("pushed_at") or "",
                        clone_error=str(exc),
                    )
                )

        domain_scores = self._score_domains()
        total_score = sum(domain_scores.values())
        primary_domain = max(domain_scores, key=domain_scores.get) if total_score else "Undetermined"
        skills = self._build_skill_insights(domain_scores)

        self.profile = {
            "username": self.username,
            "profile_url": user.get("html_url", f"https://github.com/{self.username}"),
            "name": user.get("name") or "",
            "bio": user.get("bio") or "",
            "company": user.get("company") or "",
            "location": user.get("location") or "",
            "blog": user.get("blog") or "",
            "public_repos": int(user.get("public_repos") or 0),
            "public_gists": int(user.get("public_gists") or 0),
            "followers": int(user.get("followers") or 0),
            "following": int(user.get("following") or 0),
            "created_at": user.get("created_at") or "",
            "updated_at": user.get("updated_at") or "",
            "authenticated_access": bool(self.token),
            "repositories_discovered": len(repos),
            "private_repositories_analyzed": sum(1 for r in self.repositories if r.private),
            "forks_analyzed": sum(1 for r in self.repositories if r.fork),
            "domain_scores": domain_scores,
            "total_score": total_score,
            "primary_domain": primary_domain,
            "skills": skills,
            "languages": dict(self._language_counts()),
            "libraries": dict(self._library_counts()),
            "repository_insights": [asdict(r) for r in self.repositories],
            "evidence": [asdict(e) for e in self.evidence],
            "insights": self._build_general_insights(user),
        }
        return self.profile

    def discover_repositories(self) -> list[dict[str, Any]]:
        """Discover every repository relevant to this analysis.

        Public-only mode uses ``/users/{user}/repos``. Authenticated mode uses
        ``/user/repos`` with owner/collaborator/org-member affiliation and then
        selects repositories whose owner is the target account. This is what
        allows a token belonging to the target account (or otherwise authorized
        for the target's private repository) to expose private repositories.
        """
        if self.token:
            repos = self._paginate(
                "/user/repos",
                params={
                    "visibility": "all",
                    "affiliation": "owner,collaborator,organization_member",
                    "per_page": 100,
                    "sort": "full_name",
                },
            )
            target = self.username.lower()
            repos = [
                r for r in repos
                if str((r.get("owner") or {}).get("login", "")).lower() == target
            ]
        else:
            repos = self._paginate(
                f"/users/{self.username}/repos",
                params={"type": "all", "per_page": 100, "sort": "full_name"},
            )

        if self.exclude_forks:
            repos = [r for r in repos if not r.get("fork")]
        return repos

    def analyze_resume(self, url: str) -> dict[str, Any]:
        """Analyze a resume as supplementary evidence, not as proof of skill."""
        text = self._download_text_document(url)
        scores, hits = self._score_text(text, source_type="resume", repository="resume")
        return {"scores": dict(scores), "keywords": {k: dict(v) for k, v in hits.items()}}

    def analyze_linkedin(self, url: str) -> dict[str, Any]:
        """Analyze publicly accessible LinkedIn text as supplementary evidence."""
        if not url:
            return {"scores": {}, "keywords": {}}
        try:
            response = requests.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122 Safari/537.36"
                },
                timeout=20,
            )
            response.raise_for_status()
            from bs4 import BeautifulSoup
            text = BeautifulSoup(response.text, "html.parser").get_text("\n")
        except Exception as exc:
            logger.warning("LinkedIn extraction failed: %s", exc)
            return {"scores": {}, "keywords": {}, "error": str(exc)}
        scores, hits = self._score_text(text, source_type="linkedin", repository="linkedin")
        return {"scores": dict(scores), "keywords": {k: dict(v) for k, v in hits.items()}}

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def _load_domain_config(self, config_path: str | None) -> None:
        path = Path(config_path) if config_path else Path(__file__).with_name("domains.json")
        if not path.exists():
            logger.warning("Domain config not found: %s; using dynamic evidence only", path)
            return
        with path.open("r", encoding="utf-8") as fh:
            config = json.load(fh)
        for name, cfg in config.get("domains", {}).items():
            libraries = {str(x).lower() for x in cfg.get("libraries", [])}
            keywords = {str(x).lower() for x in cfg.get("keywords", [])}
            self.domains[name] = {"libraries": libraries, "keywords": keywords}
            self.domain_regexes[name] = [
                (kw, re.compile(r"\b" + re.escape(kw) + r"\b", re.I)) for kw in keywords
            ]

    # ------------------------------------------------------------------
    # GitHub API
    # ------------------------------------------------------------------

    def _api_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": DEFAULT_USER_AGENT,
            "X-GitHub-Api-Version": "2026-03-10",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _api_get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = requests.get(
            DEFAULT_API + path,
            headers=self._api_headers(),
            params=params,
            timeout=30,
        )
        if response.status_code == 404:
            return None
        if response.status_code == 403:
            raise RuntimeError("GitHub API access/rate limit error. Supply a token with the required repository access.")
        response.raise_for_status()
        return response.json()

    def _paginate(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        page = 1
        output: list[dict[str, Any]] = []
        while True:
            page_params = dict(params)
            page_params["page"] = page
            data = self._api_get(path, page_params)
            if not data:
                break
            if not isinstance(data, list):
                break
            output.extend(data)
            if len(data) < int(params.get("per_page", 100)):
                break
            page += 1
        return output

    # ------------------------------------------------------------------
    # Repository analysis
    # ------------------------------------------------------------------

    def _analyze_repository(self, repo: dict[str, Any]) -> None:
        full_name = repo["full_name"]
        logger.info("Scanning entire repository: %s%s", full_name, " [private]" if repo.get("private") else "")
        root = self._clone_repository(repo)
        try:
            insight = RepositoryInsight(
                name=repo.get("name", ""),
                full_name=full_name,
                url=repo.get("html_url", ""),
                private=bool(repo.get("private")),
                fork=bool(repo.get("fork")),
                archived=bool(repo.get("archived")),
                default_branch=repo.get("default_branch", ""),
                description=repo.get("description") or "",
                topics=repo.get("topics") or [],
                stars=int(repo.get("stargazers_count") or 0),
                forks=int(repo.get("forks_count") or 0),
                size_kb=int(repo.get("size") or 0),
                created_at=repo.get("created_at") or "",
                updated_at=repo.get("updated_at") or "",
                pushed_at=repo.get("pushed_at") or "",
            )

            for topic in insight.topics:
                self._add_evidence(Evidence(topic, full_name, "repository_topic", evidence=f"GitHub topic: {topic}", strength="medium"))

            all_dependencies: set[str] = set()
            for path in self._iter_files(root):
                insight.files_scanned += 1
                language = LANGUAGE_BY_EXTENSION.get(path.suffix)
                if language:
                    insight.source_files_scanned += 1
                    insight.languages[language] = insight.languages.get(language, 0) + 1

                if path.name in DEPENDENCY_FILES:
                    all_dependencies.update(self._parse_dependency_file(path))

                if self._is_binary(path) or path.stat().st_size > self.max_file_bytes:
                    continue
                text = self._read_text(path)
                if not text:
                    continue
                lines = text.splitlines()
                insight.lines_scanned += len(lines)
                self._scan_source_text(full_name, path.relative_to(root).as_posix(), lines)

            insight.dependencies = sorted(all_dependencies)
            for dep in insight.dependencies:
                self._record_library_evidence(dep, full_name, "dependency_manifest", "")

            insight.skills = sorted({e.skill for e in self.evidence if e.repository == full_name})
            if self.scan_history:
                insight.author_commit_count = self._count_target_commits(full_name)

            self.repositories.append(insight)
        finally:
            if not self.keep_clones:
                shutil.rmtree(root, ignore_errors=True)

    def _clone_repository(self, repo: dict[str, Any]) -> Path:
        base = self.workdir or Path(tempfile.mkdtemp(prefix="github-profile-analyzer-"))
        base.mkdir(parents=True, exist_ok=True)
        target = base / re.sub(r"[^A-Za-z0-9_.-]", "_", repo["full_name"])
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)

        env = os.environ.copy()
        if self.token:
            # Git supports environment-backed config. The token therefore does
            # not appear in the clone URL or the command line.
            env["GIT_CONFIG_COUNT"] = "1"
            env["GIT_CONFIG_KEY_0"] = "http.extraheader"
            env["GIT_CONFIG_VALUE_0"] = f"Authorization: Bearer {self.token}"

        command = ["git", "clone", "--recurse-submodules", repo["clone_url"], str(target)]
        try:
            subprocess.run(
                command,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=1800,
                check=True,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("Git executable was not found. Install Git and ensure it is on PATH.") from exc
        except subprocess.CalledProcessError as exc:
            # Never include environment variables in the error. Git's stderr
            # can be safely surfaced because the token was not embedded in URL.
            raise RuntimeError(f"git clone failed for {repo['full_name']}: {exc.stderr[-2000:]}") from exc
        return target

    def _iter_files(self, root: Path) -> Iterable[Path]:
        for directory, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRECTORIES]
            for filename in filenames:
                yield Path(directory) / filename

    def _is_binary(self, path: Path) -> bool:
        if path.suffix.lower() in BINARY_EXTENSIONS:
            return True
        try:
            with path.open("rb") as fh:
                return b"\x00" in fh.read(4096)
        except OSError:
            return True

    def _read_text(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                return path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                return ""
        except OSError:
            return ""

    def _parse_dependency_file(self, path: Path) -> set[str]:
        name = path.name.lower()
        text = self._read_text(path)
        deps: set[str] = set()
        if name in {"requirements.txt", "pipfile"}:
            for line in text.splitlines():
                m = re.match(r"\s*([A-Za-z0-9_.-]+)", line)
                if m and not line.lstrip().startswith("#"):
                    deps.add(m.group(1).lower())
        elif name == "package.json":
            try:
                data = json.loads(text)
                for section in ("dependencies", "devDependencies", "peerDependencies"):
                    deps.update(str(x).lower() for x in data.get(section, {}))
            except json.JSONDecodeError:
                pass
        elif name in {"cargo.toml", "pyproject.toml", "go.mod", "pom.xml", "composer.json", "pubspec.yaml"}:
            for line in text.splitlines():
                for token in re.findall(r"[A-Za-z][A-Za-z0-9_.@/-]{2,}", line):
                    if token.lower() not in {"true", "false", "version", "name", "dependencies", "require"}:
                        deps.add(token.lower())
        else:
            for line in text.splitlines():
                token = line.strip().split("==")[0].split(">=")[0].split("@")[0]
                if token and re.match(r"^[A-Za-z0-9_.-]+$", token):
                    deps.add(token.lower())
        return deps

    # ------------------------------------------------------------------
    # Evidence extraction
    # ------------------------------------------------------------------

    def _scan_source_text(self, repository: str, relative_path: str, lines: list[str]) -> None:
        extension = Path(relative_path).suffix.lower()
        language = LANGUAGE_BY_EXTENSION.get(extension)
        if language:
            self._add_evidence(Evidence(language, repository, "source_language", relative_path, evidence=f"{language} source file", strength="medium"))

        for line_no, line in enumerate(lines, start=1):
            imports = self._extract_imports(line, extension)
            for library in imports:
                self._record_library_evidence(library, repository, "code_import", relative_path, line_no)

            for domain, patterns in self.domain_regexes.items():
                for keyword, pattern in patterns:
                    if pattern.search(line):
                        self._add_evidence(Evidence(domain, repository, "source_keyword", relative_path, line_no, line.strip()[:300], "strong"))

            # Dependency/technology names not necessarily present in domains.json
            for technology in self._detect_known_technologies(line):
                self._add_evidence(Evidence(technology, repository, "technology_signal", relative_path, line_no, line.strip()[:300], "strong"))

    def _extract_imports(self, line: str, extension: str) -> set[str]:
        result: set[str] = set()
        patterns = []
        if extension in {".py", ".pyw"}:
            patterns = [r"^\s*import\s+([A-Za-z0-9_]+)", r"^\s*from\s+([A-Za-z0-9_]+)"]
        elif extension in {".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte"}:
            patterns = [r"from\s+[\"']([^\"']+)[\"']", r"require\(\s*[\"']([^\"']+)[\"']\s*\)"]
        elif extension in {".c", ".h", ".cc", ".cpp", ".cxx", ".hpp"}:
            patterns = [r"^\s*#include\s*[<\"]([^>\"]+)[>\"]"]
        elif extension in {".java", ".kt", ".kts", ".scala"}:
            patterns = [r"^\s*import\s+([A-Za-z0-9_.]+)"]
        elif extension == ".go":
            patterns = [r"[\"']([^\"']+)[\"']"]
        for pattern in patterns:
            for match in re.finditer(pattern, line):
                value = match.group(1).split("/")[0].lower()
                if value.startswith("@") and "/" in match.group(1):
                    parts = match.group(1).split("/")
                    value = "/".join(parts[:2]).lower()
                if value and len(value) > 1:
                    result.add(value)
        return result

    def _record_library_evidence(self, library: str, repository: str, source_type: str, path: str, line: int | None = None) -> None:
        normalized = library.lower().strip()
        for domain, cfg in self.domains.items():
            if normalized in cfg.get("libraries", set()):
                self._add_evidence(Evidence(domain, repository, source_type, path, line, f"Library/dependency: {library}", "strong"))

    def _detect_known_technologies(self, line: str) -> set[str]:
        # These are intentionally additive; domains.json remains the main
        # configurable taxonomy.
        known = {
            "pytorch": "PyTorch", "tensorflow": "TensorFlow", "keras": "Keras",
            "scikit-learn": "scikit-learn", "sklearn": "scikit-learn", "pandas": "Pandas",
            "numpy": "NumPy", "scipy": "SciPy", "qiskit": "Qiskit", "cirq": "Cirq",
            "pennylane": "PennyLane", "cuda": "CUDA", "docker": "Docker",
            "kubernetes": "Kubernetes", "terraform": "Terraform", "aws": "AWS",
            "azure": "Azure", "gcp": "Google Cloud", "fastapi": "FastAPI",
            "django": "Django", "flask": "Flask", "react": "React", "next.js": "Next.js",
            "langchain": "LangChain", "transformers": "Hugging Face Transformers",
            "opencv": "OpenCV", "spark": "Apache Spark", "pyspark": "PySpark",
        }
        lower = line.lower()
        return {label for token, label in known.items() if re.search(r"\b" + re.escape(token) + r"\b", lower)}

    def _add_evidence(self, evidence: Evidence) -> None:
        # Avoid exploding the report with identical repeated observations.
        key = (evidence.skill, evidence.repository, evidence.source_type, evidence.path, evidence.line, evidence.evidence)
        if not hasattr(self, "_evidence_keys"):
            self._evidence_keys: set[tuple[Any, ...]] = set()
        if key not in self._evidence_keys:
            self._evidence_keys.add(key)
            self.evidence.append(evidence)

    # ------------------------------------------------------------------
    # Git history and scoring
    # ------------------------------------------------------------------

    def _count_target_commits(self, repository: str) -> int:
        """Count commits attributed to the target GitHub login where possible."""
        data = self._api_get(f"/repos/{repository}/commits", {"author": self.username, "per_page": 100})
        if not isinstance(data, list):
            return 0
        # Pagination is intentionally limited here: the number is an evidence
        # signal, not a complete contribution ledger.
        return len(data)

    def _score_domains(self) -> dict[str, int]:
        scores = Counter()
        for evidence in self.evidence:
            if evidence.skill not in self.domains:
                continue
            weight = {
                "code_import": 10,
                "dependency_manifest": 10,
                "source_keyword": 3,
                "repository_topic": 5,
                "technology_signal": 8,
                "source_language": 1,
            }.get(evidence.source_type, 1)
            scores[evidence.skill] += weight
        return {domain: int(scores.get(domain, 0)) for domain in self.domains}

    def _build_skill_insights(self, domain_scores: dict[str, int]) -> list[dict[str, Any]]:
        counts = Counter(e.skill for e in self.evidence)
        output = []
        for skill, count in counts.most_common():
            skill_evidence = [e for e in self.evidence if e.skill == skill]
            output.append({
                "skill": skill,
                "evidence_count": count,
                "confidence": self._confidence(skill_evidence),
                "proof": [asdict(e) for e in skill_evidence[:25]],
            })
        return output

    def _confidence(self, evidence: list[Evidence]) -> str:
        strong = sum(1 for e in evidence if e.strength == "strong")
        imports = sum(1 for e in evidence if e.source_type in {"code_import", "dependency_manifest"})
        repos = len({e.repository for e in evidence})
        if imports >= 3 and repos >= 2:
            return "high"
        if imports >= 1 or strong >= 3:
            return "medium"
        return "low"

    def _language_counts(self) -> Counter:
        counter = Counter()
        for repo in self.repositories:
            counter.update(repo.languages)
        return counter

    def _library_counts(self) -> Counter:
        counter = Counter()
        for evidence in self.evidence:
            if evidence.source_type in {"code_import", "dependency_manifest"}:
                counter[evidence.evidence.replace("Library/dependency: ", "")] += 1
        return counter

    def _build_general_insights(self, user: dict[str, Any]) -> dict[str, Any]:
        active = [r for r in self.repositories if not r.archived]
        recent = sorted(active, key=lambda r: r.pushed_at or "", reverse=True)[:10]
        total_lines = sum(r.lines_scanned for r in self.repositories)
        return {
            "active_repositories": len(active),
            "archived_repositories": len(self.repositories) - len(active),
            "total_source_files_scanned": sum(r.source_files_scanned for r in self.repositories),
            "total_text_lines_scanned": total_lines,
            "most_recent_repositories": [r.full_name for r in recent],
            "largest_repositories_by_scanned_files": [
                r.full_name for r in sorted(self.repositories, key=lambda x: x.files_scanned, reverse=True)[:10]
            ],
            "private_access_note": (
                "Private repositories were included only when the supplied GitHub token had access to them."
                if self.token else
                "No token supplied; analysis is limited to publicly accessible repositories."
            ),
            "interpretation_note": (
                "Skills are inferred from repository/code evidence. Repository ownership alone is not treated as proof of authorship."
            ),
        }

    # ------------------------------------------------------------------
    # Resume helpers
    # ------------------------------------------------------------------

    def _download_text_document(self, url: str) -> str:
        if not url:
            return ""
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        response.raise_for_status()
        payload = response.content
        if payload.startswith(b"%PDF"):
            try:
                import pypdf
                import io
                reader = pypdf.PdfReader(io.BytesIO(payload))
                return "\n".join(page.extract_text() or "" for page in reader.pages)
            except Exception:
                return ""
        return response.text

    def _score_text(self, text: str, source_type: str, repository: str) -> tuple[Counter, defaultdict[str, Counter]]:
        scores = Counter()
        hits: defaultdict[str, Counter] = defaultdict(Counter)
        for line_no, line in enumerate(text.splitlines(), 1):
            for domain, patterns in self.domain_regexes.items():
                for keyword, pattern in patterns:
                    if pattern.search(line):
                        scores[domain] += 3
                        hits[domain][keyword] += 1
                        self._add_evidence(Evidence(domain, repository, source_type, line=line_no, evidence=line.strip()[:300], strength="low"))
        return scores, hits

    @staticmethod
    def _normalise_username(username: str) -> str:
        value = username.strip().rstrip("/")
        value = re.sub(r"^https?://github\.com/", "", value, flags=re.I)
        return value.split("/")[0].strip()


def analyze_candidate(
    username: str,
    token: str | None = None,
    resume_url: str | None = None,
    linkedin_url: str | None = None,
    config_path: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Backward-compatible functional wrapper around ``GitHubProfileAnalyzer``."""
    analyzer = GitHubProfileAnalyzer(username, token=token, config_path=config_path, **kwargs)
    result = analyzer.analyze()
    if resume_url:
        result["resume"] = analyzer.analyze_resume(resume_url)
    if linkedin_url:
        result["linkedin"] = analyzer.analyze_linkedin(linkedin_url)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Evidence-backed GitHub profile analyzer")
    parser.add_argument("username", help="GitHub username or profile URL")
    parser.add_argument("--token", default=None, help="GitHub access token (or GITHUB_TOKEN)")
    parser.add_argument("--resume", default=None, help="Public/direct resume URL")
    parser.add_argument("--linkedin", default=None, help="Public LinkedIn profile URL")
    parser.add_argument("--config", default=None, help="Path to domains.json")
    parser.add_argument("--output", default="profile_analysis.json")
    parser.add_argument("--exclude-forks", action="store_true")
    parser.add_argument("--keep-clones", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    result = analyze_candidate(
        args.username,
        token=args.token,
        resume_url=args.resume,
        linkedin_url=args.linkedin,
        config_path=args.config,
        exclude_forks=args.exclude_forks,
        keep_clones=args.keep_clones,
    )
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False)
    print(f"Analysis written to {args.output}")


if __name__ == "__main__":
    main()
