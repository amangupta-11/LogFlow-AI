import os
import json
import random
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv
from backend.extractor import get_gemini_model, get_openai_client


load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Fallback templates for local synthetic log generation
TEMPLATES = {
    "nginx": {
        "format": "{timestamp} [{severity}] {pid}#{tid}: *{connection_id} {message}",
        "messages": {
            "INFO": [
                "starting gunicorn work process",
                "gracefully shutting down workers",
                "resizing connection pool to 20",
                "SSL session cache configured successfully"
            ],
            "WARN": [
                "conflicting server name 'localhost' on 0.0.0.0:80, ignored",
                "low free disk space for temporary buffering path /var/cache/nginx",
                "open file cache limit 1000 exceeded, performance may be degraded",
                "a client request body buffering occurred"
            ],
            "ERROR": [
                "upstream timed out (110: Connection timed out) while connecting to backend server",
                "open() '/usr/share/nginx/html/favicon.ico' failed (2: No such file or directory)",
                "directory index of '/var/www/html/' is forbidden",
                "connect() to unix:/tmp/gunicorn.sock failed (111: Connection refused) while connecting to upstream"
            ],
            "CRITICAL": [
                "worker process {pid} exited on signal 11 (core dumped)",
                "master process crashed due to segmentation fault",
                "failed to bind to port 0.0.0.0:80 (98: Address already in use)",
                "critical failure reading config file: syntax error in line 42"
            ]
        }
    },
    "docker": {
        "format": "{timestamp} [docker] {severity} - {message}",
        "messages": {
            "INFO": [
                "Container started successfully with ID {uuid}",
                "Docker engine version {version} initialized",
                "Created bridge network interface docker0",
                "Layer cache pull complete for image: node:20-alpine"
            ],
            "WARN": [
                "Memory limit not specified. Defaulting to unlimited memory.",
                "Unable to find image 'ubuntu:latest' locally, pulling from registry",
                "IPv4 forwarding is disabled. Networking will be restricted.",
                "Read-only rootfs configured. Temporary writes may fail."
            ],
            "ERROR": [
                "failed to start container due to network bridge issue: port binding failed",
                "Error response from daemon: Container {uuid} is not running",
                "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?",
                "OCI runtime create failed: container_linux.go:380: starting container process caused 'permission denied'"
            ],
            "CRITICAL": [
                "Docker daemon crashed with signal SIGABRT (134)",
                "Storage driver 'overlay2' failed to mount disk: out of inodes",
                "Daemon startup failed: failed to initialize storage driver: devmapper: device already exists",
                "Kernel panic: Docker kernel namespace mapping corrupted"
            ]
        }
    },
    "kubernetes": {
        "format": "{timestamp} {severity} [kubelet] - {message}",
        "messages": {
            "INFO": [
                "Pod {pod} scheduled on node {node}",
                "Successfully pulled image {image}",
                "Container {container} started successfully",
                "Resource quota validated successfully for namespace default"
            ],
            "WARN": [
                "Liveness probe failed for container {container}, health check timed out",
                "Volume mount latency exceeded threshold: 2.4s",
                "Pod {pod} CPU limit exceeded, throttling applied",
                "Secret mount failed: kubernetes API server connection timed out, retrying"
            ],
            "ERROR": [
                "pod restarted automatically after health check failure",
                "Failed to pull image {image}: ImagePullBackOff - manifest not found",
                "Container {container} crashed: exit code 139 (segmentation fault)",
                "StatefulSet scheduler failed: replica allocation failed due to insufficient CPU"
            ],
            "CRITICAL": [
                "Node {node} status is NotReady: disk pressure detected",
                "Kubernetes API Master Node connection lost, cluster heartbeat timeout",
                "Etcd database corrupted: cluster state recovery failed",
                "CoreDNS service failed to boot, all pod resolution offline"
            ]
        }
    },
    "spring boot": {
        "format": "{timestamp} {severity} [{thread}] {logger_name} : {message}",
        "messages": {
            "INFO": [
                "Starting Application using Java {version}",
                "Tomcat initialized on port(s): 8080 (http)",
                "Exposing 15 endpoint(s) beneath technology path '/actuator'",
                "Completed initialization in 4210 ms"
            ],
            "WARN": [
                "No active profile set, falling back to default profiles: default",
                "JpaRepositories auto-configuration deferred due to slow connection pool initialization",
                "Deprecated config key 'server.servlet.session.timeout' replaced by 'server.reactive.session.timeout'",
                "Slow Query Warning: execution took 1250ms (threshold 1000ms)"
            ],
            "ERROR": [
                "Application run failed: Connection to database failed",
                "org.hibernate.exception.ConstraintViolationException: Could not execute statement",
                "Servlet.service() for servlet [dispatcherServlet] in context with path [] threw exception: Request processing failed",
                "Failed to deserialize request body: Invalid JSON format at line 1, column 24"
            ],
            "CRITICAL": [
                "Fatal error: JVM running out of heap memory (java.lang.OutOfMemoryError: Java heap space)",
                "HikariPool-1 - Connection is not available, request timed out after 30000ms",
                "Spring Boot Context failure: Configuration initialization aborted due to circular dependency",
                "Database Migration Failed: Liquibase migration check failed on checksum validation"
            ]
        }
    },
    "default": {
        "format": "{timestamp} [{severity}] {message}",
        "messages": {
            "INFO": [
                "Service started successfully",
                "Connected to message broker client",
                "Configuration reloaded without downtime",
                "Heartbeat signal sent to master node"
            ],
            "WARN": [
                "High resource usage detected, CPU usage at 85%",
                "Connection pool capacity at 90%",
                "Disk space check: 12% remaining on mount path",
                "API call latency warning: 2.3 seconds response time"
            ],
            "ERROR": [
                "Internal Server Error: processing request failed",
                "Failed to write changes to local configuration database",
                "Permission denied writing to filesystem path /var/log/app",
                "DNS lookup failed for external service api.provider.com"
            ],
            "CRITICAL": [
                "System service crash: unexpected EOF encountered in storage stream",
                "Fatal application panic: core configuration missing",
                "Security breach alert: 15 failed login attempts from IP 198.51.100.42",
                "Hardware failure: cluster node disconnection detected"
            ]
        }
    }
}

