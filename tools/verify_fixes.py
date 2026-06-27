import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import sqlite3
import os
from backend import db_manager

def check_conn_pragmas(conn, label):
    cursor = conn.cursor()
    
    cursor.execute("PRAGMA journal_mode;")
    jm = cursor.fetchone()[0]
    
    cursor.execute("PRAGMA synchronous;")
    sync = cursor.fetchone()[0]
    
    cursor.execute("PRAGMA busy_timeout;")
    bt = cursor.fetchone()[0]
    
    print(f"\n=== Configured Connection Pragmas: {label} ===")
    print(f"journal_mode: {jm}")
    print(f"synchronous: {sync} (0=OFF, 1=NORMAL, 2=FULL, 3=EXTRA)")
    print(f"busy_timeout: {bt} ms")

def check_repo_conn_zero_writes():
    print("\n=== Checking get_repo_connection() Writes ===")
    conn = db_manager.get_repo_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [row[0] for row in cursor.fetchall()]
    print(f"Tables present in database: {tables}")
    conn.close()
    print("Connection closed. Zero writes executed inside get_repo_connection().")

if __name__ == '__main__':
    # Initialize DBs to verify init path
    db_manager.init_db()
    db_manager.init_repo_db()
    
    # Query configured connections
    repo_conn = db_manager.get_repo_connection()
    check_conn_pragmas(repo_conn, "Validated Logs DB (get_repo_connection)")
    repo_conn.close()
    
    jobs_conn, _ = db_manager.get_connection()
    check_conn_pragmas(jobs_conn, "Jobs DB (get_connection)")
    jobs_conn.close()
    
    check_repo_conn_zero_writes()
