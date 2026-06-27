import os
import re
import json
import logging
from datetime import datetime
from backend import db_manager
from backend.crawler import discover_and_classify_platform

logger = logging.getLogger(__name__)

# Predefined Seed Catalog for 9 Target Environments
SEED_CATALOG = {
    "AWS service catalog": [
        {"name": "Amazon S3", "category": "Cloud Platform", "vendor": "Amazon Web Services"},
        {"name": "Amazon EC2", "category": "Cloud Platform", "vendor": "Amazon Web Services"},
        {"name": "Amazon RDS", "category": "Database", "vendor": "Amazon Web Services"},
        {"name": "AWS Lambda", "category": "Cloud Platform", "vendor": "Amazon Web Services"},
        {"name": "Amazon DynamoDB", "category": "Database", "vendor": "Amazon Web Services"}
    ],
    "Azure services": [
        {"name": "Azure Blob Storage", "category": "Cloud Platform", "vendor": "Microsoft Azure"},
        {"name": "Azure Virtual Machines", "category": "Cloud Platform", "vendor": "Microsoft Azure"},
        {"name": "Azure SQL Database", "category": "Database", "vendor": "Microsoft Azure"},
        {"name": "Azure Functions", "category": "Cloud Platform", "vendor": "Microsoft Azure"}
    ],
    "GCP services": [
        {"name": "Google Cloud Storage", "category": "Cloud Platform", "vendor": "Google Cloud Platform"},
        {"name": "Google Compute Engine", "category": "Cloud Platform", "vendor": "Google Cloud Platform"},
        {"name": "Google Cloud SQL", "category": "Database", "vendor": "Google Cloud Platform"},
        {"name": "Google Cloud Functions", "category": "Cloud Platform", "vendor": "Google Cloud Platform"}
    ],
    "CNCF landscape": [
        {"name": "Prometheus", "category": "Monitoring Tool", "vendor": "CNCF"},
        {"name": "Envoy", "category": "Web Server", "vendor": "CNCF"},
        {"name": "CoreDNS", "category": "Router", "vendor": "CNCF"},
        {"name": "containerd", "category": "Container Platform", "vendor": "CNCF"}
    ],
    "Docker ecosystem": [
        {"name": "Docker Engine", "category": "Container Platform", "vendor": "Docker Inc."},
        {"name": "Docker Compose", "category": "Container Platform", "vendor": "Docker Inc."},
        {"name": "Docker Registry", "category": "Container Platform", "vendor": "Docker Inc."}
    ],
    "Kubernetes ecosystem": [
        {"name": "Kubelet", "category": "Container Platform", "vendor": "Kubernetes"},
        {"name": "Kube-proxy", "category": "Container Platform", "vendor": "Kubernetes"},
        {"name": "Kubectl", "category": "Container Platform", "vendor": "Kubernetes"}
    ],
    "Oracle products": [
        {"name": "Oracle Database", "category": "Database", "vendor": "Oracle Corporation"},
        {"name": "WebLogic Server", "category": "Application Server", "vendor": "Oracle Corporation"},
        {"name": "VirtualBox", "category": "Virtualization", "vendor": "Oracle Corporation"}
    ],
    "VMware products": [
        {"name": "VMware ESXi", "category": "Virtualization", "vendor": "VMware / Broadcom"},
        {"name": "VMware vCenter", "category": "Virtualization", "vendor": "VMware / Broadcom"},
        {"name": "VMware NSX", "category": "Virtualization", "vendor": "VMware / Broadcom"}
    ],
    "Red Hat products": [
        {"name": "Red Hat Enterprise Linux", "category": "Operating System", "vendor": "Red Hat"},
        {"name": "OpenShift", "category": "Container Platform", "vendor": "Red Hat"},
        {"name": "Ansible", "category": "Middleware", "vendor": "Red Hat"}
    ]
}

# Validation Lists
GENERIC_WORDS = {
    "the", "end", "an", "and", "of", "to", "in", "for", "with", "a", "is", "on", "that", "by", "this", "it", "from",
    "skus", "wikipedia", "services", "products", "tools", "systems", "landscapes", "management", "logs", "crawled",
    "extracted", "validated", "source", "status", "position", "crawling", "audit", "search", "results", "found", "inc"
}

CATEGORY_WORDS = {
    "storage", "security", "platform", "monitoring", "database", "container", "cloud",
    "middleware", "network", "routing", "firewall", "virtualization", "application server",
    "operating system", "web server", "service", "system", "tool", "category", "generic"
}

