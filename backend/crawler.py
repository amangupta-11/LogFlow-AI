import logging
import requests
from bs4 import BeautifulSoup
import time
import random
import re
from urllib.parse import urlparse, parse_qs

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
]

from backend.search_providers import (
    DuckDuckGoProvider, BingProvider, BraveProvider, YahooProvider, AOLProvider
)
import threading

PROVIDERS = [
    DuckDuckGoProvider(),
    BingProvider(),
    BraveProvider(),
    YahooProvider(),
    AOLProvider()
]

thread_local = threading.local()

def get_thread_diagnostics():
    if not hasattr(thread_local, "search_diagnostics"):
        thread_local.search_diagnostics = []
    return thread_local.search_diagnostics

def clear_thread_diagnostics():
    thread_local.search_diagnostics = []

def search_yahoo(query: str, max_results: int = 7) -> list:
    """
    Search Yahoo for logs matching the query.
    Backward-compatible wrapper using YahooProvider.
    """
    res = YahooProvider().search(query, max_results=max_results)
    return res.get("results", [])

def search_aol(query: str, max_results: int = 7) -> list:
    """
    Search AOL for logs matching the query.
    Backward-compatible wrapper using AOLProvider.
    """
    res = AOLProvider().search(query, max_results=max_results)
    return res.get("results", [])


PLATFORM_CATEGORIES = {
    "docker": {"category": "Container Platform", "vendor": "Docker Inc.", "technology": "Docker"},
    "kubernetes": {"category": "Container Platform", "vendor": "CNCF", "technology": "Kubernetes"},
    "apache": {"category": "Web Server", "vendor": "Apache Software Foundation", "technology": "Apache HTTP Server"},
    "nginx": {"category": "Web Server", "vendor": "NGINX / F5", "technology": "NGINX"},
    "ubuntu": {"category": "Operating System", "vendor": "Canonical Ltd.", "technology": "Ubuntu Linux"},
    "centos": {"category": "Operating System", "vendor": "Red Hat / CentOS Project", "technology": "CentOS Linux"},
    "opensuse": {"category": "Operating System", "vendor": "SUSE", "technology": "openSUSE"},
    "aix": {"category": "Operating System", "vendor": "IBM", "technology": "AIX"},
    "oracle": {"category": "Database", "vendor": "Oracle Corporation", "technology": "Oracle Database"},
    "postgresql": {"category": "Database", "vendor": "PostgreSQL Global Development Group", "technology": "PostgreSQL"},
    "mysql": {"category": "Database", "vendor": "Oracle / MySQL", "technology": "MySQL"},
    "mongodb": {"category": "Database", "vendor": "MongoDB Inc.", "technology": "MongoDB"},
    "hyper-v": {"category": "Virtualization", "vendor": "Microsoft", "technology": "Hyper-V"},
    "vmware": {"category": "Virtualization", "vendor": "VMware / Broadcom", "technology": "VMware vSphere"},
    "fortigate": {"category": "Firewall", "vendor": "Fortinet", "technology": "FortiGate"},
    "juniper": {"category": "Router", "vendor": "Juniper Networks", "technology": "Juniper Router OS"},
    "android": {"category": "Operating System", "vendor": "Google", "technology": "Android OS"},
    "aws": {"category": "Cloud Platform", "vendor": "Amazon Web Services", "technology": "AWS"},
    "weblogic": {"category": "Application Server", "vendor": "Oracle", "technology": "WebLogic Application Server"},
    "nginx/docker": {"category": "Web Server / Container Platform", "vendor": "NGINX / F5 / Docker Inc.", "technology": "NGINX / Docker"},
    "unknown": {"category": "Unknown", "vendor": "Unknown Vendor", "technology": "Unknown"},
}

def classify_tier(url: str, title: str = "", snippet: str = "") -> int:
    url_lower = url.lower()
    title_lower = title.lower() if title else ""
    snippet_lower = snippet.lower() if snippet else ""
    
    # Tier 1: Official Documentation / Vendor KB
    tier1_kw = ["docs.", "documentation.", "reference", "guide", "wiki", "/doc/", "/docs/", "/help/", "support."]
    if any(x in url_lower or x in title_lower or x in snippet_lower for x in tier1_kw):
        return 1
        
    # Tier 2: GitHub Issues, Vendor Community
    tier2_kw = ["github.com", "community.", "forum."]
    if any(x in url_lower or x in title_lower or x in snippet_lower for x in tier2_kw):
        return 2
        
    # Tier 3: Stack Overflow / Server Fault / Super User / Stack Exchange
    tier3_kw = ["stackoverflow.com", "serverfault.com", "superuser.com", "stackexchange.com"]
    if any(x in url_lower or x in title_lower or x in snippet_lower for x in tier3_kw):
        return 3
        
    # Tier 4: Blogs / Forums / others
    return 4

