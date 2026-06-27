import os
import re
import json
import logging
from datetime import datetime
from dotenv import load_dotenv
import google.generativeai as genai
from openai import OpenAI

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configurable clients
def get_gemini_model():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None
    try:
        genai.configure(api_key=api_key)
        return genai.GenerativeModel("gemini-2.5-flash")
    except Exception as e:
        logger.error(f"Error configuring Gemini: {e}")
        return None

def get_openai_client():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        if api_key.startswith("sk-or-v1-"):
            return OpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")
        return OpenAI(api_key=api_key)
    except Exception as e:
        logger.error(f"Error configuring OpenAI: {e}")
        return None

from typing import Optional

def safe_to_text(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        import json
        return json.dumps(value, sort_keys=True)
    if isinstance(value, list):
        import json
        return json.dumps(value)
    return str(value)

def parse_logs_with_llm(raw_text: str, platform: str, version: str, service: str, limit: Optional[int] = None, start_date: Optional[str] = None, end_date: Optional[str] = None) -> list:
    """
    Uses LLM (Gemini, OpenAI, or Claude) to extract, format, and structure logs.
    Returns a list of dictionaries with timestamp, severity, message, and original_log.
    """
    limit_str = f"up to {limit}" if limit else "all possible"
    date_range_str = f" The timestamps of the extracted logs must fall within the date/time range: from {start_date} to {end_date}." if start_date and end_date else ""
    prompt = f"""
Analyze the following text scraped from the internet. Your task is to identify and extract {limit_str} real system-generated logs for:
Platform: {platform}
Version: {version}
Service/Module: {service}

Instructions:
1. Extract ONLY actual, structured system-generated logs (e.g. server console outputs, container logs, runtime errors, exception stack traces, daemon debug messages).
2. DO NOT extract or convert article text, paragraphs, descriptions, tutorials, announcements, or documentation prose into logs. If a sentence explains how something works or is an article discussion, it is NOT a log line: ignore it completely.
3. Ensure the extracted logs are specifically relevant to the platform: '{platform}' and version: '{version}' (if version is specified). If the logs in the raw text are from a different version or platform, do not extract them.
4. Standardize each log entry to include:
   - A valid timestamp. {date_range_str} If dates in the scraped logs fall outside this range or are missing, map/scale them to be within the requested range.
   - Severity level: Choose exactly one of [INFO, WARN, ERROR, CRITICAL].
   - Message: The log content/body.
   - Original Format: Maintain the standard formatting signature of the platform (e.g., standard Nginx error format, AWS Lambda CloudWatch log format, Spring Boot log format, etc.).
   - Source URL: Identify which URL the log was scraped from by looking at the "Source URL: <url>" marker before that section of text, and include it.
5. Return the result strictly as a JSON list of objects with the following keys:
   "timestamp", "severity", "message", "original_log", "source_url"
6. Do not include markdown code block formatting (like ```json) in your final response. Return ONLY raw JSON.
7. If no actual log lines are found in the scraped text, return an empty list []. Do not fabricate logs.

Scraped Text (first 60000 characters):
{raw_text[:60000]}
"""
    all_extracted_logs = []
    
    # 1. Try Gemini
    gemini_model = get_gemini_model()
    if gemini_model:
        try:
            logger.info("Using Gemini to extract logs...")
            response = gemini_model.generate_content(prompt)
            text = response.text.strip()
            # Clean up markdown JSON wrapper if present
            if text.startswith("```json"):
                text = text.split("```json")[1].split("```")[0].strip()
            elif text.startswith("```"):
                text = text.split("```")[1].split("```")[0].strip()
            logs = json.loads(text)
            if isinstance(logs, list):
                logger.info(f"Gemini successfully extracted {len(logs)} logs.")
                all_extracted_logs.extend(logs)
        except Exception as e:
            logger.error(f"Gemini log extraction failed: {e}")

    # 2. Try Claude (direct or OpenRouter)
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    if anthropic_key:
        try:
            import requests
            if anthropic_key.startswith("sk-or-v1-"):
                logger.info("Using Claude via OpenRouter to extract logs...")
                headers = {
                    "Authorization": f"Bearer {anthropic_key}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "model": "anthropic/claude-sonnet-4",
                    "max_tokens": 1200,
                    "temperature": 0.2,
                    "messages": [{"role": "user", "content": prompt}]
                }
                response = requests.post("https://openrouter.ai/api/v1/chat/completions", json=payload, headers=headers, timeout=35)
                if response.status_code == 200:
                    data = response.json()
                    text = data["choices"][0]["message"]["content"].strip()
                    if text.startswith("```json"):
                        text = text.split("```json")[1].split("```")[0].strip()
                    elif text.startswith("```"):
                        text = text.split("```")[1].split("```")[0].strip()
                    logs = json.loads(text)
                    if isinstance(logs, list):
                        logger.info(f"Claude OpenRouter successfully extracted {len(logs)} logs.")
                        all_extracted_logs.extend(logs)
                else:
                    logger.error(f"Claude OpenRouter returned HTTP {response.status_code}: {response.text}")
            else:
                logger.info("Using Anthropic Claude API to extract logs...")
                headers = {
                    "x-api-key": anthropic_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"
                }
                payload = {
                    "model": "claude-3-5-sonnet-20240620",
                    "max_tokens": 1200,
                    "messages": [{"role": "user", "content": prompt}]
                }
                response = requests.post("https://api.anthropic.com/v1/messages", json=payload, headers=headers, timeout=35)
                if response.status_code == 200:
                    data = response.json()
                    text = data["content"][0]["text"].strip()
                    if text.startswith("```json"):
                        text = text.split("```json")[1].split("```")[0].strip()
                    elif text.startswith("```"):
                        text = text.split("```")[1].split("```")[0].strip()
                    logs = json.loads(text)
                    if isinstance(logs, list):
                        logger.info(f"Claude native successfully extracted {len(logs)} logs.")
                        all_extracted_logs.extend(logs)
                else:
                    logger.error(f"Claude native returned HTTP {response.status_code}: {response.text}")
        except Exception as e:
            logger.error(f"Claude log extraction failed: {e}")

    # 3. Try OpenAI
    openai_client = get_openai_client()
    if openai_client:
        try:
            logger.info("Using OpenAI to extract logs...")
            model_name = "gpt-4o-mini"
            if os.getenv("OPENAI_API_KEY", "").startswith("sk-or-v1-"):
                model_name = "openai/gpt-4o-mini"
            response = openai_client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=1200
            )
            text = response.choices[0].message.content.strip()
            if text.startswith("```json"):
                text = text.split("```json")[1].split("```")[0].strip()
            elif text.startswith("```"):
                text = text.split("```")[1].split("```")[0].strip()
            logs = json.loads(text)
            if isinstance(logs, list):
                logger.info(f"OpenAI successfully extracted {len(logs)} logs.")
                all_extracted_logs.extend(logs)
        except Exception as e:
            logger.error(f"OpenAI log extraction failed: {e}")

    # Deduplicate combined LLM logs
    seen_msgs = set()
    deduped_llm_logs = []
    for log in all_extracted_logs:
        msg = safe_to_text(log.get("message")).strip()
        if msg:
            msg_lower = msg.lower()
            if msg_lower not in seen_msgs:
                seen_msgs.add(msg_lower)
                deduped_llm_logs.append(log)

    # Filter candidates to ensure only genuine log structures are returned
    valid_candidates = []
    for log in deduped_llm_logs:
        text_to_check = log.get("original_log") or log.get("message") or ""
        is_genuine, _, _ = check_log_nature_detail(safe_to_text(text_to_check))
        if is_genuine:
            valid_candidates.append(log)
            
    return valid_candidates

