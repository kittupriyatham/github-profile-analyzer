import os
import glob
import json
import argparse
import logging
from typing import Any, Dict, List, Tuple
from dataclasses import dataclass, field

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

@dataclass
class EvaluationResult:
    filename: str
    candidate_id: str = ""
    total_score: int = 0
    feedback: List[str] = field(default_factory=list)
    has_syntax_errors: bool = False
    imports_used: set = field(default_factory=set)

def parse_ipynb(file_path: str) -> Tuple[List[str], List[str]]:
    """Extract code and markdown lines from a Jupyter notebook file."""
    code_lines: List[str] = []
    markdown_lines: List[str] = []
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
    except Exception as e:
        logging.error(f"Error parsing {file_path}: {e}")
    return code_lines, markdown_lines

def evaluate_notebook(file_path: str) -> EvaluationResult:
    """Evaluate a single Jupyter Notebook submission."""
    filename = os.path.basename(file_path)
    # Extract candidate ID from filename if possible (e.g., '123_assessment.ipynb' -> '123')
    candidate_id = filename.split('_')[0] if '_' in filename else filename.split('.')[0]
    
    result = EvaluationResult(filename=filename, candidate_id=candidate_id)
    
    code_lines, markdown_lines = parse_ipynb(file_path)
    
    if not code_lines and not markdown_lines:
        result.feedback.append("Notebook is empty or failed to parse.")
        return result

    # --- 1. Static Analysis & Scoring (Customizable) ---
    full_code = "".join(code_lines).lower()
    full_markdown = "".join(markdown_lines).lower()
    
    # --- 1. MCQ Scoring ---
    import re
    score = 0
    
    # We look for `-[x]`, `-[X]`, `-[v]`, `-[V]` or `**Answer:**` next to the correct answers
    mcq_answers = [
        r"(?:\[[xXvV]\]|\*\*Answer:\*\*)\s*a maximally entangled bell state", # Q1
        r"(?:\[[xXvV]\]|\*\*Answer:\*\*)\s*pauli-x gate", # Q2
        r"(?:\[[xXvV]\]|\*\*Answer:\*\*)\s*it has an equal 50% probability of collapsing", # Q3
        r"(?:\[[xXvV]\]|\*\*Answer:\*\*)\s*it binds the python function to a specific quantum device", # Q4
        r"(?:\[[xXvV]\]|\*\*Answer:\*\*)\s*it performs amplitude amplification", # Q5
        r"(?:\[[xXvV]\]|\*\*Answer:\*\*)\s*the cost function's landscape becomes extremely flat" # Q6
    ]
    
    for i, pattern in enumerate(mcq_answers, 1):
        if re.search(pattern, full_markdown):
            score += 5
            result.feedback.append(f"Q{i}: Correct")
        else:
            result.feedback.append(f"Q{i}: Incorrect/Missing")

    # --- 2. Coding Challenge Scoring ---
    # Strip whitespace to make checking easier
    code_no_spaces = full_code.replace(" ", "")

    # Challenge 1: PennyLane RY rotation and Z expectation
    if "defchallenge_1" in code_no_spaces:
        if "qnode" in code_no_spaces and (".ry(" in code_no_spaces or "qml.ry(" in code_no_spaces) and "expval" in code_no_spaces and "pauliz" in code_no_spaces:
            score += 15
            result.feedback.append("C1: Passed")
        else:
            result.feedback.append("C1: Missing key PennyLane elements")
            
    # Challenge 2: Cirq X gate and measurement
    if "defchallenge_2" in code_no_spaces:
        if "cirq.circuit" in code_no_spaces and ("cirq.x(" in code_no_spaces or ".x(" in code_no_spaces) and "measure(" in code_no_spaces:
            score += 15
            result.feedback.append("C2: Passed")
        else:
            result.feedback.append("C2: Missing Cirq elements")

    # Challenge 3: Qiskit Bell state (H + CX)
    if "defchallenge_3" in code_no_spaces:
        if "quantumcircuit" in code_no_spaces and ".h(" in code_no_spaces and ".cx(" in code_no_spaces:
            score += 15
            result.feedback.append("C3: Passed")
        else:
            result.feedback.append("C3: Missing H or CX gates")

    # Challenge 4: Qiskit PQC (ParameterVector, RX, CX)
    if "defchallenge_4" in code_no_spaces:
        if "parametervector" in code_no_spaces and ".rx(" in code_no_spaces and ".cx(" in code_no_spaces:
            score += 25
            result.feedback.append("C4: Passed")
        else:
            result.feedback.append("C4: Missing ParameterVector, RX, or CX")

    # --- 3. Basic Syntax Check ---
    # We wrap the code check in try/except. Strip magics and shell commands (! and %)
    clean_code = "\n".join([line for line in code_lines if not line.strip().startswith("!") and not line.strip().startswith("%")])
    try:
        compile(clean_code, '<string>', 'exec')
    except SyntaxError as e:
        result.has_syntax_errors = True
        result.feedback.append(f"Syntax Error: {e}")
        score -= 10 # Penalize syntax errors

    result.total_score = max(0, score) # Max possible is 30 (MCQs) + 70 (Coding) = 100
    return result

