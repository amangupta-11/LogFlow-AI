import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import os
import sys
import time
import socket
import json
import threading
from datetime import datetime

# Setup workspace path
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from backend import db_manager
from tools.verify_notifications import MockSMTPServer

# Configure test environment
os.environ["SMTP_HOST"] = "127.0.0.1"
os.environ["SMTP_PORT"] = "1025"
os.environ["SMTP_USER"] = "testuser"
os.environ["SMTP_PASSWORD"] = "testpass"
os.environ["EMAIL_TO"] = "admin@logcollector.local"

def prepare_database():
    print("Clearing history and queues for fresh cycle metrics...")
    conn = db_manager.get_repo_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM notification_history")
    cursor.execute("DELETE FROM notification_queue")
    cursor.execute("DELETE FROM agent_job_history")
    cursor.execute("DELETE FROM agent_health_history")
    conn.commit()
    conn.close()

def run_cycle():
    prepare_database()

    # Start mock SMTP server
    smtp_server = MockSMTPServer()
    smtp_server.start()

    print("\n=== STARTING AUTOMATIC CYCLE SIMULATION ===")
    
    # Import jobs
    from backend.autonomous_agent import (
        run_job_technology_discovery,
        run_job_log_discovery,
        run_job_repository_health_check,
        run_job_daily_summary_report
    )

    print("\n--- 1. Running Technology Discovery ---")
    run_job_technology_discovery()

    print("\n--- 2. Running Log Discovery ---")
    run_job_log_discovery()

    print("\n--- 3. Running Repository Health Check ---")
    run_job_repository_health_check()

    print("\n--- 4. Running Daily Summary Report ---")
    run_job_daily_summary_report()

    print("\n=== CYCLE COMPLETE, FETCHING RUNTIME EVIDENCE ===")
    time.sleep(2)  # Wait for SMTP server queues to settle

    print(f"\nTotal Emails Received by SMTP Server: {len(smtp_server.emails)}")
    for i, email in enumerate(smtp_server.emails):
        print(f"  Email {i+1}:")
        print(f"    From: {email.get('from')}")
        print(f"    To:   {email.get('to')}")
        # Print a snippet of the body
        body = email.get('body', '')
        lines = [line for line in body.split('\n') if line.strip() and not line.startswith('Subject:') and not line.startswith('From:') and not line.startswith('To:')]
        snippet = " | ".join(lines[:3])
        print(f"    Body Snippet: {snippet[:150]}")

    print("\n=== sqlite: SELECT id, job_type, status, start_time, end_time FROM agent_job_history ===")
    conn = db_manager.get_repo_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, job_type, status, start_time, end_time FROM agent_job_history ORDER BY id ASC")
    for r in cursor.fetchall():
        print(f"  ID: {r[0]} | Type: {r[1]} | Status: {r[2]} | Start: {r[3]} | End: {r[4]}")

    print("\n=== sqlite: SELECT id, timestamp, notification_type, recipient, subject, status FROM notification_history ===")
    cursor.execute("SELECT id, timestamp, notification_type, recipient, subject, status FROM notification_history ORDER BY id ASC")
    for r in cursor.fetchall():
        print(f"  ID: {r[0]} | Time: {r[1]} | Type: {r[2]} | Recipient: {r[3]} | Subject: {r[4]} | Status: {r[5]}")

    print("\n=== sqlite: SELECT id, created_at, notification_type, status FROM notification_queue ===")
    cursor.execute("SELECT id, created_at, notification_type, status FROM notification_queue ORDER BY id ASC")
    rows = cursor.fetchall()
    if not rows:
        print("  No pending or queued notifications (all sent directly!).")
    for r in rows:
        print(f"  ID: {r[0]} | Created: {r[1]} | Type: {r[2]} | Status: {r[3]}")

    print("\n=== fetch: /api/dashboard/stats ===")
    from backend import main
    import asyncio
    async def get_stats():
        stats = await main.get_dashboard_stats()
        print(json.dumps(stats, indent=2))
    asyncio.run(get_stats())

    smtp_server.stop()

if __name__ == '__main__':
    run_cycle()
