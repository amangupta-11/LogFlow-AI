import os
from pathlib import Path
import logging
from fastapi import FastAPI, HTTPException, Body, UploadFile, File, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing import Optional, List
from dotenv import load_dotenv, set_key
import uuid
import tempfile

from backend import db_manager, batch_processor

from backend.crawler import collect_logs_from_web
from backend.extractor import parse_logs_with_llm, parse_logs_with_regex, map_source_urls, safe_to_text
from backend.generator import generate_synthetic_logs
from backend.validator import validate_logs_with_claude
from backend.vector_store import vector_store


load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="AI Log Collector & Generator API")
frontend_path = Path(__file__).resolve().parent.parent / "frontend"

# Enable CORS for frontend development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Request/Response Schemas
class CollectRequest(BaseModel):
    platform: str = Field(..., example="Nginx")
    version: Optional[str] = Field("", example="1.25")
    service: Optional[str] = Field("", example="upstream")
    count: Optional[int] = Field(10, example=10)
    start_date: Optional[str] = Field("2000-01-01T00:00", example="2000-01-01T00:00")
    end_date: Optional[str] = Field(None, example="2026-05-29T17:35")

class GenerateRequest(BaseModel):
    platform: str = Field(..., example="Docker")
    version: Optional[str] = Field("", example="25")
    service: Optional[str] = Field("", example="daemon")
    severity: Optional[str] = Field("ALL", example="ERROR")
    count: Optional[int] = Field(5, example=5)
    scenario: Optional[str] = Field("", example="container bridge network error")
    start_date: Optional[str] = Field("2000-01-01T00:00", example="2000-01-01T00:00")
    end_date: Optional[str] = Field(None, example="2026-05-29T17:35")

class SettingsRequest(BaseModel):
    gemini_key: Optional[str] = None
    openai_key: Optional[str] = None
    anthropic_key: Optional[str] = None

# API Endpoints
def merge_and_deduplicate_logs(llm_logs: list, regex_logs: list) -> list:
    combined = []
    seen_messages = set()
    for log in llm_logs:
        msg = safe_to_text(log.get("message")).strip()
        if not msg:
            continue
        msg_lower = msg.lower()
        if msg_lower not in seen_messages:
            seen_messages.add(msg_lower)
            combined.append(log)
    for log in regex_logs:
        msg = safe_to_text(log.get("message")).strip()
        if not msg:
            continue
        msg_lower = msg.lower()
        if msg_lower not in seen_messages:
            seen_messages.add(msg_lower)
            combined.append(log)
    return combined

def calculate_coverage_score(logs: list) -> int:
    """
    Calculate Coverage Score (0-100) based on:
    - sources found: +10 per unique domain (up to +30)
    - validated logs: +10 per validated log (up to +40)
    - source diversity: +15 if >= 2 source rank tiers (Tiers 1-4) are represented in validated logs
    - confidence: +15 if average log confidence (of validated logs) is >= 85
    """
    validated_logs = [log for log in logs if log.get("validation", {}).get("valid", False)]
    if not validated_logs:
        return 0
        
    # 1. Sources found (unique domains of validated logs)
    unique_domains = set()
    for log in validated_logs:
        url = safe_to_text(log.get("source_url", ""))
        if url:
            try:
                from urllib.parse import urlparse
                domain = urlparse(url).netloc.lower()
                if domain.startswith("www."):
                    domain = domain[4:]
                if domain:
                    unique_domains.add(domain)
            except Exception:
                pass
    sources_score = min(len(unique_domains) * 10, 30)
    
    # 2. Validated logs count
    validated_score = min(len(validated_logs) * 10, 40)
    
    # 3. Source diversity
    tiers_represented = set()
    for log in validated_logs:
        rank = log.get("source_rank") or log.get("validation", {}).get("source_rank")
        if rank in [1, 2, 3, 4]:
            tiers_represented.add(rank)
    diversity_score = 15 if len(tiers_represented) >= 2 else 0
    
    # 4. Confidence
    avg_confidence = sum(log.get("validation", {}).get("confidence", 0) for log in validated_logs) / len(validated_logs)
    confidence_score = 15 if avg_confidence >= 85 else 0
    
    return sources_score + validated_score + diversity_score + confidence_score