import pandas as pd
import urllib.request
import re
import tempfile
import time

def download_colab_notebook(url: str, dest_path: str) -> bool:
    """Extracts the file ID from a Colab/Drive URL and downloads the raw .ipynb."""
    # Extract ID from Google Drive or Colab links
    match = re.search(r'/d/([a-zA-Z0-9-_]+)', url)
    if not match:
        match = re.search(r'id=([a-zA-Z0-9-_]+)', url)
    if not match:
        match = re.search(r'/drive/([a-zA-Z0-9-_]+)', url)
        
    if not match:
        logging.warning(f"Could not extract ID from URL: {url}")
        return False
        
    file_id = match.group(1)
    download_url = f"https://drive.google.com/uc?export=download&id={file_id}"
    
    try:
        req = urllib.request.Request(download_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as response:
            with open(dest_path, 'wb') as f:
                f.write(response.read())
        return True
    except Exception as e:
        logging.error(f"Failed to download {url}: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Jupyter Notebook Assessment Evaluator")
    parser.add_argument("input_excel", nargs="?", default="QIntern26 Project 25 Assessment Submission Upload (Responses).xlsx", help="Path to the Excel file containing Colab links")
    parser.add_argument("--url-column", default="Upload your solved assessment file", help="Name of the column containing the Colab/Drive URLs")
    parser.add_argument("--output", default="qintern_assessment_results.xlsx", help="Output Excel filename")
    parser.add_argument("--threshold", type=int, default=50, help="Minimum score to clear the assessment")
    args = parser.parse_args()

    input_path = args.input_excel
    if not os.path.isfile(input_path):
        logging.error(f"Input file not found: {input_path}")
        return

    logging.info(f"Reading Excel file: {input_path}")
    try:
        df = pd.read_excel(input_path)
    except Exception as e:
        logging.error(f"Failed to read Excel file: {e}")
        return

    # Find the URL column
    url_col = None
    for col in df.columns:
        if args.url_column.lower() in str(col).lower() or "colab" in str(col).lower() or "link" in str(col).lower():
            url_col = col
            break
            
    if not url_col:
        logging.error(f"Could not find a column matching '{args.url_column}' or containing 'link'/'colab'.")
        logging.info(f"Available columns: {list(df.columns)}")
        return

    logging.info(f"Using column '{url_col}' for notebook links.")

    # Prepare new columns
    df['Total Score'] = 0
    df['Syntax Errors'] = False
    df['Feedback Summary'] = ""
    df['Cleared Assessment'] = False

    with tempfile.TemporaryDirectory() as temp_dir:
        for idx, row in df.iterrows():
            url = str(row[url_col]).strip()
            if not url or "http" not in url:
                continue
                
            logging.info(f"Processing candidate row {idx+1}: {url}")
            temp_ipynb = os.path.join(temp_dir, f"candidate_{idx}.ipynb")
            
            if download_colab_notebook(url, temp_ipynb):
                result = evaluate_notebook(temp_ipynb)
                
                df.at[idx, 'Total Score'] = result.total_score
                df.at[idx, 'Syntax Errors'] = result.has_syntax_errors
                df.at[idx, 'Feedback Summary'] = " | ".join(result.feedback)
                df.at[idx, 'Cleared Assessment'] = bool(result.total_score >= args.threshold)
                
                # Small delay to avoid Google Drive rate limits
                time.sleep(1)
            else:
                df.at[idx, 'Feedback Summary'] = "Failed to download notebook"

    logging.info(f"Saving results to {args.output}")
    df.to_excel(args.output, index=False)
    logging.info("Done!")

if __name__ == "__main__":
    main()