KNOWN_PRODUCTS = {
    "kubernetes": ("TECHNOLOGY", 1.0),
    "docker": ("TECHNOLOGY", 1.0),
    "prometheus": ("TECHNOLOGY", 1.0),
    "grafana": ("TECHNOLOGY", 1.0),
    "postgresql": ("TECHNOLOGY", 1.0),
    "mysql": ("TECHNOLOGY", 1.0),
    "oracle database": ("PRODUCT", 1.0),
    "oracle db": ("PRODUCT", 1.0),
    "amazon s3": ("SERVICE", 1.0),
    "amazon rds": ("SERVICE", 1.0),
    "vmware esxi": ("PRODUCT", 1.0),
    "hyper-v": ("PRODUCT", 1.0),
    "openshift": ("PRODUCT", 1.0),
    "ansible": ("TECHNOLOGY", 1.0),
    "rhel": ("PRODUCT", 1.0),
    "weblogic": ("PRODUCT", 1.0),
    "virtualbox": ("PRODUCT", 1.0),
    "vcenter": ("PRODUCT", 1.0),
    "nsx": ("PRODUCT", 1.0),
    "vsan": ("PRODUCT", 1.0),
    "coredns": ("PRODUCT", 1.0),
    "containerd": ("PRODUCT", 1.0),
    "envoy": ("PRODUCT", 1.0),
    "jaeger": ("PRODUCT", 1.0),
    "fluentd": ("PRODUCT", 1.0),
    "linkerd": ("PRODUCT", 1.0),
    "helm": ("PRODUCT", 1.0),
    "argocd": ("PRODUCT", 1.0),
    "harbor": ("PRODUCT", 1.0),
    "azure sql": ("SERVICE", 1.0),
    "azure cosmos": ("SERVICE", 1.0),
    "google cloud sql": ("SERVICE", 1.0),
    "google compute engine": ("SERVICE", 1.0),
    "google cloud storage": ("SERVICE", 1.0),
    "google cloud functions": ("SERVICE", 1.0),
    "azure functions": ("SERVICE", 1.0),
    "azure virtual machines": ("SERVICE", 1.0),
    "azure blob storage": ("SERVICE", 1.0),
    "aws lambda": ("SERVICE", 1.0),
    "amazon dynamodb": ("SERVICE", 1.0),
    "amazon sqs": ("SERVICE", 1.0),
    "amazon sns": ("SERVICE", 1.0),
    "amazon ec2": ("SERVICE", 1.0),
    "aws cloudtrail": ("SERVICE", 1.0),
    "amazon route 53": ("SERVICE", 1.0),
    "amazon cloudfront": ("SERVICE", 1.0)
}

def ask_llm_to_classify(name):
    """
    Uses dynamic LLM call to classify technology candidates with OpenAI/Gemini fallback.
    """
    from backend.extractor import get_openai_client, get_gemini_model
    prompt = f"""
    You are a technology classification engine.
    Classify the following term: '{name}'
    Identify if it is a real specific technology, product, or service (e.g. Docker, Amazon S3, VMware ESXi), 
    or if it is a broad category/generic term (e.g. Storage, Security, Cloud, Container).
    
    Choose ONE classification from: TECHNOLOGY, PRODUCT, SERVICE, CATEGORY, GENERIC_TERM, UNKNOWN.
    Choose accepted as True if it is TECHNOLOGY, PRODUCT, or SERVICE; otherwise False.
    Choose a confidence score between 0.0 and 1.0.
    Provide a brief reason.
    
    Return ONLY a JSON block like:
    {{"classification": "PRODUCT", "confidence": 0.9, "accepted": true, "reason": "Reason details"}}
    """
    # 1. Try Gemini
    model = get_gemini_model()
    if model:
        try:
            res = model.generate_content(prompt)
            res_text = res.text
            match = re.search(r'\{.*\}', res_text, re.DOTALL)
            if match:
                data = json.loads(match.group(0))
                return data["classification"], data["confidence"], data["accepted"], data["reason"]
        except Exception:
            pass
            
    # 2. Try OpenAI / OpenRouter
    client = get_openai_client()
    if client:
        try:
            res = client.chat.completions.create(
                model="openai/gpt-4o-mini" if "openrouter.ai" in str(client.base_url) else "gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=150,
                response_format={"type": "json_object"}
            )
            data = json.loads(res.choices[0].message.content)
            return data["classification"], data["confidence"], data["accepted"], data["reason"]
        except Exception:
            pass
            
    raise ValueError("LLM classification not available or failed")

