"""
GitHub Profile Analyzer — candidate screening pipeline.

Can be used as a standalone CLI tool:
    python analyzer.py <username> [--resume URL] [--linkedin URL] ...

Or imported as a library:
    from analyzer import analyze_candidate
    results = analyze_candidate("octocat", token="ghp_...")
"""

import os
import sys
import re
import shutil
import subprocess
import time
import urllib.request
import urllib.error
import json
import argparse
import tempfile
import stat
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

# ---------------------------------------------------------------------------
# Domain taxonomy — populated at runtime by load_domain_config()
# ---------------------------------------------------------------------------
DOMAINS: dict[str, dict[str, set]] = {}
DOMAIN_REGEXES: dict[str, list[tuple[str, re.Pattern]]] = {}
IMPORTANT_THRESHOLDS: dict[str, int] = {
    "default_min_score": 5,
    "secondary_min_score": 15,
}

HEADERS = {
    "User-Agent": "GitHub-Profile-Analyzer"
}

BINARY_EXTENSIONS = {
    '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.ico', '.webp',
    '.mp3', '.wav', '.ogg', '.flac', '.mp4', '.avi', '.mkv', '.mov', '.webm',
    '.zip', '.tar', '.gz', '.tgz', '.rar', '.7z', '.bz2',
    '.pdf', '.docx', '.xlsx', '.pptx',
    '.exe', '.dll', '.so', '.dylib', '.bin', '.pyc', '.o', '.a', '.lib',
    '.pt', '.pth', '.onnx', '.pb', '.h5', '.weights', '.tflite', '.model',
    '.pkl', '.pickle', '.joblib', '.npy', '.npz', '.csv', '.tsv', '.json'
}


# ===================================================================
# Configuration
# ===================================================================

def load_domain_config(config_path: str) -> None:
    """Load domain taxonomy from external JSON config file."""
    global DOMAINS, DOMAIN_REGEXES, IMPORTANT_THRESHOLDS
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
    except FileNotFoundError:
        print(f"Error: Config file not found: {config_path}", file=sys.stderr)
        print("Create a domains.json file or specify path with --config.", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in config file: {e}", file=sys.stderr)
        sys.exit(1)

    raw_domains = config.get("domains", {})
    if not raw_domains:
        print("Error: No domains defined in config file.", file=sys.stderr)
        sys.exit(1)

    DOMAINS = {}
    for domain, cfg in raw_domains.items():
        DOMAINS[domain] = {
            "libraries": set(cfg.get("libraries", [])),
            "keywords": set(cfg.get("keywords", []))
        }

    IMPORTANT_THRESHOLDS = config.get("important_repo_thresholds", IMPORTANT_THRESHOLDS)

    DOMAIN_REGEXES = {}
    for domain, cfg in DOMAINS.items():
        patterns = []
        for kw in cfg["keywords"]:
            pattern = re.compile(r'\b' + re.escape(kw) + r'\b', re.IGNORECASE)
            patterns.append((kw, pattern))
        DOMAIN_REGEXES[domain] = patterns

    domain_names = ", ".join(DOMAINS.keys())
    print(f"[Config] Loaded {len(DOMAINS)} domains: {domain_names}")


# ===================================================================
# GitHub API helpers
# ===================================================================

def get_repos(username: str, token: str | None = None) -> list[dict]:
    """Fetches all repositories from GitHub API."""
    repos: list[dict] = []
    page = 1
    while True:
        url = f"https://api.github.com/users/{username}/repos?per_page=100&page={page}"
        req = urllib.request.Request(url, headers=HEADERS)
        if token:
            req.add_header("Authorization", f"token {token}")
        try:
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode())
                if not data:
                    break
                repos.extend(data)
                if len(data) < 100:
                    break
                page += 1
        except urllib.error.HTTPError as e:
            print(f"Error fetching repos from GitHub API: {e}", file=sys.stderr)
            if e.code == 403:
                print("Rate limit exceeded. Try providing a token with --token.", file=sys.stderr)
            return []
        except Exception as e:
            print(f"Failed to connect to GitHub API: {e}", file=sys.stderr)
            return []
    return repos