def discover_and_classify_platform(platform: str) -> tuple[str, str, str]:
    """
    Classify input platform into category, vendor, and technology.
    If not in the local dictionary, performs a search to infer details.
    Returns (category, vendor, technology).
    """
    platform_key = platform.strip().lower()
    
    # Check dictionary first
    if platform_key in PLATFORM_CATEGORIES:
        info = PLATFORM_CATEGORIES[platform_key]
        return info["category"], info["vendor"], info["technology"]
        
    # If not found, do dynamic discovery
    logger.info(f"Platform '{platform}' not in predefined list. Performing dynamic discovery...")
    # Search for the platform name
    search_query = f"{platform} wiki OR official site"
    results = search_log_sources(search_query, max_results=3)
        
    # Default values
    inferred_category = "Unknown"
    inferred_vendor = "Unknown Vendor"
    inferred_technology = platform.strip().capitalize()
    
    # Parse results
    combined_text = ""
    for r in results:
        combined_text += f" {r.get('title', '')} {r.get('snippet', '')} {r.get('url', '')}"
    combined_text = combined_text.lower()
    
    # Check for category keywords
    category_keywords = {
        "Operating System": ["operating system", "linux", "distro", "windows", "unix", "kernel", "os", "debian", "redhat", "centos", "fedora"],
        "Database": ["database", "sql", "nosql", "db", "rdbms", "query", "relational", "key-value store"],
        "Web Server": ["web server", "http server", "proxy", "reverse proxy", "load balancer"],
        "Application Server": ["application server", "app server", "java server", "servlet", "jakarta ee"],
        "Virtualization": ["virtualization", "hypervisor", "virtual machine", "vm", "kvm", "xen"],
        "Container Platform": ["container", "docker", "kubernetes", "podman", "orchestration", "containerization"],
        "Cloud Platform": ["cloud platform", "aws", "gcp", "azure", "cloud service", "cloud computing"],
        "Firewall": ["firewall", "security appliance", "utm", "packet filter", "network security"],
        "Router": ["router", "routing", "switch", "network switch", "gateway"],
        "Storage": ["storage", "nas", "san", "distributed storage", "filesystem", "object storage"],
        "Middleware": ["middleware", "message broker", "rabbitmq", "kafka", "mq", "message queue"],
        "Monitoring Tool": ["monitoring", "observability", "metrics", "tracing", "logging tool", "apm"]
    }
    
    for cat, keywords in category_keywords.items():
        if any(kw in combined_text for kw in keywords):
            inferred_category = cat
            break
            
    # Check for vendor
    vendors = [
        "Apache", "Microsoft", "Google", "Amazon", "Oracle", "IBM", "Red Hat", "SUSE", "VMware", "Broadcom",
        "HashiCorp", "Cisco", "Juniper", "Fortinet", "F5", "NGINX", "Docker", "CNCF", "Linux Foundation"
    ]
    for v in vendors:
        if re.search(r'\b' + re.escape(v.lower()) + r'\b', combined_text):
            inferred_vendor = v
            break
            
    return inferred_category, inferred_vendor, inferred_technology

def expand_queries_progressive(platform: str, category: str, stage: int, version: str = "", service: str = "") -> list[str]:
    """
    Generates search queries for a given stage.
    """
    v_term = f" {version}" if version else ""
    s_term = f" {service}" if service else ""
    pf = f"{platform}{v_term}{s_term}".strip()
    
    if stage == 1:
        # Stage 1: 3 high-value queries
        return [
            f'{pf} "actual log" site:github.com/issues',
            f'{pf} "real log" site:github.com',
            f'{pf} "log sample" troubleshooting'
        ]
    elif stage == 2:
        # Stage 2: troubleshooting and error output
        return [
            f'{pf} "stack trace" site:github.com',
            f'{pf} "error output" forum',
            f'{pf} "fatal log" Exception',
            f'{pf} "exception log" stack trace'
        ]
    elif stage == 3:
        # Stage 3: Full expansion & Artifact Query Family
        return [
            f'{pf} "incident report" logs',
            f'{pf} "crash logs" postmortem',
            f'{pf} "outage logs" actual',
            f'{pf} "diagnostics" raw logs',
            f'{pf} "bug report" log'
        ]
    return []

def apply_per_source_limit(logs: list) -> list:
    """
    Ensure we have at most 3 validated logs per unique source URL.
    """
    counts = {}
    limited_logs = []
    for log in logs:
        url = str(log.get("source_url") or "").strip()
        is_valid = log.get("validation", {}).get("valid", False)
        if is_valid:
            counts[url] = counts.get(url, 0) + 1
            if counts[url] > 3:
                # Exceeds limit, make it invalid
                log_copy = log.copy()
                log_copy["validation"] = log["validation"].copy()
                log_copy["validation"]["valid"] = False
                log_copy["validation"]["reason"] = "Per-source limit of 3 validated logs reached"
                log_copy["validation"]["confidence"] = 0
                limited_logs.append(log_copy)
            else:
                limited_logs.append(log)
        else:
            limited_logs.append(log)
    return limited_logs

