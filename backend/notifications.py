import os
import sys
import smtplib
import datetime
import json
import traceback
from email.mime.text import MIMEText
from backend import db_manager

# Ensure stdout/stderr handle UTF-8 symbols on Windows
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

# Configurable cooldowns in minutes
COOLDOWN_CONFIG = {
    "new_technology": 5,
    "new_logs": 5,
    "low_validation": 15,
    "high_duplicate": 15,
    "domain_downgraded": 15,
    "discovery_failed": 15,
    "health_check_failed": 0,
    "email_failure": 15,
    "agent_crash": 0
}

def get_cooldown_minutes(event_type):
    # Allow environment override like EMAIL_COOLDOWN_new_technology=10
    env_val = os.getenv(f"EMAIL_COOLDOWN_{event_type}")
    if env_val is not None:
        try:
            return int(env_val)
        except ValueError:
            pass
    return COOLDOWN_CONFIG.get(event_type, 15)

def check_cooldown(event_type, technology=None, subject=None):
    cooldown_mins = get_cooldown_minutes(event_type)
    return db_manager.check_notification_cooldown(event_type, technology, subject, cooldown_mins)

def get_repo_stats_summary():
    try:
        stats = db_manager.get_repository_health_data()
        return (
            f"Repository Statistics Summary:\n"
            f"  - Total Validated Logs: {stats.get('total_logs', 0)}\n"
            f"  - Total Duplicates Skipped: {stats.get('duplicates_skipped', 0)}\n"
            f"  - Unique Crawled Sources: {stats.get('unique_sources', 0)}\n"
            f"  - Unique Technologies Tracked: {stats.get('total_technologies', 0)}\n"
            f"  - DB Storage Size: {stats.get('size_mb', 0.0)} MB\n"
            f"  - Last Update Date: {stats.get('last_update', 'N/A')}\n"
        )
    except Exception as e:
        return f"Repository Statistics: Not available ({e})"

def send_event_notification(event_type, severity, subject, content_body, job_id=None, technology=None):
    """
    Sends an event-driven notification. Stores it in notification_history and notification_queue first.
    Applies cooldown checks.
    """
    # 1. Cooldown & Duplicate Check
    is_throttled = check_cooldown(event_type, technology, subject)
    initial_status = "Throttled" if is_throttled else "Pending"
    
    now_str = datetime.datetime.utcnow().isoformat() + "Z"
    recipient = os.getenv("EMAIL_TO", os.getenv("SMTP_TO", "admin@logcollector.local"))
    
    # We will log it in both history and queue before sending
    queue_id = None
    history_id = None
    
    try:
        queue_content = json.dumps({
            "subject": subject,
            "content": content_body,
            "job_id": job_id,
            "technology": technology,
            "severity": severity
        })
        queue_id, history_id = db_manager.save_event_notification(
            event_type, recipient, subject, initial_status, queue_content, is_throttled, severity, job_id, technology, now_str
        )
    except Exception as dberr:
        print(f"Database logging failed before send: {dberr}")
        # Try to proceed with temporary IDs if DB fails (should not happen)
        queue_id = queue_id or 9999
        history_id = history_id or 9999
            
    if is_throttled:
        print(f"Notification '{subject}' throttled by cooldown rule.")
        return False
        
    # 2. Append metadata headers to email body
    repo_stats = ""
    # Include repository statistics for important events
    if event_type in ["new_technology", "new_logs", "domain_downgraded", "health_check_failed", "agent_crash"]:
        repo_stats = get_repo_stats_summary()
        
    meta_header = (
        f"==================================================\n"
        f" EVENT NOTIFICATION METADATA\n"
        f"==================================================\n"
        f"  Event ID:      {queue_id}\n"
        f"  Event Type:    {event_type}\n"
        f"  Severity:      {severity}\n"
        f"  Timestamp:     {now_str}\n"
        f"  Job ID:        {job_id or 'N/A'}\n"
        f"  Technology:    {technology or 'N/A'}\n"
    )
    if repo_stats:
        meta_header += f"--------------------------------------------------\n{repo_stats}"
    meta_header += f"==================================================\n\n"
    
    full_email_body = meta_header + content_body
    
    # 3. SMTP parameters
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port_str = os.getenv("SMTP_PORT")
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    smtp_to = os.getenv("EMAIL_TO")
    
    if not all([smtp_host, smtp_port_str, smtp_user, smtp_password, smtp_to]):
        err_msg = "SMTP credentials missing in environment."
        print(err_msg)
        update_notification_status(queue_id, history_id, "Failed", err_msg)
        return False
        
    try:
        smtp_port = int(smtp_port_str)
    except ValueError:
        smtp_port = 587
        
    msg = MIMEText(full_email_body, "plain")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = smtp_to
    
    server = None
    try:
        print(f"Sending SMTP email: '{subject}' to {smtp_to}...")
        if smtp_port == 465:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=15.0)
        else:
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=15.0)
            server.ehlo()
            if server.has_extn("starttls"):
                server.starttls()
                server.ehlo()
            
        server.login(smtp_user, smtp_password)
        server.sendmail(smtp_user, [smtp_to], msg.as_string())
        
        print(f"SMTP delivery succeeded for: '{subject}'")
        update_notification_status(queue_id, history_id, "Sent", error_msg="", delivered=True)
        return True
    except Exception as smtp_err:
        err_str = str(smtp_err)
        print(f"SMTP delivery failed for: '{subject}'. Error: {err_str}")
        
        # Schedule next retry with 5-minute offset
        next_retry_time = (datetime.datetime.utcnow() + datetime.timedelta(minutes=5)).isoformat() + "Z"
        update_notification_status(queue_id, history_id, "Retrying", err_str, next_retry=next_retry_time)
        return False
    finally:
        if server:
            try:
                server.quit()
            except Exception:
                pass


