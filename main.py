import os
import argparse
import logging
from datetime import datetime
from tqdm import tqdm

from loaders import load_candidates
from analyzer import analyze_candidate, load_domain_config
from scoring import compute_candidate_scores, determine_shortlist
from exporters import export_results

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def main():
    parser = argparse.ArgumentParser(description="QIntern Batch Profile Analyzer")
    parser.add_argument("input_spreadsheet", help="Path to input CSV or Excel file")
    parser.add_argument("--output", default="qintern_results.xlsx", help="Output Excel filename")
    parser.add_argument("--config", default=None, help="Path to domains.json config")
    parser.add_argument("--token", default=None, help="GitHub Personal Access Token (or set GITHUB_TOKEN env var)")
    parser.add_argument("--exclude-forks", action="store_true", help="Exclude forks from GitHub analysis")
    args = parser.parse_args()

    token = args.token or os.getenv("GITHUB_TOKEN")
    
    # Pre-load config
    config_path = args.config or os.path.join(os.path.dirname(os.path.abspath(__file__)), "domains.json")
    try:
        load_domain_config(config_path)
    except Exception as e:
        logging.error(f"Failed to load domain config: {e}")
        return

    logging.info(f"Loading candidates from {args.input_spreadsheet}...")
    try:
        candidates = load_candidates(args.input_spreadsheet)
    except Exception as e:
        logging.error(f"Failed to load spreadsheet: {e}")
        return

    logging.info(f"Loaded {len(candidates)} candidates. Starting analysis pipeline.")

    start_time = datetime.now()
    error_count = 0

    for idx, candidate in enumerate(tqdm(candidates, desc="Analyzing candidates"), 1):
        # Only run analyzer if we have at least one valid input URL
        if not candidate.classical_github and not candidate.quantum_github and not candidate.resume_url and not candidate.linkedin_url:
            candidate.analysis_error = "No trackable profiles provided"
            candidate.analysis_complete = True
            
            # Give a default reject shortlist status
            candidate.computed_status = "REJECT"
            candidate.status_reason = "No trackable profiles provided"
            continue

        try:
            # 1. Run Analyzer Pipeline
            analysis_results = analyze_candidate(
                classical_github=candidate.classical_github,
                quantum_github=candidate.quantum_github,
                resume_url=candidate.resume_url,
                linkedin_url=candidate.linkedin_url,
                token=token,
                config_path=config_path
            )

            # Update candidate with raw analyzer data
            candidate.languages = analysis_results.get('languages', {})
            candidate.libraries = analysis_results.get('libraries', {})
            candidate.resume_scores = analysis_results.get('resume_scores', {})
            candidate.linkedin_scores = analysis_results.get('linkedin_scores', {})
            candidate.repos_scanned = analysis_results.get('repos_scanned', 0)

            # 2. Run Scoring Engine
            scored_results = compute_candidate_scores(analysis_results)
            
            candidate.domain_scores = scored_results['domain_scores']
            candidate.total_score = scored_results['total_score']
            candidate.primary_domain = scored_results['primary_domain']

            # 3. Run Shortlisting Engine
            shortlist_res = determine_shortlist(
                scores=scored_results['domain_scores'],
                has_quantum=scored_results['has_quantum'],
                has_ml=scored_results['has_ml'],
                has_software=scored_results['has_software'],
                has_cloud=scored_results['has_cloud'],
                has_math=scored_results['has_math'],
                repos_scanned=candidate.repos_scanned,
                resume_evaluated=analysis_results.get('resume_evaluated', False),
                linkedin_screened=analysis_results.get('linkedin_screened', False)
            )

            # Update candidate with shortlisting decisions
            candidate.computed_status = shortlist_res.status
            candidate.assigned_bucket = shortlist_res.assigned_bucket
            candidate.status_reason = shortlist_res.reason
            
            candidate.analysis_complete = True

        except Exception as e:
            logging.error(f"Error processing candidate {candidate.full_name}: {e}")
            candidate.analysis_error = str(e)
            candidate.analysis_complete = True
            error_count += 1

    processing_time = (datetime.now() - start_time).total_seconds()
    
    # Calculate summary stats
    shortlisted_count = sum(1 for c in candidates if getattr(c, 'computed_status', '') == "SHORTLIST")
    review_count = sum(1 for c in candidates if getattr(c, 'computed_status', '') == "REVIEW")
    rejected_count = sum(1 for c in candidates if getattr(c, 'computed_status', '') == "REJECT")

    run_metadata = {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'total_processed': len(candidates),
        'shortlisted': shortlisted_count,
        'review': review_count,
        'rejected': rejected_count,
        'errors': error_count,
        'processing_time': f"{processing_time:.1f}s",
        'config_used': config_path
    }

    logging.info("Exporting results to Excel...")
    try:
        output_path = export_results(candidates, args.output, run_metadata=run_metadata)
        logging.info(f"Pipeline complete! Results saved to {output_path}")
    except Exception as e:
        logging.error(f"Failed to export Excel results: {e}")


if __name__ == "__main__":
    main()
