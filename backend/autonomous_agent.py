import os
import sys
import time
import logging
import traceback
from datetime import datetime, timedelta

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("autonomous_agent.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("AutonomousAgent")

scheduler = None

# Add workspace to path to resolve imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from backend import db_manager
from backend.crawler import collect_logs_from_web
from backend.discovery_agent import run_seed_discovery, run_dynamic_discovery, run_catalog_audit

# Crash Handling Helpers
import json

def handle_agent_crash(exception, tb_str):
    crash_file = "agent_crash_info.json"
    last_running_job = "None"
    current_tech = "None"
    repo_count = 0
    last_successful_job = "None"
    
    try:
        last_running_job, current_tech, last_successful_job, repo_count = db_manager.get_crash_diagnostic_stats()
    except Exception as e:
        logger.error(f"Error querying agent stats on crash: {e}")
        
    crash_data = {
        "exception": str(exception),
        "stack_trace": tb_str,
        "last_running_job": last_running_job,
        "last_successful_job": last_successful_job,
        "current_tech": current_tech,
        "repo_count": repo_count,
        "crash_time": datetime.utcnow().isoformat() + "Z"
    }
    
    try:
        with open(crash_file, "w") as f:
            json.dump(crash_data, f)
        logger.info(f"Crash log saved to {crash_file}")
    except Exception as e:
        logger.error(f"Failed to write crash log: {e}")
        
    try:
        from backend.notifications import send_event_notification
        subject = "Autonomous Agent Stopped Unexpectedly"
        body = (
            f"The Autonomous Agent has crashed due to an unhandled exception.\n\n"
            f"Exception: {exception}\n"
            f"Stack Trace:\n{tb_str}\n\n"
            f"Last Running Job: {last_running_job}\n"
            f"Last Successful Job: {last_successful_job}\n"
            f"Current Technology: {current_tech}\n"
            f"Repository Count: {repo_count}\n"
            f"Recovery Status: Stopped / Recovery Pending\n"
            f"Crash Time: {crash_data['crash_time']}"
        )
        send_event_notification(
            event_type="agent_crash",
            severity="CRITICAL",
            subject=subject,
            content_body=body
        )
    except Exception as n_err:
        logger.error(f"Failed to send crash email immediately: {n_err}")

def setup_global_exception_hook():
    def global_exception_handler(exctype, value, tb):
        tb_str = "".join(traceback.format_exception(exctype, value, tb))
        logger.critical(f"Unhandled exception: {value}\n{tb_str}")
        handle_agent_crash(value, tb_str)
        sys.__excepthook__(exctype, value, tb)
    sys.excepthook = global_exception_handler

# Call hook setup at startup
setup_global_exception_hook()

def check_and_report_previous_crash():
    crash_file = "agent_crash_info.json"
    if os.path.exists(crash_file):
        try:
            with open(crash_file, "r") as f:
                crash_data = json.load(f)
            
            from backend.notifications import send_event_notification
            subject = "Autonomous Agent Stopped Unexpectedly"
            body = (
                f"The Autonomous Agent has restarted after a crash or unexpected shutdown.\n\n"
                f"Exception: {crash_data.get('exception', 'Unknown')}\n"
                f"Stack Trace:\n{crash_data.get('stack_trace', 'No stack trace')}\n\n"
                f"Last Running Job: {crash_data.get('last_running_job', 'None')}\n"
                f"Last Successful Job: {crash_data.get('last_successful_job', 'None')}\n"
                f"Current Technology: {crash_data.get('current_tech', 'None')}\n"
                f"Repository Count: {crash_data.get('repo_count', 0)}\n"
                f"Recovery Status: Restarted & Active\n"
                f"Restart Time: {datetime.utcnow().isoformat() + 'Z'}"
            )
            send_event_notification(
                event_type="agent_crash",
                severity="CRITICAL",
                subject=subject,
                content_body=body
            )
            os.remove(crash_file)
            logger.info("Previous agent crash reported successfully and log cleared.")
        except Exception as ex:
            logger.error(f"Error processing crash file: {ex}")

# Discovery Limits Configuration
MAX_TECHNOLOGIES_PER_CYCLE = int(os.getenv("MAX_TECHNOLOGIES_PER_CYCLE", 5))
MAX_QUERIES_PER_TECHNOLOGY = int(os.getenv("MAX_QUERIES_PER_TECHNOLOGY", 5))
MAX_URLS_PER_TECHNOLOGY = int(os.getenv("MAX_URLS_PER_TECHNOLOGY", 5))
MAX_RUNTIME_PER_JOB = int(os.getenv("MAX_RUNTIME_PER_JOB", 900))  # seconds

# Database helper functions
# Database helper functions
def recover_stale_locks():
    """
    Finds jobs with status='running' that started more than 10 minutes ago
    and marks them as failed.
    """
    logger.info("Checking for stale job locks older than 10 minutes...")
    try:
        recovered_jobs = db_manager.recover_stale_job_locks()
        for job_id, job_type in recovered_jobs:
            logger.warning(f"Stale lock detected: Job ID {job_id} ({job_type}) has been running for >10 minutes. Recovering...")
            try:
                db_manager.log_agent_event("lock_recovery", f"Auto-recovered stale lock for job ID {job_id} ({job_type})")
            except Exception as e:
                logger.error(f"Error logging lock recovery event: {e}")
        if recovered_jobs:
            logger.info(f"Stale lock recovery complete. Recovered {len(recovered_jobs)} job(s).")
        else:
            logger.info("No stale locks found.")
    except Exception as e:
        logger.error(f"Error during stale lock recovery: {e}")

def acquire_job_lock(job_type):
    """
    Checks if a job of the same type is already running.
    If not, inserts a 'running' entry in agent_job_history and returns its ID.
    Otherwise, returns None.
    """
    return db_manager.acquire_job_lock(job_type)

def release_job_lock(job_id, status, records_processed=0, errors=""):
    """
    Updates the job history record status and end_time, releasing the lock.
    """
    try:
        db_manager.release_job_lock(job_id, status, records_processed, errors)
    except Exception as e:
        logger.error(f"Error releasing job lock: {e}")

def log_health_status(component, status, details):
    """
    Inserts a record into the agent_health_history table.
    """
    try:
        db_manager.log_health_status(component, status, details)
    except Exception as e:
        logger.error(f"Error logging health status: {e}")

def is_agent_paused():
    try:
        return db_manager.is_agent_paused()
    except Exception as e:
        logger.error(f"Error checking if agent is paused: {e}")
        return False

def update_agent_status_db(status=None, current_job=None, current_tech=None):
    try:
        db_manager.update_agent_status_db(status, current_job, current_tech)
    except Exception as e:
        logger.error(f"Error updating agent status in DB: {e}")

def update_next_run_times_in_db():
    try:
        next_run = (datetime.utcnow() + timedelta(days=1)).isoformat() + "Z"
        db_manager.update_scheduler_next_run_times(next_run, next_run, next_run, next_run)
    except Exception as e:
        logger.error(f"Error updating next run times in DB: {e}")

def set_agent_idle():
    db_manager.set_agent_idle()
    db_manager.update_agent_status_field(current_phase="Idle")

def log_runtime_metrics(tech_processed, urls_crawled, logs_extracted, logs_validated, logs_inserted, failures,
                        urls_skipped=0, pages_log_rich=0, pages_low_value=0, insert_yield_pct=0.0):
    """
    Inserts a record into the agent_runtime_metrics table.
    """
    try:
        db_manager.log_runtime_metrics(tech_processed, urls_crawled, logs_extracted, logs_validated, logs_inserted, failures,
                                       urls_skipped, pages_log_rich, pages_low_value, insert_yield_pct)
    except Exception as e:
        logger.error(f"Error logging runtime metrics: {e}")

def enqueue_notification(notification_type, content):
    """
    Enqueues a notification into notification_queue and returns queue ID.
    """
    try:
        return db_manager.enqueue_notification(notification_type, content)
    except Exception as e:
        logger.error(f"Error enqueuing notification: {e}")
        return None

def mark_notification_sent(queue_id):
    """
    Marks the enqueued notification as processed/sent.
    """
    try:
        db_manager.mark_notification_sent(queue_id)
    except Exception as e:
        logger.error(f"Error marking notification sent: {e}")

# APScheduler Job Handlers
def run_job_technology_discovery():
    if is_agent_paused():
        logger.info("Technology Discovery Job skipped: Agent is currently PAUSED.")
        db_manager.log_agent_event("job_skipped", "Technology Discovery Job skipped: Agent is paused")
        return
        
    logger.info("Executing scheduled Technology Discovery Job...")
    job_id = acquire_job_lock("technology_discovery")
    if not job_id:
        logger.warning("Technology Discovery Job skipped: lock active (already running)")
        return
        
    start_time = time.time()
    errors = ""
    status = "success"
    techs_found = 0
    
    try:
        from backend.notifications import process_notification_queue
        process_notification_queue()
    except Exception as e:
        logger.error(f"Error processing notification queue in technology discovery: {e}")

    try:
        db_manager.log_agent_event("job_start", "Starting Technology Discovery Job")
        db_manager.update_agent_status_field(
            status="running",
            current_job="technology_discovery",
            current_phase="Searching",
            cycle_start_time=datetime.utcnow().isoformat() + "Z",
            technologies_total=0,
            technologies_processed=0,
            current_query=0,
            total_queries=0,
            current_url=0,
            total_urls=0,
            cycle_urls_crawled=0,
            cycle_pages_classified=0,
            cycle_logs_extracted=0,
            cycle_logs_validated=0,
            cycle_logs_inserted=0,
            cycle_duplicates_skipped=0
        )
        
        start_utc = datetime.utcfromtimestamp(start_time).isoformat() + "Z"
        
        # Run baseline seeds
        run_seed_discovery()
        
        # Proactively audit and crawl
        run_dynamic_discovery()
        
        new_tech_rows = db_manager.get_new_accepted_technologies(start_utc)
        final_count = db_manager.get_accepted_catalog_count()
        
        techs_found = len(new_tech_rows)
        logger.info(f"Technology Discovery Job complete. Discovered {techs_found} accepted technologies.")
        log_health_status("technology_discovery", "healthy", f"Successfully completed discovery cycle. Found {techs_found} technologies.")
        db_manager.log_agent_event("job_end", f"Technology Discovery Job completed. Found {techs_found} new technologies.")
        
        if techs_found > 0:
            from backend.notifications import send_event_notification
            tech_names = [t[0] for t in new_tech_rows]
            if len(tech_names) == 1:
                subject = f"New Technology Discovered: {tech_names[0]}"
            else:
                subject = f"{len(tech_names)} New Technologies Discovered"
                
            content = f"The following new technologies have been discovered and cataloged during the current discovery cycle:\n\n"
            for t_name, t_cat, t_vend in new_tech_rows:
                disc_source = "Unknown"
                log_queries_val = "N/A"
                try:
                    db_details = db_manager.get_technology_discovery_details(t_name)
                    if db_details:
                        disc_source = db_details[0] or "Unknown"
                        log_queries_val = db_details[1] or "N/A"
                except Exception:
                    pass
                
                content += f"--------------------------------------------------\n"
                content += f"Technology Name:            {t_name}\n"
                content += f"Category:                   {t_cat}\n"
                content += f"Vendor:                     {t_vend}\n"
                content += f"Discovery Source:           {disc_source}\n"
                content += f"Generated Search Queries:   {log_queries_val}\n"
            content += f"--------------------------------------------------\n\n"
            content += f"Discovery Time: {datetime.utcnow().isoformat() + 'Z'}\n"
            content += f"Job ID:         {job_id}\n"
            
            send_event_notification(
                event_type="new_technology",
                severity="INFO",
                subject=subject,
                content_body=content,
                job_id=job_id
            )
            
    except Exception as e:
        status = "failed"
        errors = f"Error: {e}\n{traceback.format_exc()}"
        logger.error(f"Technology Discovery Job failed: {errors}")
        log_health_status("technology_discovery", "unhealthy", f"Discovery cycle failed: {e}")
        db_manager.log_agent_event("job_failed", f"Job failed: technology_discovery | Error: {e}")
        
        try:
            from backend.notifications import send_event_notification
            subject = f"Log Discovery Failed - technology_discovery"
            content = f"The scheduled job 'technology_discovery' has failed.\n\nError Details:\n{errors}\n\nTimestamp: {datetime.utcnow().isoformat() + 'Z'}"
            send_event_notification(
                event_type="discovery_failed",
                severity="ERROR",
                subject=subject,
                content_body=content,
                job_id=job_id
            )
        except Exception as mail_err:
            logger.error(f"Failed to send failure email: {mail_err}")
    finally:
        set_agent_idle()
        release_job_lock(job_id, status, records_processed=techs_found, errors=errors)
        db_manager.log_agent_event("job_completed", "Job completed: technology_discovery")
        update_next_run_times_in_db()

def run_job_log_discovery():
    if is_agent_paused():
        logger.info("Log Discovery Job skipped: Agent is currently PAUSED.")
        db_manager.log_agent_event("job_skipped", "Log Discovery Job skipped: Agent is paused")
        return
        
    logger.info("Executing scheduled Log Discovery Job...")
    job_id = acquire_job_lock("log_discovery")
    if not job_id:
        logger.warning("Log Discovery Job skipped: lock active (already running)")
        return
        
    try:
        from backend.notifications import process_notification_queue
        process_notification_queue()
    except Exception as e:
        logger.error(f"Error processing notification queue in log discovery: {e}")
        
    start_time = time.time()
    errors = ""
    status = "success"
    
    # Initialize metric accumulators
    techs_processed = 0
    urls_crawled = 0
    urls_skipped = 0
    pages_log_rich = 0
    pages_low_value = 0
    logs_extracted = 0
    logs_validated = 0
    logs_inserted = 0
    failures = 0
    cycle_duplicates_skipped = 0
    
    # Get repository log count before job starts
    try:
        repo_count_before = db_manager.get_validated_logs_count()
    except Exception:
        repo_count_before = 0

    try:
        db_manager.log_agent_event("job_start", "Starting Log Discovery Job")
        
        # Only discover logs for technologies with zero repository logs (gap recovery)
        tech_rows = db_manager.get_gap_technologies()
        tech_rows_subset = tech_rows[:MAX_TECHNOLOGIES_PER_CYCLE]
        
        if not tech_rows_subset:
            logger.info("No gap technologies found - all accepted technologies have logs in the repository.")
            db_manager.log_agent_event("info", "No gap technologies found - all accepted technologies have logs.")
        else:
            logger.info(f"Found {len(tech_rows_subset)} gap technologies to process for log discovery.")
            db_manager.log_agent_event("info", f"Found {len(tech_rows_subset)} gap technologies to process.")
        
        # Calculate total queries for progress stats
        from backend.discovery_agent import generate_log_queries
        total_queries_count = 0
        for t in tech_rows_subset:
            try:
                q_list = generate_log_queries(t["technology_name"], t["category"])[:MAX_QUERIES_PER_TECHNOLOGY]
                total_queries_count += len(q_list)
            except Exception:
                pass
                
        db_manager.update_agent_status_field(
            status="running",
            current_job="log_discovery",
            current_phase="Searching",
            cycle_start_time=datetime.utcnow().isoformat() + "Z",
            technologies_total=len(tech_rows_subset),
            technologies_processed=0,
            current_query=0,
            total_queries=total_queries_count,
            current_url=0,
            total_urls=0,
            cycle_urls_crawled=0,
            cycle_pages_classified=0,
            cycle_logs_extracted=0,
            cycle_logs_validated=0,
            cycle_logs_inserted=0,
            cycle_duplicates_skipped=0
        )
        
        query_counter = 0
        
        for tech in tech_rows_subset:
            # Operational limit check: Abort gracefully if time limit exceeded
            elapsed = time.time() - start_time
            if elapsed > MAX_RUNTIME_PER_JOB:
                logger.warning(f"Log Discovery Job runtime limit reached ({MAX_RUNTIME_PER_JOB}s). Gracefully aborting cycle...")
                errors = f"Graceful abort: job exceeded MAX_RUNTIME_PER_JOB limit of {MAX_RUNTIME_PER_JOB} seconds."
                status = "warning"
                log_health_status("log_discovery", "warning", errors)
                db_manager.log_agent_event("warning", "Log Discovery Job gracefully aborted: runtime limit reached")
                break
                
            tech_name = tech["technology_name"]
            logger.info(f"  Processing logs for: {tech_name}")
            db_manager.log_agent_event("info", f"Processing logs for tech: {tech_name}")
            update_agent_status_db(current_tech=tech_name)
            db_manager.update_agent_status_field(technologies_processed=techs_processed)
            
            urls_crawled_tech = 0
            logs_extracted_tech = 0
            logs_validated_tech = 0
            logs_inserted_tech = 0
            duplicates_skipped_tech = 0
            
            tech_failed = False
            tech_exception = None
            tech_traceback = ""
            
            try:
                queries = generate_log_queries(tech_name, tech["category"])[:MAX_QUERIES_PER_TECHNOLOGY]
                
                for q in queries:
                    try:
                        query_counter += 1
                        db_manager.update_agent_status_field(
                            current_query=query_counter,
                            current_phase="Searching"
                        )
                        db_manager.log_agent_event("query_executed", f"Running query '{q}' for {tech_name}")
                        # Run search & crawl for the technology
                        res = collect_logs_from_web(tech_name, count=5, max_urls=MAX_URLS_PER_TECHNOLOGY)
                        
                        # Accumulate metrics from this URL run
                        urls_crawled_tech_query = 0
                        pages_log_rich_tech_query = 0
                        pages_low_value_tech_query = 0
                        
                        for url, info in res.url_info_map.items():
                            crawled_status = info.get("Crawled")
                            classified_as = info.get("Classified As")
                            
                            if crawled_status == "Yes":
                                urls_crawled += 1
                                urls_crawled_tech += 1
                                urls_crawled_tech_query += 1
                                if classified_as == "log-rich":
                                    pages_log_rich += 1
                                    pages_log_rich_tech_query += 1
                                elif classified_as == "low-value":
                                    pages_low_value += 1
                                    pages_low_value_tech_query += 1
                            else:
                                urls_skipped += 1
                        
                        extracted_len = len(res.extracted_logs)
                        logs_extracted += extracted_len
                        logs_extracted_tech += extracted_len
                        
                        # Log mapping & repository insert
                        valid_logs = [l for l in res.extracted_logs if l.get("validation", {}).get("valid", False)]
                        logs_validated += len(valid_logs)
                        logs_validated_tech += len(valid_logs)
                        
                        inserted = 0
                        dups = 0
                        if valid_logs:
                            db_manager.log_agent_event("logs_validated", f"Extracted & validated {len(valid_logs)} logs for {tech_name}")
                            inserted, dups = db_manager.insert_validated_logs(
                                valid_logs,
                                job_platform=tech_name,
                                job_product_name=tech_name,
                                job_log_type="diagnostic"
                            )
                            logs_inserted += inserted
                            logs_inserted_tech += inserted
                            cycle_duplicates_skipped += dups
                            duplicates_skipped_tech += dups
                            if inserted > 0:
                                db_manager.log_agent_event("logs_inserted", f"Inserted {inserted} logs for {tech_name} (skipped {dups} duplicates)")
                        else:
                            db_manager.log_agent_event("logs_extracted", f"Extracted 0 valid logs for query '{q}'")
                            
                        # Update SQLite at query boundary
                        db_manager.update_agent_status_field(
                            cycle_urls_crawled=urls_crawled,
                            cycle_pages_classified=pages_log_rich + pages_low_value,
                            cycle_logs_extracted=logs_extracted,
                            cycle_logs_validated=logs_validated,
                            cycle_logs_inserted=logs_inserted,
                            cycle_duplicates_skipped=cycle_duplicates_skipped,
                            current_phase="Idle"
                        )
                    except Exception as ex:
                        failures += 1
                        logger.error(f"  Error processing query '{q}' for {tech_name}: {ex}")
                        log_health_status("crawler", "warning", f"Search/Crawl query fail '{q}': {ex}")
                        db_manager.log_agent_event("error", f"Search/Crawl failed for '{q}': {ex}")
                        tech_failed = True
                        tech_exception = ex
                        tech_traceback = traceback.format_exc()
            except Exception as outer_ex:
                tech_failed = True
                tech_exception = outer_ex
                tech_traceback = traceback.format_exc()
                
            # Trigger Log Discovery Failed if tech discovery errored out
            if tech_failed:
                try:
                    from backend.notifications import send_event_notification
                    subject = f"Log Discovery Failed - {tech_name}"
                    body = (
                        f"Log discovery failed for technology: {tech_name}\n\n"
                        f"Technology:    {tech_name}\n"
                        f"Failure Stage: Crawl / Ingestion Cycle\n"
                        f"Exception:     {tech_exception}\n"
                        f"Stack Trace:\n{tech_traceback}\n\n"
                        f"Retry Status:  Will retry in next scheduled cycle (1 hour)\n"
                        f"Job ID:        {job_id}"
                    )
                    send_event_notification(
                        event_type="discovery_failed",
                        severity="ERROR",
                        subject=subject,
                        content_body=body,
                        job_id=job_id,
                        technology=tech_name
                    )
                except Exception as n_err:
                    logger.error(f"Failed to send discovery failure email for {tech_name}: {n_err}")
                    
            # Low Validation Rate Warning Trigger
            if logs_extracted_tech > 0:
                val_rate = (logs_validated_tech / logs_extracted_tech) * 100.0
                if val_rate < 40.0:
                    try:
                        from backend.notifications import send_event_notification
                        subject = f"Low Validation Rate - {tech_name}"
                        body = (
                            f"The log validation rate for technology '{tech_name}' fell below 40%.\n\n"
                            f"Validation Rate: {val_rate:.1f}%\n"
                            f"URLs Crawled:    {urls_crawled_tech}\n"
                            f"Logs Extracted:  {logs_extracted_tech}\n"
                            f"Logs Validated:  {logs_validated_tech}\n\n"
                            f"Possible Cause:  Noisy sources or strict validation parameters.\n"
                            f"Recommendation:  Optimize query keywords or refine LLM validator prompts."
                        )
                        send_event_notification(
                            event_type="low_validation",
                            severity="WARNING",
                            subject=subject,
                            content_body=body,
                            job_id=job_id,
                            technology=tech_name
                        )
                    except Exception as n_err:
                        logger.error(f"Failed to send low validation rate warning: {n_err}")
                        
            # High Duplicate Warning Trigger
            if logs_validated_tech > 0:
                dup_rate = (duplicates_skipped_tech / logs_validated_tech) * 100.0
                if dup_rate > 70.0:
                    try:
                        from backend.notifications import send_event_notification
                        subject = f"High Duplicate Rate Detected"
                        body = (
                            f"Duplicate rate exceeded 70% during discovery of technology '{tech_name}'.\n\n"
                            f"Technology:           {tech_name}\n"
                            f"Duplicate Percentage: {dup_rate:.1f}%\n"
                            f"Duplicate Count:      {duplicates_skipped_tech}\n"
                            f"Validated Logs:       {logs_validated_tech}\n"
                            f"Inserted Logs:        {logs_inserted_tech}\n\n"
                            f"Recommendation:       Crawler is hitting duplicate documents. Consider expanding query parameters."
                        )
                        send_event_notification(
                            event_type="high_duplicate",
                            severity="WARNING",
                            subject=subject,
                            content_body=body,
                            job_id=job_id,
                            technology=tech_name
                        )
                    except Exception as n_err:
                        logger.error(f"Failed to send duplicate warning notification: {n_err}")

            techs_processed += 1
            if techs_processed >= MAX_TECHNOLOGIES_PER_CYCLE:
                logger.info(f"Capped log discovery processing at MAX_TECHNOLOGIES_PER_CYCLE ({MAX_TECHNOLOGIES_PER_CYCLE})")
                break
                
        # Update metrics database
        insert_yield_pct = round(logs_inserted * 100.0 / urls_crawled, 2) if urls_crawled > 0 else 0.0
        log_runtime_metrics(techs_processed, urls_crawled, logs_extracted, logs_validated, logs_inserted, failures,
                            urls_skipped=urls_skipped, pages_log_rich=pages_log_rich, pages_low_value=pages_low_value,
                            insert_yield_pct=insert_yield_pct)
        logger.info(f"Log Discovery Job complete. Processed {techs_processed} techs, inserted {logs_inserted} validated logs.")
        log_health_status("log_discovery", "healthy", f"Logged {logs_inserted} inserts, {failures} failures.")
        db_manager.log_agent_event("job_end", f"Log Discovery Job completed. Processed {techs_processed} technologies, inserted {logs_inserted} logs.")
        
        # Get repository log count after job finishes
        try:
            repo_count_after = db_manager.get_validated_logs_count()
        except Exception:
            repo_count_after = repo_count_before + logs_inserted
            
        # Send consolidated New Validated Logs Added notification
        if logs_inserted > 0:
            try:
                from backend.notifications import send_event_notification
                subject = f"{logs_inserted} New Validated Logs Added"
                tech_names_list = [t["technology_name"] for t in tech_rows_subset[:techs_processed]]
                body = (
                    f"Consolidated log discovery cycle completed and successfully added new validated logs.\n\n"
                    f"Technologies Processed:   {', '.join(tech_names_list)}\n"
                    f"URLs Crawled:             {urls_crawled}\n"
                    f"Logs Extracted:           {logs_extracted}\n"
                    f"Logs Validated:           {logs_validated}\n"
                    f"Logs Inserted:            {logs_inserted}\n"
                    f"Duplicates Skipped:       {logs_validated - logs_inserted}\n"
                    f"Repository Count Before:  {repo_count_before}\n"
                    f"Repository Count After:   {repo_count_after}\n"
                    f"Job Duration:             {time.time() - start_time:.1f} seconds\n"
                    f"Job ID:                   {job_id}"
                )
                send_event_notification(
                    event_type="new_logs",
                    severity="INFO",
                    subject=subject,
                    content_body=body,
                    job_id=job_id
                )
            except Exception as mail_err:
                logger.error(f"Failed to send consolidated logs notification: {mail_err}")
                
    except Exception as e:
        status = "failed"
        errors = f"Error: {e}\n{traceback.format_exc()}"
        logger.error(f"Log Discovery Job failed: {errors}")
        log_health_status("log_discovery", "unhealthy", f"Log discovery failed: {e}")
        db_manager.log_agent_event("error", f"Log Discovery Job failed: {e}")
        
        try:
            from backend.notifications import send_event_notification
            subject = f"Log Discovery Failed - log_discovery"
            content = f"The scheduled job 'log_discovery' has failed.\n\nError Details:\n{errors}\n\nTimestamp: {datetime.utcnow().isoformat() + 'Z'}"
            send_event_notification(
                event_type="discovery_failed",
                severity="ERROR",
                subject=subject,
                content_body=content,
                job_id=job_id
            )
        except Exception as mail_err:
            logger.error(f"Failed to send discovery job failure email: {mail_err}")
    finally:
        set_agent_idle()
        release_job_lock(job_id, status, records_processed=logs_inserted, errors=errors)
        db_manager.log_agent_event("job_completed", "Job completed: log_discovery")
        update_next_run_times_in_db()

def check_job_type_health(job_type, job_name):
    # 1. Fetch last 3 completed executions
    history = db_manager.get_last_job_executions(job_type, limit=3)
    
    # 2. Determine target state
    if not history:
        new_state = "healthy"
        latest_status = "success"
        latest_err = ""
        latest_time = None
    else:
        latest = history[0]
        latest_status = latest["status"]
        latest_err = latest["errors"] or ""
        latest_time = latest["end_time"] or latest["start_time"]
        
        if latest_status == "success":
            new_state = "healthy"
        elif latest_status == "warning":
            new_state = "warning"
        elif latest_status == "failed":
            # Check if last 3 are failed
            if len(history) >= 3 and all(h["status"] == "failed" for h in history):
                new_state = "critical"
            else:
                new_state = "error"
                
    # 3. Fetch stored state
    row = db_manager.get_agent_health_alert_state(job_type)
    if row:
        last_state = row["last_state"]
        downtime_start = row["downtime_start"]
    else:
        last_state = "healthy"
        downtime_start = None
        # Insert initial healthy state
        db_manager.insert_initial_health_alert_state(job_type, "healthy", None)
        
    email_to_send = None
    
    # 4. Check for transition
    if last_state != new_state:
        # State changed!
        if new_state == "healthy":
            # Transition to healthy -> Recovery Notice
            # Send recovery email if we were previously unhealthy
            if last_state in ("warning", "error", "critical"):
                # Calculate downtime
                failed_time_str = downtime_start or latest_time
                recovery_time_str = latest_time or datetime.utcnow().isoformat() + "Z"
                
                # Parse times
                downtime_str = "Unknown"
                try:
                    ft_clean = failed_time_str.split(".")[0].replace("Z", "").replace("T", " ")
                    rt_clean = recovery_time_str.split(".")[0].replace("Z", "").replace("T", " ")
                    ft = datetime.strptime(ft_clean, "%Y-%m-%d %H:%M:%S")
                    rt = datetime.strptime(rt_clean, "%Y-%m-%d %H:%M:%S")
                    diff = rt - ft
                    seconds = int(diff.total_seconds())
                    if seconds < 0:
                        seconds = 0
                    hours = seconds // 3600
                    minutes = (seconds % 3600) // 60
                    secs = seconds % 60
                    parts = []
                    if hours > 0:
                        parts.append(f"{hours}h")
                    if minutes > 0 or hours > 0:
                        parts.append(f"{minutes}m")
                    parts.append(f"{secs}s")
                    downtime_str = " ".join(parts)
                except Exception as ex:
                    logger.error(f"Error parsing downtime dates '{failed_time_str}' / '{recovery_time_str}': {ex}")
                
                email_to_send = {
                    "type": "recovery",
                    "subject": f"Recovery Notice - {job_name} Recovered Successfully",
                    "body": (
                        f"Job Type:          {job_name}\n"
                        f"Failed Time:       {failed_time_str}\n"
                        f"Recovery Time:     {recovery_time_str}\n"
                        f"Downtime Duration: {downtime_str}\n"
                        f"Current Status = Healthy"
                    ),
                    "severity": "INFO",
                    "event_type": "health_check_failed"
                }
                # Reset downtime start
                downtime_start = None
        else:
            # Transition to warning/error/critical
            if last_state == "healthy":
                # Start of downtime
                downtime_start = latest_time or datetime.utcnow().isoformat() + "Z"
                
            if new_state == "warning":
                subject = f"Repository Warning - {job_name} Completed with Warnings"
                body = (
                    f"Job Type:          {job_name}\n"
                    f"Status:            Warning\n"
                    f"Time:              {latest_time}\n"
                    f"Details/Errors:    {latest_err}"
                )
                email_to_send = {
                    "type": "warning",
                    "subject": subject,
                    "body": body,
                    "severity": "WARNING",
                    "event_type": "health_check_failed"
                }
            elif new_state == "error":
                subject = f"Repository Error - {job_name} Failed"
                body = (
                    f"Job Type:          {job_name}\n"
                    f"Status:            Failed\n"
                    f"Time:              {latest_time}\n"
                    f"Details/Errors:    {latest_err}"
                )
                email_to_send = {
                    "type": "error",
                    "subject": subject,
                    "body": body,
                    "severity": "ERROR",
                    "event_type": "health_check_failed"
                }
            elif new_state == "critical":
                subject = f"Repository Critical - Multiple Consecutive Job Failures"
                body = (
                    f"Job Type:          {job_name}\n"
                    f"Status:            CRITICAL (3 consecutive failures)\n"
                    f"Last Failed Time:  {latest_time}\n"
                    f"Details/Errors:    {latest_err}"
                )
                email_to_send = {
                    "type": "critical",
                    "subject": subject,
                    "body": body,
                    "severity": "CRITICAL",
                    "event_type": "health_check_failed"
                }
                
        # Update database with new state
        db_manager.update_agent_health_alert_state(job_type, new_state, downtime_start)
        
    return email_to_send, new_state, latest_err

def run_job_repository_health_check():
    if is_agent_paused():
        logger.info("Repository Health Check Job skipped: Agent is currently PAUSED.")
        db_manager.log_agent_event("job_skipped", "Repository Health Check Job skipped: Agent is paused")
        return
        
    logger.info("Executing scheduled Repository Health Check Job...")
    job_id = acquire_job_lock("health_check")
    if not job_id:
        logger.warning("Repository Health Check Job skipped: lock active (already running)")
        return
        
    try:
        from backend.notifications import process_notification_queue
        process_notification_queue()
    except Exception as e:
        logger.error(f"Error processing notification queue in health check: {e}")

    errors = ""
    status = "success"
    checks_passed = 0
    alerts = []
    
    try:
        db_manager.log_agent_event("job_start", "Starting Repository Health Check Job")
        db_manager.update_agent_status_field(
            status="running",
            current_job="health_check",
            current_phase="Validating",
            cycle_start_time=datetime.utcnow().isoformat() + "Z",
            technologies_total=0,
            technologies_processed=0,
            current_query=0,
            total_queries=0,
            current_url=0,
            total_urls=0,
            cycle_urls_crawled=0,
            cycle_pages_classified=0,
            cycle_logs_extracted=0,
            cycle_logs_validated=0,
            cycle_logs_inserted=0,
            cycle_duplicates_skipped=0
        )
        
        # Call cleanup helper inside db_manager to enforce event logs retention
        db_manager.cleanup_event_logs()
        
        # 1. Database Integrity check
        integrity_res = db_manager.run_database_integrity_check()
        if integrity_res != "ok":
            raise db_manager.DatabaseError(f"Database integrity check failed: {integrity_res}")
        checks_passed += 1
        
        # 2. State-aware checks for monitored job types
        monitored_jobs = [
            ("log_discovery", "Log Discovery"),
            ("technology_discovery", "Technology Discovery"),
            ("health_check", "Repository Health Check"),
            ("daily_report", "Daily Summary Report")
        ]
        
        from backend.notifications import send_event_notification
        
        notifications_to_send = []
        for jtype, jname in monitored_jobs:
            email_info, new_state, latest_err = check_job_type_health(jtype, jname)
            
            # If the state is not healthy, log it in alerts
            if new_state != "healthy":
                severity_val = "error" if new_state == "error" else ("critical" if new_state == "critical" else "warning")
                alerts.append((
                    jtype,
                    severity_val,
                    f"Job '{jname}' is currently in {new_state.upper()} state. Error: {latest_err}"
                ))
            
            # Collect notification if state changed
            if email_info:
                notifications_to_send.append(email_info)
        
        # Send notifications after connection is closed
        for email_info in notifications_to_send:
            try:
                send_event_notification(
                    event_type="health_check_failed",
                    severity=email_info["severity"],
                    subject=email_info["subject"],
                    content_body=email_info["body"],
                    job_id=job_id
                )
            except Exception as mail_err:
                logger.error(f"Failed to send health alert email: {mail_err}")
        
        # 3. Recalculate coverage metrics after connection is closed
        db_manager.recalculate_technology_coverage()
        checks_passed += 1
        
        # Log gathered health status warnings after connection is closed
        for comp, status_val, details in alerts:
            log_health_status(comp, status_val, details)
            
        logger.info("Repository Health Check complete: Database is healthy.")
        log_health_status("health_check", "healthy", "Integrity checks passed and coverage metrics recalculated.")
        db_manager.log_agent_event("job_end", "Repository Health Check completed successfully.")
        
    except Exception as e:
        status = "failed"
        errors = f"Error: {e}\n{traceback.format_exc()}"
        logger.error(f"Repository Health Check failed: {errors}")
        db_manager.log_agent_event("job_failed", f"Repository Health Check failed: {e}")
        try:
            log_health_status("health_check", "unhealthy", f"Health check validation failure: {e}")
        except Exception:
            pass
            
        try:
            from backend.notifications import send_event_notification
            subject = f"Repository Critical - Multiple Consecutive Job Failures"
            content = (
                f"A critical error occurred while executing the repository health check.\n\n"
                f"Component:       health_check\n"
                f"Severity:        CRITICAL\n"
                f"Error:           {e}\n"
                f"Recovery Action: Re-initialize the database connection or check sqlite integrity manually.\n\n"
                f"Stack Trace:\n{traceback.format_exc()}"
            )
            send_event_notification(
                event_type="health_check_failed",
                severity="CRITICAL",
                subject=subject,
                content_body=content,
                job_id=job_id
            )
        except Exception as mail_err:
            logger.error(f"Failed to send health check failure email: {mail_err}")
    finally:
        set_agent_idle()
        release_job_lock(job_id, status, records_processed=checks_passed, errors=errors)
        update_next_run_times_in_db()

def run_job_daily_summary_report():
    if is_agent_paused():
        logger.info("Daily Summary Report Job skipped: Agent is currently PAUSED.")
        db_manager.log_agent_event("job_skipped", "Daily Summary Report Job skipped: Agent is paused")
        return
        
    logger.info("Executing scheduled Daily Summary Report Job...")
    job_id = acquire_job_lock("daily_report")
    if not job_id:
        logger.warning("Daily Summary Report Job skipped: lock active (already running)")
        return
        
    try:
        from backend.notifications import process_notification_queue
        process_notification_queue()
    except Exception as e:
        logger.error(f"Error processing notification queue in daily report: {e}")

    errors = ""
    status = "success"
    
    conn = None
    try:
        db_manager.log_agent_event("job_start", "Starting Daily Summary Report Job")
        db_manager.update_agent_status_field(
            status="running",
            current_job="daily_report",
            current_phase="Saving",
            cycle_start_time=datetime.utcnow().isoformat() + "Z",
            technologies_total=0,
            technologies_processed=0,
            current_query=0,
            total_queries=0,
            current_url=0,
            total_urls=0,
            cycle_urls_crawled=0,
            cycle_pages_classified=0,
            cycle_logs_extracted=0,
            cycle_logs_validated=0,
            cycle_logs_inserted=0,
            cycle_duplicates_skipped=0
        )
        
        # 1. Gather stats in the last 24 hours
        yesterday_str = (datetime.utcnow() - timedelta(days=1)).isoformat()
        report_data = db_manager.get_daily_report_data(yesterday_str)
        
        new_techs = report_data["new_techs"]
        total_techs = report_data["total_techs"]
        new_logs = report_data["new_logs"]
        total_logs = report_data["total_logs"]
        job_stats = report_data["job_stats"]
        failed_jobs_list = report_data["failed_jobs_list"]
        alert_lines = report_data["alert_lines"]
        
        # Growth Rate
        growth_rate = (new_logs / (total_logs - new_logs) * 100.0) if (total_logs - new_logs) > 0 else 0.0
        
        # Generate the simulated email report content
        report_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        
        if failed_jobs_list or any(s[0] == 'failed' for s in job_stats):
            # Compile Failure Report
            report_type = "FAILURE_REPORT"
            report_content = f"""# AUTONOMOUS AGENT DAILY FAILURE REPORT - {report_time}

An alert is raised because scheduled jobs have encountered failures or warnings in the last 24 hours.

## Job Performance Summary
"""
            for st, cnt, jt in job_stats:
                report_content += f"- Job Type: `{jt}` | Status: `{st}` | Run Count: {cnt}\n"
                
            report_content += f"""
## Recent Job Stack Traces / Errors
{chr(10).join(failed_jobs_list) if failed_jobs_list else "- No stack traces found."}

## Blocked Sources & Component Anomalies
{chr(10).join(alert_lines) if alert_lines else "- No anomalous health events detected."}

## Repository Operational Metrics
- Total Accepted Technologies: {total_techs}
- Total Logs in Repository: {total_logs}
- Growth Rate (24h): {growth_rate:.2f}%
"""
        else:
            # Compile Success Report
            report_type = "SUCCESS_REPORT"
            report_content = f"""# AUTONOMOUS AGENT DAILY SUCCESS REPORT - {report_time}

All scheduled jobs have completed successfully in the last 24 hours.

## Ingestion Metrics (Last 24 Hours)
- **New Technologies Discovered & Accepted**: {new_techs} (Total tracked: {total_techs})
- **New Validated Logs Crawled**: {new_logs}
- **Total Validated Logs**: {total_logs}
- **Repository Daily Growth**: {growth_rate:.2f}%

## Job Operations Stats
All jobs completed without errors.
"""
            for st, cnt, jt in job_stats:
                report_content += f"- Job Type: `{jt}` | Status: `{st}` | Run Count: {cnt}\n"
                
        # 1. Write report content to Notification Queue Table (mandatory step 3)
        queue_id = enqueue_notification(report_type, report_content)
        
        # 2. Write to physical file on disk (simulating email delivery)
        import tempfile
        notifications_dir = os.path.abspath(os.path.join(tempfile.gettempdir(), "log_collector_notifications"))
        os.makedirs(notifications_dir, exist_ok=True)
        
        file_name = f"{report_type.lower()}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.md"
        report_file_path = os.path.join(notifications_dir, file_name)
        
        with open(report_file_path, "w", encoding="utf-8") as rf:
            rf.write(report_content)
            
        # 3. Mark notification sent (queued items)
        if queue_id is not None:
            mark_notification_sent(queue_id)
            
        logger.info(f"Daily Summary Notification created: {report_file_path}")
        log_health_status("daily_report", "healthy", f"Daily report successfully saved to {file_name}")
        db_manager.log_agent_event("job_end", "Daily Summary Report Job completed successfully.")
    except Exception as e:
        status = "failed"
        errors = f"Error: {e}\n{traceback.format_exc()}"
        logger.error(f"Daily Summary Report Job failed: {errors}")
        log_health_status("daily_report", "unhealthy", f"Report consolidation failed: {e}")
        db_manager.log_agent_event("job_failed", f"Daily Summary Report Job failed: {e}")
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
        set_agent_idle()
        release_job_lock(job_id, status, records_processed=1, errors=errors)
        update_next_run_times_in_db()

# Main runner for autonomous agent (Refactored to execute one cycle and exit)
def start_agent():
    logger.info("Initializing Autonomous Log Discovery Agent...")
    
    # Run DB init to ensure tables and baseline aliases are ready
    db_manager.init_repo_db()

    # Recover stale locks at startup
    recover_stale_locks()
    
    # Check for previous unhandled crashes to report
    check_and_report_previous_crash()
    
    # Process notification queue on startup
    try:
        from backend.notifications import process_notification_queue
        process_notification_queue()
    except Exception as e:
        logger.error(f"Error processing notification queue on startup: {e}")

    logger.info("Starting one complete autonomous execution cycle...")
    run_job_technology_discovery()
    run_job_log_discovery()
    run_job_repository_health_check()
    run_job_daily_summary_report()
    
    # Process notification queue at the end of the cycle
    try:
        from backend.notifications import process_notification_queue
        process_notification_queue()
    except Exception as e:
        logger.error(f"Error processing notification queue at end of cycle: {e}")

    logger.info("Autonomous execution cycle completed successfully.")

if __name__ == "__main__":
    start_agent()
