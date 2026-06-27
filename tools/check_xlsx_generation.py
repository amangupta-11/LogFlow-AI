import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import os

path = "backend/outputs/job_516179fb-1a8b-4722-a5eb-138f7fa1ec4a/diagnostic_report.xlsx"
print("Exists:", os.path.exists(path))
if os.path.exists(path):
    print("Size:", os.path.getsize(path))
else:
    print("Does not exist. Files in directory:")
    dir_path = "backend/outputs/job_516179fb-1a8b-4722-a5eb-138f7fa1ec4a"
    if os.path.exists(dir_path):
        print(os.listdir(dir_path))
    else:
        print("Directory does not exist!")