@app.post("/api/collect")
async def collect_logs(req: CollectRequest):
    try:
        logger.info(f"Starting log collection for {req.platform} (version: {req.version})")
        # 1. Scrape web
        scraped_text = collect_logs_from_web(req.platform, req.version, req.service, req.count)
        
        logs = []
        source = "scraped" if str(scraped_text).strip() else "synthetic_fallback"
        
        # 2. Extract logs if scraping returned content
        if str(scraped_text).strip():
            if hasattr(scraped_text, "extracted_logs") and scraped_text.extracted_logs:
                logs = scraped_text.extracted_logs
            else:
                llm_logs = parse_logs_with_llm(scraped_text, req.platform, req.version, req.service, req.count, req.start_date, req.end_date)
                regex_logs = parse_logs_with_regex(scraped_text, req.platform, req.version, req.service, req.count, req.start_date, req.end_date)
                
                # Map source URLs to make sure they are never empty
                llm_logs = map_source_urls(llm_logs, scraped_text)
                regex_logs = map_source_urls(regex_logs, scraped_text)
                
                # Merge and deduplicate
                logs = merge_and_deduplicate_logs(llm_logs, regex_logs)
                
                # Validation Layer: Always call validate_logs_with_claude which handles Claude -> OpenAI -> Unverified fallbacks
                if logs:
                    logs = validate_logs_with_claude(logs, scraped_text, req.platform, req.version)
            
        # 4. Save to semantic vector store
        if logs:
            vector_store.add_logs(logs, req.platform, req.version, req.service)
            # Ingest to central validated logs repository
            db_manager.insert_validated_logs(
                logs,
                job_platform=req.platform,
                job_product_name="",
                job_log_type=req.service
            )
            
        return {
            "status": "success",
            "platform": req.platform,
            "version": req.version,
            "service": req.service,
            "source": source,
            "logs": logs,
            "coverage_score": calculate_coverage_score(logs)
        }
    except Exception as e:
        logger.error(f"Error collecting logs: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/generate")
async def generate_logs(req: GenerateRequest):
    try:
        logger.info(f"Generating synthetic logs for {req.platform} (scenario: {req.scenario})")
        logs = generate_synthetic_logs(
            platform=req.platform,
            version=req.version,
            service=req.service,
            severity=req.severity,
            count=req.count,
            scenario=req.scenario,
            start_date=req.start_date,
            end_date=req.end_date
        )
        
        # Tag synthetic logs
        if logs:
            for log in logs:
                log["validation"] = {"valid": True, "reason": "Synthetically generated (Assumed valid)"}
            vector_store.add_logs(logs, req.platform, req.version, req.service)
            
        return {
            "status": "success",
            "platform": req.platform,
            "version": req.version,
            "service": req.service,
            "logs": logs
        }
    except Exception as e:
        logger.error(f"Error generating logs: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/search")
async def search_logs(query: str, limit: int = 10):
    try:
        results = vector_store.search(query, limit)
        return {
            "query": query,
            "results": results
        }
    except Exception as e:
        logger.error(f"Error searching logs: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/settings")
async def get_settings():
    # Return status of keys without exposing raw keys
    return {
        "gemini_key_configured": bool(os.getenv("GEMINI_API_KEY")),
        "openai_key_configured": bool(os.getenv("OPENAI_API_KEY")),
        "anthropic_key_configured": bool(os.getenv("ANTHROPIC_API_KEY"))
    }

