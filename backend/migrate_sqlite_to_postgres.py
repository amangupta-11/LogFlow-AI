import os
import sys
import sqlite3
import psycopg2
from dotenv import load_dotenv

# Add project root to sys.path to enable imports of backend modules
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

TABLE_KEYS = {
    "jobs": "id",
    "job_rows": "id",
    "validated_logs": "normalized_hash",
    "repository_metrics": "key",
    "repository_discovery_history": "id",
    "technology_catalog": "technology_name",
    "technology_coverage": "technology_name",
    "technology_aliases": "alias",
    "technology_log_profile": "technology_name",
    "notification_queue": "id",
    "notification_history": "id",
    "agent_job_history": "id",
    "agent_health_history": "id",
    "agent_runtime_metrics": "id",
    "domain_performance": "domain",
    "agent_status": "id",
    "agent_control_queue": "id",
    "agent_event_feed": "id",
    "agent_health_alert_states": "job_type"
}

def migrate_table(sqlite_conn, pg_conn, table_name):
    print(f"[{table_name}] Auditing table schema...", flush=True)
    # 1. Fetch columns from PostgreSQL to match intersection
    pg_cursor = pg_conn.cursor()
    pg_cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name = %s", (table_name.lower(),))
    pg_cols = set(r[0].lower() for r in pg_cursor.fetchall())
    pg_cursor.close()
    
    if not pg_cols:
        print(f"[{table_name}] Warning: Table does not exist in Postgres.", flush=True)
        return 0, 0, "Table not in Postgres"

    # 2. Fetch rows and columns from SQLite
    print(f"[{table_name}] Querying source SQLite table...", flush=True)
    sqlite_cursor = sqlite_conn.cursor()
    try:
        sqlite_cursor.execute(f"SELECT * FROM {table_name}")
        sqlite_rows = sqlite_cursor.fetchall()
    except sqlite3.OperationalError as e:
        print(f"[{table_name}] SQLite table query failed: {e}", flush=True)
        return 0, 0, "Not in SQLite"
        
    sqlite_count = len(sqlite_rows)
    sqlite_cols = [desc[0] for desc in sqlite_cursor.description]
    sqlite_cursor.close()
    
    print(f"[{table_name}] Found {sqlite_count} rows in SQLite.", flush=True)
    
    # Get common columns
    common_cols = [col for col in sqlite_cols if col.lower() in pg_cols]
    
    if len(common_cols) == 0:
        print(f"[{table_name}] No common columns found between SQLite and Postgres.", flush=True)
        return sqlite_count, 0, "No common columns"
        
    # 3. Check Postgres count
    pg_cursor = pg_conn.cursor()
    pg_cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
    pg_count = pg_cursor.fetchone()[0]
    
    if sqlite_count == 0:
        pg_cursor.close()
        print(f"[{table_name}] SQLite table is empty.", flush=True)
        return 0, pg_count, "Success (SQLite empty)"
    
    # 4. Get existing keys in PostgreSQL to ensure idempotency
    key_col = TABLE_KEYS.get(table_name, "id")
    if key_col not in common_cols:
        key_col = common_cols[0]
        
    pg_cursor.execute(f"SELECT {key_col} FROM {table_name}")
    existing_keys = set(row[0] for row in pg_cursor.fetchall())
    print(f"[{table_name}] Found {len(existing_keys)} existing keys in Postgres.", flush=True)
    
    # 4b. Exclude 'id' from insertion columns for tables where check key is not 'id'
    # to avoid clashing on specific auto-increment IDs.
    if key_col != "id" and "id" in common_cols:
        common_cols.remove("id")
        print(f"[{table_name}] Excluded 'id' column from migration to avoid primary key clashes.", flush=True)

    # Get indices of common columns in SQLite row
    col_indices = [sqlite_cols.index(col) for col in common_cols]

    # 5. Insert missing rows into Postgres
    inserted = 0
    col_str = ", ".join(common_cols)
    val_placeholders = ", ".join(["%s"] * len(common_cols))
    insert_query = f"INSERT INTO {table_name} ({col_str}) VALUES ({val_placeholders})"
    
    key_idx = sqlite_cols.index(key_col)
    
    for idx, row in enumerate(sqlite_rows):
        row_key = row[key_idx]
        if row_key in existing_keys:
            continue
            
        row_values = [row[i] for i in col_indices]
        pg_cursor.execute(insert_query, row_values)
        inserted += 1
        
        if inserted % 100 == 0:
            print(f"[{table_name}] Processed {idx+1}/{sqlite_count} rows... inserted {inserted} new records.", flush=True)
        
    pg_conn.commit()
    print(f"[{table_name}] Committed {inserted} new records to Postgres.", flush=True)
    
    # 6. Reset serial sequence if the table has a serial/auto-increment id
    has_serial_id = False
    if "id" in common_cols and table_name not in ["jobs", "agent_health_alert_states", "repository_metrics", "technology_coverage", "technology_log_profile", "domain_performance"]:
        has_serial_id = True
        
    # We also want to reset sequence if we excluded ID but it is present in target Postgres schema
    if key_col != "id" and table_name in ["validated_logs", "technology_catalog"]:
        has_serial_id = True

    if has_serial_id:
        try:
            seq_query = f"""
                SELECT setval(
                    COALESCE(
                        pg_get_serial_sequence('{table_name}', 'id'), 
                        '{table_name}_id_seq'
                    ), 
                    COALESCE(MAX(id), 1)
                ) FROM {table_name}
            """
            pg_cursor.execute(seq_query)
            pg_conn.commit()
            print(f"[{table_name}] Reset auto-increment sequence.", flush=True)
        except Exception as e:
            pg_conn.rollback()
            print(f"[{table_name}] Warning: Could not reset sequence: {e}", flush=True)
            
    # 7. Fetch final count from Postgres
    pg_cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
    pg_count = pg_cursor.fetchone()[0]
    pg_cursor.close()
    
    status = "Success"
    if pg_count < sqlite_count:
        status = "Count Mismatch"
        
    return sqlite_count, pg_count, status

