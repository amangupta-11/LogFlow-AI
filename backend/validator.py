import os
import json
import logging
import requests
import re
from dotenv import load_dotenv
from openai import OpenAI
from backend.extractor import check_log_nature_detail, check_log_nature, classify_candidate_nature, detect_platform_match_type, safe_to_text


load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DISABLE_STRICT_VALIDATION = os.getenv("DISABLE_STRICT_VALIDATION", "false").lower() == "true"

def classify_source(source_url: str, page_title: str) -> str:
    url = source_url.lower()
    title = page_title.lower()
    
    if "github.com" in url and ("/issues/" in url or "/pull/" in url):
        return "GITHUB_ISSUE"
    if "raw.githubusercontent.com" in url or "pastebin.com/raw/" in url or "/raw" in url or url.endswith(".log") or url.endswith(".txt"):
        return "LOG_FILE"
    if any(x in url for x in ["blog", "dev.to", "hashnode", "medium.com"]) or "blog" in title:
        return "BLOG"
    if any(x in url for x in ["stackoverflow.com", "serverfault.com", "superuser.com", "reddit.com", "forum", "discuss", "groups.google"]) or "forum" in title or "discussion" in title:
        return "FORUM"
    if any(x in url for x in ["docs.", "documentation.", "reference", "guide", "wiki", "/doc/", "/docs/", "/help/", "support."]) or "documentation" in title or "guide" in title or "reference" in title:
        return "DOCUMENTATION"
    if any(x in url for x in ["troubleshoot", "debug", "incident", "postmortem", "issue", "error", "fix", "solve"]) or any(x in title for x in ["troubleshoot", "debug", "incident", "postmortem", "issue", "error", "fix", "solve"]):
        return "TROUBLESHOOTING_PAGE"
        
    return "TROUBLESHOOTING_PAGE"

def check_platform_relevance(platform: str, source_url: str, page_title: str, crawl_context: str) -> bool:
    """
    Determines if the platform is relevant based on source context.
    Checks:
    - source_url contains platform name (case-insensitive)
    - page_title contains platform name (case-insensitive)
    - crawl_context contains platform name (case-insensitive)
    """
    if not platform:
        return True
    
    plat_lower = platform.strip().lower()
    if not plat_lower:
        return True
        
    url = source_url.lower()
    title = page_title.lower()
    context = crawl_context.lower()
    
    # Direct check
    if plat_lower in url or plat_lower in title or plat_lower in context:
        return True
        
    # Check individual tokens of length > 2 (e.g. "aws" or "lambda" for "aws lambda")
    tokens = [t for t in re.split(r'[\s\-_]+', plat_lower) if len(t) > 2]
    if tokens:
        for t in tokens:
            if t in url or t in title or t in context:
                return True
                
    # Check common aliases
    aliases = {
        "aws lambda": ["lambda", "amazon web services", "aws"],
        "cloudwatch": ["aws", "amazon", "cloudwatch"],
        "syslog": ["syslog", "linux", "systemd", "rsyslog"],
        "nginx": ["nginx", "webserver"],
        "apache": ["apache", "httpd"],
        "kubernetes": ["k8s", "kubernetes", "kubectl", "pod"],
        "docker": ["docker", "container", "dockerd"]
    }
    
    if plat_lower in aliases:
        for alias in aliases[plat_lower]:
            if alias in url or alias in title or alias in context:
                return True
                
    return False

def normalize_text_for_match(text: str) -> str:
    if not text:
        return ""
    # Convert to lowercase
    text = text.lower()
    # Normalize quotation marks (curly/backticks to straight double quote)
    text = re.sub(r'[\u201c\u201d\u201e\u201f\u2033\u2036”’‘“]', '"', text)
    text = re.sub(r'[\u2018\u2019\u201a\u201b\u2032\u2035\'`]', '"', text)
    # Normalize line breaks and tabs to single space
    text = re.sub(r'[\r\n\t]+', ' ', text)
    # Remove duplicate spaces
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def replace_timestamps_with_marker(text: str) -> str:
    # 1. ISO-8601 / standard datetime patterns
    text = re.sub(r'\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[-+]\d{2}:?\d{2})?', ' <TIMESTAMP> ', text)
    # 2. Syslog style
    text = re.sub(r'\b[A-Za-z]{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?', ' <TIMESTAMP> ', text)
    # 3. Apache/Nginx Access style
    text = re.sub(r'\[?\d{2}/[A-Za-z]{3}/\d{4}:\d{2}:\d{2}:\d{2}(?:\s+[-+]\d{4})?\]?', ' <TIMESTAMP> ', text)
    # 4. Apache Error style
    text = re.sub(r'\[?[A-Za-z]{3}\s+[A-Za-z]{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?\s+\d{4}\]?', ' <TIMESTAMP> ', text)
    # 5. Generic time-only logs
    text = re.sub(r'\b\d{2}:\d{2}:\d{2}(?:\.\d+)?\b', ' <TIMESTAMP> ', text)
    return text