def score_result(res: dict) -> tuple[int, int]:
    url = res.get("url", "").lower()
    title = res.get("title", "").lower()
    snippet = res.get("snippet", "").lower()
    
    score = 0
    
    # 1. Highest Priority (+30 to +50 points)
    highest_domains = ["github.com", "stackoverflow.com", "serverfault.com"]
    highest_paths = ["/issues", "/discussions"]
    highest_keywords = ["incident report", "postmortem", "rca", "outage"]
    
    for domain in highest_domains:
        if domain in url:
            score += 50
            
    for path in highest_paths:
        if path in url:
            score += 40
            
    for kw in highest_keywords:
        if kw in url or kw in title or kw in snippet:
            score += 30
            
    # 2. Medium Priority (+25 points)
    has_troubleshooting = any(kw in url or kw in title or kw in snippet for kw in ["troubleshoot", "debug", "error", "fix", "solve", "support"])
    has_logs = any(kw in url or kw in title or kw in snippet for kw in ["raw log", "syslog dump", "access log", "error log", "incident log", "log dump"])
    
    if has_troubleshooting and has_logs:
        score += 25
    elif has_troubleshooting or has_logs:
        score += 15
        
    # 3. Lowest Priority (-30 points)
    lowest_keywords = ["docs", "documentation", "tutorial", "tutorials", "knowledge-center", "knowledge", "marketing", "pricing", "feature", "features", "about", "article", "guide", "reference", "blog", "medium.com", "dev.to"]
    for kw in lowest_keywords:
        if kw in url or kw in title or kw in snippet:
            score -= 30
            
    # Determine rank
    rank = classify_tier(url, title, snippet)
    return score, rank


def search_log_sources(query: str, max_results: int = 7) -> list:
    """
    Search sequentially through DuckDuckGo, Bing, Brave, Yahoo, AOL.
    Uses the first provider that returns >0 results.
    """
    logger.info(f"Searching for query: {query}")
    
    for provider in PROVIDERS:
        try:
            res = provider.search(query, max_results=max_results)
            results = res.get("results", [])
            status_code = res.get("status_code", 0)
            error = res.get("error", "success")
            duration = res.get("duration", 0.0)
        except Exception as e:
            results = []
            status_code = 0
            error = f"Exception: {str(e)}"
            duration = 0.0
            
        diag_entry = {
            "Query": query,
            "Provider": provider.name,
            "HTTP Status": status_code,
            "Results Parsed": len(results),
            "URLs Returned": len(results),
            "Duration": duration,
            "Failure Reason": error
        }
        get_thread_diagnostics().append(diag_entry)
        
        if results:
            logger.info(f"{provider.name} returned {len(results)} results for query: {query}")
            results.sort(key=lambda x: score_result(x)[0], reverse=True)
            return results
            
    logger.warning(f"All providers returned zero results for query: {query}")
    return []



class ScrapedContent(str):
    status = "no_logs_found"
    reason = ""
    failure_stage = ""
    is_log_rich = False
    likelihood_score = 0


class ScrapedText(str):
    pass


def classify_page_likelihood(text: str) -> tuple[bool, int]:
    if not text:
        return False, 0
    text_lower = text.lower()
    indicators = [
        "error", "warn", "fatal", "exception", "traceback",
        "stack trace", "event id", "ora-", "critical", "segmentation fault"
    ]
    score = sum(text_lower.count(ind) for ind in indicators)
    is_log_rich = score >= 2
    return is_log_rich, score