def main():
    print("Migration utility starting...", flush=True)
    if not DATABASE_URL:
        print("Error: DATABASE_URL is not set in the environment.", flush=True)
        sys.exit(1)
        
    print("Connecting to target PostgreSQL...", flush=True)
    if DATABASE_URL.startswith("postgres://"):
        pg_url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    else:
        pg_url = DATABASE_URL
        
    try:
        pg_conn = psycopg2.connect(pg_url, connect_timeout=10)
        print("Connected to PostgreSQL successfully.", flush=True)
    except Exception as e:
        print(f"Error connecting to PostgreSQL: {e}", flush=True)
        sys.exit(1)
        
    print("Initializing / verifying PostgreSQL schemas...", flush=True)
    try:
        from backend import db_manager
        db_manager.init_db()
        db_manager.init_repo_db()
        print("PostgreSQL schemas verified/created.", flush=True)
    except Exception as e:
        print(f"Error checking schemas: {e}", flush=True)
        pg_conn.close()
        sys.exit(1)
    
    # Identify source SQLite database paths
    jobs_db_path = os.getenv("SQLITE_DB_PATH", "jobs.db")
    if not os.path.isabs(jobs_db_path):
        jobs_db_path = os.path.abspath(os.path.join(os.path.dirname(__file__), jobs_db_path))
        
    repo_db_path = os.getenv("REPO_DB_PATH", "validated_logs.db")
    if not os.path.isabs(repo_db_path):
        repo_db_path = os.path.abspath(os.path.join(os.path.dirname(__file__), repo_db_path))
        
    print(f"Source jobs.db: {jobs_db_path}", flush=True)
    print(f"Source validated_logs.db: {repo_db_path}", flush=True)
    
    # Connect to SQLite databases
    jobs_conn = None
    if os.path.exists(jobs_db_path):
        try:
            print("Connecting to source jobs.db SQLite...", flush=True)
            jobs_conn = sqlite3.connect(jobs_db_path, timeout=10)
            print("Connected to jobs.db SQLite.", flush=True)
        except Exception as e:
            print(f"Error connecting to jobs SQLite DB: {e}", flush=True)
    else:
        print(f"Warning: jobs.db does not exist at {jobs_db_path}", flush=True)
        
    repo_conn = None
    if os.path.exists(repo_db_path):
        try:
            print("Connecting to source validated_logs.db SQLite...", flush=True)
            repo_conn = sqlite3.connect(repo_db_path, timeout=10)
            print("Connected to validated_logs.db SQLite.", flush=True)
        except Exception as e:
            print(f"Error connecting to repository SQLite DB: {e}", flush=True)
    else:
        print(f"Warning: validated_logs.db does not exist at {repo_db_path}", flush=True)
        
    # Table mapping to connection
    tables_to_migrate = {
        # jobs.db
        "jobs": jobs_conn,
        "job_rows": jobs_conn,
        # validated_logs.db
        "validated_logs": repo_conn,
        "repository_metrics": repo_conn,
        "repository_discovery_history": repo_conn,
        "technology_catalog": repo_conn,
        "technology_coverage": repo_conn,
        "technology_aliases": repo_conn,
        "technology_log_profile": repo_conn,
        "notification_queue": repo_conn,
        "notification_history": repo_conn,
        "agent_job_history": repo_conn,
        "agent_health_history": repo_conn,
        "agent_runtime_metrics": repo_conn,
        "domain_performance": repo_conn,
        "agent_status": repo_conn,
        "agent_control_queue": repo_conn,
        "agent_event_feed": repo_conn,
        "agent_health_alert_states": repo_conn
    }
    
    print("\nStarting migration loop...", flush=True)
    
    results = []
    has_errors = False
    
    for table_name, sqlite_conn in tables_to_migrate.items():
        print("=" * 60, flush=True)
        print(f"Migrating table: {table_name}", flush=True)
        if sqlite_conn is None:
            print(f"Skipping: Connection to SQLite database for {table_name} is not available.", flush=True)
            results.append((table_name, 0, 0, "No SQLite Connection"))
            continue
            
        try:
            sqlite_count, pg_count, status = migrate_table(sqlite_conn, pg_conn, table_name)
            results.append((table_name, sqlite_count, pg_count, status))
            if status == "Count Mismatch":
                has_errors = True
        except Exception as e:
            pg_conn.rollback()
            print(f"Error migrating table {table_name}: {e}", flush=True)
            results.append((table_name, 0, 0, f"Error: {e}"))
            has_errors = True
            
    print("=" * 60, flush=True)
    print("\nAll tables processed. Final Migration Summary:\n", flush=True)
    print("-" * 75, flush=True)
    print(f"{'Table Name':<30} | {'SQLite Rows':<11} | {'Postgres Rows':<13} | {'Status'}", flush=True)
    print("-" * 75, flush=True)
    
    for table_name, sqlite_count, pg_count, status in results:
        print(f"{table_name:<30} | {sqlite_count:<11} | {pg_count:<13} | {status}", flush=True)
        
    print("-" * 75, flush=True)
    
    # Close connections
    if jobs_conn:
        jobs_conn.close()
    if repo_conn:
        repo_conn.close()
    pg_conn.close()
    
    if has_errors:
        print("\nMigration completed with warning/errors.", flush=True)
        sys.exit(1)
    else:
        print("\nMigration completed successfully!", flush=True)
        sys.exit(0)

if __name__ == "__main__":
    main()
