import pandas as pd
import re

file_path = r'C:\Users\kittu\projects\github_profile_analyzer\qintern_results.xlsx'
df = pd.read_excel(file_path)

def map_bucket(reason):
    if not isinstance(reason, str):
        return 'Unknown'
    
    buckets = []
    if 'Quantum' in reason or 'QC' in reason or 'quantum' in reason.lower():
        buckets.append('QC')
    if 'ML/AI' in reason or 'ML' in reason or 'ml' in reason.lower():
        buckets.append('ML/DL/DS/AI')
    if 'SE' in reason or 'se' in reason.lower().split():
        buckets.append('SE')
        
    if buckets:
        return '/'.join(buckets)
    return reason

df['Assigned Bucket'] = df['Reason'].apply(map_bucket)
df.to_excel(file_path, index=False)
print("Updated excel file successfully!")
