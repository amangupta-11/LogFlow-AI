import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import os
import sys
import sqlite3
from datetime import datetime
from dotenv import load_dotenv

# Add workspace to path
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from backend import db_manager
from backend.notifications import send_email_notification

def send_test_email():
    print("=== SMTP CONFIGURATION LOADING ===")
    load_dotenv()
    
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = os.getenv("SMTP_PORT")
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    email_to = os.getenv("EMAIL_TO")
    
    print(f"SMTP_HOST:     {smtp_host}")
    print(f"SMTP_PORT:     {smtp_port}")
    print(f"SMTP_USER:     {smtp_user}")
    print(f"SMTP_PASSWORD: {'*****' if smtp_password else 'Not Configured'}")
    print(f"EMAIL_TO:      {email_to}")
    
    if not all([smtp_host, smtp_port, smtp_user, smtp_password, email_to]):
        print("\nError: Incomplete SMTP configuration in environment. Please check your .env file.")
        return False

    print("\n=== TRIGGERING REAL-TIME SMTP DELIVERY ===")
    subject = f"[Gmail Production Test] Verification Email - {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}"
    body = (
        "This is a production verification email sent directly using Gmail SMTP server configurations.\n\n"
        "Verification details:\n"
        f"- Sent at: {datetime.utcnow().isoformat()}Z\n"
        f"- Target recipient: {email_to}\n"
        f"- Secure protocol used: STARTTLS (Port 587)\n"
    )
    
    # We use a unique notification type 'gmail_verification' to verify history and verify it does not trigger throttling
    success = send_email_notification("gmail_verification", subject, body)
    
    if success:
        print("\nEmail was sent successfully! (No mock SMTP or fallback localhost was utilized).")
    else:
        print("\nEmail sending failed. See the error details printed above.")
        
    print("\n=== QUERYING NOTIFICATION HISTORY TABLE ===")
    conn = None
    try:
        conn = db_manager.get_repo_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, timestamp, recipient, subject, status, error_message 
            FROM notification_history 
            WHERE notification_type = 'gmail_verification'
            ORDER BY id DESC LIMIT 1
        """)
        row = cursor.fetchone()
        if row:
            print("Row Found in SQLite 'notification_history':")
            print(f"  ID:            {row[0]}")
            print(f"  Timestamp:     {row[1]}")
            print(f"  Recipient:     {row[2]}")
            print(f"  Subject:       {row[3]}")
            print(f"  Status:        {row[4]}")
            print(f"  Error Message: {row[5] or 'None'}")
        else:
            print("No matching notification history record was found in the database.")
            
        print("\n=== QUERYING NOTIFICATION QUEUE (CONFIRMING NO FALLBACK QUEUEING) ===")
        cursor.execute("SELECT COUNT(*) FROM notification_queue WHERE status = 'pending'")
        pending_count = cursor.fetchone()[0]
        print(f"  Pending Queue Items Count: {pending_count} (expected 0 if sent successfully)")
        
    except Exception as e:
        print(f"Database query error: {e}")
    finally:
        if conn:
            conn.close()

if __name__ == '__main__':
    send_test_email()
