import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import unittest
import sys
import os

# Adjust paths to import backend
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "backend"))

from backend.crawler import collect_logs_from_web
from backend.generator import generate_synthetic_logs_local
from backend.vector_store import LocalVectorStore

class TestLogCollectorSystem(unittest.TestCase):

    def test_local_generator(self):
        """Test the template-based local synthetic log generator."""
        logs = generate_synthetic_logs_local("nginx", "1.25", "upstream", "ERROR", 5, "Connection timed out")
        
        self.assertEqual(len(logs), 5)
        for log in logs:
            self.assertIn("timestamp", log)
            self.assertEqual(log["severity"], "ERROR")
            self.assertIn("message", log)
            self.assertIn("original_log", log)
            # Verify custom scenario is simulated on the last line
            if "Simulated Scenario Alert" in log["message"]:
                self.assertIn("Connection timed out", log["message"])

    def test_vector_store_keyword_fallback(self):
        """Test local vector store database creation, indexing, and keyword search fallback."""
        # Cleanup files if they exist to start fresh
        db_dir = os.path.join(os.path.dirname(__file__), "backend", "db")
        meta_file = os.path.join(db_dir, "logs_metadata_test.json")
        
        # We subclass or instantiate a store and override save locations for isolation
        store = LocalVectorStore()
        
        # Index dummy logs
        test_logs = [
            {"timestamp": "2026-05-28T12:00:00Z", "severity": "ERROR", "message": "Nginx upstream connection reset by peer", "original_log": "error"},
            {"timestamp": "2026-05-28T12:01:00Z", "severity": "INFO", "message": "Spring Boot starting application process", "original_log": "info"},
            {"timestamp": "2026-05-28T12:02:00Z", "severity": "WARN", "message": "Docker bridge network capacity reached", "original_log": "warn"}
        ]
        
        # Test keyword search fallback by adding logs
        store.metadata = []
        store.embeddings = []
        store.add_logs(test_logs, "TestPlat")
        
        # Perform query
        results = store.search("connection reset", limit=2)
        self.assertTrue(len(results) > 0)
        self.assertEqual(results[0]["platform"], "TestPlat")
        self.assertIn("connection reset", results[0]["message"])
        
        results_docker = store.search("bridge network", limit=2)
        self.assertTrue(len(results_docker) > 0)
        self.assertIn("bridge network", results_docker[0]["message"])

    def test_source_url_mapping(self):
        """Test regex parser correctly extracts and maps source URLs from scraped text."""
        from backend.extractor import parse_logs_with_regex
        
        raw_scraped_text = """
Source URL: https://example.com/logs/nginx
2026-06-11T12:00:00Z ERROR Nginx connection refused from upstream
=== NEW SOURCE ===
Source URL: https://example.com/logs/docker
2026-06-11T12:01:00Z WARN Docker container out of memory
"""
        
        logs = parse_logs_with_regex(raw_scraped_text, "nginx", "", "")
        
        self.assertEqual(len(logs), 2)
        self.assertEqual(logs[0]["source_url"], "https://example.com/logs/nginx")
        self.assertEqual(logs[1]["source_url"], "https://example.com/logs/docker")
        self.assertIn("Nginx connection refused", logs[0]["message"])
        self.assertIn("Docker container out of memory", logs[1]["message"])

    def test_log_verification_engine(self):
        """Test validation rules for positive (genuine) and negative (rejected) cases."""
        from backend.extractor import check_log_nature_detail
        from backend.validator import deterministic_validation, classify_source, check_platform_relevance
        
        # 1. Test source classification
        self.assertEqual(classify_source("https://github.com/nginx/nginx/issues/123", "Nginx connection reset issue"), "GITHUB_ISSUE")
        self.assertEqual(classify_source("https://example.com/raw/logs.txt", "Raw Log File"), "LOG_FILE")
        self.assertEqual(classify_source("https://medium.com/blog/nginx-errors", "How to fix Nginx errors blog"), "BLOG")
        self.assertEqual(classify_source("https://stackoverflow.com/questions/123", "Nginx config error stackoverflow"), "FORUM")
        self.assertEqual(classify_source("https://nginx.org/en/docs/ngx_core_module.html", "Core functionality documentation"), "DOCUMENTATION")
        self.assertEqual(classify_source("https://example.com/troubleshoot/nginx-502", "Troubleshooting Nginx 502 bad gateway"), "TROUBLESHOOTING_PAGE")

        # 2. Test platform relevance context checks
        self.assertTrue(check_platform_relevance("nginx", "https://example.com/nginx-help", "Fixing 502", "Platform=nginx"))
        self.assertTrue(check_platform_relevance("aws lambda", "https://example.com/lambda-trigger", "Lambda docs", ""))
        self.assertTrue(check_platform_relevance("apache", "https://example.com/httpd-error", "Apache HTTPD log", ""))
        self.assertFalse(check_platform_relevance("nginx", "https://example.com/apache-help", "Apache HTTPD Server", "Platform=apache"))

        # 3. Test genuine positive logs (should pass check_log_nature_detail with >= 2 indicators)
        positive_logs = {
            "AWS Lambda": "[ERROR] Runtime.ImportModuleError: Unable to import module 'index'",
            "CloudWatch": "START RequestId: c3e3b7b2-8a9d-4e9b-8e2b-7c9d4e9b8e2b Version: $LATEST",
            "Linux Syslog": "Jun 15 10:15:30 host daemon[123]: [info] Service started successfully",
            "Nginx Access Logs": '127.0.0.1 - - [15/Jun/2026:10:15:30 +0000] "GET /index.html HTTP/1.1" 200 1024',
            "Apache Error Logs": "[Mon Jun 15 10:15:30 2026] [error] [client 127.0.0.1] Client sent malformed Host header",
            "Java Stack Trace": "java.lang.NullPointerException at com.example.App.main(App.java:42)",
            "Docker Logs": "db-service_1  | 2026-06-15 10:20:45 INFO org.postgresql.Driver - Connecting to Database",
            "Kubernetes Logs": "2026-06-15T10:12:43.987Z INFO Pod started successfully",
            "Java stack trace without timestamp": "at com.example.App.main(App.java:42)",
            "Kubernetes CrashLoopBackOff event": "pod/my-pod-12345 (db) status is CrashLoopBackOff",
            "Docker runtime failure": "docker runtime failure: container exited with status 137 (OOMKilled)",
            "AWS Lambda Invoke Error": "AWS Lambda Invoke Error: Process exited before completing request",
            "Linux kernel panic log": "Kernel panic - not syncing: Attempted to kill init! exitcode=0x00000007"
        }
        
        for name, log in positive_logs.items():
            is_genuine, reason, indicators = check_log_nature_detail(log)
            self.assertTrue(is_genuine, f"Failed positive check for {name}: '{log}'. Reason: {reason}")
            self.assertTrue(indicators >= 2, f"Failed indicators count check for {name}: got {indicators} (needs >= 2)")

        # 4. Test rejections (should fail check_log_nature_detail or return < 2 indicators)
        negative_cases = {
            "Exception alone": "NullPointerException",
            "Error alone": "Connection refused",
            "Timestamp alone": "2026-06-15T10:12:43.987Z",
            "Nginx config": "server {\n    listen 80;\n    server_name localhost;\n}",
            "Apache config": "DocumentRoot \"/var/www/html\"\nServerName www.example.com",
            "YAML docker-compose": "version: '3'\nservices:\n  web:\n    image: nginx:latest\n    ports:\n      - \"80:80\"",
            "Kubernetes manifest": "apiVersion: v1\nkind: Pod\nmetadata:\n  name: nginx-pod",
            "JSON config": "{\n  \"name\": \"app\",\n  \"dependencies\": {\n    \"express\": \"^4.18.2\"\n  }\n}",
            "XML config": "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<configuration>\n  <property>val</property>\n</configuration>",
            "Shell script": "#!/bin/bash\nset -e\necho \"Running script\"",
            "Shell command": "$ sudo apt-get update && sudo apt-get install nginx",
            "Documentation prose": "In this tutorial, we will show you how to configure your Nginx server block for hosting.",
            "Blog prose": "Nginx is a highly performant web server that allows you to configure reverse proxy blocks easily.",
            "Special case 1": "Press ESC to enter menu",
            "Special case 2": "Filesystem type is ext2fs",
            "Special case 3": "kernel /boot/vmlinuz",
            "Special case 4": "initrd /boot/initramfs",
            "Execution failed sentence": "Execution failed due to configuration error",
            "Gateway response sentence": "Gateway response body: { ... }"
        }
        
        for name, text in negative_cases.items():
            is_genuine, reason, indicators = check_log_nature_detail(text)
            self.assertFalse(is_genuine, f"Failed to reject negative case {name}: '{text}'. Reason: {reason}, Indicators: {indicators}")

        # 5. Test deterministic_validation integration
        raw_text_ctx = """
Source URL: https://github.com/nginx/nginx/issues/123
Source Title: Nginx 502 Bad Gateway Connection Refused Issue
Crawl Context: Platform=nginx, Version=1.25, Service=upstream
127.0.0.1 - - [15/Jun/2026:10:15:30 +0000] "GET /index.html HTTP/1.1" 200 1024
2026-06-15T10:12:43.987Z ERROR Nginx upstream connection refused
=== NEW SOURCE ===
Source URL: https://example.com/blog/nginx-errors
Source Title: Common Nginx Error Logs and How to Fix Them
Crawl Context: Platform=nginx
2026-06-15 10:20:45,123 ERROR Connection timed out to backend
"""
        
        # Valid GitHub issue log
        log_github = {
            "message": "Nginx upstream connection refused",
            "original_log": "2026-06-15T10:12:43.987Z ERROR Nginx upstream connection refused",
            "source_url": "https://github.com/nginx/nginx/issues/123",
            "source_title": "Nginx 502 Bad Gateway Connection Refused Issue",
            "source_platform": "nginx",
            "source_version": "1.25",
            "source_service": "upstream"
        }
        is_valid, reason, confidence = deterministic_validation(log_github, raw_text_ctx, "nginx")
        self.assertTrue(is_valid, f"Failed validation: {reason}")
        self.assertEqual(confidence, 100) # GITHUB_ISSUE has cap 100

        # Valid Blog log (exact match present -> boosted to 90)
        log_blog = {
            "message": "Connection timed out to backend",
            "original_log": "2026-06-15 10:20:45,123 ERROR Connection timed out to backend",
            "source_url": "https://example.com/blog/nginx-errors",
            "source_title": "Common Nginx Error Logs and How to Fix Them",
            "source_platform": "nginx",
            "source_version": "",
            "source_service": ""
        }
        is_valid, reason, confidence = deterministic_validation(log_blog, raw_text_ctx, "nginx")
        self.assertTrue(is_valid, f"Failed validation: {reason}")
        self.assertEqual(confidence, 75) # BLOG with exact match boosted to 90, then penalized by 15 because platform_match_type is generic_log

        # Mismatched platform (apache requested, but source context is nginx)
        is_valid, reason, confidence = deterministic_validation(log_github, raw_text_ctx, "apache")
        self.assertFalse(is_valid, f"Should have failed platform relevance: {reason}")
        
        # Mismatched text (not present in raw_text)
        log_missing = log_github.copy()
        log_missing["message"] = "Some other log that does not exist in raw text"
        log_missing["original_log"] = "Some other log that does not exist in raw text"
        is_valid, reason, confidence = deterministic_validation(log_missing, raw_text_ctx, "nginx")
        self.assertFalse(is_valid, f"Should have failed exact text match: {reason}")

        # 6. Test normalized/fuzzy matching (e.g. Apache logs with modified spacing/formatting)
        raw_text_apache = """
Source URL: https://example.com/troubleshoot/apache
Source Title: Apache Troubleshooting Page
Crawl Context: Platform=apache
[Mon Jun 15 10:15:30 2026] \t [error] \t [client 127.0.0.1] \t Client   sent   malformed   Host   header
"""
        # Spacing is modified (tabs, multiple spaces, etc.)
        log_apache = {
            "message": "Client sent malformed Host header",
            "original_log": "[Mon Jun 15 10:15:30 2026] [error] [client 127.0.0.1] Client sent malformed Host header",
            "source_url": "https://example.com/troubleshoot/apache",
            "source_title": "Apache Troubleshooting Page",
            "source_platform": "apache",
            "source_version": "",
            "source_service": ""
        }
        
        is_valid, reason, confidence = deterministic_validation(log_apache, raw_text_apache, "apache")
        self.assertTrue(is_valid, f"Failed normalized/fuzzy match validation: {reason}")
        self.assertEqual(confidence, 95) # TROUBLESHOOTING_PAGE cap

    def test_manual_vs_excel_regression(self):
        """Regression test comparing search queries and results in Manual Mode and Excel Mode."""
        from unittest.mock import patch
        from backend.crawler import ScrapedContent
        
        dummy_results = [
            {"title": "AWS RDS Log File", "url": "https://example.com/logs/aws_rds", "snippet": "AWS RDS ERROR connection failed"}
        ]
        
        with patch('backend.crawler.search_log_sources', return_value=dummy_results) as mock_search, \
             patch('backend.crawler.scrape_url_content', return_value=ScrapedContent("2026-06-15 10:20:45 ERROR connection failed\n")) as mock_scrape:
            # 1. Manual Mode execution
            manual_res = collect_logs_from_web("AWS RDS", "2009", "")
            # 2. Excel Mode execution
            excel_res = collect_logs_from_web("AWS", "2009-present", "Error Log", product_name="Amazon RDS")
            
            manual_len = len(manual_res)
            excel_len = len(excel_res)
            
            print(f"\n--- REGRESSION TEST DIAGNOSTICS ---")
            print(f"Manual Mode Results Length: {manual_len}")
            print(f"Excel Mode Results Length: {excel_len}")
            
            # If Manual returns content but Excel returns nothing, it is a regression.
            if manual_len > 0 and excel_len == 0:
                print(f"ERROR: Excel Mode returned no results but Manual Mode returned content.")
                print(f"Manual Mode Queries: {getattr(manual_res, 'queries', [])}")
                print(f"Manual Mode Visited URLs: {getattr(manual_res, 'visited_urls', [])}")
                print(f"Excel Mode Queries: {getattr(excel_res, 'queries', [])}")
                print(f"Excel Mode Visited URLs: {getattr(excel_res, 'visited_urls', [])}")
                self.fail("Excel Mode returned no logs while Manual Mode returned logs.")

    def test_log_validation_pipeline_redesign(self):
        """Test cases for high-quality REAL_LOG validation pipeline."""
        from backend.extractor import check_log_nature_detail, classify_candidate_nature
        from backend.validator import deterministic_validation
        
        # 1. test_article_title_rejected
        text1 = "How Docker Logs Reveal CrashLoopBackOff Root Causes"
        is_genuine1, reason1, score1 = check_log_nature_detail(text1)
        self.assertFalse(is_genuine1)
        self.assertIn("ARTICLE_TITLE", reason1)
        
        # 2. test_documentation_sentence_rejected
        text2 = "Docker logs help identify container startup failures."
        is_genuine2, reason2, score2 = check_log_nature_detail(text2)
        self.assertFalse(is_genuine2)
        self.assertTrue(any(x in reason2 for x in ["DOCUMENTATION", "PROSE"]))
        
        # 3. test_apache_spacing_variation
        raw_text_spacing = """
Source URL: https://example.com/troubleshoot/apache
Source Title: Apache Troubleshooting Page
Crawl Context: Platform=apache
[Mon Jun 15 10:15:30 2026] \t [error] \t [client 127.0.0.1] \t Client   sent   malformed   Host   header
"""
        log_spacing = {
            "message": "Client sent malformed Host header",
            "original_log": "[Mon Jun 15 10:15:30 2026] [error] [client 127.0.0.1] Client sent malformed Host header",
            "source_url": "https://example.com/troubleshoot/apache",
            "source_title": "Apache Troubleshooting Page",
            "source_platform": "apache"
        }
        is_valid3, reason3, conf3 = deterministic_validation(log_spacing, raw_text_spacing, "apache")
        self.assertTrue(is_valid3, f"Failed spacing check: {reason3}")
        
        # 4. test_timestamp_variation
        raw_text_timestamp = """
Source URL: https://example.com/troubleshoot/apache
Source Title: Apache Troubleshooting Page
Crawl Context: Platform=apache
[Thu Jun 18 14:00:00 2026] [error] [client 127.0.0.1] Client sent malformed Host header
"""
        log_timestamp = {
            "message": "Client sent malformed Host header",
            "original_log": "[Mon Jun 15 10:15:30 2026] [error] [client 127.0.0.1] Client sent malformed Host header",
            "source_url": "https://example.com/troubleshoot/apache",
            "source_title": "Apache Troubleshooting Page",
            "source_platform": "apache"
        }
        is_valid4, reason4, conf4 = deterministic_validation(log_timestamp, raw_text_timestamp, "apache")
        self.assertTrue(is_valid4, f"Failed timestamp variation check: {reason4}")
        
        # 5. test_severity_mismatch
        raw_text_severity = """
Source URL: https://example.com/troubleshoot/apache
Source Title: Apache Troubleshooting Page
Crawl Context: Platform=apache
[Mon Jun 15 10:15:30 2026] [info] [client 127.0.0.1] Client sent malformed Host header
"""
        log_severity = {
            "message": "Client sent malformed Host header",
            "original_log": "[Mon Jun 15 10:15:30 2026] [error] [client 127.0.0.1] Client sent malformed Host header",
            "source_url": "https://example.com/troubleshoot/apache",
            "source_title": "Apache Troubleshooting Page",
            "source_platform": "apache"
        }
        is_valid5, reason5, conf5 = deterministic_validation(log_severity, raw_text_severity, "apache")
        self.assertFalse(is_valid5, f"Should have failed due to severity mismatch")
        self.assertIn("Severity mismatch", reason5)
        
        # 6. test_real_docker_error_log
        raw_text_docker = """
Source URL: https://example.com/troubleshoot/docker
Source Title: Docker Issues Page
Crawl Context: Platform=docker
db-service_1  | 2026-06-15 10:20:45 ERROR org.postgresql.Driver - Database connection failed
"""
        log_docker = {
            "message": "org.postgresql.Driver - Database connection failed",
            "original_log": "db-service_1  | 2026-06-15 10:20:45 ERROR org.postgresql.Driver - Database connection failed",
            "source_url": "https://example.com/troubleshoot/docker",
            "source_title": "Docker Issues Page",
            "source_platform": "docker"
        }
        is_valid6, reason6, conf6 = deterministic_validation(log_docker, raw_text_docker, "docker")
        self.assertTrue(is_valid6, f"Failed real docker log check: {reason6}")
        self.assertEqual(log_docker["validation"]["log_type"], "REAL_LOG")
        
        # 7. test_blog_title_with_timestamp_rejected
        text7_a = "2026-06-18: How to configure Nginx logs"
        is_genuine7_a, reason7_a, score7_a = check_log_nature_detail(text7_a)
        self.assertFalse(is_genuine7_a)
        self.assertIn("ARTICLE_TITLE", reason7_a)
        
        text7_b = "How Docker Logs Reveal CrashLoopBackOff Root Causes [2026-06-18]"
        is_genuine7_b, reason7_b, score7_b = check_log_nature_detail(text7_b)
        self.assertFalse(is_genuine7_b)
        self.assertIn("ARTICLE_TITLE", reason7_b)

    def test_platform_dynamic_discovery(self):
        """Test vendor/category/technology inference for known and unknown platforms."""
        from backend.crawler import discover_and_classify_platform
        
        # Test pre-defined dictionary platform
        cat, vendor, tech = discover_and_classify_platform("nginx")
        self.assertEqual(cat, "Web Server")
        self.assertEqual(vendor, "NGINX / F5")
        self.assertEqual(tech, "NGINX")
        
        # Test pre-defined dictionary platform (case-insensitive)
        cat_kube, vendor_kube, tech_kube = discover_and_classify_platform("Kubernetes")
        self.assertEqual(cat_kube, "Container Platform")
        self.assertEqual(vendor_kube, "CNCF")
        
        # Test unknown platform fallback
        cat_unk, vendor_unk, tech_unk = discover_and_classify_platform("unknown_service_xyz")
        self.assertEqual(tech_unk, "Unknown_service_xyz")

    def test_progressive_search_bypasses_stages(self):
        """Verify that collect_logs_from_web runs stage-by-stage and stops early when target validated count is reached."""
        from unittest.mock import patch
        from backend.crawler import collect_logs_from_web
        
        # Mock search_log_sources to return results that will yield enough validated logs at Stage 1
        dummy_results = [
            {"title": "Docker Error Log File", "url": "https://example.com/logs/docker", "snippet": "db-service_1  | 2026-06-15 10:20:45 ERROR connection failed"}
        ]
        
        with patch('backend.crawler.search_log_sources', return_value=dummy_results) as mock_search, \
             patch('backend.crawler.scrape_url_content', return_value="db-service_1  | 2026-06-15 10:20:45 ERROR connection failed\n") as mock_scrape:
            # Stage 1 has 3 queries. Running Stage 1 will perform at most 3 search queries.
            # We want count=1. Since the mock yields a validated log in Stage 1, it should stop and not query Stage 2 or 3.
            res = collect_logs_from_web("docker", count=1)
            
            # Count of search calls should be <= 3 (only queries in Stage 1)
            self.assertTrue(mock_search.call_count <= 3)
            # Extracted logs should be populated
            self.assertTrue(len(res.extracted_logs) > 0)
            self.assertTrue(res.extracted_logs[0]["validation"]["valid"])

    def test_per_source_limit(self):
        """Verify no more than 3 validated logs are allowed per unique source URL."""
        from backend.crawler import apply_per_source_limit
        
        logs = [
            {"source_url": "https://example.com/source1", "validation": {"valid": True, "reason": "ok", "confidence": 95}},
            {"source_url": "https://example.com/source1", "validation": {"valid": True, "reason": "ok", "confidence": 95}},
            {"source_url": "https://example.com/source1", "validation": {"valid": True, "reason": "ok", "confidence": 95}},
            {"source_url": "https://example.com/source1", "validation": {"valid": True, "reason": "ok", "confidence": 95}}, # 4th log
            {"source_url": "https://example.com/source2", "validation": {"valid": True, "reason": "ok", "confidence": 95}}
        ]
        
        limited_logs = apply_per_source_limit(logs)
        self.assertEqual(len(limited_logs), 5)
        self.assertTrue(limited_logs[0]["validation"]["valid"])
        self.assertTrue(limited_logs[1]["validation"]["valid"])
        self.assertTrue(limited_logs[2]["validation"]["valid"])
        # The 4th log from source1 should be marked invalid due to limit
        self.assertFalse(limited_logs[3]["validation"]["valid"])
        self.assertEqual(limited_logs[3]["validation"]["reason"], "Per-source limit of 3 validated logs reached")
        # source2 should be valid
        self.assertTrue(limited_logs[4]["validation"]["valid"])

    def test_coverage_score_calculation(self):
        """Verify the 0-100 coverage score calculation rules."""
        from backend.main import calculate_coverage_score
        
        # Case 1: Empty logs -> score 0
        self.assertEqual(calculate_coverage_score([]), 0)
        
        # Case 2: 1 validated log, 1 domain, 1 tier represented, low confidence (average 80)
        logs_low = [
            {
                "source_url": "https://example.com/blog/logs",
                "source_rank": 4, # Tier 4
                "validation": {"valid": True, "confidence": 80}
            }
        ]
        # sources score: 1 domain * 10 = 10
        # validated score: 1 log * 10 = 10
        # diversity: 0 (only 1 tier represented)
        # confidence: 0 (average 80 < 85)
        # expected total: 20
        self.assertEqual(calculate_coverage_score(logs_low), 20)
        
        # Case 3: High scoring case (multiple domains, multiple tiers, high average confidence)
        logs_high = [
            {
                "source_url": "https://github.com/issues/1",
                "source_rank": 2, # Tier 2
                "validation": {"valid": True, "confidence": 95}
            },
            {
                "source_url": "https://docs.microsoft.com/ref",
                "source_rank": 1, # Tier 1
                "validation": {"valid": True, "confidence": 90}
            },
            {
                "source_url": "https://stackoverflow.com/q/2",
                "source_rank": 3, # Tier 3
                "validation": {"valid": True, "confidence": 85}
            }
        ]
        # sources score: 3 unique domains * 10 = 30
        # validated score: 3 logs * 10 = 30
        # diversity: 15 (Tiers 1, 2, 3 represented -> >= 2 tiers)
        # confidence: 15 (average confidence (95+90+85)/3 = 90 >= 85)
        # expected total: 30 + 30 + 15 + 15 = 90
        self.assertEqual(calculate_coverage_score(logs_high), 90)

    def test_discovery_metadata(self):
        """Verify that all 6 metadata fields (platform, category, vendor, source_type, source_rank, query_used) are populated and enriched."""
        from backend.validator import deterministic_validation
        
        raw_text_ctx = """
        Source URL: https://github.com/nginx/nginx/issues/123
        Source Title: Nginx upstream server down
        Crawl Context: Platform=nginx, Version=1.25, Service=upstream, QueryUsed=nginx "error logs", SourceRank=2
        127.0.0.1 - - [15/Jun/2026:10:15:30 +0000] "GET /index.html HTTP/1.1" 200 1024
        2026-06-15T10:12:43.987Z ERROR upstream server connection refused
        """
        
        log = {
            "message": "upstream server connection refused",
            "original_log": "2026-06-15T10:12:43.987Z ERROR upstream server connection refused",
            "source_url": "https://github.com/nginx/nginx/issues/123",
            "source_title": "Nginx upstream server down",
            "source_platform": "nginx",
            "source_version": "1.25",
            "source_service": "upstream",
            "query_used": 'nginx "error logs"',
            "source_rank": 2
        }
        
        is_valid, reason, confidence = deterministic_validation(log, raw_text_ctx, "nginx")
        self.assertTrue(is_valid)
        
        # Verify 6 metadata fields in root
        self.assertEqual(log["platform"], "nginx")
        self.assertEqual(log["category"], "Web Server")
        self.assertEqual(log["vendor"], "NGINX / F5")
        self.assertEqual(log["source_type"], "GITHUB_ISSUE")
        self.assertEqual(log["source_rank"], 2)
        self.assertEqual(log["query_used"], 'nginx "error logs"')
        
        # Verify 6 metadata fields in validation sub-dictionary
        val_sub = log["validation"]
        self.assertEqual(val_sub["platform"], "nginx")
        self.assertEqual(val_sub["category"], "Web Server")
        self.assertEqual(val_sub["vendor"], "NGINX / F5")
        self.assertEqual(val_sub["source_type"], "GITHUB_ISSUE")
        self.assertEqual(val_sub["source_rank"], 2)
        self.assertEqual(val_sub["query_used"], 'nginx "error logs"')

    def test_technology_validation_layer(self):
        """Verify the classification and acceptance/rejection of technologies and categories."""
        from backend.discovery_agent import classify_candidate_technology
        
        # 1. Test reject cases (categories, generic terms, unknown)
        reject_examples = ["Storage", "Security", "Platform", "Monitoring", "Database", "Container", "Cloud"]
        for term in reject_examples:
            classification, confidence, accepted, reason = classify_candidate_technology(term)
            self.assertFalse(accepted, f"Should reject {term}")
            self.assertEqual(classification, "CATEGORY", f"{term} should be classified as CATEGORY")
            self.assertTrue(0.0 <= confidence <= 1.0, f"Confidence for {term} should be in range [0, 1]")

        # 2. Test accept cases (actual technologies/products/services)
        accept_examples = [
            "Kubernetes", "Docker", "Prometheus", "Grafana", "PostgreSQL",
            "Oracle Database", "Amazon S3", "Amazon RDS", "VMware ESXi", "Hyper-V"
        ]
        for term in accept_examples:
            classification, confidence, accepted, reason = classify_candidate_technology(term)
            self.assertTrue(accepted, f"Should accept {term}")
            self.assertIn(classification, ["TECHNOLOGY", "PRODUCT", "SERVICE"], f"{term} should be TECHNOLOGY, PRODUCT, or SERVICE")
            self.assertTrue(0.0 <= confidence <= 1.0, f"Confidence for {term} should be in range [0, 1]")

    def test_autonomous_discovery_agent_logic(self):
        """Verify scheduler, job locking, aliases database tables, mapping priority, status thresholds, and notification queue."""
        import tempfile
        import shutil
        from backend import db_manager
        
        # Isolated test database
        fd, temp_db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        
        old_repo_db_path = os.environ.get("REPO_DB_PATH")
        os.environ["REPO_DB_PATH"] = temp_db_path
        old_db_url = os.environ.get("DATABASE_URL")
        if old_db_url:
            del os.environ["DATABASE_URL"]
        old_db_manager_url = db_manager.DATABASE_URL
        db_manager.DATABASE_URL = None
        
        try:
            # 1. Initialize tables & aliases
            db_manager.init_repo_db()
            conn = db_manager.get_repo_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [r[0] for r in cursor.fetchall()]
            
            # Check table existence
            self.assertIn("technology_coverage", tables)
            self.assertIn("technology_aliases", tables)
            self.assertIn("technology_log_profile", tables)
            self.assertIn("notification_queue", tables)
            self.assertIn("agent_job_history", tables)
            self.assertIn("agent_health_history", tables)
            self.assertIn("agent_runtime_metrics", tables)
            
            # Check default aliases seeded
            cursor.execute("SELECT COUNT(*) FROM technology_aliases")
            self.assertTrue(cursor.fetchone()[0] > 0)
            
            # 2. Test Job Locking in autonomous_agent
            from backend.autonomous_agent import acquire_job_lock, release_job_lock
            job_id1 = acquire_job_lock("test_lock_job")
            self.assertIsNotNone(job_id1)
            
            # Overlapping lock should fail
            job_id2 = acquire_job_lock("test_lock_job")
            self.assertIsNone(job_id2)
            
            # Release and re-acquire should succeed
            release_job_lock(job_id1, "success")
            job_id3 = acquire_job_lock("test_lock_job")
            self.assertIsNotNone(job_id3)
            release_job_lock(job_id3, "success")
            
            # 3. Test Notification Queue
            from backend.autonomous_agent import enqueue_notification, mark_notification_sent
            q_id = enqueue_notification("SUCCESS_REPORT", "Notification Content Details")
            cursor.execute("SELECT status, content FROM notification_queue WHERE id = ?", (q_id,))
            q_row = cursor.fetchone()
            self.assertEqual(q_row["status"], "pending")
            self.assertEqual(q_row["content"], "Notification Content Details")
            
            mark_notification_sent(q_id)
            cursor.execute("SELECT status FROM notification_queue WHERE id = ?", (q_id,))
            self.assertEqual(cursor.fetchone()[0], "sent")
            
            # 4. Recalculate technology coverage & status checks
            cursor.execute("INSERT OR IGNORE INTO technology_catalog (technology_name, accepted) VALUES ('Amazon S3', 1)")
            cursor.execute("INSERT OR IGNORE INTO technology_catalog (technology_name, accepted) VALUES ('Prometheus', 1)")
            cursor.execute("INSERT OR IGNORE INTO technology_catalog (technology_name, accepted) VALUES ('Oracle Database', 1)")
            conn.commit()
            
            # Seed logs (exact, alias, and product mapping matching)
            # Log 1: Amazon S3 (exact match product_name)
            cursor.execute("""
                INSERT INTO validated_logs (platform, product_name, source_url, first_seen, last_seen, error_code, event_type, component, discovered_at)
                VALUES ('AWS', 'Amazon S3', 'https://s3-source', '2026-06-23T12:00', '2026-06-23T12:00', '403', 'AccessDenied', 's3', '2026-06-23T12:00')
            """)
            # Log 2: Prometheus (exact match platform)
            cursor.execute("""
                INSERT INTO validated_logs (platform, product_name, source_url, first_seen, last_seen, error_code, event_type, component, discovered_at)
                VALUES ('Prometheus', '', 'https://prom-source', '2026-06-23T12:00', '2026-06-23T12:00', '500', 'QueryTimeout', 'prometheus', '2026-06-23T12:00')
            """)
            # Log 3: Oracle Database via alias
            cursor.execute("""
                INSERT INTO validated_logs (platform, product_name, source_url, first_seen, last_seen, error_code, event_type, component, discovered_at)
                VALUES ('oracle db', '', 'https://oracle-source', '2026-06-23T12:00', '2026-06-23T12:00', 'ORA-00600', 'DatabaseError', 'db', '2026-06-23T12:00')
            """)
            # Log 4: Amazon RDS Oracle (product mapping: matches Amazon RDS and Oracle Database)
            cursor.execute("""
                INSERT INTO validated_logs (platform, product_name, source_url, first_seen, last_seen, error_code, event_type, component, discovered_at)
                VALUES ('AWS', 'Amazon RDS Oracle', 'https://rds-oracle-source', '2026-06-23T12:00', '2026-06-23T12:00', 'ORA-01017', 'LoginFailed', 'rds', '2026-06-23T12:00')
            """)
            conn.commit()
            
            # Recalculate
            metrics = db_manager.recalculate_technology_coverage()
            self.assertEqual(metrics["technologies_producing_logs"], 3)
            
            # Verify status threshold calculations
            cursor.execute("SELECT status FROM technology_coverage WHERE technology_name = 'Amazon S3'")
            self.assertEqual(cursor.fetchone()[0], "WEAK")
            
            # Verify dashboard metrics endpoint integration
            from fastapi.testclient import TestClient
            from backend.main import app
            client = TestClient(app)
            response = client.get("/api/dashboard-metrics")
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(data["status"], "success")
            self.assertIn("technologies_tracked", data["metrics"])
            self.assertIn("repository_size_mb", data["metrics"])
            
            conn.close()
        finally:
            if old_repo_db_path is not None:
                os.environ["REPO_DB_PATH"] = old_repo_db_path
            else:
                os.environ.pop("REPO_DB_PATH", None)
                
            if old_db_url is not None:
                os.environ["DATABASE_URL"] = old_db_url
            else:
                os.environ.pop("DATABASE_URL", None)
            
            db_manager.DATABASE_URL = old_db_manager_url
            
            if os.path.exists(temp_db_path):
                for _ in range(5):
                    try:
                        os.remove(temp_db_path)
                        break
                    except Exception:
                        import time
                        time.sleep(0.1)

if __name__ == "__main__":
    unittest.main()


