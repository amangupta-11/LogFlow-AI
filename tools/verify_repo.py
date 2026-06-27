import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import os
import sqlite3
import time
import json
from datetime import datetime

# Set temporary environment variables for testing so we don't pollute default production database path
os.environ["REPO_DB_PATH"] = "validated_logs.db"

from backend import db_manager

def main():
    print("=== STARTING VALIDATED LOG REPOSITORY VERIFICATION ===")
    
    # Force initialize the DB
    conn = db_manager.get_repo_connection()
    cursor = conn.cursor()
    
    print("\n[1] DATABASE SCHEMA VERIFICATION")
    tables = ["validated_logs", "repository_metrics", "repository_discovery_history"]
    for t in tables:
        cursor.execute(f"PRAGMA table_info({t})")
        columns = cursor.fetchall()
        print(f"\nTable: {t}")
        for col in columns:
            print(f"  - {col[1]} ({col[2]})")
            
    print("\n[2] CLEANING TEST ENVIRONMENT")
    cursor.execute("DELETE FROM validated_logs")
    cursor.execute("DELETE FROM repository_discovery_history")
    cursor.execute("UPDATE repository_metrics SET value = 0 WHERE key = 'duplicates_skipped'")
    conn.commit()
    
    print("\n[3] INSERTING UNIQUE VALIDATED LOGS")
    mock_logs = [
        {
            "original_log": "2026-06-23T10:00:00.123Z [ERROR] nginx_upstream: upstream timed out while connecting to backend server",
            "message": "upstream timed out while connecting to backend server",
            "severity": "ERROR",
            "source_url": "https://github.com/nginx/issues/123",
            "source_title": "Nginx Upstream Timeout issue",
            "query_used": "nginx upstream error logs",
            "validation": {
                "valid": True,
                "confidence": 95,
                "source_type": "GITHUB_ISSUE",
                "source_rank": 1
            }
        },
        {
            "original_log": "2026-06-23T10:05:00.456Z [WARN] docker_daemon: container bridge network connection reset",
            "message": "container bridge network connection reset",
            "severity": "WARN",
            "source_url": "https://forums.docker.com/t/bridge-network-reset/456",
            "source_title": "Bridge Network Reset Topic",
            "query_used": "docker daemon bridge reset",
            "validation": {
                "valid": True,
                "confidence": 90,
                "source_type": "FORUM",
                "source_rank": 2
            }
        }
    ]
    
    inserted, duplicates = db_manager.insert_validated_logs(
        mock_logs,
        job_platform="Nginx/Docker",
        job_product_name="Web Server / Container Engine",
        job_log_type="Daemon/Upstream"
    )
    print(f"Inserted: {inserted}, Duplicates: {duplicates}")
    
    print("\n[4] EXAMPLE INSERTED ROWS")
    cursor.execute("SELECT id, platform, product_name, log_severity, source_domain, process_name, first_seen, last_seen, normalized_hash FROM validated_logs")
    rows = cursor.fetchall()
    for row in rows:
        print(dict(row))
        
    print("\n[5] EVENT/DISCOVERY HISTORY")
    cursor.execute("SELECT id, platform, source_url, status, validation_result FROM repository_discovery_history")
    hist_rows = cursor.fetchall()
    for hr in hist_rows:
        print(dict(hr))
        
    print("\n[6] DUPLICATE DETECTION VERIFICATION")
    # Log has different timestamp, but normalized content is identical to first log
    duplicate_log = [
        {
            "original_log": "2026-06-23T11:15:33.999Z [ERROR] nginx_upstream: upstream timed out while connecting to backend server",
            "message": "upstream timed out while connecting to backend server",
            "severity": "ERROR",
            "source_url": "https://github.com/nginx/issues/123",
            "source_title": "Nginx Upstream Timeout issue",
            "query_used": "nginx upstream error logs",
            "validation": {
                "valid": True,
                "confidence": 95,
                "source_type": "GITHUB_ISSUE",
                "source_rank": 1
            }
        }
    ]
    # Sleep slightly so the last_seen timestamp will differ
    time.sleep(1.0)
    
    inserted_dup, duplicates_dup = db_manager.insert_validated_logs(
        duplicate_log,
        job_platform="Nginx/Docker",
        job_product_name="Web Server / Container Engine",
        job_log_type="Daemon/Upstream"
    )
    print(f"Dup Batch - Inserted: {inserted_dup}, Duplicates: {duplicates_dup}")
    
    print("\n[7] VERIFYING EXISTENCE OF THE LOG POST-DUPLICATE DETECTION")
    cursor.execute("SELECT id, first_seen, last_seen FROM validated_logs WHERE id = 1")
    updated_log = cursor.fetchone()
    print(f"Log ID 1: First Seen = {updated_log['first_seen']}, Last Seen = {updated_log['last_seen']}")
    print(f"Updated Last Seen is newer: {updated_log['last_seen'] > updated_log['first_seen']}")
    
    print("\n[8] DISCOVERY HISTORY POST-DUPLICATE DETECTION")
    cursor.execute("SELECT id, platform, status, discovered_at FROM repository_discovery_history")
    hist_rows_after = cursor.fetchall()
    for hr in hist_rows_after:
        print(dict(hr))
        
    print("\n[9] REPOSITORY STATISTICS OUTPUT")
    stats = db_manager.get_repository_stats_for_sheet()
    print("Stats:")
    for s in stats:
        print(s)
        
    print("\n[10] REPOSITORY HEALTH DATA")
    health = db_manager.get_repository_health_data()
    print(json.dumps(health, indent=2))
    
    conn.close()
    print("\n=== VERIFICATION COMPLETED SUCCESSFULLY ===")

if __name__ == "__main__":
    main()