def scrape_url_content(url: str, timeout: int = 10) -> ScrapedContent:
    """
    Fetch the content of a url and extract raw text using BeautifulSoup.
    Optimized for code blocks and pre tags where logs are typically stored.
    """
    # Prevent scrapers from visiting domains that require JavaScript/heavy cookies or block standard requests
    if any(x in url for x in ["medium.com", "linkedin.com", "twitter.com", "superuser.com"]):
        res = ScrapedContent("")
        res.status = "access_denied"
        res.reason = "Domain blocklisted"
        res.failure_stage = "search_filtering"
        return res
    
    # Check if domain has been downgraded due to poor yield
    try:
        from backend.db_manager import is_downgraded_domain
        parsed_domain = urlparse(url).netloc.lower()
        if parsed_domain.startswith("www."):
            parsed_domain = parsed_domain[4:]
        if is_downgraded_domain(parsed_domain):
            logger.info(f"Skipping downgraded domain: {parsed_domain} (URL: {url})")
            res = ScrapedContent("")
            res.status = "access_denied"
            res.reason = f"Domain '{parsed_domain}' downgraded: 20+ crawls with 0 validated logs"
            res.failure_stage = "search_filtering"
            return res
    except Exception as e:
        logger.debug(f"Domain downgrade check failed for {url}: {e}")
    
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5"
    }
    
    try:
        logger.info(f"Scraping URL: {url}")
        from backend.db_manager import log_agent_event
        log_agent_event("opening_url", f"Opening URL {url}...")
        
        # Convert Pastebin URLs to raw
        if "pastebin.com" in url and "/raw/" not in url:
            path_parts = urlparse(url).path.strip('/').split('/')
            if path_parts:
                paste_id = path_parts[0]
                if paste_id and '/' not in paste_id:
                    url = f"https://pastebin.com/raw/{paste_id}"
                    logger.info(f"Converted Pastebin URL to raw: {url}")

        # Modify GitHub URLs to fetch raw/clean files if they point to blobs
        if "github.com" in url and "/blob/" in url:
            url = url.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")
        
        response = requests.get(url, headers=headers, timeout=timeout, verify=True)
        if response.status_code != 200:
            logger.warning(f"Failed to fetch {url}: HTTP {response.status_code}")
            res = ScrapedContent("")
            res.status = "access_denied"
            res.reason = f"HTTP {response.status_code}"
            res.failure_stage = "crawling"
            return res
        
        # If it's raw text/markdown (like github raw), return it directly
        content_type = response.headers.get("Content-Type", "")
        if "text/plain" in content_type or url.endswith(".txt") or "raw.githubusercontent.com" in url or "pastebin.com/raw/" in url:
            raw_text = response.text[:15000]
            is_rich, score = classify_page_likelihood(raw_text)
            from backend.db_manager import log_agent_event
            log_agent_event("page_classified", f"URL: {url} | Score: {score} | Log-rich: {is_rich}")
            if not is_rich:
                res = ScrapedContent("")
                res.status = "low_value_page"
                res.reason = f"Low log page likelihood (score: {score})"
                res.failure_stage = "pre_extraction_filter"
                res.is_log_rich = False
                res.likelihood_score = score
                return res
            res = ScrapedContent(raw_text)
            res.status = "no_logs_found"
            res.reason = "No machine-generated logs detected"
            res.failure_stage = "validation"
            res.is_log_rich = True
            res.likelihood_score = score
            return res
            
        soup = BeautifulSoup(response.text, "html.parser")
        
        # GitHub Gist raw detection and fetch
        if "gist.github.com" in url and "/raw" not in url:
            raw_a = soup.find("a", class_="btn-sm", string=re.compile(r"Raw", re.I)) or soup.find("a", href=re.compile(r"/raw/"))
            if raw_a and raw_a.get("href"):
                raw_url = raw_a.get("href")
                if raw_url.startswith("/"):
                    raw_url = "https://gist.github.com" + raw_url
                try:
                    logger.info(f"Fetching raw Gist content from: {raw_url}")
                    raw_resp = requests.get(raw_url, headers=headers, timeout=timeout, verify=True)
                    if raw_resp.status_code == 200:
                        raw_text = raw_resp.text[:15000]
                        is_rich, score = classify_page_likelihood(raw_text)
                        from backend.db_manager import log_agent_event
                        log_agent_event("page_classified", f"URL: {url} | Score: {score} | Log-rich: {is_rich}")
                        if not is_rich:
                            res = ScrapedContent("")
                            res.status = "low_value_page"
                            res.reason = f"Low log page likelihood (score: {score})"
                            res.failure_stage = "pre_extraction_filter"
                            res.is_log_rich = False
                            res.likelihood_score = score
                            return res
                        res = ScrapedContent(raw_text)
                        res.status = "no_logs_found"
                        res.reason = "No machine-generated logs detected"
                        res.failure_stage = "validation"
                        res.is_log_rich = True
                        res.likelihood_score = score
                        return res
                except Exception as ex:
                    logger.warning(f"Failed to fetch raw Gist: {ex}")
        
        # Remove unwanted script/style elements
        for element in soup(["script", "style", "nav", "footer", "header", "aside"]):
            element.decompose()
            
        # Classify HTML page visible text before extraction
        page_text = soup.get_text()
        is_rich, score = classify_page_likelihood(page_text)
        from backend.db_manager import log_agent_event
        log_agent_event("page_classified", f"URL: {url} | Score: {score} | Log-rich: {is_rich}")
        if not is_rich:
            res = ScrapedContent("")
            res.status = "low_value_page"
            res.reason = f"Low log page likelihood (score: {score})"
            res.failure_stage = "pre_extraction_filter"
            res.is_log_rich = False
            res.likelihood_score = score
            return res

        # Give higher priority to log-rich tags and classes
        log_sections = []
        for tag in soup.find_all(["pre", "code", "textarea"]):
            text = tag.get_text().strip()
            if len(text) > 40:  # Ignore short snippets
                log_sections.append(text)
                
        # Also check elements with class names typical of code blocks
        for tag in soup.find_all(class_=re.compile(r'\b(code|highlight|terminal|console|blob-code-inner|log-content)\b', re.I)):
            text = tag.get_text().strip()
            if len(text) > 40 and text not in log_sections:
                # Avoid nesting duplicates
                if not any(text in existing for existing in log_sections):
                    log_sections.append(text)
                
        combined_logs = "\n\n--- Code/Log Block ---\n\n".join(log_sections) if log_sections else ""
        
        # If the log blocks are too small, also append general body text
        if len(combined_logs) < 300:
            body_text = soup.get_text(separator="\n")
            lines = [line.strip() for line in body_text.splitlines() if line.strip()]
            cleaned_text = "\n".join(lines)
            if combined_logs:
                combined_logs = combined_logs + "\n\n--- Body Text Fallback ---\n\n" + cleaned_text
            else:
                combined_logs = cleaned_text
                
        res = ScrapedContent(combined_logs[:20000])
        res.status = "no_logs_found"
        res.reason = "No machine-generated logs detected"
        res.failure_stage = "validation"
        res.is_log_rich = True
        res.likelihood_score = score
        return res
        
    except Exception as e:
        logger.error(f"Error scraping {url}: {e}")
        res = ScrapedContent("")
        res.status = "crawl_failed"
        res.reason = str(e)
        res.failure_stage = "crawling"
        return res

CATEGORY_CATALOGS = {
    "Cloud Platform": ["docs.aws.amazon.com", "cloud.google.com", "learn.microsoft.com"],
    "Cloud": ["docs.aws.amazon.com", "cloud.google.com", "learn.microsoft.com"],
    "Container Platform": ["docs.docker.com", "kubernetes.io", "github.com"],
    "Container": ["docs.docker.com", "kubernetes.io", "github.com"],
    "Containers": ["docs.docker.com", "kubernetes.io", "github.com"],
    "Database": ["postgresql.org", "oracle.com", "mongodb.com", "mysql.com"],
    "Databases": ["postgresql.org", "oracle.com", "mongodb.com", "mysql.com"],
    "Operating System": ["ubuntu.com", "redhat.com", "opensuse.org", "ibm.com"],
    "Operating Systems": ["ubuntu.com", "redhat.com", "opensuse.org", "ibm.com"],
    "Web Server": ["nginx.org", "httpd.apache.org"],
    "Web Servers": ["nginx.org", "httpd.apache.org"]
}