@app.post("/api/settings")
async def update_settings(req: SettingsRequest):
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    
    # Save settings to .env file
    try:
        if req.gemini_key is not None:
            set_key(env_path, "GEMINI_API_KEY", req.gemini_key.strip())
            os.environ["GEMINI_API_KEY"] = req.gemini_key.strip()
            
        if req.openai_key is not None:
            set_key(env_path, "OPENAI_API_KEY", req.openai_key.strip())
            os.environ["OPENAI_API_KEY"] = req.openai_key.strip()

        if req.anthropic_key is not None:
            set_key(env_path, "ANTHROPIC_API_KEY", req.anthropic_key.strip())
            os.environ["ANTHROPIC_API_KEY"] = req.anthropic_key.strip()
            
        # Re-initialize vector store's embedding system or any caches
        vector_store.load()
        
        return {"status": "success", "message": "Settings updated successfully"}
    except Exception as e:
        logger.error(f"Error updating settings: {e}")
        raise HTTPException(status_code=500, detail="Failed to save settings locally")

@app.on_event("startup")
async def startup_event():
    try:
        db_manager.init_db()
    except Exception as e:
        logger.error(f"Failed to initialize main database on startup: {e}")
        
    try:
        db_manager.init_repo_db()
    except Exception as e:
        logger.error(f"Failed to initialize repository database on startup: {e}")
        
    try:
        batch_processor.resume_unfinished_jobs()
    except Exception as e:
        logger.error(f"Failed to resume unfinished jobs on startup: {e}")


@app.post("/api/upload-batch")
async def upload_batch(file: UploadFile = File(...), background_tasks: BackgroundTasks = None):
    try:
        filename = file.filename
        _, ext = os.path.splitext(filename.lower())
        if ext not in (".csv", ".xlsx"):
            raise HTTPException(status_code=400, detail="Only .csv and .xlsx files are supported.")
        
        # Save uploaded file to temp file
        temp_fd, temp_path = tempfile.mkstemp(suffix=ext)
        os.close(temp_fd)
        
        with open(temp_path, "wb") as f:
            f.write(await file.read())
        
        # Parse rows
        rows = batch_processor.parse_uploaded_file(temp_path)
        if not rows:
            try:
                os.remove(temp_path)
            except:
                pass
            raise HTTPException(status_code=400, detail="The uploaded file is empty or could not be parsed.")
        
        job_id = str(uuid.uuid4())
        db_manager.create_job(job_id, len(rows))
        
        for row in rows:
            platform = row.get("platform", "").strip()
            version = row.get("version", "").strip()
            service = row.get("service", "").strip()
            product_name = row.get("product_name", "").strip()
            error_code = row.get("error_code", "").strip()
            excel_error_message = row.get("excel_error_message", "").strip()
            reason = row.get("reason", "").strip()
            source = row.get("source", "").strip()
            error_message_long = row.get("error_message_long", "").strip()
            category = row.get("category", "").strip()
            
            # Extract count / max logs
            max_logs_val = row.get("max_logs") or row.get("max logs") or row.get("count") or row.get("maxlogs") or row.get("limit") or "10"
            try:
                max_logs = int(max_logs_val)
            except:
                max_logs = 10
            
            db_manager.add_job_row(
                job_id, platform, version, service, max_logs,
                product_name=product_name,
                error_code=error_code,
                excel_error_message=excel_error_message,
                reason=reason,
                source=source,
                error_message_long=error_message_long,
                category=category
            )
            
        try:
            os.remove(temp_path)
        except:
            pass
            
        if background_tasks:
            background_tasks.add_task(batch_processor.process_batch_job, job_id)
        else:
            import threading
            t = threading.Thread(target=batch_processor.process_batch_job, args=(job_id,))
            t.daemon = True
            t.start()
            
        return {
            "status": "success",
            "job_id": job_id,
            "total_rows": len(rows),
            "message": "Batch processing started in background."
        }
    except Exception as e:
        logger.error(f"Error in upload_batch: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/batch-status/{job_id}")
