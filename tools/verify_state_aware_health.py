import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import os
import sys
import sqlite3
import time
from datetime import datetime, timedelta

# Add workspace to path to resolve imports
sys.path.append(os.path.abspath(os.path.dirname(__file__)))
from backend import db_manager
from backend.autonomous_agent import run_job_repository_health_check

def setup_test_db():
    conn = db_manager.get_repo_connection()
    cursor = conn.cursor()
    
    # Clean up test entries
    cursor.execute("DELETE FROM agent_job_history WHERE job_type = 'log_discovery'")
    cursor.execute("DELETE FROM agent_health_alert_states WHERE job_type = 'log_discovery'")
    cursor.execute("DELETE FROM notification_queue WHERE notification_type = 'health_check_failed'")
    cursor.execute("DELETE FROM notification_history WHERE notification_type = 'health_check_failed'")
    conn.commit()
    conn.close()

def inject_job(status, offset_seconds=0, errors=""):
    conn = db_manager.get_repo_connection()
    cursor = conn.cursor()
    
    start_time = (datetime.utcnow() - timedelta(seconds=offset_seconds)).isoformat() + "Z"
    end_time = (datetime.utcnow() - timedelta(seconds=offset_seconds - 5)).isoformat() + "Z"
    
    cursor.execute(
        "INSERT INTO agent_job_history (job_type, start_time, end_time, status, records_processed, errors) "
        "VALUES ('log_discovery', ?, ?, ?, 10, ?)",
        (start_time, end_time, status, errors)
    )
    conn.commit()
    conn.close()
    print(f"Injected completed 'log_discovery' job with status='{status}' at {start_time}")

def get_latest_notifications():
    conn = db_manager.get_repo_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, content FROM notification_queue "
        "WHERE notification_type = 'health_check_failed' ORDER BY id DESC"
    )
    rows = []
    for r in cursor.fetchall():
        import json
        try:
            c_dict = json.loads(r["content"])
            rows.append({
                "id": r["id"],
                "subject": c_dict.get("subject", ""),
                "content": c_dict.get("content", ""),
                "severity": c_dict.get("severity", "")
            })
        except Exception:
            pass
    conn.close()
    return rows