def get_repo_topics(username: str, repo_name: str, token: str | None = None) -> list[str]:
    """Fetches topics/tags for a specific repository from GitHub API."""
    url = f"https://api.github.com/repos/{username}/{repo_name}/topics"
    headers_with_preview = dict(HEADERS)
    headers_with_preview["Accept"] = "application/vnd.github.mercy-preview+json"
    req = urllib.request.Request(url, headers=headers_with_preview)
    if token:
        req.add_header("Authorization", f"token {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode())
            return data.get("names", [])
    except Exception:
        return []


# ===================================================================
# Resume / LinkedIn text extraction
# ===================================================================

def extract_text_from_gdrive_resume(url: str) -> str:
    """Downloads a resume from a Google Drive sharing link and handles raw text or PDFs."""
    if not url:
        return ""
    print(f"[Resume] Detecting Google Drive target asset link...")

    # Extract structural unique file ID from common share URLs
    match = re.search(r'/d/([a-zA-Z0-9-_]+)', url)
    if not match:
        match = re.search(r'id=([a-zA-Z0-9-_]+)', url)

    if not match:
        print("[Warning] Could not extract valid Google Drive Document ID from target string.", file=sys.stderr)
        return ""

    file_id = match.group(1)
    direct_download_url = f"https://drive.google.com/uc?export=download&id={file_id}"

    req = urllib.request.Request(direct_download_url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req) as response:
            file_payload = response.read()

        # Programmatic determination if payload is raw binary PDF binary data
        if file_payload.startswith(b'%PDF'):
            try:
                import pypdf
            except ImportError:
                print("[Dependency Error] File verified as PDF format, but 'pypdf' package is missing.", file=sys.stderr)
                print("Run command: 'pip install pypdf' to automatically analyze resumes.", file=sys.stderr)
                return ""

            with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
                tmp_file.write(file_payload)
                tmp_filename = tmp_file.name

            extracted_text = ""
            try:
                pdf_reader = pypdf.PdfReader(tmp_filename)
                for page in pdf_reader.pages:
                    extracted_text += page.extract_text() or ""
            finally:
                os.remove(tmp_filename)
            print(f"[Resume] Extracted {len(extracted_text)} bytes of descriptive text from PDF document.")
            return extracted_text
        else:
            return file_payload.decode('utf-8', errors='ignore')
    except Exception as err:
        print(f"[Warning] Handshake or parse pipeline failed for document link: {err}", file=sys.stderr)
        return ""


def extract_text_from_linkedin(url: str) -> str:
    """Fetches public LinkedIn profile content and strips basic HTML tags."""
    if not url:
        return ""
    print(f"[LinkedIn] Requesting target public interface profile data...")

    browser_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5"
    }
    req = urllib.request.Request(url, headers=browser_headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            html_raw = response.read().decode('utf-8', errors='ignore')
        # Filter raw HTML syntax down to single block structures
        filtered_text = re.sub(r'<[^>]+>', ' ', html_raw)
        return filtered_text
    except urllib.error.HTTPError as err:
        print(f"[Warning] Security verification returned HTTP Status {err.code}. Anti-scraping gates active.", file=sys.stderr)
        return ""
    except Exception as err:
        print(f"[Warning] Intermittent connection drop reading network link: {err}", file=sys.stderr)
        return ""


# ===================================================================
# Scoring / taxonomy helpers
# ===================================================================

def score_text_by_taxonomy(text: str) -> tuple[Counter, defaultdict]:
    """Scans standalone text strings using global domain taxonomy engine rules."""
    text_scores: Counter = Counter()
    text_keywords: defaultdict = defaultdict(Counter)
    if not text:
        return text_scores, text_keywords

    text_lines = text.split('\n')
    for line in text_lines:
        for domain, matching_patterns in DOMAIN_REGEXES.items():
            for keyword, compiled_regex in matching_patterns:
                if compiled_regex.search(line):
                    text_keywords[domain][keyword] += 1
                    text_scores[domain] += 3  # Assigned keyword weight for curated artifacts
    return text_scores, text_keywords


def collect_discovered_items(
    discovered_topics: Counter,
    all_unmatched_imports: Counter,
) -> list[list]:
    """Identifies libraries and keywords not yet covered by the domain taxonomy."""
    all_known_libs: set[str] = set()
    all_known_kws: set[str] = set()
    for cfg in DOMAINS.values():
        all_known_libs.update(cfg.get("libraries", set()))
        all_known_kws.update(cfg.get("keywords", set()))

    stdlib_modules = {
        "os", "sys", "re", "json", "math", "random", "time", "datetime", "collections",
        "itertools", "functools", "pathlib", "typing", "abc", "io", "copy", "string",
        "hashlib", "logging", "argparse", "subprocess", "threading", "multiprocessing",
        "socket", "http", "urllib", "email", "html", "xml", "csv", "sqlite3", "pickle",
        "struct", "enum", "dataclasses", "contextlib", "warnings", "traceback", "inspect",
        "ast", "dis", "gc", "ctypes", "platform", "shutil", "glob", "tempfile",
        "textwrap", "pprint", "operator", "decimal", "fractions", "statistics",
        "unittest", "doctest", "pdb", "profile", "timeit", "array", "queue",
        "heapq", "bisect", "weakref", "types", "codecs", "locale", "gettext",
        "secrets", "hmac", "base64", "binascii", "uuid", "ipaddress",
        "configparser", "zipfile", "tarfile", "gzip", "bz2", "lzma",
        "signal", "mmap", "select", "selectors", "asyncio", "concurrent",
        "test", "__future__", "importlib", "pkgutil", "setuptools", "pip",
        "site", "sysconfig", "distutils", "venv", "ensurepip",
        "app", "config", "settings", "models", "views", "urls", "forms",
        "utils", "helpers", "constants", "exceptions", "tests", "main",
        "setup", "manage", "conftest", "fixtures", "migrations"
    }

    discovered: list[list] = []
    for lib, count in all_unmatched_imports.most_common():
        if (count >= 2 and lib not in stdlib_modules and lib not in all_known_libs
                and not lib.startswith("_") and len(lib) > 1):
            discovered.append(["Library", lib, count])

    for topic, count in discovered_topics.most_common():
        topic_lower = topic.lower().replace("-", " ")
        if topic_lower not in all_known_kws and topic_lower not in stdlib_modules:
            discovered.append(["Keyword", topic_lower, count])

    return discovered


# ===================================================================
# File scanning helpers
# ===================================================================

def is_binary(file_path: str) -> bool:
    """Return True if *file_path* appears to be a binary file."""
    _, ext = os.path.splitext(file_path)
    if ext.lower() in BINARY_EXTENSIONS:
        return True
    try:
        with open(file_path, 'rb') as f:
            chunk = f.read(1024)
            return b'\x00' in chunk
    except Exception:
        return True


def extract_python_imports(line: str) -> str | None:
    """Extract the top-level module name from a Python import statement."""
    m1 = re.match(r'^\s*import\s+([a-zA-Z0-9_]+)', line)
    if m1:
        return m1.group(1).lower()
    m2 = re.match(r'^\s*from\s+([a-zA-Z0-9_]+)', line)
    if m2:
        return m2.group(1).lower()
    return None


def extract_js_imports(line: str) -> str | None:
    """Extract the top-level package name from a JS/TS import or require()."""
    m1 = re.search(r'from\s+[\'"]([^\'\"]+)[\'"]', line)
    if m1:
        pkg = m1.group(1)
        if pkg.startswith('@'):
            parts = pkg.split('/')
            return '/'.join(parts[:2]).lower() if len(parts) >= 2 else pkg.lower()
        return pkg.split('/')[0].lower()
    m2 = re.search(r'require\(\s*[\'"]([^\'\"]+)[\'"]\s*\)', line)
    if m2:
        pkg = m2.group(1)
        if pkg.startswith('@'):
            parts = pkg.split('/')
            return '/'.join(parts[:2]).lower() if len(parts) >= 2 else pkg.lower()
        return pkg.split('/')[0].lower()
    return None


def extract_cpp_includes(line: str) -> str | None:
    """Extract the header / library name from a C/C++ #include directive."""
    m = re.match(r'^\s*#include\s*[<"]([^>"]+)[>"]', line)
    if m:
        header = m.group(1)
        return header.split('/')[0].lower()
    return None


def parse_dependency_file(file_path: str, filename: str) -> set[str]:
    """Parse known dependency manifests and return the set of library names."""
    libs: set[str] = set()
    try:
        if filename == "requirements.txt":
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    m = re.match(r'^([a-zA-Z0-9_-]+)', line)
                    if m:
                        libs.add(m.group(1).lower())
        elif filename == "package.json":
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                data = json.load(f)
                for dep_type in ["dependencies", "devDependencies"]:
                    if dep_type in data:
                        for dep in data[dep_type]:
                            libs.add(dep.lower())
        elif filename == "Cargo.toml":
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                in_deps = False
                for line in f:
                    line = line.strip()
                    if line.startswith('[dependencies]') or line.startswith('[dev-dependencies]'):
                        in_deps = True
                        continue
                    elif line.startswith('[') and in_deps:
                        in_deps = False
                    if in_deps and '=' in line:
                        m = re.match(r'^([a-zA-Z0-9_-]+)', line)
                        if m:
                            libs.add(m.group(1).lower())
    except Exception:
        pass
    return libs


def parse_ipynb(file_path: str) -> tuple[list[str], list[str]]:
    """Extract code and markdown lines from a Jupyter notebook file."""
    code_lines: list[str] = []
    markdown_lines: list[str] = []
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            notebook = json.load(f)
            cells = notebook.get("cells", [])
            for cell in cells:
                cell_type = cell.get("cell_type")
                source = cell.get("source", [])
                if isinstance(source, str):
                    source = [source]
                if cell_type == "code":
                    code_lines.extend(source)
                elif cell_type == "markdown":
                    markdown_lines.extend(source)
    except Exception:
        pass
    return code_lines, markdown_lines


def clean_dir(dir_path: str) -> None:
    """Force-remove a directory tree, handling read-only files on Windows."""
    if not os.path.exists(dir_path):
        return
    for root, dirs, files in os.walk(dir_path, topdown=False):
        for name in files:
            file_path = os.path.join(root, name)
            try:
                os.chmod(file_path, stat.S_IWRITE)
                os.remove(file_path)
            except Exception:
                pass
        for name in dirs:
            dir_path_sub = os.path.join(root, name)
            try:
                os.chmod(dir_path_sub, stat.S_IWRITE)
                os.rmdir(dir_path_sub)
            except Exception:
                pass
    try:
        shutil.rmtree(dir_path)
    except Exception:
        pass


# ===================================================================
# Input normalization helpers
# ===================================================================

def normalize_github_input(value: str) -> str | None:
    """Normalize a GitHub username or profile URL to a plain username.

    Accepts raw usernames, ``@user`` handles, or full ``github.com/`` URLs.
    Returns ``None`` for empty / blank input.
    """
    if not value:
        return None
    value = value.strip()
    if 'github.com/' in value:
        return value.split('github.com/')[-1].split('/')[0]
    return value.replace('@', '').strip('/')


def normalize_linkedin_input(value: str) -> str | None:
    """Normalize a LinkedIn identifier to a full profile URL.

    If *value* already contains ``linkedin.com/`` it is returned as-is.
    Otherwise it is treated as a vanity slug and wrapped in the canonical URL.
    Returns ``None`` for empty / blank input.
    """
    if not value:
        return None
    value = value.strip()
    if 'linkedin.com/' in value:
        return value
    value = value.replace('@', '').strip('/')
    return f'https://www.linkedin.com/in/{value}/'


# ===================================================================
# Core analysis pipeline
# ===================================================================


def is_repo_url(url: str) -> bool:
    import urllib.parse
    parsed = urllib.parse.urlparse(url)
    parts = [p for p in parsed.path.split('/') if p]
    return len(parts) >= 2

def score_repo_for_domain(repo_name: str, topics: list[str], domain: str) -> int:
    score = 0
    text = (repo_name + " " + " ".join(topics)).lower()
    if domain == "Quantum Technology":
        if any(x in text for x in ['qiskit', 'quantum', 'qml', 'cirq', 'pennylane']): score += 10
    elif domain == "ML/DL/DS/AI":
        if any(x in text for x in ['ml', 'ai', 'deep', 'learning', 'data', 'neural']): score += 10
    return score

def process_github_input(val: str, domain_focus: str, token: str) -> tuple[str, list[dict]]:
    if not val or len(val.strip()) == 0:
        return "", []
        
    val = val.strip()
    
    # If it's pure text description
    if "github.com" not in val and (" " in val or len(val) > 40):
        return val, []

    # If it's a github URL
    if "github.com" in val:
        # Check if it's a direct repo (github.com/user/repo)
        path = val.split("github.com/")[-1].split("/")
        if len(path) >= 2 and path[1].strip():
            user = path[0].strip()
            repo = path[1].strip()
            # Clean up repo name (remove .git etc)
            if repo.endswith(".git"): repo = repo[:-4]
            return "", [{"name": repo, "clone_url": f"https://github.com/{user}/{repo}.git"}]
            
    # It's a profile URL or username
    username = normalize_github_input(val) or val
    repos = get_repos(username, token)
    repos = [r for r in repos if not r.get("fork", False)]
    
    # Score repos for this domain
    scored_repos = []
    for r in repos:
        topics = get_repo_topics(username, r["name"], token)
        s = score_repo_for_domain(r["name"], topics, domain_focus)
        scored_repos.append((s, r))
    
    scored_repos.sort(key=lambda x: x[0], reverse=True)
    # Return top 2 repos
    return "", [x[1] for x in scored_repos[:2]]

def analyze_candidate(
    classical_github: str = "",
    quantum_github: str = "",
    resume_url: str | None = None,
    linkedin_url: str | None = None,
    token: str | None = None,
    config_path: str | None = None,
) -> dict[str, Any]:
    """Run the full candidate-screening pipeline and return structured results.

    Parameters
    ----------
    github_username:
        GitHub username or profile URL.
    resume_url:
        Optional Google Drive link to a résumé (PDF or plain text).
    linkedin_url:
        Optional public LinkedIn profile URL.
    token:
        GitHub Personal Access Token.  Falls back to the ``GITHUB_TOKEN``
        environment variable when *None*.
    config_path:
        Path to the domain taxonomy JSON config.  Defaults to
        ``domains.json`` next to this script.
    exclude_forks:
        When *True*, forked repositories are excluded from analysis.

    Returns
    -------
    dict
        A dictionary containing all analysis results (scores, languages,
        libraries, keywords, per-repo breakdowns, etc.).
    """

    # -- Resolve config & load taxonomy (idempotent guard) ----------------
    if not DOMAINS:
        resolved_config = config_path or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "domains.json"
        )
        load_domain_config(resolved_config)

    # -- Normalize inputs -------------------------------------------------
    resume_url = resume_url.strip() if resume_url else None
    linkedin_url = normalize_linkedin_input(linkedin_url) if linkedin_url else None

    # -- Resolve token (never hardcoded) ----------------------------------
    token = token or os.getenv('GITHUB_TOKEN')

    # -- Core profile-level accumulators ----------------------------------
    profile_languages: Counter = Counter()
    profile_libraries: defaultdict = defaultdict(set)
    profile_keywords: defaultdict = defaultdict(Counter)
    profile_domain_scores: Counter = Counter()

    resume_scores: Counter = Counter()
    resume_keywords: defaultdict = defaultdict(Counter)
    linkedin_scores: Counter = Counter()
    linkedin_keywords: defaultdict = defaultdict(Counter)

    # -- 1. Resume Analysis Context ---------------------------------------
    if resume_url:
        resume_text = extract_text_from_gdrive_resume(resume_url)
        if resume_text:
            resume_scores, resume_keywords = score_text_by_taxonomy(resume_text)
            profile_domain_scores.update(resume_scores)
            for d in DOMAINS:
                profile_keywords[d].update(resume_keywords[d])

    # -- 2. LinkedIn Profile Context --------------------------------------
    if linkedin_url:
        linkedin_text = extract_text_from_linkedin(linkedin_url)
        if linkedin_text:
            linkedin_scores, linkedin_keywords = score_text_by_taxonomy(linkedin_text)
            profile_domain_scores.update(linkedin_scores)
            for d in DOMAINS:
                profile_keywords[d].update(linkedin_keywords[d])

    # -- 3. GitHub Mining Engine (Classical + Quantum) --------------------
    repos = []
    
    # Classical
    c_text, c_repos = process_github_input(classical_github, "Software Engineering", token)
    if c_text:
        c_scores, c_kws = score_text_by_taxonomy(c_text)
        profile_domain_scores.update(c_scores)
        for d in DOMAINS: profile_keywords[d].update(c_kws[d])
    repos.extend(c_repos)
    
    # Quantum
    q_text, q_repos = process_github_input(quantum_github, "Quantum Technology", token)
    if q_text:
        q_scores, q_kws = score_text_by_taxonomy(q_text)
        profile_domain_scores.update(q_scores)
        for d in DOMAINS: profile_keywords[d].update(q_kws[d])
    repos.extend(q_repos)

    # Deduplicate repos
    seen_urls = set()
    unique_repos = []
    for r in repos:
        if r['clone_url'] not in seen_urls:
            unique_repos.append(r)
            seen_urls.add(r['clone_url'])
    repos = unique_repos
    
    print(f"Found {len(repos)} optimal repositories to process.")
    username = "candidate"  # placeholder since we use direct repo links now

    repo_analyses: list[dict] = []

    all_unmatched_imports: Counter = Counter()
    all_discovered_topics: Counter = Counter()
    global_start_time = time.time()

    if repos:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_root:
            for idx, repo in enumerate(repos, 1):
                if time.time() - global_start_time > 60 * 60:
                    print(f"\n[Warning] Global timeout of 60 minutes reached.")
                    break
                repo_name = repo["name"]
                is_fork = repo.get("fork", False)
                clone_url = repo["clone_url"]
                if token:
                    clone_url = clone_url.replace("https://", f"https://{token}@")

                repo_topics = get_repo_topics(username, repo_name, token)
                for topic in repo_topics:
                    all_discovered_topics[topic.lower()] += 1

                print(f"[{idx}/{len(repos)}] Analyzing: {repo_name}...", end="", flush=True)

                repo_temp_path = os.path.join(temp_root, repo_name)
                clone_success = False
                env = os.environ.copy()
                env["GIT_TERMINAL_PROMPT"] = "0"
                env["GIT_ASKPASS"] = "echo"  # Prevent GUI prompt on Windows

                for attempt in range(1, 3):
                    if os.path.exists(repo_temp_path):
                        clean_dir(repo_temp_path)
                    try:
                        subprocess.run(
                            ["git", "clone", "-c", "credential.helper=", "--depth", "1", clone_url, repo_temp_path],
                            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            env=env, timeout=15
                        )
                        clone_success = True
                        break
                    except subprocess.TimeoutExpired:
                        if attempt < 2:
                            print(f"\n[Warning] Clone timed out for {repo_name}, retrying...")
                            time.sleep(2)
                        continue
                    except subprocess.CalledProcessError:
                        break

                if not clone_success:
                    print(" -> FAILED")
                    continue

                repo_languages: Counter = Counter()
                repo_libraries: defaultdict = defaultdict(set)
                repo_keywords: defaultdict = defaultdict(Counter)
                repo_domain_scores: Counter = Counter()

                ignored_folders = {
                    '.git', 'node_modules', 'venv', '.venv', 'env', '.env', 'target',
                    'dist', 'build', 'out', '.next', '.nuxt', '__pycache__', '.idea',
                    '.vscode', 'bower_components', 'tmp', 'temp'
                }

                for root, dirs, files in os.walk(repo_temp_path):
                    dirs[:] = [d for d in dirs if d.lower() not in ignored_folders]

                    for file in files:
                        file_path = os.path.join(root, file)
                        filename = os.path.basename(file)
                        _, ext = os.path.splitext(file)
                        ext = ext.lower() if ext else "no_extension"

                        lang_map = {
                            ".py": "Python", ".ipynb": "Jupyter Notebook",
                            ".cpp": "C++", ".cc": "C++", ".cxx": "C++", ".h": "C++", ".hpp": "C++",
                            ".js": "JavaScript", ".jsx": "JavaScript",
                            ".ts": "TypeScript", ".tsx": "TypeScript",
                            ".go": "Go", ".rs": "Rust", ".java": "Java",
                            ".kt": "Kotlin", ".swift": "Swift", ".rb": "Ruby",
                            ".cs": "C#", ".sh": "Shell", ".md": "Markdown"
                        }
                        lang = lang_map.get(ext)
                        if lang:
                            repo_languages[lang] += 1

                        dep_libs = parse_dependency_file(file_path, filename)
                        if dep_libs:
                            for lib in dep_libs:
                                matched = False
                                for domain, config in DOMAINS.items():
                                    if lib in config["libraries"]:
                                        repo_libraries[domain].add(lib)
                                        repo_domain_scores[domain] += 10
                                        matched = True
                                if not matched:
                                    all_unmatched_imports[lib] += 1

                        content_lines: list[str] = []
                        markdown_lines: list[str] = []

                        file_size = os.path.getsize(file_path)
                        if file_size <= 1.5 * 1024 * 1024:
                            if ext == ".ipynb":
                                content_lines, markdown_lines = parse_ipynb(file_path)
                            elif not is_binary(file_path):
                                for encoding in ['utf-8', 'latin-1']:
                                    try:
                                        with open(file_path, 'r', encoding=encoding, errors='ignore') as f:
                                            content_lines = f.readlines()
                                        break
                                    except Exception:
                                        continue

                        file_keywords: defaultdict = defaultdict(set)

                        for line in content_lines:
                            imp = None
                            if ext == ".py" or ext == ".ipynb":
                                imp = extract_python_imports(line)
                            elif ext in [".js", ".jsx", ".ts", ".tsx"]:
                                imp = extract_js_imports(line)
                            elif ext in [".cpp", ".cc", ".cxx", ".h", ".hpp"]:
                                imp = extract_cpp_includes(line)

                            if imp:
                                matched = False
                                for domain, config in DOMAINS.items():
                                    if imp in config["libraries"]:
                                        repo_libraries[domain].add(imp)
                                        repo_domain_scores[domain] += 5
                                        matched = True
                                if not matched:
                                    all_unmatched_imports[imp] += 1

                            for domain, patterns in DOMAIN_REGEXES.items():
                                for kw, pattern in patterns:
                                    if pattern.search(line):
                                        file_keywords[domain].add(kw)

                        for line in markdown_lines:
                            for domain, patterns in DOMAIN_REGEXES.items():
                                for kw, pattern in patterns:
                                    if pattern.search(line):
                                        file_keywords[domain].add(kw)

                        for domain, kws in file_keywords.items():
                            for kw in kws:
                                repo_keywords[domain][kw] += 1
                                repo_domain_scores[domain] += 1

                clean_dir(repo_temp_path)
                print(" -> DONE")

                profile_languages.update(repo_languages)
                profile_domain_scores.update(repo_domain_scores)
                for d in DOMAINS:
                    profile_libraries[d].update(repo_libraries[d])
                    profile_keywords[d].update(repo_keywords[d])

                primary_domain = "General Software Engineering"
                max_score = 0
                if repo_domain_scores:
                    primary_domain, max_score = repo_domain_scores.most_common(1)[0]
                    if max_score < 5:
                        primary_domain = "General Software Engineering"

                repo_analyses.append({
                    "name": repo_name, "fork": is_fork, "languages": repo_languages,
                    "libraries": {d: list(libs) for d, libs in repo_libraries.items() if libs},
                    "keywords": {d: [kw for kw, _ in kws.most_common(5)] for d, kws in repo_keywords.items() if kws},
                    "scores": dict(repo_domain_scores), "primary_domain": primary_domain, "max_score": max_score
                })

        all_domains = list(DOMAINS.keys())
        repo_analyses.sort(key=lambda r: sum(r["scores"].get(d, 0) for d in all_domains), reverse=True)

    # -- Derive overall primary domain ------------------------------------
    primary_domain = "General Software Engineering"
    if profile_domain_scores:
        top_domain, top_score = profile_domain_scores.most_common(1)[0]
        if top_score >= IMPORTANT_THRESHOLDS.get("default_min_score", 5):
            primary_domain = top_domain

    # -- Discovered items -------------------------------------------------
    discovered_items = collect_discovered_items(all_discovered_topics, all_unmatched_imports)

    # -- Assemble return dict ---------------------------------------------
    return {
        'username': username,
        'domain_scores': dict(profile_domain_scores),
        'languages': dict(profile_languages),
        'libraries': {d: list(libs) for d, libs in profile_libraries.items()},
        'keywords': {d: dict(kws) for d, kws in profile_keywords.items()},
        'resume_scores': dict(resume_scores),
        'linkedin_scores': dict(linkedin_scores),
        'resume_keywords': {d: dict(kws) for d, kws in resume_keywords.items()},
        'linkedin_keywords': {d: dict(kws) for d, kws in linkedin_keywords.items()},
        'repo_analyses': repo_analyses,
        'repos_scanned': len(repos),
        'resume_evaluated': bool(resume_url),
        'linkedin_screened': bool(linkedin_url),
        'primary_domain': primary_domain,
        'total_score': sum(profile_domain_scores.values()),
        'discovered_items': discovered_items,
    }


