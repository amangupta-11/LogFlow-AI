import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import os

txt_path = "backend/outputs/job_516179fb-1a8b-4722-a5eb-138f7fa1ec4a/diagnostic_report.txt"
with open(txt_path, "r", encoding="utf-8") as f:
    content = f.read()

# Let's extract the part between ROW 10 and ROW 11
start_idx = content.find("ROW 10: Platform=AWS | Product=Amazon RDS MySQL | Log Type=Error Log")
end_idx = content.find("ROW 11: Platform=AWS | Product=Amazon RDS MySQL | Log Type=General Log")

row_content = content[start_idx:end_idx]
lines = row_content.splitlines()

in_diag = False
diag_lines = []
for line in lines:
    if "Search Diagnostics:" in line:
        in_diag = True
        continue
    if in_diag:
        if "Validation Format Detected:" in line:
            break
        diag_lines.append(line)

print("Search Diagnostics lines count:", len([l for l in diag_lines if l.strip()]))
print("First 10 lines of diagnostics:")
for l in diag_lines[:15]:
    print(l)