def extract_severity(text: str) -> str:
    for sev in ["critical", "fatal", "error", "warn", "warning", "info", "debug", "trace"]:
        if re.search(r'\b' + sev + r'\b', text, re.IGNORECASE):
            if sev in ["warning", "warn"]:
                return "WARN"
            if sev in ["error", "fatal", "critical"]:
                return "ERROR"
            return sev.upper()
    return ""

def deterministic_validation(log: dict, raw_text: str, platform: str) -> tuple[bool, str, int]:
    is_valid, reason, confidence = _actual_deterministic_validation(log, raw_text, platform)
    log["originally_valid"] = is_valid
    if DISABLE_STRICT_VALIDATION:
        if log.get("message") or log.get("original_log"):
            return True, f"[OVERRIDDEN] {reason}", confidence
    return is_valid, reason, confidence

def _actual_deterministic_validation(log: dict, raw_text: str, platform: str) -> tuple[bool, str, int]:
    """
    Performs deterministic validation on a single log entry.
    Returns (is_valid, reason, confidence).
    """
    msg = safe_to_text(log.get("message")).strip()
    orig = safe_to_text(log.get("original_log")).strip()
    source_url = safe_to_text(log.get("source_url")).strip()
    source_title = safe_to_text(log.get("source_title")).strip()
    source_platform = log.get("source_platform", "")
    source_version = log.get("source_version", "")
    
    # Extract query_used and source_rank from log
    query_used = log.get("query_used", "")
    source_rank = log.get("source_rank", 4)
    
    # Classify platform dynamically or via dictionary
    from backend.crawler import discover_and_classify_platform
    category, vendor, technology = discover_and_classify_platform(platform or source_platform)
    
    text_to_check = orig if orig else msg
    if not text_to_check:
        return False, "Empty log message and original log", 0
        
    # 1. Classification check
    is_genuine, nature_reason, score_from_nature = check_log_nature_detail(text_to_check)
    log_type, machine_generated_score = classify_candidate_nature(text_to_check)
    platform_match_type = detect_platform_match_type(text_to_check)
    
    source_type = classify_source(source_url, source_title)
    
    if not is_genuine:
        # Enrich root
        log["platform"] = platform or source_platform or technology
        log["category"] = category
        log["vendor"] = vendor
        log["source_type"] = source_type
        log["source_rank"] = source_rank
        log["query_used"] = query_used
        
        log["validation"] = {
            "valid": False,
            "reason": nature_reason,
            "confidence": 0,
            "log_type": log_type,
            "machine_generated_score": machine_generated_score,
            "platform_match_type": platform_match_type,
            "source_verified": False,
            "validation_reason": nature_reason,
            "normalized_similarity": 0.0,
            "platform": platform or source_platform or technology,
            "category": category,
            "vendor": vendor,
            "source_type": source_type,
            "source_rank": source_rank,
            "query_used": query_used
        }
        return False, nature_reason, 0
        
    # 2. Source relevance verification
    target_platform = platform if platform else source_platform
    platform_clean = target_platform.strip().lower() if target_platform else ""
    crawl_context_str = f"Platform={source_platform}, Version={source_version}"
    
    source_relevant = False
    if source_url:
        source_relevant = True
        if platform_clean:
            plat_rel = check_platform_relevance(platform_clean, source_url, source_title, crawl_context_str)
            in_raw = platform_clean in raw_text.lower()
            if not (plat_rel or in_raw):
                source_relevant = False
                
    # 3. Structural pattern match
    platform_pattern_match = (platform_match_type != "generic_log")
    
    # 4. Normalized similarity comparison (replacing timestamps with <TIMESTAMP> marker first)
    cand_ts_marker = replace_timestamps_with_marker(text_to_check)
    norm_cand = normalize_text_for_match(cand_ts_marker)
    
    norm_raw = normalize_text_for_match(replace_timestamps_with_marker(raw_text))
    
    normalized_similarity = 0.0
    best_matching_line = ""
    
    if norm_cand and norm_cand in norm_raw:
        normalized_similarity = 1.0
        best_matching_line = text_to_check
    else:
        # Split raw text into lines to find the best match
        raw_lines = raw_text.splitlines()
        max_sim = 0.0
        best_line = ""
        import difflib
        
        for line in raw_lines:
            line_strip = line.strip()
            if not line_strip:
                continue
            norm_line = normalize_text_for_match(replace_timestamps_with_marker(line_strip))
            if not norm_line:
                continue
                
            if norm_cand in norm_line or norm_line in norm_cand:
                sim = 1.0
            else:
                sim = difflib.SequenceMatcher(None, norm_cand, norm_line).ratio()
                
            if sim > max_sim:
                max_sim = sim
                best_line = line_strip
                
        normalized_similarity = max_sim
        best_matching_line = best_line
        
    # 5. Severity validation
    severity_mismatch = False
    if best_matching_line:
        extracted_sev = extract_severity(text_to_check)
        source_sev = extract_severity(best_matching_line)
        if extracted_sev and source_sev and extracted_sev != source_sev:
            severity_mismatch = True

    # 6. Final Validation Rule
    is_valid = (
        (log_type in ["REAL_LOG", "STACKTRACE"]) and
        source_relevant and
        (platform_pattern_match or normalized_similarity >= 0.90) and
        (not severity_mismatch)
    )
    
    # 7. Confidence & Caps calculation
    caps = {
        "LOG_FILE": 100,
        "GITHUB_ISSUE": 100,
        "TROUBLESHOOTING_PAGE": 95,
        "FORUM": 90,
        "DOCUMENTATION": 85,
        "BLOG": 80
    }
    cap_value = caps.get(source_type, 90)
    confidence = cap_value
    
    if source_type in ["BLOG", "DOCUMENTATION"]:
        confidence = 90
        
    # Apply confidence penalty when platform_match_type == "generic_log"
    if platform_match_type == "generic_log":
        confidence -= 15
        if confidence < 0:
            confidence = 0
            
    if not is_valid:
        confidence = 0
        
    # Reason construction
    if not source_relevant:
        reason = "Source context is not relevant to the platform or URL is missing"
    elif severity_mismatch:
        reason = f"Severity mismatch: extracted severity ({extract_severity(text_to_check)}) differs from source severity ({extract_severity(best_matching_line)})"
    elif not (platform_pattern_match or normalized_similarity >= 0.90):
        reason = f"No platform pattern match and normalized similarity ({normalized_similarity:.2f}) is below 0.90"
    else:
        reason = f"Validated log (Source type: {source_type}, Match type: {platform_match_type}, Similarity: {normalized_similarity:.2f})"
        
    # Enrich root
    log["platform"] = platform or source_platform or technology
    log["category"] = category
    log["vendor"] = vendor
    log["source_type"] = source_type
    log["source_rank"] = source_rank
    log["query_used"] = query_used
    
    log["validation"] = {
        "valid": is_valid,
        "reason": reason,
        "confidence": confidence,
        "log_type": log_type,
        "machine_generated_score": machine_generated_score,
        "platform_match_type": platform_match_type,
        "source_verified": is_valid,
        "validation_reason": reason,
        "normalized_similarity": normalized_similarity,
        "platform": platform or source_platform or technology,
        "category": category,
        "vendor": vendor,
        "source_type": source_type,
        "source_rank": source_rank,
        "query_used": query_used
    }
    
    return is_valid, reason, confidence