def classify_candidate_technology(tech_name):
    """
    Validation Layer: Classifies and validates a technology candidate name.
    Returns (classification, confidence, accepted, rejection_reason).
    """
    clean_name = tech_name.strip()
    name_lower = clean_name.lower()
    
    # 1. Length and generic words check
    if len(clean_name) <= 2 or name_lower in GENERIC_WORDS:
        return "GENERIC_TERM", 1.0, False, "Generic word or too short"
        
    # 2. Category words check
    if name_lower in CATEGORY_WORDS:
        return "CATEGORY", 1.0, False, f"Generic category name: {clean_name}"
        
    # Single-word category check
    words = name_lower.split()
    if len(words) == 1 and words[0] in CATEGORY_WORDS:
        return "CATEGORY", 1.0, False, f"Generic category name: {clean_name}"
        
    # 3. Known product match
    # Check exact match
    if name_lower in KNOWN_PRODUCTS:
        cls, conf = KNOWN_PRODUCTS[name_lower]
        return cls, conf, True, "Known technology baseline seed"
        
    # Check substring matches
    for prod, (cls, conf) in KNOWN_PRODUCTS.items():
        if prod in name_lower and len(words) > 1:
            return cls, conf, True, f"Contains known technology: {prod}"
            
    # 4. LLM call fallback
    try:
        classification, confidence, accepted, reason = ask_llm_to_classify(clean_name)
        return classification, confidence, accepted, reason
    except Exception:
        # Heuristic-based classification fallback if offline / LLM failed
        # If it looks like a cloud/vendor product
        vendors_prefixes = ["aws", "azure", "google", "vmware", "red hat", "oracle", "amazon", "apache", "docker", "kubernetes", "prometheus"]
        if any(name_lower.startswith(vp) for vp in vendors_prefixes):
            if "service" in name_lower:
                return "SERVICE", 0.8, True, "Inferred vendor service"
            else:
                return "PRODUCT", 0.8, True, "Inferred vendor product"
                
        # Capitalized proper nouns
        if clean_name[0].isupper() and len(clean_name) >= 3:
            return "TECHNOLOGY", 0.7, True, "Inferred proper noun technology"
            
        return "UNKNOWN", 0.5, False, "Unable to classify candidate"

def generate_log_queries(tech_name, category):
    safe_name = tech_name.strip()
    
    # Generate queries that prefer the requested platforms and keywords
    queries = [
        f'{safe_name} "actual log" site:github.com',
        f'{safe_name} "real log" site:github.com/issues',
        f'{safe_name} "stack trace" troubleshooting',
        f'{safe_name} "log sample" incident report',
        f'{safe_name} "error output" forum',
        f'{safe_name} "fatal log" Exception',
        f'{safe_name} "exception log" stack trace'
    ]
    
    # Add category-specific high-intent queries
    cat_lower = category.lower()
    if "database" in cat_lower:
        queries.extend([
            f'{safe_name} "database error logs" "actual log"',
            f'{safe_name} "slow query logs" "real log"'
        ])
    elif "web server" in cat_lower or "proxy" in cat_lower:
        queries.extend([
            f'{safe_name} "error logs" "log sample"',
            f'{safe_name} "upstream logs" "actual log"'
        ])
    elif "container" in cat_lower:
        queries.extend([
            f'{safe_name} "daemon logs" "real log"',
            f'{safe_name} "container stdout logs" "log sample"'
        ])
    else:
        queries.extend([
            f'{safe_name} "error logs" "actual log"',
            f'{safe_name} "diagnostic logs" "real log"'
        ])
        
    return queries

def upsert_technology(tech_name, category, vendor, discovery_source, log_queries, classification=None, confidence=None, accepted=None, reason=None):
    """
    Upserts technology into catalog with audit fields.
    """
    if classification is None:
        classification, confidence, accepted, reason = classify_candidate_technology(tech_name)
    return db_manager.upsert_technology_in_catalog(
        tech_name, category, vendor, discovery_source, log_queries, classification, confidence, accepted, reason
    )

def run_seed_discovery():
    print("\n[1] POPULATING SEED CATALOG FOR 9 TARGET ENVIRONMENTS")
    counts = {"inserted": 0, "updated": 0}
    for source, technologies in SEED_CATALOG.items():
        for tech in technologies:
            queries = generate_log_queries(tech["name"], tech["category"])
            status = upsert_technology(
                tech["name"],
                tech["category"],
                tech["vendor"],
                source,
                queries
            )
            counts[status] += 1
    print(f"Seed Catalog complete: Inserted {counts['inserted']}, Updated {counts['updated']}")
    return counts