# ===================================================================
# Report generation
# ===================================================================

def write_report(results: dict, output_path: str) -> None:
    """Write a human-readable text report from *results* to *output_path*.

    Parameters
    ----------
    results:
        The dictionary returned by :func:`analyze_candidate`.
    output_path:
        Filesystem path for the output report file.
    """
    username = results['username']

    # Reconstruct Counter / defaultdict views needed for formatting
    profile_domain_scores = Counter(results['domain_scores'])
    profile_languages = Counter(results['languages'])
    profile_libraries: dict[str, list] = results['libraries']
    profile_keywords: dict[str, dict] = results['keywords']
    resume_scores = Counter(results['resume_scores'])
    linkedin_scores = Counter(results['linkedin_scores'])
    resume_keywords: dict[str, dict] = results.get('resume_keywords', {})
    linkedin_keywords: dict[str, dict] = results.get('linkedin_keywords', {})
    repo_analyses: list[dict] = results['repo_analyses']
    repos_scanned: int = results['repos_scanned']
    resume_evaluated: bool = results['resume_evaluated']
    linkedin_screened: bool = results['linkedin_screened']

    print(f"Writing integrated structural analytics report to: {output_path}...")
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("=" * 80 + "\n")
        f.write(f"{'INTEGRATED CANDIDATE PROFILE SKILL LOG MATRIX':^80}\n")
        f.write("=" * 80 + "\n")
        f.write(f"Target Profile Id:    {username}\n")
        f.write(f"Generated On:         {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"GitHub Repos Scanned: {repos_scanned}\n")
        f.write(f"Resume Evaluated:     {'Yes' if resume_evaluated else 'No'}\n")
        f.write(f"LinkedIn Screened:    {'Yes' if linkedin_screened else 'No'}\n")
        f.write("=" * 80 + "\n\n")

        # Section 1: Aggregated Multi-Channel Taxonomy Scores
        f.write("=" * 80 + "\n")
        f.write("1. CONSOLIDATED TAXONOMY WEIGHT PROFILE (ALL CHANNELS)\n")
        f.write("=" * 80 + "\n")
        max_possible = max(profile_domain_scores.values()) if profile_domain_scores else 1
        for domain in DOMAINS:
            score = profile_domain_scores[domain]
            bar_len = int((score / max_possible) * 30) if max_possible > 0 else 0
            bar = "■" * bar_len + "□" * (30 - bar_len)
            f.write(f"   - {domain:<25} [{bar}] (Total Score: {score})\n")
        f.write("\n")

        # Section 2: Isolated Cross-Channel Breakdown Matrices
        f.write("=" * 80 + "\n")
        f.write("2. SEPARATED ARTIFACT CHANNEL CHANNEL ANALYTICS\n")
        f.write("=" * 80 + "\n")

        # Resume Sub-section
        f.write("A. Google Drive Resume Context Metrics:\n")
        if resume_evaluated and resume_scores:
            for domain in DOMAINS:
                if resume_scores[domain] > 0:
                    kws = resume_keywords.get(domain, {})
                    top_kw_str = ", ".join(
                        [f"{k}(x{c})" for k, c in Counter(kws).most_common(5)]
                    )
                    f.write(f"   * {domain:<22} -> Subscore: {resume_scores[domain]:<3} | Keywords: {top_kw_str}\n")
        else:
            f.write("   [No Resume details submitted or zero taxonomy keywords cataloged]\n")
        f.write("\n")

        # LinkedIn Sub-section
        f.write("B. LinkedIn Profile Interface Context Metrics:\n")
        if linkedin_screened and linkedin_scores:
            for domain in DOMAINS:
                if linkedin_scores[domain] > 0:
                    kws = linkedin_keywords.get(domain, {})
                    top_kw_str = ", ".join(
                        [f"{k}(x{c})" for k, c in Counter(kws).most_common(5)]
                    )
                    f.write(f"   * {domain:<22} -> Subscore: {linkedin_scores[domain]:<3} | Keywords: {top_kw_str}\n")
        else:
            f.write("   [No LinkedIn profiles extracted or zero profile tokens verified]\n")
        f.write("\n")

        # Codebase Languages Breakdown
        f.write("C. Code Repository Core Compilation Languages:\n")
        if profile_languages:
            for lang, count in profile_languages.most_common():
                f.write(f"   - {lang:<20} ({count} structural workspace source files)\n")
        else:
            f.write("   [No native repositories scanned inside GitHub domain metrics]\n")
        f.write("\n")

        # Section 3: Core Framework Library and Conceptual Registries
        f.write("=" * 80 + "\n")
        f.write("3. DETAILED PHRASE & CONCEPT CONTEXT REGISTRY\n")
        f.write("=" * 80 + "\n")
        for domain in DOMAINS:
            f.write(f" * {domain}:\n")
            libs = profile_libraries.get(domain, [])
            if libs:
                f.write(f"   - Code Libraries: {', '.join(sorted(libs))}\n")
            kws = profile_keywords.get(domain, {})
            if kws:
                top_kws = [f"{kw} (x{count})" for kw, count in Counter(kws).most_common(10)]
                f.write(f"   - Core Framework Concepts: {', '.join(top_kws)}\n")
            if not libs and not kws:
                f.write("   - No domain signals registered across candidate profiles.\n")
        f.write("\n")

        # Section 4: Code Repository Specific Lineage
        if repo_analyses:
            f.write("=" * 80 + "\n")
            f.write("4. COMPLETE SOURCE WORKSPACE INDEX BREAKDOWN\n")
            f.write("=" * 80 + "\n")
            for idx, r in enumerate(repo_analyses, 1):
                f.write(f"#{idx} Repository Profile: {r['name']} {'(Fork Asset)' if r['fork'] else ''}\n")
                langs = ", ".join([f"{l} ({c})" for l, c in Counter(r["languages"]).most_common(3)])
                f.write(f"  - System Languages: {langs if langs else 'None detected'}\n")
                scores_str = ", ".join([f"{d}: {s}" for d, s in r["scores"].items() if s > 0])
                f.write(f"  - Structural Weights: {scores_str if scores_str else 'General Space: 0'}\n")
                f.write("-" * 40 + "\n")

        f.write("=" * 80 + "\n")
        f.write(f"{'END OF PIPELINE AUDIT REPORT':^80}\n")
        f.write("=" * 80 + "\n")

    print(f"Aggregated workspace audit profile closed successfully. Manifest logs: {output_path}")


# ===================================================================
# CLI entry point
# ===================================================================

def main() -> None:
    """Parse CLI arguments, run the analysis, and write the report."""
    parser = argparse.ArgumentParser(
        description="Analyze GitHub profile along with Resume and LinkedIn contexts using core domain taxonomies."
    )
    parser.add_argument("username", help="GitHub username or profile URL to analyze")
    parser.add_argument("--resume", help="Google Drive link pointing directly to applicant's Resume asset", default=None)
    parser.add_argument("--linkedin", help="Public URL configuration link for Candidate LinkedIn profile", default=None)
    parser.add_argument("--config", help="Path to domain taxonomy JSON config (default: domains.json)", default=None)
    parser.add_argument("--token", help="GitHub Personal Access Token", default=None)
    parser.add_argument("--exclude-forks", action="store_true", help="Exclude forks from indexing metric tallies")
    parser.add_argument("--output", help="Path to save text report metrics", default=None)
    args = parser.parse_args()

    results = analyze_candidate(
        github_username=args.username,
        resume_url=args.resume,
        linkedin_url=args.linkedin,
        token=args.token,
        config_path=args.config,
        exclude_forks=args.exclude_forks,
    )

    output_path = args.output or f"{results['username']}_skills_report.txt"
    write_report(results, output_path)


if __name__ == "__main__":
    main()