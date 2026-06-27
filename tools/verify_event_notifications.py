import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import os
import sys
import time
import sqlite3
import socket
import threading
import json
from datetime import datetime

# Ensure stdout/stderr handle UTF-8 symbols on Windows
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

# Add workspace path
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from backend import db_manager
from backend.notifications import (
    send_event_notification,
    process_notification_queue
)

class MockSMTPServer:
    def __init__(self, host='127.0.0.1', port=1025):
        self.host = host
        self.port = port
        self.emails = []
        self.sock = None
        self.thread = None
        self.running = False

    def start(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, self.port))
        self.sock.listen(5)
        self.running = True
        self.thread = threading.Thread(target=self._listen, daemon=True)
        self.thread.start()
        print(f"Mock SMTP Server started on {self.host}:{self.port}")

    def _listen(self):
        while self.running:
            try:
                conn, addr = self.sock.accept()
                t = threading.Thread(target=self._handle_client, args=(conn,), daemon=True)
                t.start()
            except Exception:
                break

    def _handle_client(self, conn):
        try:
            conn.sendall(b"220 localhost SMTP ready\r\n")
            data = b""
            email_data = {}
            in_data = False
            message_body = []
            while True:
                line = b""
                while not line.endswith(b"\r\n"):
                    chunk = conn.recv(1)
                    if not chunk:
                        return
                    line += chunk
                
                cmd = line.decode('utf-8', errors='ignore').strip()
                if in_data:
                    if cmd == ".":
                        in_data = False
                        email_data["body"] = "\n".join(message_body)
                        self.emails.append(email_data)
                        conn.sendall(b"250 OK\r\n")
                    else:
                        message_body.append(cmd)
                else:
                    if cmd.upper().startswith("HELO") or cmd.upper().startswith("EHLO"):
                        conn.sendall(b"250-localhost Hello\r\n250 AUTH PLAIN LOGIN\r\n")
                    elif cmd.upper().startswith("MAIL FROM:"):
                        email_data["from"] = cmd[10:].strip()
                        conn.sendall(b"250 OK\r\n")
                    elif cmd.upper().startswith("RCPT TO:"):
                        email_data["to"] = cmd[8:].strip()
                        conn.sendall(b"250 OK\r\n")
                    elif cmd.upper().startswith("AUTH "):
                        conn.sendall(b"235 Authentication successful\r\n")
                    elif cmd.upper() == "DATA":
                        in_data = True
                        conn.sendall(b"354 Start input, end with <CRLF>.<CRLF>\r\n")
                    elif cmd.upper() == "QUIT":
                        conn.sendall(b"221 Bye\r\n")
                        break
                    else:
                        conn.sendall(b"250 OK\r\n")
        except Exception as e:
            pass
        finally:
            conn.close()

    def stop(self):
        self.running = False
        if self.sock:
            try:
                self.sock.close()
            except:
                pass
        print("Mock SMTP Server stopped.")

def print_table_records(table_name):
    conn = db_manager.get_repo_connection()
    cursor = conn.cursor()
    cursor.execute(f"SELECT * FROM {table_name} ORDER BY id ASC")
    rows = cursor.fetchall()
    
    # Get column names
    cursor.execute(f"PRAGMA table_info({table_name})")
    cols = [col[1] for col in cursor.fetchall()]
    
    print(f"\n--- Database Rows in {table_name.upper()} (Count: {len(rows)}) ---")
    for r in rows:
        row_dict = dict(zip(cols, r))
        print(json.dumps(row_dict, indent=2))
    conn.close()