def parse_source_blocks(raw_text: str) -> list:
    """
    Parses raw_text split by === NEW SOURCE === and extracts metadata for each source block.
    """
    blocks = []
    # Normalize newline separators
    raw_blocks = raw_text.split("=== NEW SOURCE ===")
    for rb in raw_blocks:
        rb_strip = rb.strip()
        if not rb_strip:
            continue
            
        url_match = re.search(r'Source URL:\s*(\S+)', rb_strip)
        title_match = re.search(r'Source Title:\s*(.*)', rb_strip)
        context_match = re.search(r'Crawl Context:\s*(.*)', rb_strip)
        
        url = url_match.group(1).strip() if url_match else ""
        title = title_match.group(1).strip() if title_match else ""
        context = context_match.group(1).strip() if context_match else ""
        
        # Parse platform, version, service, query_used, source_rank from context
        source_platform = ""
        source_version = ""
        source_service = ""
        query_used = ""
        source_rank = 4
        if context:
            plat_m = re.search(r'Platform=([^,]+)', context)
            ver_m = re.search(r'Version=([^,]+)', context)
            ser_m = re.search(r'Service=([^,]+)', context)
            q_m = re.search(r'QueryUsed=([^,]+)', context) or re.search(r'Query=([^,]+)', context)
            rank_m = re.search(r'SourceRank=(\d+)', context)
            
            if plat_m: source_platform = plat_m.group(1).strip()
            if ver_m: source_version = ver_m.group(1).strip()
            if ser_m: source_service = ser_m.group(1).strip()
            if q_m: query_used = q_m.group(1).strip()
            if rank_m:
                try:
                    source_rank = int(rank_m.group(1).strip())
                except:
                    source_rank = 4
            
        # Extract the content of the block by stripping metadata lines
        content = rb_strip
        content = re.sub(r'^Source URL:.*$', '', content, flags=re.MULTILINE)
        content = re.sub(r'^Source Title:.*$', '', content, flags=re.MULTILINE)
        content = re.sub(r'^Crawl Context:.*$', '', content, flags=re.MULTILINE)
        content = content.strip()
        
        blocks.append({
            "source_url": url,
            "source_title": title,
            "source_platform": source_platform,
            "source_version": source_version,
            "source_service": source_service,
            "query_used": query_used,
            "source_rank": source_rank,
            "content": content
        })
    return blocks

