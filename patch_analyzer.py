import re

def patch_analyzer():
    with open("analyzer.py", "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Add process_github_input and score_repo helper
    new_helpers = """
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
"""
    
    # Insert new_helpers before analyze_candidate
    content = content.replace("def analyze_candidate(", new_helpers + "\ndef analyze_candidate(")

    # 2. Update analyze_candidate signature and logic
    old_sig = """def analyze_candidate(
    github_username: str,
    resume_url: str | None = None,
    linkedin_url: str | None = None,
    token: str | None = None,
    config_path: str | None = None,
    exclude_forks: bool = False,
) -> dict[str, Any]:"""
    
    new_sig = """def analyze_candidate(
    classical_github: str = "",
    quantum_github: str = "",
    resume_url: str | None = None,
    linkedin_url: str | None = None,
    token: str | None = None,
    config_path: str | None = None,
) -> dict[str, Any]:"""

    content = content.replace(old_sig, new_sig)
    
    # 3. Replace the input normalization and repo fetching
    old_setup = """    # -- Normalize inputs -------------------------------------------------
    username = normalize_github_input(github_username) or github_username
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

    # -- 3. GitHub Mining Engine ------------------------------------------
    print(f"Fetching repos for: {username}...")
    repos = get_repos(username, token)
    if exclude_forks:
        repos = [r for r in repos if not r.get("fork", False)]

    print(f"Found {len(repos)} repositories to process.")"""

    new_setup = """    # -- Normalize inputs -------------------------------------------------
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
"""

    content = content.replace(old_setup, new_setup)
    
    # Write back
    with open("analyzer.py", "w", encoding="utf-8") as f:
        f.write(content)
        
    print("Patched analyzer.py successfully.")

if __name__ == "__main__":
    patch_analyzer()