def local_validation(extracted_logs: list, raw_text: str, fallback_reason: str, platform: str = "", version: str = "") -> list:
    """
    Fallback validation that checks if the extracted logs actually exist in the raw scraped text,
    resemble real log structure, and match the specified version/platform using deterministic rules.
    """
    if not raw_text:
        return [{**log, "validation": {"valid": False, "reason": f"{fallback_reason} (Empty raw text)", "confidence": 0}} for log in extracted_logs]

    updated_logs = []
    for log in extracted_logs:
        log_copy = log.copy()
        is_valid, reason, confidence = deterministic_validation(log_copy, raw_text, platform)
        log_copy["validation"].update({
            "valid": is_valid,
            "reason": f"{fallback_reason}: {reason}" if fallback_reason else reason,
            "confidence": confidence
        })
        updated_logs.append(log_copy)
    return updated_logs


def validate_logs_with_openrouter(extracted_logs: list, raw_text: str, api_key: str, model_name: str, reason_prefix: str, platform: str = "", version: str = "") -> list:
    """
    Query OpenRouter's API to validate logs using specified model.
    """
    if not extracted_logs:
        return []

    logger.info(f"Connecting to OpenRouter ({model_name}) to validate logs...")
    
    prompt = f"""
You are an independent system log validation authority.
Your task is to validate a list of extracted log entries against the raw scraped text they were extracted from.
We are specifically looking for logs matching Platform: '{platform}' and Version: '{version}' (if version is specified).

Extracted Logs:
{json.dumps(extracted_logs, indent=2)}

Raw Scraped Text (first 60000 characters):
{raw_text[:60000]}

Instructions:
You are a Log Verification Engine. Assume all logs are INVALID until proven otherwise.
1. Verify if each entry is a genuine machine-generated system log line suitable for observability or RCA datasets.
2. A log may be marked "valid": true only if:
   - The content appears machine-generated and is copied directly from the raw text.
   - The content conforms to at least one of these three acceptance paths:
     - Path A (timestamp + log metadata): Contains a timestamp AND log metadata (like severity, process name, request ID, event ID, client IP, request method). Preserves existing CloudWatch, Syslog, Apache, Nginx, and Application log validation formats.
     - Path B (exception + stack trace): Contains an exception signature or stack trace (including Java stack trace lines without timestamps).
     - Path C (well-known operational error patterns): Contains well-known error signatures such as: CrashLoopBackOff, OOMKilled, Invoke Error, Kernel Panic, Segmentation Fault, segfault, OutOfMemory.
   - The content belongs to the requested platform: '{platform}' and version: '{version}' (if version is specified).
3. Reject documentation snippets, blog explanations, tutorials, or marketing text that are not complete log entries.
   - EXAMPLES TO REJECT:
     "Execution failed due to configuration error"
     "Gateway response body: { ... }"
     unless accompanied by a timestamp, requestId, or other log metadata.
   - AUTOMATICALLY REJECT the entry (set "valid": false) if it represents:
     - Human explanation, markdown content, or code comments.
     - Configuration examples, installation instructions, or command/shell command examples without log metadata.
4. Return the results strictly as a JSON object containing a list named "results", matching the length of the Extracted Logs list, where each item has exactly the keys:
   "valid", "reason"
   Set "valid": true only if confidence score is >= 90 based on exact conformant machine-generated logs.
5. Return ONLY the raw JSON object. Do not include markdown wrapping (like ```json ... ```).
"""

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": model_name,
        "max_tokens": 1000,
        "temperature": 0.2,
        "messages": [
            {"role": "user", "content": prompt}
        ]
    }
    
    if "openai" in model_name:
        payload["response_format"] = {"type": "json_object"}
        
    try:
        response = requests.post("https://openrouter.ai/api/v1/chat/completions", json=payload, headers=headers, timeout=30)
        provider = "Anthropic" if "anthropic" in model_name or "claude" in model_name.lower() else "OpenAI"
        from backend.db_manager import record_llm_validation
        if response.status_code == 200:
            data = response.json()
            text = data["choices"][0]["message"]["content"].strip()
            
            # Clean up markdown JSON wrappers if present
            if text.startswith("```json"):
                text = text.split("```json")[1].split("```")[0].strip()
            elif text.startswith("```"):
                text = text.split("```")[1].split("```")[0].strip()
                
            result_data = json.loads(text)
            
            if isinstance(result_data, list):
                validation_results = result_data
            elif isinstance(result_data, dict) and "results" in result_data:
                validation_results = result_data["results"]
            else:
                err_msg = f"Unexpected JSON structure: {result_data}"
                logger.error(f"OpenRouter response JSON format unexpected: {result_data}")
                record_llm_validation(provider, False, err_msg)
                return None
                
            if isinstance(validation_results, list) and len(validation_results) == len(extracted_logs):
                updated_logs = []
                for log, val in zip(extracted_logs, validation_results):
                    log_copy = log.copy()
                    log_copy["validation"] = {
                        "valid": val.get("valid", False),
                        "reason": f"{reason_prefix}: {val.get('reason', 'No reason provided')}"
                    }
                    updated_logs.append(log_copy)
                record_llm_validation(provider, True)
                return updated_logs
            else:
                err_msg = f"Invalid response size/format: expected {len(extracted_logs)} items, got {len(validation_results) if isinstance(validation_results, list) else 'non-list'}"
                logger.error(f"OpenRouter returned: {err_msg}")
                record_llm_validation(provider, False, err_msg)
        else:
            err_msg = f"HTTP {response.status_code}: {response.text}"
            logger.error(f"OpenRouter API returned status: {err_msg}")
            record_llm_validation(provider, False, err_msg)
    except Exception as e:
        provider = "Anthropic" if "anthropic" in model_name or "claude" in model_name.lower() else "OpenAI"
        from backend.db_manager import record_llm_validation
        err_msg = f"Connection/Parsing error: {e}"
        logger.error(f"Error calling OpenRouter validator API: {err_msg}")
        record_llm_validation(provider, False, err_msg)
        
    return None