def generate_direct_source_urls(platform: str, product_name: str, service: str, category: str) -> list:
    pf = f"{platform} {product_name}".strip()
    pf_lower = pf.lower()
    srv_lower = service.lower() if service else ""
    
    results = []
    
    if "nginx" in pf_lower:
        results.append({
            "url": "https://nginx.org/en/docs/http/ngx_http_upstream_module.html" if "upstream" in srv_lower else "https://nginx.org/en/docs/ngx_core_module.html",
            "title": f"Nginx {service} official documentation",
            "snippet": "Official Nginx server documentation module and logs"
        })
    elif "docker" in pf_lower:
        results.append({
            "url": "https://docs.docker.com/engine/daemon/logs/" if "daemon" in srv_lower else "https://docs.docker.com/engine/reference/commandline/dockerd/",
            "title": f"Docker {service} logging documentation",
            "snippet": "How to read and configure Docker daemon logs"
        })
    elif "apache" in pf_lower or "httpd" in pf_lower:
        results.append({
            "url": "https://httpd.apache.org/docs/current/logs.html",
            "title": "Apache HTTP Server Log Files",
            "snippet": "Description and examples of Apache log formats"
        })
    elif "kubernetes" in pf_lower or "k8s" in pf_lower:
        results.append({
            "url": "https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/",
            "title": "Pod Lifecycle and Logs - Kubernetes",
            "snippet": "Official Kubernetes pod runtime documentation"
        })
    elif "aws" in pf_lower or "amazon" in pf_lower:
        if "cloudtrail" in pf_lower or "cloudtrail" in srv_lower:
            results.append({
                "url": "https://docs.aws.amazon.com/awscloudtrail/latest/userguide/cloudtrail-log-file-examples.html",
                "title": "AWS CloudTrail Log File Examples",
                "snippet": "JSON format CloudTrail event log examples"
            })
        else:
            results.append({
                "url": "https://docs.aws.amazon.com/lambda/latest/dg/monitoring-functions-logs.html",
                "title": "AWS Monitoring Functions and CloudWatch Logs",
                "snippet": "Documentation for AWS service and event logging"
            })
    elif "postgres" in pf_lower:
        results.append({
            "url": "https://www.postgresql.org/docs/current/runtime-config-logging.html",
            "title": "PostgreSQL Error Reporting and Logging",
            "snippet": "Server configuration and log format documentation"
        })
    elif "mongodb" in pf_lower:
        results.append({
            "url": "https://www.mongodb.com/docs/manual/reference/log-messages/",
            "title": "MongoDB Log Messages Reference",
            "snippet": "Structured JSON log formats and severity documentation"
        })
    elif "ubuntu" in pf_lower:
        results.append({
            "url": "https://ubuntu.com/",
            "title": "Ubuntu Command Line Basics and syslog",
            "snippet": "Basic syslog configuration and log locations in Ubuntu"
        })
        
    results.append({
        "url": f"https://github.com/search?q={platform}+{service}+error+logs",
        "title": f"GitHub Search: {platform} {service} logs",
        "snippet": "Public code repositories containing log examples"
    })
    results.append({
        "url": f"https://stackoverflow.com/questions/tagged/{platform}",
        "title": f"StackOverflow questions tagged {platform}",
        "snippet": "Community troubleshooting questions and log dumps"
    })
    
    return results

def is_low_value_url(url: str) -> bool:
    if not url:
        return True
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url.lower().strip())
        path = parsed.path
        
        # 1. Navigation / Search patterns
        if "/search" in path or "/tag/" in path or "/category/" in path or "/archive/" in path:
            return True
            
        # 2. Generic homepages
        domain = parsed.netloc
        if domain.startswith("www."):
            domain = domain[4:]
            
        is_homepage = path in ("", "/", "/index.html", "/index.htm", "/index.php", "/index.asp", "/index.aspx")
        generic_domains = {
            "gist.github.com", "github.com", "stackoverflow.com", "medium.com", 
            "dev.to", "linkedin.com", "twitter.com", "x.com", "superuser.com", 
            "google.com", "bing.com", "yahoo.com", "aol.com", "brave.com"
        }
        if domain in generic_domains and is_homepage:
            return True
            
        return False
    except Exception:
        return True

