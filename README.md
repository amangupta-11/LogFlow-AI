# AI-Driven Log Collector & Diagnostic System

A platform-independent, production-ready system to automatically discover technologies, search for real-world log signatures, validate logs using state-of-the-art LLMs, ingest them into a vector/SQLite repository, and present logs through a premium web dashboard with autonomous agent scheduling and email notifications.

---

## 📂 Project Structure

The project has been cleaned up and structured as follows:

```text
log_collector_system/
├── backend/                  # Core application backend
│   ├── db/                   # Database schemas and initialization logic
│   ├── notifications/        # Live notification outputs and alert logs
│   ├── outputs/              # Active batch job processing results (ZIPs, reports)
│   ├── autonomous_agent.py   # Autonomous discovery/ingestion daemon & scheduler
│   ├── batch_processor.py    # Log processing, parsing, and excel generator
│   ├── crawler.py            # Deep web-search and URL crawler module
│   ├── db_manager.py         # SQLite connection wrapper & schema manager
│   ├── discovery_agent.py    # Logic for query formulation and technology prioritization
│   ├── extractor.py          # Multimodal LLM log extractor (Gemini/Claude/OpenAI)
│   ├── main.py               # FastAPI server entry point
│   ├── notifications.py      # Email (SMTP/Gmail) & Slack alerting service
│   ├── search_providers.py   # Search APIs (DuckDuckGo, Yahoo, AOL, Brave, Bing)
│   ├── validator.py          # Log validation logic using Claude/Gemini
│   └── vector_store.py       # In-memory numpy vector database for embeddings
├── frontend/                 # Premium web dashboard assets (HTML, CSS, JS)
├── tools/                    # Relocated verification, testing, and debugging utilities
│   ├── test_backend.py       # Main backend tests
│   ├── test_batch.py         # Batch collection & ingestion test suite
│   ├── check_scheduler.py    # Verification script for background schedules
│   └── ...                   # Additional developer diagnostic utilities
├── archive/                  # Archived historical batch job outputs and alert reports
├── .env.example              # Template configuration file for environment variables
├── .gitignore                # Python and workspace git ignores
├── requirements.txt          # Production runtime dependencies
└── README.md                 # Project documentation (This file)
```

---

## 🛠️ Installation & Setup

Ensure you have **Python 3.10+** installed on your system.

1. **Clone or locate the workspace directory**:
   ```bash
   cd log_collector_system
   ```

2. **Create and activate a Python virtual environment**:
   * **Windows (PowerShell)**:
     ```powershell
     python -m venv venv
     .\venv\Scripts\Activate.ps1
     ```
   * **Linux/macOS**:
     ```bash
     python3 -m venv venv
     source venv/bin/activate
     ```

3. **Install the runtime dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

---

## ⚙️ Environment Variables

The system relies on an `.env` file in the root directory for all runtime configuration. 

1. Copy the provided `.env.example` to create your own configuration:
   ```bash
   cp .env.example .env
   ```
2. Open `.env` and populate the required credentials:

| Variable | Description | Example |
| :--- | :--- | :--- |
| `GEMINI_API_KEY` | Google Gemini API Key (Log extraction & Crawler classification) | `AQ.Ab8...` |
| `OPENAI_API_KEY` | OpenAI API Key (or OpenRouter endpoint key) | `sk-or-v1-...` |
| `ANTHROPIC_API_KEY` | Anthropic Claude API Key (Log validation) | `sk-or-v1-...` |
| `SMTP_HOST` | Hostname of the mail server | `smtp.gmail.com` |
| `SMTP_PORT` | Port of the mail server | `587` |
| `SMTP_USER` | Gmail address sending reports | `your_gmail_address@gmail.com` |
| `SMTP_PASSWORD` | App-specific Password generated in Google settings | `abcd efgh ijkl mnop` |
| `EMAIL_TO` | Target recipient address for daily/incident notifications | `recipient_email@gmail.com` |
| `SQLITE_DB_PATH` | Path to the batch processing SQLite DB | `backend/db/jobs.db` |
| `REPO_DB_PATH` | Path to the validated logs repository SQLite DB | `backend/db/validated_logs.db` |

---

## 🚀 Starting the System

### 1. Launch the Dashboard (FastAPI Backend)

The web dashboard is served by FastAPI. Start the web server using Uvicorn:

```bash
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

Once running, open your web browser and navigate to:
* **Dashboard URL**: [http://127.0.0.1:8000](http://127.0.0.1:8000)
* **API Documentation**: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

### 2. Launch the Autonomous Agent & Scheduler

The autonomous agent runs in the background to automatically prioritize technologies, formulate search queries, crawl forums/GitHub for log signatures, validate them, and trigger daily alerts/email updates.

Start the agent using:

```bash
python backend/autonomous_agent.py
```

* This starts the persistent background scheduler that coordinates periodic cycles and daily email alerts.
* You can tune the cycle size and request rates using variables in the `.env` file (e.g. `MAX_TECHNOLOGIES_PER_CYCLE`, `MAX_QUERIES_PER_TECHNOLOGY`).