def update_notification_status(queue_id, history_id, status, error_msg="", delivered=False, next_retry=None):
    try:
        db_manager.update_notification_status(queue_id, history_id, status, error_msg, next_retry)
    except Exception as e:
        print(f"Failed to update database status for notification: {e}")

def process_notification_queue():
    """
    Scans the queue for Pending, Failed, or Retrying notifications that are ready to run,
    retries them, and sends a confirmation email upon success.
    """
    print("Running process_notification_queue task...")
    now_str = datetime.datetime.utcnow().isoformat() + "Z"
    try:
        pending_items = db_manager.get_pending_notifications(now_str)
    except Exception as e:
        print(f"Error checking pending queue: {e}")
            
    if not pending_items:
        print("No pending notifications ready for retry.")
        return
        
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port_str = os.getenv("SMTP_PORT")
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    smtp_to = os.getenv("EMAIL_TO")
    
    if not all([smtp_host, smtp_port_str, smtp_user, smtp_password, smtp_to]):
        print("SMTP settings missing. Cannot process retry queue.")
        return
        
    try:
        smtp_port = int(smtp_port_str)
    except ValueError:
        smtp_port = 587
        
    server = None
    try:
        # Verify SMTP server is online
        if smtp_port == 465:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=10.0)
        else:
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=10.0)
            server.ehlo()
            if server.has_extn("starttls"):
                server.starttls()
                server.ehlo()
        server.login(smtp_user, smtp_password)
    except Exception as e:
        print(f"SMTP remains offline. Aborting retry cycle. Error: {e}")
        return
        
    # We have established SMTP server login. Now retry items
    for q_id, n_type, content_json, retry_count, attempt_number in pending_items:
        try:
            data = json.loads(content_json)
            subject = data.get("subject", f"[Log Collector] {n_type}")
            body = data.get("content", content_json)
            job_id = data.get("job_id")
            technology = data.get("technology")
            severity = data.get("severity", "INFO")
        except Exception:
            subject = f"[Log Collector] {n_type}"
            body = content_json
            job_id = None
            technology = None
            severity = "INFO"
            
        new_retry_count = retry_count + 1
        new_attempt_number = attempt_number + 1
        
        # Build full content with metadata footer/header
        repo_stats = ""
        if n_type in ["new_technology", "new_logs", "domain_downgraded", "health_check_failed", "agent_crash"]:
            repo_stats = get_repo_stats_summary()
            
        meta_header = (
            f"==================================================\n"
            f" EVENT NOTIFICATION METADATA\n"
            f"==================================================\n"
            f"  Event ID:      {q_id}\n"
            f"  Event Type:    {n_type}\n"
            f"  Severity:      {severity}\n"
            f"  Timestamp:     {now_str}\n"
            f"  Job ID:        {job_id or 'N/A'}\n"
            f"  Technology:    {technology or 'N/A'}\n"
        )
        if repo_stats:
            meta_header += f"--------------------------------------------------\n{repo_stats}"
        meta_header += f"==================================================\n\n"
        
        full_body = meta_header + body
        
        msg = MIMEText(full_body, "plain")
        msg["Subject"] = subject
        msg["From"] = smtp_user
        msg["To"] = smtp_to
        
        item_sent = False
        err_msg = ""
        try:
            server.sendmail(smtp_user, [smtp_to], msg.as_string())
            item_sent = True
        except Exception as e:
            err_msg = str(e)
            
        try:
            if item_sent:
                # Mark queue item as Sent
                db_manager.mark_notification_sent_and_history(
                    q_id, new_retry_count, new_attempt_number, now_str, n_type, smtp_to, subject, severity, job_id, technology
                )
                print(f"Successfully delivered queued notification ID {q_id} on retry.")
                
                # Send confirmation email
                # Pending Notification Successfully Delivered
                confirm_subject = "Pending Notification Successfully Delivered"
                confirm_body = (
                    f"A pending notification has been successfully delivered after SMTP recovery.\n\n"
                    f"Original Event: {subject} ({n_type})\n"
                    f"Retry Count: {new_retry_count}\n"
                    f"Queue Duration: Attempted over {new_attempt_number} attempts\n"
                    f"Delivery Time: {now_str}\n"
                )
                
                # Add confirmation email direct send
                confirm_msg = MIMEText(confirm_body, "plain")
                confirm_msg["Subject"] = confirm_subject
                confirm_msg["From"] = smtp_user
                confirm_msg["To"] = smtp_to
                try:
                    server.sendmail(smtp_user, [smtp_to], confirm_msg.as_string())
                    # Log confirmation in history
                    db_manager.insert_notification_history(
                        now_str, 'email_confirmation', smtp_to, confirm_subject, 'Sent', '', 'INFO', job_id, technology
                    )
                except Exception as confirm_err:
                    print(f"Failed to send confirmation email: {confirm_err}")
                    # Save confirmation to queue
                    confirm_content = json.dumps({
                        "subject": confirm_subject,
                        "content": confirm_body,
                        "job_id": job_id,
                        "technology": technology,
                        "severity": "INFO"
                    })
                    db_manager.insert_notification_queue(
                        now_str, 'email_confirmation', 'Pending', confirm_content, now_str
                    )
            else:
                # Mark queue item for next retry with 5-minute backoff
                next_retry_time = (datetime.datetime.utcnow() + datetime.timedelta(minutes=5)).isoformat() + "Z"
                db_manager.mark_notification_failed_and_history(
                    q_id, new_retry_count, new_attempt_number, now_str, next_retry_time, n_type, smtp_to, subject, err_msg, severity, job_id, technology
                )
                print(f"Retry attempt {new_attempt_number} failed for queued notification ID {q_id}: {err_msg}")
        except Exception as dberr:
            print(f"Database error during queue processing item {q_id}: {dberr}")
                
    try:
        server.quit()
    except Exception:
        pass


def send_email_notification(event_type, subject, content_body):
    severity = "INFO"
    if event_type in ["health_check_failed", "agent_crash", "discovery_failed", "discovery_failure"]:
        severity = "ERROR"
    return send_event_notification(event_type, severity, subject, content_body)