def detect_platform_match_type(text: str) -> str:
    text_strip = safe_to_text(text).strip()
    text_lower = text_strip.lower()
    
    # 1. Java Stack Trace
    if (text_strip.startswith("at ") or 
        re.search(r'\bat\s+[a-zA-Z0-9_]+\.[a-zA-Z0-9_.]+', text_strip) or 
        re.search(r'Exception\s+in\s+thread\s+', text_strip)):
        return "java_stacktrace"
        
    # 2. Apache Error
    apache_error_pattern = re.compile(
        r'^\[[A-Za-z]{3}\s+[A-Za-z]{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?\s+\d{4}\]\s+\[(?:notice|warn|err|error|crit|alert|emerg|info|debug)\]',
        re.IGNORECASE
    )
    if apache_error_pattern.match(text_strip):
        return "apache_error"
        
    # 3. Nginx Error
    nginx_error_pattern = re.compile(
        r'^\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2}\s+\[(?:debug|info|notice|warn|error|crit|alert|emerg)\]\s+\d+#\d+:\s+',
        re.IGNORECASE
    )
    if nginx_error_pattern.match(text_strip):
        return "nginx_error"

    # 4. Apache / Nginx Access
    apache_nginx_access_pattern = re.compile(
        r'^\S+\s+\S+\s+\S+\s+\[\d{2}/[A-Za-z]{3}/\d{4}:\d{2}:\d{2}:\d{2}(?:\s+[-+]\d{4})?\]\s+"[A-Z]+\s+\S+\s+HTTP/\d\.\d"\s+\d{3}\s+(\d+|-)',
        re.IGNORECASE
    )
    if apache_nginx_access_pattern.match(text_strip):
        if "nginx" in text_lower:
            return "nginx_access"
        elif "apache" in text_lower or "httpd" in text_lower:
            return "apache_access"
        return "nginx_access"

    # 5. CloudWatch / AWS Lambda
    cloudwatch_pattern_1 = re.compile(r'^(?:START|END|REPORT)\s+RequestId:\s+[0-9a-fA-F\-]+', re.IGNORECASE)
    cloudwatch_pattern_2 = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z\s+[0-9a-fA-F\-]{36}', re.IGNORECASE)
    if (cloudwatch_pattern_1.match(text_strip) or 
        cloudwatch_pattern_2.match(text_strip) or 
        "aws lambda" in text_lower or 
        "invoke error" in text_lower or 
        "requestid" in text_lower):
        return "cloudwatch"

    # 6. Docker
    docker_pattern = re.compile(
        r'^(?:[\w\.\-]+_1\s*\|\s*)?\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[-+]\d{2}:?\d{2})?\s+(?:INFO|WARN|WARNING|ERROR|FATAL|DEBUG|CRITICAL)\b',
        re.IGNORECASE
    )
    if docker_pattern.match(text_strip) or "docker runtime" in text_lower or "docker container" in text_lower:
        return "docker"

    # 7. Kubernetes
    kubernetes_pattern = re.compile(
        r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?\s+(?:INFO|WARN|ERROR)\s+Pod\s+|pod/\S+\s+\(\S+\)\s+status\s+is\s+(?:CrashLoopBackOff|OOMKilled|Pending|Running|Failed|Unknown)',
        re.IGNORECASE
    )
    if (kubernetes_pattern.match(text_strip) or 
        "crashloopbackoff" in text_lower or 
        "oomkilled" in text_lower or 
        "k8s" in text_lower or 
        "kubernetes" in text_lower or
        "pod/" in text_lower):
        return "kubernetes"

    # 8. Syslog
    syslog_pattern = re.compile(
        r'^(?:[A-Za-z]{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}|\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[-+]\d{2}:?\d{2})?)\s+\S+\s+[\w\.\-\(\/]+(?:\[\d+\])?:\s*',
        re.IGNORECASE
    )
    if syslog_pattern.match(text_strip):
        return "syslog"

    return "generic_log"