def validate_logs_with_openai(extracted_logs: list, raw_text: str, reason_prefix: str = "OpenAI Validation", platform: str = "", version: str = "") -> list:
    """
    Query OpenAI's chat completions API to validate extracted logs against raw scraped content.
    Returns the log list updated with 'validation' keys, or None if the API fails or is not configured.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or api_key.startswith("sk-abcdef"):
        logger.warning("OPENAI_API_KEY is not set or is placeholder. Skipping OpenAI validation.")
        return None

    if not extracted_logs:
        return []

    # Check if key is OpenRouter key
    if api_key.startswith("sk-or-v1-"):
        logger.info("OpenAI API key is an OpenRouter key. Using OpenRouter to validate with OpenAI...")
        return validate_logs_with_openrouter(
            extracted_logs,
            raw_text,
            api_key,
            model_name="openai/gpt-4o-mini",
            reason_prefix=f"{reason_prefix} (OpenRouter)",
            platform=platform,
            version=version
        )

    logger.info("Connecting to OpenAI to validate logs...")
    
    prompt = f"""
You are an independent system log validation authority.
Your task is to validate a list of extracted log entries against the raw scraped text they were extracted from.
We are specifically looking for logs matching Platform: '{platform}' and Version: '{version}' (if version is specified).

Extracted Logs:
{json.dumps(extracted_logs, indent=2)}