async def get_batch_status(job_id: str):
    job = db_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
        
    rows = db_manager.get_job_rows(job_id)
    return {
        "status": "success",
        "job": {
            "id": job["id"],
            "status": job["status"],
            "total_rows": job["total_rows"],
            "completed_rows": job["completed_rows"],
            "failed_rows": job["failed_rows"],
            "skipped_rows": job["skipped_rows"],
            "remaining_rows": job["remaining_rows"],
            "has_zip": bool(job["zip_path"] and os.path.exists(job["zip_path"])),
            "created_at": job["created_at"],
            "updated_at": job["updated_at"]
        },
        "rows": [
            {
                "platform": r["platform"],
                "version": r["version"],
                "service": r["service"],
                "max_logs": r["max_logs"],
                "status": r["status"],
                "error_message": r["error_message"],
                "validated_count": r["validated_count"],
                "non_validated_count": r["non_validated_count"],
                "sources_found": r["sources_found"],
                "product_name": r.get("product_name") or "",
                "error_code": r.get("error_code") or "",
                "excel_error_message": r.get("excel_error_message") or "",
                "reason": r.get("reason") or "",
                "source": r.get("source") or "",
                "error_message_long": r.get("error_message_long") or "",
                "category": r.get("category") or ""
            }
            for r in rows
        ]
    }

@app.get("/api/download-zip/{job_id}")
async def download_zip(job_id: str):
    job = db_manager.get_job(job_id)
    if not job or not job["zip_path"]:
        raise HTTPException(status_code=404, detail="ZIP archive not found or not yet generated.")
        
    zip_path = job["zip_path"]
    if not os.path.exists(zip_path):
        raise HTTPException(status_code=404, detail="ZIP file physical path does not exist on server.")
        
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename="logs.zip"
    )

@app.get("/api/dashboard-metrics")
async def get_dashboard_metrics():
    try:
        import datetime as dt
        day_ago = (dt.datetime.utcnow() - dt.timedelta(days=1)).isoformat()
        metrics = db_manager.get_dashboard_metrics_data(day_ago)
        return {
            "status": "success",
            "metrics": metrics
        }
    except Exception as e:
        logger.error(f"Error fetching dashboard metrics: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/", response_class=FileResponse)
async def get_root():
    index_path = frontend_path / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    else:
        raise HTTPException(status_code=404, detail="Frontend index.html not found")

from fastapi.responses import HTMLResponse
from fastapi import WebSocket, WebSocketDisconnect