def generate_synthetic_logs_llm(platform: str, version: str, service: str, severity: str, count: int, scenario: str, start_date: str = None, end_date: str = None) -> list:
    """
    Attempts to use Gemini or OpenAI API to generate highly realistic, version-specific synthetic logs.
    """
    date_instruction = f"The generated log timestamps must fall within the range from {start_date} to {end_date} and be distributed realistically across this time period." if start_date and end_date else "Keep the timestamps close to each other (e.g. within a few seconds or minutes of each other)."
    prompt = f"""
Generate {count} highly realistic, synthetic system log entries for:
Platform: {platform}
Version: {version}
Service/Module: {service}

Details:
- Target Severity: {severity if severity != 'ALL' else 'a realistic mix of INFO, WARN, ERROR, and CRITICAL'}
- Specific Scenario / Error Type: {scenario if scenario else 'standard operational logs with realistic errors'}

Instructions:
1. Ensure the logs look exactly like real system logs generated by {platform} (version {version if version else 'latest'}).
2. The log formatting style (timestamps, layout, brackets, logging level style) must precisely match {platform}'s native formatting.
3. {date_instruction}
4. Return the result strictly as a JSON list of objects with the following keys:
   "timestamp", "severity", "message", "original_log", "source_url"
   For all synthetic logs, the "source_url" value MUST be exactly "synthetic".
5. Do not include markdown code block formatting (like ```json) in your final response. Return ONLY raw JSON.

Return a list of size {count}.
"""
    # 1. Try Gemini
    gemini_model = get_gemini_model()
    if gemini_model:
        try:
            logger.info("Using Gemini to generate synthetic logs...")
            response = gemini_model.generate_content(prompt)
            text = response.text.strip()
            if text.startswith("```json"):
                text = text.split("```json")[1].split("```")[0].strip()
            elif text.startswith("```"):
                text = text.split("```")[1].split("```")[0].strip()
            return json.loads(text)
        except Exception as e:
            logger.error(f"Gemini synthetic log generation failed: {e}")

    # 2. Try OpenAI
    openai_client = get_openai_client()
    if openai_client:
        try:
            logger.info("Using OpenAI to generate synthetic logs...")
            model_name = "gpt-4o-mini"
            if os.getenv("OPENAI_API_KEY", "").startswith("sk-or-v1-"):
                model_name = "openai/gpt-4o-mini"
            response = openai_client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=2000
            )
            text = response.choices[0].message.content.strip()
            if text.startswith("```json"):
                text = text.split("```json")[1].split("```")[0].strip()
            elif text.startswith("```"):
                text = text.split("```")[1].split("```")[0].strip()
            return json.loads(text)
        except Exception as e:
            logger.error(f"OpenAI synthetic log generation failed: {e}")

    return []