Raw Scraped Text (first 60000 characters):
{raw_text[:60000]}

Instructions:
You are a Log Verification Engine. Assume all logs are INVALID until proven otherwise.
1. Verify if each entry is a genuine machine-generated system log line suitable for observability or RCA datasets.
2. A log may be marked "valid": true only if:
   - The content appears machine-generated and is copied directly from the raw text.
   - The content conforms to at least one of these three acceptance paths:
     - Path A (timestamp + log metadata): Contains a timestamp AND log metadata (like severity, process name, request ID, event ID, client IP, request method). Preserves existing CloudWatch, Syslog, Apache, Nginx, and Application log validation formats.
     - Path B (exception + stack trace): Contains an exception signature or stack trace (including Java stack trace lines without timestamps).
     - Path C (well-known operational error patterns): Contains well-known error signatures such as: CrashLoopBackOff, OOMKilled, Invoke Error, Kernel Panic, Segmentation Fault, segfault, OutOfMemory.
   - The content belongs to the requested platform: '{platform}' and version: '{version}' (if version is specified).
3. Reject documentation snippets, blog explanations, tutorials, or marketing text that are not complete log entries.
   - EXAMPLES TO REJECT:
     "Execution failed due to configuration error"
     "Gateway response body: { ... }"
     unless accompanied by a timestamp, requestId, or other log metadata.
   - AUTOMATICALLY REJECT the entry (set "valid": false) if it represents:
     - Human explanation, markdown content, or code comments.
     - Configuration examples, installation instructions, or command/shell command examples without log metadata.
