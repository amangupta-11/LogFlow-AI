import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import sqlite3
import os
import sys
from datetime import datetime, timedelta
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

# Add workspace to path
sys.path.append(os.path.abspath(os.path.dirname(__file__)))
from backend import db_manager

def get_job_history():
    print("=== QUERY: SELECT id, job_type, status, start_time, end_time FROM agent_job_history ===")
    conn = db_manager.get_repo_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, job_type, status, start_time, end_time
        FROM agent_job_history
        ORDER BY id DESC
        LIMIT 20;
    """)
    rows = cursor.fetchall()
    for r in rows:
        print(f"ID: {r[0]} | Type: {r[1]} | Status: {r[2]} | Start: {r[3]} | End: {r[4]}")
    conn.close()

def get_running_jobs():
    print("\n=== QUERY: SELECT * FROM agent_job_history WHERE status='running' ===")
    conn = db_manager.get_repo_connection()
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(agent_job_history)")
    cols = [c[1] for c in cursor.fetchall()]
    cursor.execute("""
        SELECT *
        FROM agent_job_history
        WHERE status='running';
    """)
    rows = cursor.fetchall()
    if not rows:
        print("No running jobs found.")
    for r in rows:
        print(dict(zip(cols, r)))
    conn.close()

def check_stale_locks():
    print("\n=== CHECKING STALE LOCKS (running for >10 mins) ===")
    conn = db_manager.get_repo_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, job_type, start_time FROM agent_job_history WHERE status = 'running'")
    running_jobs = cursor.fetchall()
    now = datetime.utcnow()
    stale_found = False
    for job in running_jobs:
        job_id, job_type, start_time_str = job[0], job[1], job[2]
        t_str = start_time_str
        if t_str.endswith("Z"):
            t_str = t_str[:-1]
        try:
            start_time = datetime.fromisoformat(t_str)
            age_minutes = (now - start_time).total_seconds() / 60.0
            if age_minutes > 10.0:
                print(f"  [STALE LOCK DETECTED] Job ID {job_id} ({job_type}) has been running for {age_minutes:.2f} minutes.")
                stale_found = True
            else:
                print(f"  Active job ID {job_id} ({job_type}) has been running for {age_minutes:.2f} minutes (under 10 minutes limit).")
        except Exception as e:
            print(f"  Error checking age of job {job_id}: {e}")
    if not running_jobs:
        print("No active job locks exist.")
    elif not stale_found:
        print("All active locks are under the 10-minute threshold.")
    conn.close()

def print_env_limits():
    print("\n=== ENVIRONMENT LIMITS ===")
    from backend import autonomous_agent
    print(f"MAX_TECHNOLOGIES_PER_CYCLE: {autonomous_agent.MAX_TECHNOLOGIES_PER_CYCLE}")
    print(f"MAX_QUERIES_PER_TECHNOLOGY: {autonomous_agent.MAX_QUERIES_PER_TECHNOLOGY}")
    print(f"MAX_URLS_PER_TECHNOLOGY: {autonomous_agent.MAX_URLS_PER_TECHNOLOGY}")
    print(f"MAX_RUNTIME_PER_JOB: {autonomous_agent.MAX_RUNTIME_PER_JOB}")

def print_scheduler_jobs():
    print("\n=== APSCHEDULER JOB LISTING (As Statically Configured) ===")
    from apscheduler.schedulers.background import BackgroundScheduler
    scheduler = BackgroundScheduler()
    
    # Recreate target functions dummy definitions to avoid actual execution
    def dummy_tech(): pass
    def dummy_log(): pass
    def dummy_health(): pass
    def dummy_daily(): pass

    scheduler.add_job(
        dummy_tech,
        trigger=IntervalTrigger(hours=24),
        id="technology_discovery",
        name="Discover new technologies"
    )
    
    scheduler.add_job(
        dummy_log,
        trigger=IntervalTrigger(hours=24),
        id="log_discovery",
        name="Search, crawl and extract logs"
    )
    
    scheduler.add_job(
        dummy_health,
        trigger=IntervalTrigger(hours=24),
        id="health_check",
        name="Database integrity and jobs health check"
    )
    
    scheduler.add_job(
        dummy_daily,
        trigger=IntervalTrigger(hours=24),
        id="daily_report",
        name="Consolidate daily summary reports"
    )
    
    scheduler.start()
    
    print("scheduler.get_jobs() Output:")
    jobs = scheduler.get_jobs()
    print(jobs)
    
    print("\nJob Detail Breakdown:")
    for job in jobs:
        print(f"Job ID: {job.id}")
        print(f"  Name: {job.name}")
        print(f"  next_run_time: {job.next_run_time}")
        print(f"  trigger: {job.trigger}")
        print(f"  interval: {job.trigger.interval}")
        
    scheduler.shutdown()

if __name__ == '__main__':
    print_scheduler_jobs()
    get_job_history()
    get_running_jobs()
    check_stale_locks()
    print_env_limits()