def generate_synthetic_logs_local(platform: str, version: str, service: str, severity: str, count: int, scenario: str, start_date: str = None, end_date: str = None) -> list:
    """
    Local template-based synthetic log generator if LLMs are not available.
    """
    logger.info("Falling back to local template log generator.")
    plat_key = platform.lower().strip()
    
    # Find closest template match
    tpl = TEMPLATES.get(plat_key)
    if not tpl:
        # Search by substring
        for k, v in TEMPLATES.items():
            if k in plat_key:
                tpl = v
                break
        if not tpl:
            tpl = TEMPLATES["default"]

    results = []
    
    start_dt = None
    end_dt = None
    if start_date:
        try:
            start_dt = datetime.fromisoformat(start_date.replace("Z", ""))
        except Exception:
            pass
    if end_date:
        try:
            end_dt = datetime.fromisoformat(end_date.replace("Z", ""))
        except Exception:
            pass

    if start_dt and end_dt:
        base_time = start_dt
        total_delta = (end_dt - start_dt).total_seconds()
        step = total_delta / count if count > 1 else total_delta
    else:
        base_time = datetime.now() - timedelta(minutes=count)
        step = None
    
    pids = [random.randint(1000, 9999) for _ in range(3)]
    tids = [random.randint(100, 999) for _ in range(5)]
    conn_ids = [random.randint(10000, 99999) for _ in range(10)]
    threads = ["main", "task-scheduler-1", "http-nio-8080-exec-2", "http-nio-8080-exec-5"]
    loggers = ["org.springframework.web.servlet.DispatcherServlet", "org.hibernate.SQL", "com.example.app.Service", "org.apache.catalina.core"]
    
    severities = ["INFO", "WARN", "ERROR", "CRITICAL"] if severity == "ALL" else [severity]
    
    for i in range(count):
        # Pick severity
        sev = random.choice(severities)
        
        # Pick message base
        msg_list = tpl["messages"].get(sev, TEMPLATES["default"]["messages"][sev])
        msg = random.choice(msg_list)
        
        # Dynamic replacements in templates
        msg = msg.replace("{pid}", str(random.choice(pids)))
        msg = msg.replace("{uuid}", f"{random.randint(1000,9999)}-{random.randint(1000,9999)}")
        msg = msg.replace("{version}", version if version else "1.0.0")
        msg = msg.replace("{pod}", f"{service if service else platform}-pod-{random.randint(100,999)}")
        msg = msg.replace("{node}", f"k8s-node-{random.randint(1,5)}")
        msg = msg.replace("{image}", f"{platform}:{version if version else 'latest'}")
        msg = msg.replace("{container}", service if service else platform)
        
        # Scenario override
        if scenario and sev in ["ERROR", "CRITICAL"] and i == count - 1:
            msg = f"Simulated Scenario Alert: {scenario}"
            
        if step is not None:
            timestamp_obj = base_time + timedelta(seconds=i * step)
        else:
            timestamp_obj = base_time + timedelta(seconds=i * random.randint(2, 15))
        # Format timestamps based on platform
        if plat_key == "nginx":
            timestamp = timestamp_obj.strftime("%Y/%m/%d %H:%M:%S")
        elif plat_key == "spring boot":
            timestamp = timestamp_obj.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        else:
            timestamp = timestamp_obj.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "+00:00"
            
        # Build original format log
        fmt = tpl["format"]
        original = fmt.format(
            timestamp=timestamp,
            severity=sev,
            pid=random.choice(pids),
            tid=random.choice(tids),
            connection_id=random.choice(conn_ids),
            thread=random.choice(threads),
            logger_name=random.choice(loggers),
            message=msg,
            version=version
        )
        
        results.append({
            "timestamp": timestamp,
            "severity": sev,
            "message": msg,
            "original_log": original,
            "source_url": "synthetic"
        })
        
    return results

def generate_synthetic_logs(platform: str, version: str = "", service: str = "", severity: str = "ALL", count: int = 5, scenario: str = "", start_date: str = None, end_date: str = None) -> list:
    """
    Main generator interface. Tries LLM first, falls back to rule templates.
    """
    if count is None:
        count = 5
    # 1. Try LLM first
    logs = generate_synthetic_logs_llm(platform, version, service, severity, count, scenario, start_date, end_date)
    if logs:
        return logs
        
    # 2. Fall back to local
    return generate_synthetic_logs_local(platform, version, service, severity, count, scenario, start_date, end_date)

if __name__ == "__main__":
    print(generate_synthetic_logs("nginx", "1.25", count=3))
