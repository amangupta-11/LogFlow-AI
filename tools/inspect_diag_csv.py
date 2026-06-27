import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import csv

csv_path = "backend/outputs/job_516179fb-1a8b-4722-a5eb-138f7fa1ec4a/diagnostic_report.csv"
with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
    reader = csv.reader(f)
    header = next(reader)
    print("Header:", header)
    for idx, row in enumerate(reader):
        if idx <= 10:
            print(f"Row {idx}: {row}")