def run_dynamic_discovery():
    print("\n[2] RUNNING DYNAMIC WEB DISCOVERY AGENT")
    discovery_queries = {
        "CNCF landscape": "popular CNCF tools site:cncf.io",
        "GCP services": "list of Google Cloud Platform services wiki"
    }
    
    from backend.search_providers import DuckDuckGoProvider
    search_provider = DuckDuckGoProvider()
    
    for source, q in discovery_queries.items():
        print(f"  Searching '{source}' via query: {q}")
        try:
            res = search_provider.search(q, max_results=5)
            results = res.get("results", [])
            
            candidates = set()
            for r in results:
                title = r.get("title", "")
                snippet = r.get("snippet", "")
                combined = f"{title} {snippet}"
                words = re.findall(r'\b[A-Z][a-zA-Z0-9\-\/]{2,15}\b', combined)
                for w in words:
                    if w.lower() not in ["google", "cloud", "platform", "cncf", "linux", "wiki", "official", "services", "list", "popular"]:
                        candidates.add(w)
                        
            candidates = list(candidates)[:3]
            print(f"  Found potential dynamic candidates: {candidates}")
            
            for cand in candidates:
                # Check validation layer first before performing dynamic discovery lookup
                classification, confidence, accepted, reason = classify_candidate_technology(cand)
                if not accepted:
                    print(f"    [Skipped Candidate] {cand} classified as {classification} ({reason})")
                    # Insert as rejected record so it is documented in TECHNOLOGY_AUDIT sheet
                    upsert_technology(
                        cand, "Unknown", "Unknown Vendor", f"Dynamic Search ({source})", [],
                        classification=classification, confidence=confidence, accepted=accepted, reason=reason
                    )
                    continue
                    
                cat, vendor, tech_ver = discover_and_classify_platform(cand)
                if cat == "Unknown" or not cat:
                    cat = "Cloud / Container Platform"
                if vendor == "Unknown Vendor" or not vendor:
                    vendor = f"{source.split()[0]} Community"
                    
                queries = generate_log_queries(cand, cat)
                upsert_technology(cand, cat, vendor, f"Dynamic Search ({source})", queries,
                                  classification=classification, confidence=confidence, accepted=accepted, reason=reason)
                    
        except Exception as e:
            print(f"  Warning: failed dynamic web search for {source}: {e}")

def run_catalog_audit():
    """
    Audits the current entries in technology_catalog database table.
    Enforces quality checks and classifies false positives.
    """
    print("\n[3] RUNNING QUALITY AUDIT ON CURRENT CATALOG")
    
    rows = db_manager.get_all_catalog_technologies()
    
    audit_results = []
    
    total_technologies = 0
    false_positives = 0
    categories_rejected = 0
    generic_terms_rejected = 0
    
    for row in rows:
        tech_id = row[0]
        name = row[1]
        category = row[2]
        vendor = row[3]
        source = row[4]
        queries = json.loads(row[5])
        
        # Re-run through Validation Layer
        classification, confidence, accepted, reason = classify_candidate_technology(name)
        
        # Save validation results back to database
        upsert_technology(
            name, category, vendor, source, queries,
            classification=classification, confidence=confidence, accepted=accepted, reason=reason
        )
        
        audit_results.append({
            "name": name,
            "classification": classification,
            "confidence": confidence,
            "accepted": accepted,
            "reason": reason
        })
        
        if accepted:
            total_technologies += 1
        else:
            false_positives += 1
            if classification == "CATEGORY":
                categories_rejected += 1
            elif classification == "GENERIC_TERM":
                generic_terms_rejected += 1
                
    print("\n" + "="*50)
    print("TECHNOLOGY CATALOG QUALITY AUDIT REPORT")
    print("="*50)
    print(f"Total Discovered Records Audited: {len(rows)}")
    print(f"Accepted Technologies:           {total_technologies}")
    print(f"False Positives Rejected:        {false_positives}")
    print(f"  - Categories Rejected:         {categories_rejected}")
    print(f"  - Generic Terms Rejected:      {generic_terms_rejected}")
    print("="*50)
    
    return total_technologies, false_positives, categories_rejected, generic_terms_rejected

def run_discovery_agent():
    # 1. Run Baseline Seed catalog populator
    run_seed_discovery()
    
    # 2. Run Dynamic web crawler
    run_dynamic_discovery()
    
    # 3. Run Validation Audit to classify and filter catalog
    total_techs, false_pos, cats_rej, gens_rej = run_catalog_audit()
    return total_techs, false_pos, cats_rej, gens_rej

if __name__ == "__main__":
    run_discovery_agent()
