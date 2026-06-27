import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import csv

csv_path = "backend/outputs/job_516179fb-1a8b-4722-a5eb-138f7fa1ec4a/diagnostic_report.csv"
with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
    reader = csv.reader(f)
    header = next(reader)
    total_urls = 0
    
    for idx, row in enumerate(reader):
        search_count = int(row[4])
        total_urls += search_count
        print(f"Row {idx}: Platform={row[0]}, Product={row[1]}, LogType={row[2]}, SearchResultsCount={search_count}, RunningTotal={total_urls}")
        if idx == 9: # We want to know the size after Row 9's extend()
            break