@app.get("/dashboard", response_class=HTMLResponse)
async def get_dashboard():
    dashboard_path = Path(__file__).resolve().parent / "dashboard.html"
    if dashboard_path.exists():
        with open(dashboard_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    else:
        return HTMLResponse(content="<h1>Dashboard HTML File Not Found</h1><p>Expected at backend/dashboard.html</p>", status_code=404)

@app.get("/api/dashboard/stats")
async def get_dashboard_stats():
    try:
        return db_manager.get_dashboard_stats_data()
    except Exception as e:
        logger.error(f"Error fetching dashboard stats: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/dashboard/technologies")
async def get_dashboard_technologies():
    try:
        return db_manager.get_dashboard_technologies()
    except Exception as e:
        logger.error(f"Error fetching dashboard technologies: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/dashboard/domains")
async def get_dashboard_domains():
    try:
        return db_manager.get_dashboard_domains()
    except Exception as e:
        logger.error(f"Error fetching dashboard domains: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/dashboard/notifications")
async def get_dashboard_notifications(severity: Optional[str] = None):
    try:
        return db_manager.get_dashboard_notifications(severity)
    except Exception as e:
        logger.error(f"Error fetching dashboard notifications: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

class ControlRequest(BaseModel):
    action: str

@app.post("/api/dashboard/control")
async def control_agent(req: ControlRequest):
    action = req.action
    if action not in ["run_discovery", "run_health_check", "pause", "resume"]:
        raise HTTPException(status_code=400, detail="Invalid action")
    try:
        if action == "pause":
            db_manager.control_agent("pause")
        elif action == "resume":
            db_manager.control_agent("resume")
        elif action == "run_discovery":
            from backend import autonomous_agent
            db_manager.log_agent_event("command_executing", "Manual trigger: Log Discovery")
            autonomous_agent.update_agent_status_db(status="running", current_job="log_discovery")
            # Run discovery synchronously
            autonomous_agent.run_job_log_discovery()
            # Queue manual command and mark completed so dashboard queue updates correctly
            db_manager.control_agent(action)
            cmd = db_manager.get_next_pending_agent_command()
            if cmd:
                db_manager.complete_agent_command(cmd[0])
            autonomous_agent.set_agent_idle()
        elif action == "run_health_check":
            from backend import autonomous_agent
            db_manager.log_agent_event("command_executing", "Manual trigger: Health Check")
            autonomous_agent.update_agent_status_db(status="running", current_job="health_check")
            # Run health check synchronously
            autonomous_agent.run_job_repository_health_check()
            db_manager.control_agent(action)
            cmd = db_manager.get_next_pending_agent_command()
            if cmd:
                db_manager.complete_agent_command(cmd[0])
            autonomous_agent.set_agent_idle()
            
        return {"status": "success", "message": f"Action {action} handled successfully"}
    except Exception as e:
        logger.error(f"Error sending control action: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.api_route("/api/run-daily", methods=["GET", "POST"])
async def run_daily_endpoint():
    try:
        logger.info("Executing POST /api/run-daily - complete daily cycle")
        from backend import autonomous_agent
        from backend.notifications import process_notification_queue
        
        # Enforce Postgres readiness
        # Ensure database tables are created if not present
        db_manager.init_db()
        db_manager.init_repo_db()
        
        # 1. Recover stale locks
        autonomous_agent.recover_stale_locks()
        
        # 2. Check and report previous crash
        autonomous_agent.check_and_report_previous_crash()
        
        # 3. Technology Discovery
        autonomous_agent.run_job_technology_discovery()
        
        # 4. Log Discovery, Log Validation, Save to PostgreSQL
        autonomous_agent.run_job_log_discovery()
        
        # 5. Repository Health Check
        autonomous_agent.run_job_repository_health_check()
        
        # 6. Daily Report & Consolidation
        autonomous_agent.run_job_daily_summary_report()
        
        # 7. Process / Send Gmail Email Notifications
        process_notification_queue()
        
        return {"status": "success", "message": "One complete autonomous daily cycle executed successfully."}
    except Exception as e:
        logger.error(f"Error executing daily cycle: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/dashboard/test-email")
async def send_test_email_endpoint():
    try:
        from backend.notifications import send_email_notification
        import datetime
        subject = f"[Log Collector] Test Email Notification - {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}"
        content = "This is a real-time SMTP test email sent to verify production Gmail configuration."
        success = send_email_notification("test_email", subject, content)
        if success:
            return {"status": "success", "message": "Test email sent successfully via production SMTP"}
        else:
            raise HTTPException(status_code=500, detail="Failed to send test email. Check server log for SMTP error details.")
    except Exception as e:
        logger.error(f"Error sending test email: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.websocket("/api/agent-events/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    last_seen_id = 0
    try:
        if not db_manager.has_agent_event_feed_table():
            await websocket.send_json({"event_type": "error", "message": "agent_event_feed table not initialized"})
            return
            
        recent_events = db_manager.get_recent_agent_events(limit=50)
        
        # Send historical in chronological order
        for event in reversed(recent_events):
            await websocket.send_json(event)
            last_seen_id = max(last_seen_id, event["id"])
            
        # Polling for live events
        import asyncio
        while True:
            await asyncio.sleep(1.0)
            new_events = db_manager.get_agent_events_since(last_seen_id)
            for event in new_events:
                await websocket.send_json(event)
                last_seen_id = max(last_seen_id, event["id"])
                
    except WebSocketDisconnect:
        logger.info("Agent event websocket disconnected.")
    except Exception as e:
        logger.error(f"Error in agent event websocket: {e}", exc_info=True)

# Mount Static Frontend Files (if directory exists)
if frontend_path.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_path)), name="frontend")
else:
    logger.warning(f"Frontend directory not found at {frontend_path}. Static files not mounted.")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