4. Return the results strictly as a JSON object containing a list named "results", matching the length of the Extracted Logs list, where each item has exactly the keys:
   "valid", "reason"
   Set "valid": true only if confidence score is >= 90 based on exact conformant machine-generated logs.
"""

    try:
        from backend.db_manager import record_llm_validation
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=1000,
            timeout=20
        )
        text = response.choices[0].message.content.strip()
        data = json.loads(text)
        validation_results = data.get("results", [])
        
        if isinstance(validation_results, list) and len(validation_results) == len(extracted_logs):
            updated_logs = []
            for log, val in zip(extracted_logs, validation_results):
                log_copy = log.copy()
                log_copy["validation"] = {
                    "valid": val.get("valid", False),
                    "reason": f"{reason_prefix}: {val.get('reason', 'No reason provided')}"
                }
                updated_logs.append(log_copy)
            record_llm_validation("OpenAI", True)
            return updated_logs
        else:
            err_msg = f"Invalid response size/format: expected {len(extracted_logs)} items, got {len(validation_results) if isinstance(validation_results, list) else 'non-list'}"
            logger.error(f"OpenAI returned: {err_msg}")
            record_llm_validation("OpenAI", False, err_msg)
    except Exception as e:
        from backend.db_manager import record_llm_validation
        err_msg = f"Connection/Parsing error: {e}"
        logger.error(f"Error calling OpenAI validator API: {err_msg}")
        record_llm_validation("OpenAI", False, err_msg)

    return None

def post_validate_logs(validated_logs: list, raw_text: str, platform: str = "") -> list:
    """
    Enforces deterministic validation rules on all logs.
    Deterministic validation always overrides LLM validation.
    LLM decisions must never upgrade a rejected log to VALIDATED.
    """
    final_logs = []
    for log in validated_logs:
        log_copy = log.copy()
        
        # Determine if it is valid deterministically
        is_valid, reason, confidence = deterministic_validation(log_copy, raw_text, platform)
        
        log_copy["validation"].update({
            "valid": is_valid,
            "reason": reason,
            "confidence": confidence
        })
        final_logs.append(log_copy)
    return final_logs

def make_validation_prompt(extracted_logs: list, raw_text: str, platform: str = "", version: str = "") -> str:
    import json
    return f"""You are an independent system log validation authority.
Your task is to validate a list of extracted log entries against the raw scraped text they were extracted from.
We are specifically looking for logs matching Platform: '{platform}' and Version: '{version}' (if version is specified).

Extracted Logs:
{json.dumps(extracted_logs, indent=2)}

Raw Scraped Text (first 60000 characters):
{raw_text[:60000]}

Instructions:
You are a Log Verification Engine. Assume all logs are INVALID until proven otherwise.
1. Verify if each entry is a genuine machine-generated system log line suitable for observability or RCA datasets.
2. A log may be marked "valid": true only if:
   - The content appears machine-generated and is copied directly from the raw text.
   - The content conforms to at least one of these three acceptance paths:
     - Path A (timestamp + log metadata): Contains a timestamp AND log metadata (like severity, process name, request ID, event ID, client IP, request method).
     - Path B (exception + stack trace): Contains an exception signature or stack trace.
     - Path C (well-known operational error patterns): Contains well-known error signatures.
   - The content belongs to the requested platform: '{platform}' and version: '{version}' (if version is specified).
3. Reject documentation snippets, blog explanations, tutorials, or marketing text that are not complete log entries.
4. Return the results strictly as a JSON object containing a list named "results", matching the length of the Extracted Logs list, where each item has exactly the keys:
   "valid", "reason"
   Set "valid": true only if confidence score is >= 90 based on exact conformant machine-generated logs.