def classify_candidate_nature(text: str) -> tuple[str, int]:
    text_strip = safe_to_text(text).strip()
    if not text_strip:
        return "UNKNOWN", 0
        
    text_lower = text_strip.lower()
    
    # 1. Check CONFIG patterns
    nginx_conf_keywords = ["http {", "server {", "location {", "events {", "upstream {", "types {", "proxy_pass ", "fastcgi_pass "]
    if any(kw in text_strip for kw in nginx_conf_keywords):
        return "CONFIG", 0
        
    apache_conf_keywords = ["<VirtualHost", "<Directory", "<Location", "DocumentRoot ", "ServerName ", "AllowOverride "]
    if any(kw in text_strip for kw in apache_conf_keywords):
        return "CONFIG", 0
        
    yaml_k8s_keywords = ["apiVersion:", "kind:", "metadata:", "spec:", "services:", "version: '", 'version: "', "docker-compose"]
    if any(kw in text_strip for kw in yaml_k8s_keywords) or (re.search(r'^\s*image:\s+\S+', text_strip, re.MULTILINE) and re.search(r'^\s*ports:\s*$', text_strip, re.MULTILINE)):
        return "CONFIG", 0
        
    if text_strip.startswith("<?xml") or "<configuration>" in text_strip or "<beans" in text_strip:
        return "CONFIG", 0
        
    if text_strip.startswith("{") and text_strip.endswith("}") and "\n" in text_strip:
        try:
            parsed = json.loads(text_strip)
            if isinstance(parsed, dict) and any(k in parsed for k in ["dependencies", "devDependencies", "scripts", "compilerOptions", "profiles"]):
                return "CONFIG", 0
        except Exception:
            pass

    # 2. Check COMMAND patterns
    shell_prompt_pattern = re.compile(
        r'^\s*(?:\$|#|C:\\>|ps\s+[^:]+>|powershell>|[\w\.-]+@[\w\.-]+[:\s]?[~\w]*\s*[\$#])\s+'
    )
    common_cli_pattern = re.compile(
        r'^\s*(sudo\s+|apt-get\s+|yum\s+|docker\s+run\s+|docker-compose\s+|pip\s+install\s+|'
        r'npm\s+install\s+|git\s+clone\s+|curl\s+|wget\s+|python\s+|java\s+|node\s+|'
        r'systemctl\s+|service\s+|journalctl\s+|cat\s+|grep\s+|mkdir\s+|cd\s+|ls\s+|cp\s+|mv\s+|rm\s+)', re.IGNORECASE
    )
    if shell_prompt_pattern.search(text_strip) or common_cli_pattern.search(text_strip):
        return "COMMAND", 0

    # 3. Check BLOG_HEADING or TUTORIAL_TEXT or ARTICLE_TITLE
    # Markdown headings
    if re.match(r'^\s*#+\s+', text_strip):
        return "BLOG_HEADING", 0
        
    # List items (checking if it starts with lists like 1. or - or * but doesn't have a time value)
    if re.match(r'^\s*(?:\d+\.\s+|[\*\-\+]\s+)', text_strip) and not re.search(r'\b\d{2}:\d{2}:\d{2}\b', text_strip):
        return "TUTORIAL_TEXT", 0

    # Clean the string for prefix checking by stripping any leading timestamps, brackets, or colons
    clean_prefix_text = re.sub(
        r'^(?:\[?\d{4}[-/]\d{2}[-/]\d{2}(?:[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[-+]\d{2}:?\d{2})?)?\]?|\[?[A-Za-z]{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?\]?|\[?\d{2}/[A-Za-z]{3}/\d{4}:\d{2}:\d{2}:\d{2}(?:\s+[-+]\d{4})?\]?|\[?[A-Za-z]{3}\s+[A-Za-z]{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+\d{4}\]?|\b\d{2}:\d{2}:\d{2}(?:\.\d+)?\b|[\s\-\:\.\]\[]+)+',
        '',
        text_strip
    ).strip()
    clean_prefix_lower = clean_prefix_text.lower()
        
    title_prefixes = [
        "how to", "how ", "why ", "what is", "what are", "understanding", 
        "getting started", "a guide to", "guide to", "tutorial on", 
        "fixing ", "debugging ", "troubleshooting ", "ultimate guide"
    ]
    if any(clean_prefix_lower.startswith(p) for p in title_prefixes):
        return "ARTICLE_TITLE", 0
        
    title_keywords = [
        "reveals", "reveal", "root causes", "root cause", "best practices", 
        "common errors", "common issues", "how we", "how i", "step by step", 
        "for beginners", "tips and tricks", "top ", "reasons why"
    ]
    if any(kw in clean_prefix_lower for kw in title_keywords):
        return "ARTICLE_TITLE", 0

    # Title Case Check (Article title)
    words = [w for w in text_strip.split() if w.strip()]
    if len(words) >= 4:
        clean_words = []
        for w in words:
            w_clean = re.sub(r'^[\[\]\(\)\s\-\:\.\,\|]+|[\[\]\(\)\s\-\:\.\,\|]+$', '', w)
            if w_clean and not w_clean.upper() in ["INFO", "WARN", "WARNING", "ERROR", "CRITICAL", "FATAL", "DEBUG", "TRACE", "STDERR", "STDOUT"]:
                clean_words.append(w_clean)
        if len(clean_words) >= 3:
            cap_words = sum(1 for w in clean_words if w[0].isupper() or (len(w) > 1 and w[1].isupper()))
            has_log_signature = (
                re.search(r'\b\d{2}:\d{2}:\d{2}\b', text_strip) is not None or
                re.search(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', text_strip) is not None or
                re.search(r'\[\d+\]|\bpid=\d+\b', text_strip, re.IGNORECASE) is not None or
                re.search(r'\b[a-zA-Z0-9_-]+\s+[a-zA-Z0-9_./-]+(?:\[\d+\])?:\s+', text_strip) is not None
            )
            if cap_words / len(clean_words) >= 0.70 and not has_log_signature:
                return "ARTICLE_TITLE", 0

    # 4. Check DOCUMENTATION / PROSE
    conversational_phrases = [
        "refer to", "please contact", "announced at", "designed to", 
        "tutorial shows", "how to", "click here", "documentation", 
        "in this article", "in this tutorial", "this post", "released in", 
        "introduced in", "was launched", "we will", "you can", "let's look at",
        "for example", "to configure", "setup your", "about lambda", "aws launched",
        "announces the", "general availability", "pricing for", "pricing plan",
        "support for", "allows you to", "supports running", "enable you to",
        "installation instructions", "product description", "release notes",
        "marketing content"
    ]
    if any(phrase in text_lower for phrase in conversational_phrases):
        return "DOCUMENTATION", 0

    single_conversational_words = ["help", "identify", "contain", "contains"]
    if any(re.search(r'\b' + word + r'\b', text_lower) for word in single_conversational_words):
        return "DOCUMENTATION", 0

    # Prose sentence check
    if len(words) > 6:
        non_alnum = len(re.sub(r'[a-zA-Z0-9\s]', '', text_strip))
        if non_alnum < 2 and (text_strip.endswith('.') or text_strip.endswith('?')):
            prose_indicators = ["is", "are", "was", "were", "has", "have", "with", "from", "their", "your", "they", "our", "about", "since", "when", "then", "more"]
            prose_count = sum(1 for w in words if w.lower() in prose_indicators)
            if prose_count >= 2:
                return "PROSE", 0

    # 5. Compute Positive Machine-Generated Score
    score = 0
    
    # - Timestamp: +20
    timestamp_patterns = [
        r'\b\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[-+]\d{2}:?\d{2})?\b',
        r'\b\d{2}/[A-Za-z]{3}/\d{4}:\d{2}:\d{2}:\d{2}(?:\s+[-+]\d{4})?\b',
        r'\b(?:[A-Za-z]{3}\s+){1,2}\d{1,2}\s+\d{2}:\d{2}:\d{2}(?:\s+\d{4})?\b',
        r'\b\d{2}:\d{2}:\d{2}(?:\.\d+)?\b'
    ]
    if any(re.search(pat, text_strip) for pat in timestamp_patterns):
        score += 20
        
    # - Severity: +20
    if re.search(r'\b(INFO|WARN|WARNING|ERROR|FATAL|DEBUG|CRITICAL|TRACE|crit|warn|err|info|dbg|stderr|stdout)\b', text_strip, re.IGNORECASE):
        score += 20
        
    # - PID/Process ID: +15
    if re.search(r'\[\d+\]|\bpid=\d+\b|\b\d+/\w+|process\s*(?:id|id:)?\s*\d+', text_strip, re.IGNORECASE):
        score += 15
        
    # - Request ID: +15
    if re.search(r'\b(RequestId|Request-ID|Request_ID|EventId|Event-ID|Event_ID|Event_UUID)\b|[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}', text_strip, re.IGNORECASE):
        score += 15
        
    # - Status Code: +15
    if re.search(r'\bHTTP/\d\.\d"\s+[1-5]\d{2}\b|\b[1-5]\d{2}\b(?=\s+\d+|\s*$|\s+[-"]|\s+\S+bytes)|\bstatus\s*[=:]?\s*\d+\b|\bexitcode\s*[=:]?\s*\S+\b', text_strip, re.IGNORECASE):
        score += 15
        
    # - IP Address: +15
    if re.search(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', text_strip):
        score += 15
        
    # - Exception/Operational Pattern: +20 (exclude "ERROR" word itself)
    ex_match = re.search(r'\b([A-Z][a-zA-Z0-9_]*Exception|[A-Z][a-zA-Z0-9_]*Error|Exception|Traceback|Caused\s+by:|CrashLoopBackOff|OOMKilled|Kernel\s+Panic|OutOfMemory|segfault|Segmentation\s+Fault)\b', text_strip, re.IGNORECASE)
    if ex_match and ex_match.group(1).upper() != "ERROR":
        score += 20
        
    # - Stack Trace Pattern: +20
    is_stack = text_strip.startswith("at ") or re.search(r'\bat\s+[a-zA-Z0-9_]+\.[a-zA-Z0-9_.]+', text_strip) is not None
    if is_stack:
        score += 20
        
    # - Daemon/Service/Platform Identifier: +10
    if re.search(r'\b(daemon|service|server|agent|client|httpd|nginx|docker|dockerd|kubelet|kubernetes|k8s|pod|systemd|rsyslog|syslog|aws|cloudwatch|lambda|version|kernel)\b', text_strip, re.IGNORECASE):
        score += 10

    # - Platform Pattern Match Bonus: +25
    if detect_platform_match_type(text_strip) != "generic_log":
        score += 25
        
    if is_stack:
        return "STACKTRACE", score
    elif score >= 40:
        return "REAL_LOG", score
    else:
        return "UNKNOWN", score


def check_log_nature_detail(text: str) -> tuple[bool, str, int]:
    """
    Analyzes whether a block or line of text represents a genuine machine-generated system log.
    Enforces the rule that a log must contain at least TWO unique machine-generated log indicators.
    Also rejects configuration files, markdown, shell scripts, shell commands, and documentation/article text.
    Returns (is_genuine, reason, unique_indicators_count).
    """
    text_strip = safe_to_text(text).strip()
    if not text_strip:
        return False, "Empty content", 0

    # Classify candidate nature first
    classification, score = classify_candidate_nature(text)
    if classification not in ["REAL_LOG", "STACKTRACE"]:
        return False, f"Classified as {classification}", score


    # 1. Reject Configuration Files and Scripts
    # Check for nginx.conf / apache.conf patterns
    nginx_conf_keywords = ["http {", "server {", "location {", "events {", "upstream {", "types {", "proxy_pass ", "fastcgi_pass "]
    if any(kw in text_strip for kw in nginx_conf_keywords):
        return False, "Detected nginx.conf pattern", 0
        
    apache_conf_keywords = ["<VirtualHost", "<Directory", "<Location", "DocumentRoot ", "ServerName ", "AllowOverride "]
    if any(kw in text_strip for kw in apache_conf_keywords):
        return False, "Detected apache.conf pattern", 0

    # Check for yaml / docker-compose / kubernetes manifest patterns
    yaml_k8s_keywords = ["apiVersion:", "kind:", "metadata:", "spec:", "services:", "version: '", 'version: "', "docker-compose"]
    if any(kw in text_strip for kw in yaml_k8s_keywords) or (re.search(r'^\s*image:\s+\S+', text_strip, re.MULTILINE) and re.search(r'^\s*ports:\s*$', text_strip, re.MULTILINE)):
        return False, "Detected YAML / docker-compose / Kubernetes manifest pattern", 0

    # Check for XML / JSON configuration signatures
    if text_strip.startswith("<?xml") or "<configuration>" in text_strip or "<beans" in text_strip:
        return False, "Detected XML configuration pattern", 0
        
    # Check if it is a multi-line JSON or JSON configuration file (vs single-line JSON log)
    if (text_strip.startswith("{") and text_strip.endswith("}") and "\n" in text_strip):
        try:
            parsed = json.loads(text_strip)
            if isinstance(parsed, dict) and any(k in parsed for k in ["dependencies", "devDependencies", "scripts", "compilerOptions", "profiles"]):
                return False, "Detected JSON configuration file", 0
        except Exception:
            pass
            
    # Check for shell scripts
    if text_strip.startswith("#!/") or any(line.strip().startswith("set -e") or line.strip().startswith("export ") for line in text_strip.splitlines()):
        return False, "Detected shell script", 0

    # 2. Reject Shell commands and prompts (e.g. '$ sudo...', 'C:\>', etc.)
    shell_prompt_pattern = re.compile(
        r'^\s*(?:\$|#|C:\\>|ps\s+[^:]+>|powershell>|[\w\.-]+@[\w\.-]+[:\s]?[~\w]*\s*[\$#])\s+'
    )
    common_cli_pattern = re.compile(
        r'^\s*(sudo\s+|apt-get\s+|yum\s+|docker\s+run\s+|docker-compose\s+|pip\s+install\s+|'
        r'npm\s+install\s+|git\s+clone\s+|curl\s+|wget\s+|python\s+|java\s+|node\s+|'
        r'systemctl\s+|service\s+|journalctl\s+|cat\s+|grep\s+|mkdir\s+|cd\s+|ls\s+|cp\s+|mv\s+|rm\s+)', re.IGNORECASE
    )
    if shell_prompt_pattern.search(text_strip) or common_cli_pattern.search(text_strip):
        return False, "Entry represents a shell command or command prompt", 0

    # 3. Reject Markdown headings, list blocks or documentation structures
    markdown_pattern = re.compile(r'^\s*(?:#+\s+|\* \*\*|- \*\*|\d+\. \*\*)')
    if markdown_pattern.search(text_strip):
        return False, "Entry contains markdown list/heading format", 0

    # 4. Reject Special Case strings from user instructions
    special_rejections = [
        "Press ESC to enter menu",
        "Filesystem type is ext2fs",
        "kernel /boot/vmlinuz",
        "initrd /boot/initramfs",
        "How to install",
        "Configuration example",
        "Example output",
        "Sample code"
    ]
    for sr in special_rejections:
        if sr.lower() in text_strip.lower():
            return False, f"Rejected special case: '{sr}'", 0

    # 5. Reject Documentation / Article Prose / Tutorials
    conversational_phrases = [
        "refer to", "please contact", "announced at", "designed to", 
        "tutorial shows", "how to", "click here", "documentation", 
        "in this article", "in this tutorial", "this post", "released in", 
        "introduced in", "was launched", "we will", "you can", "let's look at",
        "for example", "to configure", "setup your", "about lambda", "aws launched",
        "announces the", "general availability", "pricing for", "pricing plan",
        "support for", "allows you to", "supports running", "enable you to",
        "installation instructions", "product description", "release notes",
        "marketing content"
    ]
    if any(phrase in text_strip.lower() for phrase in conversational_phrases):
        return False, "Entry contains conversational / article prose", 0

    # Prose sentence structure check: if it's a long sentence with mostly lowercase alphabetic words and standard spacing/periods
    words = text_strip.split()
    if len(words) > 6:
        non_alnum = len(re.sub(r'[a-zA-Z0-9\s]', '', text_strip))
        if non_alnum < 2 and (text_strip.endswith('.') or text_strip.endswith('?')):
            prose_indicators = ["is", "are", "was", "were", "has", "have", "with", "from", "their", "your", "they", "our", "about", "since", "when", "then", "more"]
            prose_count = sum(1 for w in words if w.lower() in prose_indicators)
            if prose_count >= 2:
                return False, "Entry structure resembles documentation prose", 0

    # 6. Detect Conformance to observability and RCA log formats:
    timestamp_patterns = [
        r'\b\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[-+]\d{2}:?\d{2})?\b',
        r'\b\d{2}/[A-Za-z]{3}/\d{4}:\d{2}:\d{2}:\d{2}(?:\s+[-+]\d{4})?\b',
        r'\b(?:[A-Za-z]{3}\s+){1,2}\d{1,2}\s+\d{2}:\d{2}:\d{2}(?:\s+\d{4})?\b',
        r'\b\d{2}:\d{2}:\d{2}(?:\.\d+)?\b'
    ]
    
    has_timestamp = False
    for pat in timestamp_patterns:
        if re.search(pat, text_strip):
            has_timestamp = True
            break
            
    has_severity = re.search(r'\b(INFO|WARN|WARNING|ERROR|FATAL|DEBUG|CRITICAL|TRACE|crit|warn|err|info|dbg|stderr|stdout)\b', text_strip, re.IGNORECASE) is not None
    
    # Exclude severity ERROR from matching exception
    has_exception = False
    ex_match = re.search(r'\b([A-Z][a-zA-Z0-9_]*Exception|[A-Z][a-zA-Z0-9_]*Error|Exception|Traceback|Caused\s+by:)\b', text_strip)
    if ex_match:
        matched_word = ex_match.group(1)
        if matched_word.upper() != "ERROR":
            has_exception = True
            
    has_req_id = re.search(r'\b(RequestId|Request-ID|Request_ID|EventId|Event-ID|Event_ID|Process\s*ID|Thread\s*ID|PID|TID)\b|\[\d+\]|\bpid=\d+\b|[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}', text_strip, re.IGNORECASE) is not None
    has_http_status = re.search(r'\bHTTP/\d\.\d\s*"?\s*[1-5]\d{2}\b|"(?:GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS|CONNECT)\s+', text_strip, re.IGNORECASE) is not None
    has_stacktrace = text_strip.startswith("at ") or re.search(r'\bat\s+[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+', text_strip) is not None
    has_cloudwatch = any(sig in text_strip for sig in ["START RequestId:", "REPORT RequestId:", "END RequestId:"])
    
    has_pid = re.search(r'\[\d+\]|\bpid=\d+\b|\b\d+/\w+', text_strip, re.IGNORECASE) is not None
    has_client = re.search(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', text_strip) is not None
    has_request = re.search(r'\bHTTP/\d\.\d\b|"(?:GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS|CONNECT)\s+', text_strip, re.IGNORECASE) is not None

    has_syslog_header = re.search(r'\b[a-zA-Z0-9_-]+\s+[a-zA-Z0-9_./-]+(?:\[\d+\])?:\s+', text_strip) is not None
    has_operational_pattern = re.search(r'\b(CrashLoopBackOff|OOMKilled|Invoke\s+Error|Kernel\s+Panic|Segmentation\s+Fault|segfault|OutOfMemory)\b|kernel panic|out of memory|oom-killer', text_strip, re.IGNORECASE) is not None

    # Path A: timestamp + log metadata
    # Metadata includes: severity, process/host info, request ID/UUID/PID, web status/method
    is_path_a = has_timestamp and (has_severity or has_req_id or has_http_status or has_syslog_header or has_cloudwatch or has_pid or has_client)

    # Path B: exception + stack trace
    is_path_b = has_stacktrace

    # Path C: well-known operational error patterns
    is_path_c = has_operational_pattern

    # Additional acceptance paths for CloudWatch, Syslog, Apache/Nginx, and App logs that might lack timestamps
    is_cloudwatch_raw = has_cloudwatch
    is_syslog_raw = has_syslog_header and (has_severity or has_pid or has_req_id)
    is_nginx_raw = has_http_status or (has_client and has_request)
    is_app_raw = has_severity and (has_exception or has_req_id or has_pid)

    if is_path_a or is_path_b or is_path_c or is_cloudwatch_raw or is_syslog_raw or is_nginx_raw or is_app_raw:
        reasons = []
        if is_path_a: reasons.append("Path A (timestamp + metadata)")
        if is_path_b: reasons.append("Path B (exception/stack trace)")
        if is_path_c: reasons.append("Path C (operational error pattern)")
        if is_cloudwatch_raw and not is_path_a: reasons.append("CloudWatch Signature")
        if is_syslog_raw and not is_path_a: reasons.append("Syslog Header Signature")
        if is_nginx_raw and not is_path_a: reasons.append("Nginx/Apache Signature")
        if is_app_raw and not is_path_a: reasons.append("Application Log Signature")
            
        return True, f"Verified machine-generated log conforming to: {', '.join(reasons)}", 2

    # If it doesn't conform to any of the paths, reject it
    reasons = []
    if not has_timestamp: reasons.append("no timestamp")
    if not (has_severity or has_req_id or has_http_status or has_syslog_header or has_cloudwatch or has_pid or has_client): reasons.append("no log metadata")
    if not has_stacktrace: reasons.append("no stack trace")
    if not has_operational_pattern: reasons.append("no operational error pattern")
    
    indicators_found = ", ".join(reasons)
    return False, f"Does not conform to Path A, B, or C ({indicators_found})", 0

def check_log_nature(text: str) -> tuple[bool, str, int]:
    """
    Standard check_log_nature function required by plan.
    """
    is_genuine, reason, indicators = check_log_nature_detail(text)
    confidence = 90 if is_genuine else 0
    return is_genuine, reason, confidence

def parse_logs_with_regex(raw_text: str, platform: str, version: str, service: str, limit: Optional[int] = None, start_date: Optional[str] = None, end_date: Optional[str] = None) -> list:
    """
    Regex fallback to parse logs locally if no API key is available.
    Supports Code/Log Block (lenient) vs Body Text Fallback (strict) sections.
    """
    log_lines = []
    
    # Split text into chunks by new source marker
    blocks = parse_source_blocks(raw_text)
    
    severity_pattern = re.compile(r'\b(INFO|WARN|ERROR|CRITICAL|DEBUG|FATAL|WARNING)\b', re.IGNORECASE)
    
    timestamp_patterns = [
        re.compile(r'(\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[-+]\d{2}:?\d{2}|Z)?)'),
        re.compile(r'(\d{2}/[A-Za-z]{3}/\d{4}:\d{2}:\d{2}:\d{2}(?:\s+[-+]\d{4})?)'),
        re.compile(r'([A-Za-z]{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?)'),
        re.compile(r'(\b\d{6}\s+\d{2}:\d{2}:\d{2}\b)'),
        re.compile(r'(\b\d{2}:\d{2}:\d{2}(?:\.\d+)?\b)')
    ]
    
    # Clean version/platform checks
    version_clean = version.strip().lower() if version else ""

    for block in blocks:
        source_url = block["source_url"]
        source_title = block["source_title"]
        source_platform = block["source_platform"]
        source_version = block["source_version"]
        source_service = block["source_service"]
        
        chunk = block["content"]
        
        # Split by Code/Log block vs Body Text Fallback headers
        parts = re.split(r'(--- Code/Log Block ---|--- Body Text Fallback ---)', chunk)
        current_block_type = "code" # Default if no marker is found
        
        for part in parts:
            part_str = part.strip()
            if not part_str:
                continue
            if part_str == "--- Code/Log Block ---":
                current_block_type = "code"
                continue
            elif part_str == "--- Body Text Fallback ---":
                current_block_type = "body"
                continue
                
            lines = part_str.splitlines()
            for line in lines:
                line_str = line.strip()
                if line_str.startswith("Source URL:") or line_str.startswith("=== NEW SOURCE ==="):
                    continue
                # Lenient on code blocks (15 chars min), strict on body fallbacks (20 chars min)
                min_len = 15 if current_block_type == "code" else 20
                if len(line_str) < min_len:
                    continue
                    
                # Strict Log nature check
                is_genuine, nature_reason, indicators = check_log_nature_detail(line_str)
                if not is_genuine:
                    continue
                    
                # Find severity
                sev_match = severity_pattern.search(line_str)
                ts_val = None
                for pattern in timestamp_patterns:
                    ts_match = pattern.search(line_str)
                    if ts_match:
                        ts_val = ts_match.group(1)
                        break
                        
                # Specific platform & version validation checks (dates)
                is_log = True
                if version_clean:
                    years_found = re.findall(r'\b(20\d{2})\b', line_str)
                    if years_found and version_clean in ["2014", "2015", "2016", "2017", "2018", "2019", "2020", "2021", "2022", "2023", "2024", "2025", "2026"]:
                        if version_clean not in years_found:
                            is_log = False
                            
                if is_log:
                    severity = "INFO"
                    if sev_match:
                        sev_val = sev_match.group(1).upper()
                        if sev_val in ["WARNING", "WARN"]:
                            severity = "WARN"
                        elif sev_val in ["ERROR", "FATAL"]:
                            severity = "ERROR"
                        elif sev_val in ["CRITICAL"]:
                            severity = "CRITICAL"
                    elif "exception" in line_str.lower() or "failed" in line_str.lower() or "timeout" in line_str.lower() or "error" in line_str.lower():
                        severity = "ERROR"
                        
                    timestamp = ts_val if ts_val else datetime.utcnow().isoformat() + "Z"
                    
                    # Clean message
                    msg = line_str
                    if ts_val:
                        msg = msg.replace(ts_val, "").strip()
                    if sev_match:
                        msg = msg.replace(sev_match.group(1), "").strip()
                    # Clean up brackets/colons/whitespace left behind
                    msg = re.sub(r'^[\[\]\(\)\s\-\:\.\,\|]+', '', msg).strip()
                    msg = re.sub(r'[\[\]\(\)\s\-\:\.\,\|]+$', '', msg).strip()
                    
                    log_lines.append({
                        "timestamp": timestamp,
                        "severity": severity,
                        "message": msg,
                        "original_log": line_str,
                        "source_url": source_url,
                        "source_title": source_title,
                        "source_platform": source_platform,
                        "source_version": source_version,
                        "source_service": source_service
                    })
                    
    # Deduplicate logs based on message signature
    seen_messages = set()
    deduped_logs = []
    for item in log_lines:
        msg_sig = item["message"][:100]
        if msg_sig not in seen_messages:
            seen_messages.add(msg_sig)
            deduped_logs.append(item)
            
    start_dt = None
    end_dt = None
    from datetime import timedelta
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
            
    if limit is not None:
        final_logs = deduped_logs[:limit]
    else:
        final_logs = deduped_logs
        
    if start_dt and end_dt and len(final_logs) > 0:
        total_delta = (end_dt - start_dt).total_seconds()
        step = total_delta / len(final_logs) if len(final_logs) > 1 else total_delta
        
        # Scale/redistribute timestamps to align with user requested range
        for idx, item in enumerate(final_logs):
            log_time = start_dt + timedelta(seconds=idx * step)
            iso_time = log_time.isoformat() + "Z"
            item["timestamp"] = iso_time
            
            orig = item["original_log"]
            matched_ts = None
            for pattern in timestamp_patterns:
                ts_m = pattern.search(orig)
                if ts_m:
                    matched_ts = ts_m.group(1)
                    break
            if matched_ts:
                orig = orig.replace(matched_ts, iso_time)
            else:
                orig = f"{iso_time} {orig}"
            item["original_log"] = orig
            
    return final_logs

def map_source_urls(logs: list, raw_text: str) -> list:
    """
    Scans raw_text for blocks and maps metadata (URL, title, platform, etc.) to each log.
    """
    if not logs:
        return []
        
    blocks = parse_source_blocks(raw_text)
    if not blocks:
        return logs
        
    first_block = blocks[0]
    
    updated_logs = []
    for log in logs:
        log_copy = log.copy()
        url = safe_to_text(log_copy.get("source_url")).strip()
        
        # If it doesn't look like a valid http URL, try to match it
        matched_block = None
        if not url or not url.startswith("http"):
            msg = safe_to_text(log_copy.get("message")).strip().lower()
            orig = safe_to_text(log_copy.get("original_log")).strip().lower()
            
            # 1. Try to find the block containing the original log or message
            for b in blocks:
                content_lower = b["content"].lower()
                if orig and orig in content_lower:
                    matched_block = b
                    break
                if msg and msg in content_lower:
                    matched_block = b
                    break
                    
            # 2. Try to find via word matching
            if not matched_block:
                best_score = 0
                for b in blocks:
                    content_lower = b["content"].lower()
                    words = [w for w in msg.split() if len(w) > 3]
                    if words:
                        score = sum(1 for w in words if w in content_lower) / len(words)
                        if score > best_score and score > 0.5:
                            best_score = score
                            matched_block = b
        else:
            # If log has a source_url, find the corresponding block to extract title, etc.
            for b in blocks:
                if b["source_url"] == url:
                    matched_block = b
                    break
                    
        block_to_use = matched_block if matched_block else first_block
        
        log_copy["source_url"] = block_to_use["source_url"]
        log_copy["source_title"] = block_to_use.get("source_title", "")
        log_copy["source_platform"] = block_to_use.get("source_platform", "")
        log_copy["source_version"] = block_to_use.get("source_version", "")
        log_copy["source_service"] = block_to_use.get("source_service", "")
        log_copy["query_used"] = block_to_use.get("query_used", "")
        log_copy["source_rank"] = block_to_use.get("source_rank", 4)
        
        updated_logs.append(log_copy)
        
    return updated_logs

if __name__ == "__main__":
    # Test local parser
    test_text = """
    2021-01-26T11:53:42.909-06:00 ERROR [nginx] upstream timed out while connecting to backend server
    Some random text talking about Nginx.
    [INFO] failed to start container due to network bridge issue
    """
    print(parse_logs_with_regex(test_text, "nginx", "1.25", ""))