def collect_logs_from_web(platform: str, version: str = "", service: str = "", count: int = 5, product_name: str = "", max_urls: int = None) -> str:
    """
    Search and scrape logs from the web for a platform, version and service.
    Returns aggregated text containing raw log entries found.
    """
    clear_thread_diagnostics()

    if count is None:
        count = 5
        
    # Ensure variables are stripped
    platform = platform.strip()
    version = version.strip() if version else ""
    service = service.strip() if service else ""
    product_name = product_name.strip() if product_name else ""
    
    # 1. Discover and classify platform
    category, vendor, technology = discover_and_classify_platform(platform)
    logger.info(f"Discovered platform classification: category={category}, vendor={vendor}, technology={technology}")
    
    # Combination logic for platform + product_name
    base_terms = []
    if product_name:
        if platform.lower() == "aws" and product_name.lower().startswith("amazon "):
            short_prod = product_name[7:].strip()
            base_terms.append(f"AWS {short_prod}")
            base_terms.append(product_name)
        else:
            if platform.lower() in product_name.lower():
                base_terms.append(product_name)
            elif product_name.lower() in platform.lower():
                base_terms.append(platform)
            else:
                base_terms.append(f"{platform} {product_name}")
    else:
        base_terms.append(platform)
        
    all_scraped_contents = []
    visited_urls = set()
    raw_results_count = 0
    search_results_ranked = []
    seen_search_urls = set()
    url_info_map = {}
    
    final_extracted_logs = []
    all_queries = []
    extraction_error = None
    
    def process_search_results(search_results, query_used):
        nonlocal raw_results_count
        raw_results_count += len(search_results)
        
        # Pre-fill URL info map
        for pos, res in enumerate(search_results, 1):
            url = res.get("url", "").strip()
            if url and url not in seen_search_urls:
                seen_search_urls.add(url)
                from urllib.parse import urlparse
                try:
                    domain = urlparse(url).netloc
                    if domain.startswith("www."):
                        domain = domain[4:]
                except Exception:
                    domain = ""
                _, r_rank = score_result(res)
                search_results_ranked.append({
                    "rank": len(search_results_ranked) + 1,
                    "url": url,
                    "domain": domain,
                    "source_rank": r_rank,
                    "title": res.get("title", "")
                })
                
                from backend.validator import classify_source
                source_type = classify_source(url, res.get("title", ""))
                
                if is_low_value_url(url):
                    url_info_map[url] = {
                        "Platform": platform,
                        "Product": product_name,
                        "Log Type": service,
                        "URL": url,
                        "Title": res.get("title", ""),
                        "Source Rank": r_rank,
                        "Search Rank": len(search_results_ranked),
                        "Search Query Used": query_used,
                        "Search Position": pos,
                        "Source Type": source_type,
                        "Crawled": "No",
                        "Logs Extracted": 0,
                        "Logs Validated": 0,
                        "Logs Rejected": 0,
                        "Status": "low_value_url",
                        "Reason": "Navigation page / Homepage / Search page",
                        "Failure Stage": "pre_crawl_filter"
                    }
                else:
                    url_info_map[url] = {
                        "Platform": platform,
                        "Product": product_name,
                        "Log Type": service,
                        "URL": url,
                        "Title": res.get("title", ""),
                        "Source Rank": r_rank,
                        "Search Rank": len(search_results_ranked),
                        "Search Query Used": query_used,
                        "Search Position": pos,
                        "Source Type": source_type,
                        "Crawled": "No",
                        "Logs Extracted": 0,
                        "Logs Validated": 0,
                        "Logs Rejected": 0,
                        "Status": "no_logs_found",
                        "Reason": "Search result not visited (target logs met or stage bypassed)",
                        "Failure Stage": "search_filtering"
                    }
        
        # Sort results based on domain yield prioritization
        def get_crawling_priority(res_item):
            t_url = res_item.get("url", "")
            try:
                from urllib.parse import urlparse
                t_domain = urlparse(t_url).netloc.lower()
                if t_domain.startswith("www."):
                    t_domain = t_domain[4:]
            except Exception:
                t_domain = ""
            
            from backend.db_manager import get_domain_metrics
            metrics = get_domain_metrics(t_domain)
            t_crawled = metrics["urls_crawled"]
            t_yield = metrics["yield_score"]
            
            relevance, _ = score_result(res_item)
            
            if t_yield > 0:
                domain_bonus = 1000 + t_yield * 100
            elif t_crawled == 0:
                domain_bonus = 500
            else:
                domain_bonus = 0
            return domain_bonus + relevance

        sorted_results = sorted(search_results, key=get_crawling_priority, reverse=True)

        from backend.db_manager import update_agent_status_field
        update_agent_status_field(
            current_url=0,
            total_urls=len(sorted_results),
            current_phase="Crawling"
        )

        # Crawl URLs
        for res in sorted_results:
            url = res["url"]
            if is_low_value_url(url):
                continue
            
            # Check domain downgrade and skip before crawler runs
            try:
                from urllib.parse import urlparse
                from backend.db_manager import is_downgraded_domain
                parsed_domain = urlparse(url).netloc.lower()
                if parsed_domain.startswith("www."):
                    parsed_domain = parsed_domain[4:]
                if is_downgraded_domain(parsed_domain):
                    if url in url_info_map:
                        url_info_map[url]["Status"] = "domain_downgraded"
                        url_info_map[url]["Reason"] = f"Domain '{parsed_domain}' downgraded: 20+ crawls with 0 validated logs"
                        url_info_map[url]["Failure Stage"] = "pre_crawl_filter"
                    continue
            except Exception:
                pass

            if url not in visited_urls:
                if max_urls is not None and len(visited_urls) >= max_urls:
                    logger.info(f"Capping crawls at max_urls: {max_urls}")
                    break
                
                visited_urls.add(url)
                time.sleep(random.uniform(0.2, 0.6))
                scraped_text = scrape_url_content(url)
                
                # Update status based on scraped result
                if url in url_info_map:
                    info = url_info_map[url]
                    if scraped_text and getattr(scraped_text, "is_log_rich", True):
                        info["Crawled"] = "Yes"
                        info["Status"] = "no_logs_found"
                        info["Reason"] = "No logs extracted"
                        info["Failure Stage"] = "extraction"
                        info["Likelihood Score"] = getattr(scraped_text, "likelihood_score", 0)
                        info["Classified As"] = "log-rich"
                    else:
                        status = getattr(scraped_text, "status", "")
                        if status == "low_value_page":
                            info["Crawled"] = "Yes"
                            info["Status"] = "low_value_page"
                            info["Reason"] = scraped_text.reason
                            info["Failure Stage"] = scraped_text.failure_stage
                            info["Likelihood Score"] = getattr(scraped_text, "likelihood_score", 0)
                            info["Classified As"] = "low-value"
                        else:
                            info["Crawled"] = "No"
                            if hasattr(scraped_text, "status") and scraped_text.status:
                                info["Status"] = scraped_text.status
                                info["Reason"] = scraped_text.reason
                                info["Failure Stage"] = scraped_text.failure_stage
                            else:
                                if any(x in url for x in ["medium.com", "linkedin.com", "twitter.com", "superuser.com"]):
                                    info["Status"] = "access_denied"
                                    info["Reason"] = "Domain blocklisted"
                                    info["Failure Stage"] = "search_filtering"
                                else:
                                    info["Status"] = "crawl_failed"
                                    info["Reason"] = "Empty response or fetch error"
                                    info["Failure Stage"] = "crawling"
                                    
                if scraped_text and getattr(scraped_text, "is_log_rich", True):
                    title = res.get("title", "")
                    _, r_rank = score_result(res)
                    block_text = (
                        f"Source URL: {url}\n"
                        f"Source Title: {title}\n"
                        f"Crawl Context: Platform={platform}, Product={product_name}, Version={version}, Service={service}, QueryUsed={query_used}, SourceRank={r_rank}\n"
                        f"{scraped_text}"
                    )
                    all_scraped_contents.append(block_text)

    # Calculate total queries across all 3 stages for progress reporting
    total_queries = 0
    for stg in [1, 2, 3]:
        for base in base_terms:
            total_queries += len(expand_queries_progressive(base, category, stg, version, service))
    query_counter = 0

    # Progressive stage loop: Stage 1, Stage 2, Stage 3
    for stage in [1, 2, 3]:
        # Expand queries for this stage
        stage_queries = []
        for base in base_terms:
            stage_queries.extend(expand_queries_progressive(base, category, stage, version, service))
        all_queries.extend(stage_queries)
        
        logger.info(f"Stage {stage} generated queries: {stage_queries}")
        print(f"GENERATED_SEARCH_QUERY (Stage {stage}): {stage_queries}")
        
        for query in stage_queries:
            query_counter += 1
            from backend.db_manager import update_agent_status_field
            update_agent_status_field(
                current_query=query_counter,
                total_queries=total_queries,
                current_phase="Searching"
            )
            
            search_results = search_log_sources(query, max_results=3)
            process_search_results(search_results, query)
            
        # If all search providers returned zero results, activate DIRECT_SOURCE_DISCOVERY site queries
        if len(seen_search_urls) == 0:
            logger.info("No search results found with standard queries. Activating DIRECT_SOURCE_DISCOVERY site queries...")
            domains = CATEGORY_CATALOGS.get(category, []) or ["github.com", "stackoverflow.com"]
            direct_queries = []
            for domain in domains:
                direct_queries.append(f"site:{domain} {platform} {service} logs")
                
            for query in direct_queries:
                search_results = search_log_sources(query, max_results=3)
                process_search_results(search_results, query)
                
        # If STILL no search results found, fallback to direct URLs
        if len(seen_search_urls) == 0:
            logger.info("Search engines returned zero results. Activating DIRECT_SOURCE_DISCOVERY direct URLs...")
            direct_results = generate_direct_source_urls(platform, product_name, service, category)
            process_search_results(direct_results, "DIRECT_DISCOVERY_FALLBACK_URL")
            
            # Record a virtual diagnostic entry for direct discovery
            diag_entry = {
                "Query": "DIRECT_DISCOVERY_FALLBACK_URL",
                "Provider": "DirectDiscovery",
                "HTTP Status": 200,
                "Results Parsed": len(direct_results),
                "URLs Returned": len(direct_results),
                "Duration": 0.0,
                "Failure Reason": "success"
            }
            get_thread_diagnostics().append(diag_entry)
            
        # After searching/scraping this stage, let's extract and validate logs from all scraped content so far
        if all_scraped_contents:
            from backend.db_manager import update_agent_status_field
            update_agent_status_field(current_phase="Extracting")
            accumulated_text = "\n\n=== NEW SOURCE ===\n\n".join(all_scraped_contents)
            
            # Import extract/validate functions inline to avoid circular dependencies
            from backend.extractor import parse_logs_with_llm, parse_logs_with_regex, map_source_urls, safe_to_text
            from backend.validator import validate_logs_with_claude

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
            
            try:
                llm_logs = parse_logs_with_llm(accumulated_text, platform, version, service, count)
                regex_logs = parse_logs_with_regex(accumulated_text, platform, version, service, count)
                
                # Map source URLs (which also extracts query_used and source_rank)
                llm_logs = map_source_urls(llm_logs, accumulated_text)
                regex_logs = map_source_urls(regex_logs, accumulated_text)
                
                logs = merge_and_deduplicate_logs(llm_logs, regex_logs)
                
                if logs:
                    from backend.db_manager import update_agent_status_field
                    update_agent_status_field(current_phase="Validating")
                    logs = validate_logs_with_claude(logs, accumulated_text, platform, version)
                    
                    # Apply per-source limit: at most 3 validated logs per unique source URL
                    logs = apply_per_source_limit(logs)
                    
                    # Keep final extracted logs
                    final_extracted_logs = logs
                    
                    # Reset counts for updating (since we validate stage-by-stage with full accumulated text)
                    for info in url_info_map.values():
                        info["Logs Extracted"] = 0
                        info["Logs Validated"] = 0
                        info["Logs Rejected"] = 0
                        
                    # Update extraction/validation counts from current run
                    for log in logs:
                        log_url = log.get("source_url")
                        if log_url in url_info_map:
                            info = url_info_map[log_url]
                            info["Logs Extracted"] += 1
                            if log.get("validation", {}).get("valid", False):
                                info["Logs Validated"] += 1
                            else:
                                info["Logs Rejected"] += 1
                                
                    # Update status/reason/failure stage for visited URLs
                    for url in visited_urls:
                        if url in url_info_map:
                            info = url_info_map[url]
                            if info["Logs Validated"] > 0:
                                info["Status"] = "validated"
                                info["Reason"] = ""
                                info["Failure Stage"] = ""
                            elif info["Logs Extracted"] > 0:
                                info["Status"] = "no_logs_found"
                                info["Reason"] = "Logs extracted but validation rejected all"
                                info["Failure Stage"] = "validation"
                            else:
                                if info["Crawled"] == "Yes":
                                    info["Status"] = "no_logs_found"
                                    info["Reason"] = "No logs extracted"
                                    info["Failure Stage"] = "extraction"
                                    
                    # Count validated logs
                    validated_count = sum(1 for log in logs if log.get("validation", {}).get("valid", False))
                    logger.info(f"End of Stage {stage}: {validated_count} validated logs found (Target: {count})")
                    
                    if validated_count >= count:
                        logger.info(f"Target count of {count} validated logs satisfied at Stage {stage}. Bypassing subsequent stages.")
                        break
            except Exception as extraction_err:
                logger.error(f"Extraction or validation failed at Stage {stage}: {extraction_err}", exc_info=True)
                extraction_error = extraction_err
                
                # Update status/reason/failure stage for visited URLs to reflect extraction failure
                for url in visited_urls:
                    if url in url_info_map:
                        info = url_info_map[url]
                        if info.get("Logs Validated", 0) == 0:
                            if info["Crawled"] == "Yes":
                                info["Status"] = "no_logs_found"
                                info["Reason"] = f"Extraction/validation error: {extraction_err}"
                                info["Failure Stage"] = "extraction"
                            else:
                                info["Status"] = "crawl_failed"
                                if not info.get("Reason") or info.get("Reason") == "Search result not visited (target logs met or stage bypassed)":
                                    info["Reason"] = f"Extraction/validation error: {extraction_err}"
                                    info["Failure Stage"] = "crawling"
                
                # Exit stage loop so we do not repeat the failure in subsequent stages
                break
                    
    combined_text = "\n\n=== NEW SOURCE ===\n\n".join(all_scraped_contents)
    res_obj = ScrapedText(combined_text)
    res_obj.queries = all_queries
    res_obj.raw_results_count = len(url_info_map)
    res_obj.visited_urls = list(visited_urls)
    res_obj.search_results_ranked = search_results_ranked
    res_obj.extracted_logs = final_extracted_logs
    res_obj.url_info_map = url_info_map
    res_obj.extraction_error = extraction_error
    
    # Compile enriched search diagnostics
    enriched_diagnostics = []
    thread_diags = get_thread_diagnostics()
    for diag in thread_diags:
        diag_query = diag.get("Query", "")
        diag_provider = diag.get("Provider", "")
        
        urls_returned = diag.get("URLs Returned", 0)
        urls_crawled = 0
        urls_rejected = 0
        
        for url, info in url_info_map.items():
            if info.get("Search Query Used") == diag_query:
                if diag.get("Results Parsed", 0) > 0 or diag_query == "DIRECT_DISCOVERY_FALLBACK_URL":
                    if info.get("Crawled") == "Yes":
                        urls_crawled += 1
                    if info.get("Logs Rejected", 0) > 0:
                        urls_rejected += 1
                        
        enriched_diagnostics.append({
            "Platform": platform,
            "Product": product_name,
            "Log Type": service,
            "Query": diag_query,
            "Provider": diag_provider,
            "HTTP Status": diag.get("HTTP Status", 0),
            "Results Parsed": diag.get("Results Parsed", 0),
            "URLs Returned": urls_returned,
            "URLs Crawled": urls_crawled,
            "URLs Rejected": urls_rejected,
            "Duration": round(diag.get("Duration", 0.0), 3),
            "Failure Reason": diag.get("Failure Reason", "")
        })
        
    res_obj.search_diagnostics = enriched_diagnostics
    return res_obj


if __name__ == "__main__":
    # Quick standalone test
    print("Testing search...")
    res = collect_logs_from_web("nginx", "1.25", count=2)
    print(f"Scraped {len(res)} characters.")