def run_verification():
    print("Initializing repository schema and migrations...")
    db_manager.init_repo_db()
    
    # Clean up tables first
    conn = db_manager.get_repo_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM notification_history")
    cursor.execute("DELETE FROM notification_queue")
    conn.commit()
    conn.close()
    
    # Setup test env SMTP settings
    os.environ["SMTP_HOST"] = "127.0.0.1"
    os.environ["SMTP_PORT"] = "1025"
    os.environ["SMTP_USER"] = "testuser@gmail.com"
    os.environ["SMTP_PASSWORD"] = "testpass"
    os.environ["EMAIL_TO"] = "admin@logcollector.local"

    # Start SMTP server
    smtp_server = MockSMTPServer()
    smtp_server.start()
    
    print("\n==============================================")
    print("TRIGGERING THE 9 EVENT-DRIVEN NOTIFICATIONS")
    print("==============================================")

    # 1. New Technology Discovered
    print("\n1. Triggering: New Technology Discovered (Single)...")
    send_event_notification(
        event_type="new_technology",
        severity="INFO",
        subject="New Technology Discovered: Prometheus",
        content_body="Prometheus was discovered and validated under Category 'Monitoring' and Vendor 'Cloud Native Computing Foundation'.",
        job_id="job-tech-01",
        technology="Prometheus"
    )

    print("\n1b. Triggering: New Technology Discovered (Multiple)...")
    send_event_notification(
        event_type="new_technology",
        severity="INFO",
        subject="3 New Technologies Discovered",
        content_body="Prometheus, Envoy, CoreDNS were discovered and accepted during the cycle.",
        job_id="job-tech-01",
        technology=None
    )

    # 2. New Validated Logs Added
    print("\n2. Triggering: New Validated Logs Added...")
    send_event_notification(
        event_type="new_logs",
        severity="INFO",
        subject="15 New Validated Logs Added",
        content_body="Consolidated Discovery Cycle completed successfully. Processed: Docker, Nginx, Kubernetes.",
        job_id="job-logs-01",
        technology="Docker"
    )

    # 3. Low Validation Rate
    print("\n3. Triggering: Low Validation Rate Warning...")
    send_event_notification(
        event_type="low_validation",
        severity="WARNING",
        subject="Low Validation Rate - Kubernetes",
        content_body="Validation rate fell to 32% (below 40% threshold). Extracted: 50, Validated: 16.",
        job_id="job-logs-01",
        technology="Kubernetes"
    )

    # 4. High Duplicate Rate
    print("\n4. Triggering: High Duplicate Rate Warning...")
    send_event_notification(
        event_type="high_duplicate",
        severity="WARNING",
        subject="High Duplicate Rate Detected",
        content_body="Duplicate rate is 78.5% for Nginx logs. Skipped: 110, Validated: 140.",
        job_id="job-logs-01",
        technology="Nginx"
    )

    # 5. Domain Downgraded
    print("\n5. Triggering: Domain Downgraded warning...")
    send_event_notification(
        event_type="domain_downgraded",
        severity="WARNING",
        subject="Domain Downgraded - geeksforgeeks.org",
        content_body="geeksforgeeks.org downgraded because of poor yield score: 0 validated logs out of 24 crawled urls.",
        job_id="job-logs-01",
        technology=None
    )

    # 6. Log Discovery Failed
    print("\n6. Triggering: Log Discovery Failed error...")
    send_event_notification(
        event_type="discovery_failed",
        severity="ERROR",
        subject="Log Discovery Failed - Oracle Database",
        content_body="Crawl execution failed for Oracle Database due to stack overflow in search query generation.",
        job_id="job-logs-02",
        technology="Oracle Database"
    )

    # 7. Repository Health Check Failed
    print("\n7. Triggering: Repository Health Check Failed error...")
    send_event_notification(
        event_type="health_check_failed",
        severity="CRITICAL",
        subject="Repository Health Check Failed",
        content_body="Critical Component: Database. Error: PRAGMA integrity_check failed (Database corrupt).",
        job_id="job-health-01"
    )

    # 8. SMTP Delivery Failure & Queue Retry Flow
    print("\n8. Triggering: SMTP Delivery Failure flow...")
    print("Temporarily stopping Mock SMTP Server to force failure...")
    smtp_server.stop()
    
    # Try sending when offline - should queue as Pending / Retrying
    send_event_notification(
        event_type="low_validation",
        severity="WARNING",
        subject="Low Validation Rate - AWS Lambda",
        content_body="AWS Lambda logs validation rate is 25%. This will fail delivery and queue.",
        job_id="job-logs-03",
        technology="AWS Lambda"
    )
    
    print("\nPrinting queue records while SMTP is offline (expecting 1 Pending/Retrying status item)...")
    print_table_records("notification_queue")
    
    print("\nRestarting Mock SMTP Server for Queue Recovery...")
    smtp_server = MockSMTPServer()
    smtp_server.start()
    
    print("Triggering queue retry processing...")
    process_notification_queue()

    # 9. Autonomous Agent Crash Recovery Notification
    print("\n9. Triggering: Autonomous Agent Crash recovery...")
    # Emulate crash info write
    crash_file = "agent_crash_info.json"
    crash_data = {
        "exception": "ConnectionRefusedError: [Errno 61] Connection refused",
        "stack_trace": "  File \"autonomous_agent.py\", line 494, in run_job_log_discovery\n    res = collect_logs_from_web(...)",
        "last_running_job": "log_discovery",
        "last_successful_job": "health_check",
        "current_tech": "Kubernetes",
        "repo_count": 842,
        "crash_time": datetime.utcnow().isoformat() + "Z"
    }
    with open(crash_file, "w") as f:
        json.dump(crash_data, f)
        
    # Import autonomous_agent module dynamically to run restart crash check
    from backend import autonomous_agent
    print("Running previous crash check and report...")
    autonomous_agent.check_and_report_previous_crash()

    # Final outputs and evidence gathering
    print("\n==============================================")
    print("VERIFICATION COMPLETED. GATHERING EVIDENCE")
    print("==============================================")
    
    print(f"\nTotal emails received by Mock SMTP Server: {len(smtp_server.emails)}")
    for i, mail in enumerate(smtp_server.emails):
        print(f"\nEmail #{i+1}:")
        print(f"  To:      {mail.get('to')}")
        print(f"  From:    {mail.get('from')}")
        print(f"  Body Snippet:\n{mail.get('body', '')[:350]}\n...")
        
    print_table_records("notification_history")
    print_table_records("notification_queue")

    smtp_server.stop()
    print("\nVerification process completed!")

if __name__ == '__main__':
    run_verification()