def get_alert_state():
    conn = db_manager.get_repo_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT last_state, downtime_start FROM agent_health_alert_states WHERE job_type = 'log_discovery'")
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def run_test():
    print("=== STARTING STATE-AWARE HEALTH CHECK VERIFICATION ===")
    setup_test_db()
    
    # Test 1: Start Healthy
    print("\n--- Test 1: Initially Healthy ---")
    inject_job("success", offset_seconds=100)
    run_job_repository_health_check()
    
    notifs = get_latest_notifications()
    state = get_alert_state()
    print(f"Current Alert State: {state}")
    print(f"Notifications generated: {len(notifs)}")
    assert len(notifs) == 0, "Healthy job should not send alerts."
    assert state["last_state"] == "healthy", "Expected alert state to be healthy."
    
    # Test 2: Transition Healthy -> Failed (ERROR email)
    print("\n--- Test 2: Transition Healthy -> Failed (Should send ERROR email) ---")
    inject_job("failed", offset_seconds=80, errors="Database connection lost")
    run_job_repository_health_check()
    
    notifs = get_latest_notifications()
    state = get_alert_state()
    print(f"Current Alert State: {state}")
    print(f"Notifications generated: {len(notifs)}")
    assert len(notifs) == 1, "Expected exactly 1 notification."
    assert "Orange" in notifs[0]["subject"] or "Error" in notifs[0]["subject"], f"Expected Error subject, got: {notifs[0]['subject']}"
    assert state["last_state"] == "error", "Expected alert state to be error."
    assert state["downtime_start"] is not None, "Downtime start should be set."
    
    # Test 3: Transition Failed -> Failed (No duplicate email)
    print("\n--- Test 3: Stable Failed -> Failed state (Should not send duplicate email) ---")
    inject_job("failed", offset_seconds=60, errors="Database timeout")
    run_job_repository_health_check()
    
    notifs = get_latest_notifications()
    state = get_alert_state()
    print(f"Current Alert State: {state}")
    print(f"Notifications generated: {len(notifs)}")
    assert len(notifs) == 1, "Expected no new notification (still 1 total)."
    assert state["last_state"] == "error", "Expected alert state to remain error."
    
    # Test 4: Consecutives Failure 3 (Transition Error -> Critical)
    print("\n--- Test 4: 3 Consecutive Failures (Should send CRITICAL email) ---")
    inject_job("failed", offset_seconds=40, errors="Disk space full")
    run_job_repository_health_check()
    
    notifs = get_latest_notifications()
    state = get_alert_state()
    print(f"Current Alert State: {state}")
    print(f"Notifications generated: {len(notifs)}")
    assert len(notifs) == 2, "Expected a new CRITICAL notification (2 total)."
    assert "Critical" in notifs[0]["subject"] or "Red" in notifs[0]["subject"], f"Expected Critical subject, got: {notifs[0]['subject']}"
    assert state["last_state"] == "critical", "Expected alert state to transition to critical."
    
    # Test 5: Stable Critical -> Critical (No duplicate email)
    print("\n--- Test 5: Stable Critical state (Should not send duplicate email) ---")
    inject_job("failed", offset_seconds=20, errors="Disk space full again")
    run_job_repository_health_check()
    
    notifs = get_latest_notifications()
    state = get_alert_state()
    print(f"Current Alert State: {state}")
    print(f"Notifications generated: {len(notifs)}")
    assert len(notifs) == 2, "Expected no new notification (still 2 total)."
    assert state["last_state"] == "critical", "Expected alert state to remain critical."
    
    # Test 6: Recovery Transition Critical -> Healthy (Should send RECOVERY notice)
    print("\n--- Test 6: Recovery Transition (Should send RECOVERY notice with downtime duration) ---")
    inject_job("success", offset_seconds=0)
    run_job_repository_health_check()
    
    notifs = get_latest_notifications()
    state = get_alert_state()
    print(f"Current Alert State: {state}")
    print(f"Notifications generated: {len(notifs)}")
    assert len(notifs) == 3, "Expected a new RECOVERY notification (3 total)."
    latest_notif = notifs[0]
    print(f"Recovery Subject: {latest_notif['subject']}")
    print(f"Recovery Body:\n{latest_notif['content']}")
    
    assert "Recovery Notice" in latest_notif["subject"] or "Recovered" in latest_notif["subject"], f"Expected Recovery subject, got: {latest_notif['subject']}"
    assert "Downtime Duration" in latest_notif["content"], "Downtime Duration should be present in content."
    assert "Current Status = Healthy" in latest_notif["content"], "Current Status should be listed as Healthy."
    assert state["last_state"] == "healthy", "Expected alert state to transition back to healthy."
    assert state["downtime_start"] is None, "Downtime start should be reset to None."

    # Test 7: Transition Healthy -> Warning (Should send WARNING email)
    print("\n--- Test 7: Transition Healthy -> Warning (Should send WARNING email) ---")
    inject_job("warning", offset_seconds=0, errors="Graceful abort: job runtime limit reached")
    run_job_repository_health_check()
    
    notifs = get_latest_notifications()
    state = get_alert_state()
    print(f"Current Alert State: {state}")
    print(f"Notifications generated: {len(notifs)}")
    assert len(notifs) == 4, "Expected a new WARNING notification (4 total)."
    assert "Warning" in notifs[0]["subject"] or "Yellow" in notifs[0]["subject"], f"Expected Warning subject, got: {notifs[0]['subject']}"
    assert state["last_state"] == "warning", "Expected alert state to transition to warning."
    
    print("\n=== ALL STATE-AWARE HEALTH CHECK TESTS PASSED SUCCESSFULLY! ===")

if __name__ == "__main__":
    run_test()
