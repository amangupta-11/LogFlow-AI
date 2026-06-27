import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import os
import sys
import time
import sqlite3
import socket
import threading
from datetime import datetime

# Setup workspace path
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from backend import db_manager
from backend.notifications import (
    send_email_notification,
    process_notification_queue,
    log_notification_history
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
                        conn.sendall(b"250-localhost Hello\r\n250 STARTTLS\r\n")
                    elif cmd.upper().startswith("MAIL FROM:"):
                        email_data["from"] = cmd[10:].strip()
                        conn.sendall(b"250 OK\r\n")
                    elif cmd.upper().startswith("RCPT TO:"):
                        email_data["to"] = cmd[8:].strip()
                        conn.sendall(b"250 OK\r\n")
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


def run_tests():
    print("Initializing databases...")
    db_manager.init_repo_db()

    # Clear prior test runs for notification_history and notification_queue to start fresh
    conn = db_manager.get_repo_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM notification_history")
    cursor.execute("DELETE FROM notification_queue")
    conn.commit()
    conn.close()

    # Setup Env variables for testing
    os.environ["SMTP_HOST"] = "127.0.0.1"
    os.environ["SMTP_PORT"] = "1025"
    os.environ["SMTP_USER"] = "testuser"
    os.environ["SMTP_PASSWORD"] = "testpass"
    os.environ["EMAIL_TO"] = "admin@logcollector.local"

    # Start Mock SMTP Server
    smtp_server = MockSMTPServer()
    smtp_server.start()

    print("\n--- TEST 1: Sending email over SMTP ---")
    subject1 = "Test Subject 1"
    body1 = "This is a test notification body."
    sent = send_email_notification("new_technology", subject1, body1)
    print(f"Sent successfully (expected True): {sent}")
    print(f"Emails received by server: {len(smtp_server.emails)}")
    if smtp_server.emails:
        print(f"Last Email Details:\n  To: {smtp_server.emails[-1].get('to')}\n  Subject: [Check Header] / Body length: {len(smtp_server.emails[-1].get('body', ''))}")

    # Check notification_history
    conn = db_manager.get_repo_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM notification_history")
    rows = cursor.fetchall()
    print(f"Notification History Rows Count (expected 1): {len(rows)}")
    for r in rows:
        print(f"  ID: {r[0]} | Timestamp: {r[1]} | Type: {r[2]} | Recipient: {r[3]} | Subject: {r[4]} | Status: {r[5]}")
    conn.close()

    print("\n--- TEST 2: Failure Notification Throttling ---")
    subject_fail1 = "[Autonomous Agent] Job Failure: test_job"
    body_fail1 = "Details of failure 1"
    sent_fail1 = send_email_notification("discovery_failure", subject_fail1, body_fail1)
    print(f"First failure alert sent status (expected True): {sent_fail1}")

    subject_fail2 = "[Autonomous Agent] Job Failure: test_job"
    body_fail2 = "Details of failure 2 (within 60m)"
    sent_fail2 = send_email_notification("discovery_failure", subject_fail2, body_fail2)
    print(f"Second failure alert sent status (expected False - throttled): {sent_fail2}")

    # Check notification_history showing throttling
    conn = db_manager.get_repo_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM notification_history WHERE notification_type = 'discovery_failure'")
    rows = cursor.fetchall()
    print(f"Discovery Failure History Rows (expected 2): {len(rows)}")
    for r in rows:
        print(f"  ID: {r[0]} | Type: {r[2]} | Subject: {r[4]} | Status: {r[5]} | Error Msg: {r[6]}")
    conn.close()

    print("\n--- TEST 3: Offline Queue Fallback (SMTP Server Stopped) ---")
    smtp_server.stop()
    
    subject_offline = "Offline Alert Subject"
    body_offline = "Offline notification body content."
    sent_offline = send_email_notification("hourly_summary", subject_offline, body_offline)
    print(f"Offline notification sent status (expected False): {sent_offline}")

    # Check notification_queue
    conn = db_manager.get_repo_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM notification_queue WHERE status = 'pending'")
    rows = cursor.fetchall()
    print(f"Pending Notification Queue Rows (expected 1): {len(rows)}")
    for r in rows:
        print(f"  ID: {r[0]} | Created At: {r[1]} | Type: {r[2]} | Status: {r[3]} | Content: {r[4][:100]}...")
    conn.close()

    print("\n--- TEST 4: Online Queue Recovery ---")
    # Start SMTP back up
    smtp_server = MockSMTPServer()
    smtp_server.start()

    # Trigger recovery
    process_notification_queue()

    # Check if queue status has updated
    conn = db_manager.get_repo_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM notification_queue WHERE status = 'pending'")
    pending_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM notification_queue WHERE status = 'sent'")
    sent_count = cursor.fetchone()[0]
    print(f"Queue Stats - Pending: {pending_count} (expected 0) | Sent: {sent_count} (expected 1)")
    
    # Check history count
    cursor.execute("SELECT COUNT(*) FROM notification_history")
    history_count = cursor.fetchone()[0]
    print(f"Total Notification History Records: {history_count}")
    conn.close()

    print("\n--- TEST 5: Dashboard Stats Integration ---")
    # Emulate the main.py API endpoint call
    from backend import main
    import asyncio
    
    async def fetch_dashboard():
        stats = await main.get_dashboard_stats()
        print("Mocked Dashboard Response:")
        print(f"  emails_sent_today:     {stats.get('emails_sent_today')}")
        print(f"  emails_failed_today:   {stats.get('emails_failed_today')}")
        print(f"  pending_notifications: {stats.get('pending_notifications')}")
        print(f"  last_email_status:     {stats.get('last_email_status')}")
        print(f"  last_email_time:       {stats.get('last_email_time')}")

    asyncio.run(fetch_dashboard())

    smtp_server.stop()
    print("\nAll tests completed!")

if __name__ == '__main__':
    run_tests()