5. Return ONLY the raw JSON object. Do not include markdown wrapping (like ```json ... ```).
"""

def validate_with_gemini_provider(extracted_logs: list, raw_text: str, platform: str, version: str) -> list:
    import time
    import json
    import google.generativeai as genai
    from backend.db_manager import record_llm_validation
    
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key or api_key.startswith("sk-abcdef"):
        return None
        
    chunk_size = 15
    validated_logs = []
    
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            model_name="gemini-2.5-flash",
            generation_config={"response_mime_type": "application/json"}
        )
        
        for i in range(0, len(extracted_logs), chunk_size):
            chunk = extracted_logs[i:i+chunk_size]
            prompt = make_validation_prompt(chunk, raw_text, platform, version)
            
            start_time = time.time()
            response = model.generate_content(prompt)
            duration = time.time() - start_time
            
            text = response.text.strip()
            data = json.loads(text)
            validation_results = data.get("results", [])
            
            if not isinstance(validation_results, list) or len(validation_results) != len(chunk):
                raise ValueError(f"Invalid response format: expected {len(chunk)} results, got {len(validation_results) if isinstance(validation_results, list) else 'non-list'}")
                
            for log, val in zip(chunk, validation_results):
                log_copy = log.copy()
                log_copy["validation"] = {
                    "valid": val.get("valid", False),
                    "reason": f"Gemini Verification: {val.get('reason', 'No reason provided')}"
                }
                validated_logs.append(log_copy)
                
            record_llm_validation("Gemini", True, response_time=duration)
            
        return validated_logs
        
    except Exception as e:
        duration = time.time() - start_time if 'start_time' in locals() else 0.0
        err_msg = str(e)
        logger.error(f"Gemini provider validation failed: {err_msg}")
        record_llm_validation("Gemini", False, err_msg, response_time=duration)
        return None

def validate_with_openai_provider(extracted_logs: list, raw_text: str, platform: str, version: str) -> list:
    import time
    import json
    from openai import OpenAI
    from backend.db_manager import record_llm_validation
    
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or api_key.startswith("sk-abcdef"):
        return None
        
    is_openrouter = api_key.startswith("sk-or-v1-")
    chunk_size = 15
    validated_logs = []
    
    try:
        if is_openrouter:
            client = OpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")
            model_name = "openai/gpt-4o-mini"
        else:
            client = OpenAI(api_key=api_key)
            model_name = "gpt-4o-mini"
            
        for i in range(0, len(extracted_logs), chunk_size):
            chunk = extracted_logs[i:i+chunk_size]
            prompt = make_validation_prompt(chunk, raw_text, platform, version)
            
            start_time = time.time()
            response = client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.2,
                max_tokens=1000,
                timeout=20
            )
            duration = time.time() - start_time
            
            text = response.choices[0].message.content.strip()
            data = json.loads(text)
            validation_results = data.get("results", [])
            
            if not isinstance(validation_results, list) or len(validation_results) != len(chunk):
                raise ValueError(f"Invalid response format: expected {len(chunk)} results, got {len(validation_results) if isinstance(validation_results, list) else 'non-list'}")
                
            for log, val in zip(chunk, validation_results):
                log_copy = log.copy()
                log_copy["validation"] = {
                    "valid": val.get("valid", False),
                    "reason": f"OpenAI Verification: {val.get('reason', 'No reason provided')}"
                }
                validated_logs.append(log_copy)
                
            record_llm_validation("OpenAI", True, response_time=duration)
            
        return validated_logs
        
    except Exception as e:
        duration = time.time() - start_time if 'start_time' in locals() else 0.0
        err_msg = str(e)
        logger.error(f"OpenAI provider validation failed: {err_msg}")
        record_llm_validation("OpenAI", False, err_msg, response_time=duration)
        return None

def validate_with_anthropic_provider(extracted_logs: list, raw_text: str, platform: str, version: str) -> list:
    import time
    import json
    import requests
    from backend.db_manager import record_llm_validation
    
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key or api_key.startswith("sk-abcdef") or api_key.startswith("sk-ant-api03-KjVcE3O4"):
        return None
        
    is_openrouter = api_key.startswith("sk-or-v1-")
    chunk_size = 15
    validated_logs = []
    
    try:
        for i in range(0, len(extracted_logs), chunk_size):
            chunk = extracted_logs[i:i+chunk_size]
            prompt = make_validation_prompt(chunk, raw_text, platform, version)
            
            start_time = time.time()
            if is_openrouter:
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "model": "anthropic/claude-sonnet-4",
                    "max_tokens": 1500,
                    "messages": [{"role": "user", "content": prompt}]
                }
                response = requests.post("https://openrouter.ai/api/v1/chat/completions", json=payload, headers=headers, timeout=25)
                duration = time.time() - start_time
                
                if response.status_code != 200:
                    raise RuntimeError(f"HTTP {response.status_code}: {response.text}")
                    
                data = response.json()
                text = data["choices"][0]["message"]["content"].strip()
            else:
                headers = {
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"
                }
                payload = {
                    "model": "claude-3-5-sonnet-20240620",
                    "max_tokens": 1500,
                    "messages": [{"role": "user", "content": prompt}]
                }
                response = requests.post("https://api.anthropic.com/v1/messages", json=payload, headers=headers, timeout=25)
                duration = time.time() - start_time
                
                if response.status_code != 200:
                    raise RuntimeError(f"HTTP {response.status_code}: {response.text}")
                    
                data = response.json()
                text = data["content"][0]["text"].strip()
                
            if text.startswith("```json"):
                text = text.split("```json")[1].split("```")[0].strip()
            elif text.startswith("```"):
                text = text.split("```")[1].split("```")[0].strip()
                
            validation_results = json.loads(text)
            
            if isinstance(validation_results, dict) and "results" in validation_results:
                validation_results = validation_results["results"]
                
            if not isinstance(validation_results, list) or len(validation_results) != len(chunk):
                raise ValueError(f"Invalid response format: expected {len(chunk)} results, got {len(validation_results) if isinstance(validation_results, list) else 'non-list'}")
                
            for log, val in zip(chunk, validation_results):
                log_copy = log.copy()
                log_copy["validation"] = {
                    "valid": val.get("valid", False),
                    "reason": f"Claude Verification: {val.get('reason', 'No reason provided')}"
                }
                validated_logs.append(log_copy)
                
            record_llm_validation("Anthropic", True, response_time=duration)
            
        return validated_logs
        
    except Exception as e:
        duration = time.time() - start_time if 'start_time' in locals() else 0.0
        err_msg = str(e)
        logger.error(f"Anthropic provider validation failed: {err_msg}")
        record_llm_validation("Anthropic", False, err_msg, response_time=duration)
        return None

def validate_logs_with_claude(extracted_logs: list, raw_text: str, platform: str = "", version: str = "") -> list:
    """
    Validation Pipeline with Failover:
    1. Gemini
    2. OpenAI
    3. Anthropic
    4. Local offline validation (Regex / Deterministic Validator)
    """
    if not extracted_logs:
        return []
        
    # 1. Try Gemini
    logger.info("Pipeline Step 1: Trying Gemini...")
    res = validate_with_gemini_provider(extracted_logs, raw_text, platform, version)
    if res is not None:
        logger.info("Gemini validation succeeded.")
        return post_validate_logs(res, raw_text, platform)
        
    logger.info("Gemini validation failed or unconfigured. Falling back to OpenAI validation...")
    
    # 2. Try OpenAI
    logger.info("Pipeline Step 2: Trying OpenAI...")
    res = validate_with_openai_provider(extracted_logs, raw_text, platform, version)
    if res is not None:
        logger.info("OpenAI validation succeeded.")
        return post_validate_logs(res, raw_text, platform)
        
    logger.info("OpenAI validation failed or unconfigured. Falling back to Anthropic validation...")
    
    # 3. Try Anthropic
    logger.info("Pipeline Step 3: Trying Anthropic...")
    res = validate_with_anthropic_provider(extracted_logs, raw_text, platform, version)
    if res is not None:
        logger.info("Anthropic validation succeeded.")
        return post_validate_logs(res, raw_text, platform)
        
    logger.warning("Anthropic validation failed or unconfigured. Falling back to local offline validation...")
    
    # 4. Fallback to Regex/Deterministic Validator
    logger.warning("Pipeline Step 4: All LLMs failed. Falling back to local offline validation.")
    res = local_validation(extracted_logs, raw_text, "Local Verification Fallback", platform, version)
    return post_validate_logs(res, raw_text, platform)



