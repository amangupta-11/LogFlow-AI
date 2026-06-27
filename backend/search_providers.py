import time
import warnings
import requests
import random
import logging
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs, unquote

# Suppress the cosmetic "package renamed" RuntimeWarning from duckduckgo_search
warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*duckduckgo_search.*renamed.*")

try:
    from ddgs import DDGS
    _DDGS_AVAILABLE = True
except ImportError:
    try:
        from duckduckgo_search import DDGS
        _DDGS_AVAILABLE = True
    except ImportError:
        _DDGS_AVAILABLE = False

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
]

class SearchProvider:
    name = "BaseProvider"

    def search(self, query: str, max_results: int = 7) -> dict:
        """
        Executes query search.
        Returns a dictionary containing:
        - "results": list of dicts with keys 'title', 'url', 'snippet'
        - "status_code": HTTP response status code
        - "error": error message or "success"
        - "duration": execution duration in seconds
        """
        raise NotImplementedError("Search provider must implement search method")

    def _get_headers(self) -> dict:
        return {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate",  # Avoid br to prevent decoding issues
        }


class DuckDuckGoProvider(SearchProvider):
    name = "DuckDuckGo"

    def _search_via_ddgs_library(self, query: str, max_results: int) -> list:
        """Use the duckduckgo-search library (API-based, no HTML scraping)."""
        # DDG doesn't handle exact-phrase quotes well; strip them to improve recall
        clean_query = query.replace('"', '')
        results = []
        with DDGS(timeout=8) as ddgs:
            for r in ddgs.text(clean_query, max_results=max_results):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                })
                if len(results) >= max_results:
                    break
        return results

    def _search_via_html_scraper(self, query: str, max_results: int) -> tuple:
        """Fallback: scrape html.duckduckgo.com directly."""
        results = []
        status_code = 0
        url = "https://html.duckduckgo.com/html/"
        headers = self._get_headers()
        response = requests.post(url, data={"q": query}, headers=headers, timeout=5)
        status_code = response.status_code
        if status_code == 200:
            soup = BeautifulSoup(response.text, "html.parser")
            for item in soup.select(".result"):
                a_tag = item.select_one(".result__title a")
                if not a_tag:
                    continue
                href = a_tag.get("href", "")
                title = a_tag.get_text().strip()
                if "uddg=" in href:
                    try:
                        parsed = urlparse(href)
                        queries = parse_qs(parsed.query)
                        if "uddg" in queries:
                            href = queries["uddg"][0]
                    except Exception:
                        pass
                if not href or "duckduckgo.com" in href:
                    continue
                snippet_div = item.select_one(".result__snippet")
                snippet = snippet_div.get_text().strip() if snippet_div else ""
                results.append({"title": title, "url": href, "snippet": snippet})
                if len(results) >= max_results:
                    break
        return results, status_code

    def search(self, query: str, max_results: int = 7) -> dict:
        start_time = time.time()
        results = []
        status_code = 200
        error_msg = "success"

        # --- Primary: duckduckgo-search library (API-based) ---
        if _DDGS_AVAILABLE:
            try:
                results = self._search_via_ddgs_library(query, max_results)
                if results:
                    return {
                        "results": results,
                        "status_code": 200,
                        "error": "success",
                        "duration": time.time() - start_time,
                    }
                else:
                    # DDGS returned 0 — skip the HTML fallback (it always times out).
                    # Let the caller move on to Bing/Brave/Yahoo/AOL.
                    logger.debug("DDGS library returned 0 results; skipping HTML fallback.")
                    return {
                        "results": [],
                        "status_code": 200,
                        "error": "No results found (DDGS returned empty)",
                        "duration": time.time() - start_time,
                    }
            except Exception as e:
                logger.warning(f"DuckDuckGo DDGS library error (will try HTML fallback): {e}")

        # --- Fallback: HTML scraper (only reached if DDGS library not installed) ---
        try:
            results, status_code = self._search_via_html_scraper(query, max_results)
            if not results:
                error_msg = f"No results found (HTML scraper, HTTP {status_code})"
        except Exception as e:
            error_msg = f"Request failed: {str(e)}"
            logger.error(f"DuckDuckGo HTML scraper error: {e}", exc_info=True)

        return {
            "results": results,
            "status_code": status_code,
            "error": error_msg,
            "duration": time.time() - start_time,
        }


class YahooProvider(SearchProvider):
    name = "Yahoo"

    def search(self, query: str, max_results: int = 7) -> dict:
        start_time = time.time()
        results = []
        status_code = 0
        error_msg = "success"

        try:
            url = "https://search.yahoo.com/search"
            headers = self._get_headers()
            response = requests.get(url, params={"p": query}, headers=headers, timeout=5)
            status_code = response.status_code

            if status_code == 200:
                soup = BeautifulSoup(response.text, "html.parser")
                algo_elements = soup.find_all("div", class_="algo")
                
                for item in algo_elements:
                    a_tag = item.find("a")
                    if not a_tag:
                        continue
                    href = a_tag.get("href", "")
                    title = a_tag.get_text().strip()
                    
                    if "/RU=" in href:
                        try:
                            parts = href.split("/RU=")
                            if len(parts) > 1:
                                target_url = parse_qs(urlparse(href).query).get("RU", [None])[0]
                                if not target_url:
                                    target_url = parts[1].split("/RK=")[0]
                                    target_url = unquote(target_url)
                                href = target_url
                        except Exception as e:
                            logger.error(f"Error resolving Yahoo redirect: {e}")
                            
                    if "yahoo.com" in href or href.startswith("#") or not href:
                        continue
                        
                    snippet_div = item.find("div", class_="compText") or item.find("div", class_="abstr")
                    snippet = snippet_div.get_text().strip() if snippet_div else ""
                    
                    results.append({
                        "title": title,
                        "url": href,
                        "snippet": snippet
                    })
                    if len(results) >= max_results:
                        break
                        
                if not results:
                    error_msg = "No results found in HTML response"
            else:
                error_msg = f"HTTP error {status_code}"
                if status_code == 500 or "captcha" in response.text.lower() or "verify" in response.text.lower():
                    error_msg = "Bot detection / Blocked (Yahoo returned 500/Captcha)"
        except Exception as e:
            error_msg = f"Request failed: {str(e)}"
            logger.error(f"Yahoo search error: {e}", exc_info=True)

        return {
            "results": results,
            "status_code": status_code,
            "error": error_msg,
            "duration": time.time() - start_time
        }


class BingProvider(SearchProvider):
    name = "Bing"

    def search(self, query: str, max_results: int = 7) -> dict:
        start_time = time.time()
        results = []
        status_code = 0
        error_msg = "success"

        try:
            url = "https://www.bing.com/search"
            headers = self._get_headers()
            response = requests.get(url, params={"q": query}, headers=headers, timeout=5)
            status_code = response.status_code

            if status_code == 200:
                soup = BeautifulSoup(response.text, "html.parser")
                
                # Check for captcha / verify page
                if "captcha" in response.text.lower() or "verify" in response.text.lower() or "blocked" in response.text.lower():
                    error_msg = "Bot detection / Captcha triggered"
                else:
                    containers = soup.select("li.b_algo")
                    for item in containers:
                        a_tag = item.select_one("h2 a")
                        if not a_tag:
                            continue
                        href = a_tag.get("href", "")
                        title = a_tag.get_text().strip()
                        
                        if not href or "bing.com" in href or "microsoft.com" in href:
                            continue
                            
                        snippet_p = item.select_one("p") or item.select_one(".b_caption")
                        snippet = snippet_p.get_text().strip() if snippet_p else ""
                        
                        results.append({
                            "title": title,
                            "url": href,
                            "snippet": snippet
                        })
                        if len(results) >= max_results:
                            break
                    
                    if not results:
                        error_msg = "No results found in HTML response"
            else:
                error_msg = f"HTTP error {status_code}"
        except Exception as e:
            error_msg = f"Request failed: {str(e)}"
            logger.error(f"Bing search error: {e}", exc_info=True)

        return {
            "results": results,
            "status_code": status_code,
            "error": error_msg,
            "duration": time.time() - start_time
        }


class BraveProvider(SearchProvider):
    name = "Brave"

    def search(self, query: str, max_results: int = 7) -> dict:
        start_time = time.time()
        results = []
        status_code = 0
        error_msg = "success"

        try:
            url = "https://search.brave.com/search"
            headers = self._get_headers()
            response = requests.get(url, params={"q": query}, headers=headers, timeout=5)
            status_code = response.status_code

            if status_code == 200:
                soup = BeautifulSoup(response.text, "html.parser")
                
                if "captcha" in response.text.lower() or "verify" in response.text.lower() or "challenge" in response.text.lower():
                    error_msg = "Bot detection / Challenge page triggered"
                else:
                    containers = soup.select("div.result") or soup.select(".web-result")
                    for item in containers:
                        a_tag = item.select_one("a.title") or item.select_one("a")
                        if not a_tag:
                            continue
                        href = a_tag.get("href", "")
                        title = a_tag.get_text().strip()
                        
                        if not href or "brave.com" in href:
                            continue
                            
                        snippet_div = item.select_one(".snippet") or item.select_one("p")
                        snippet = snippet_div.get_text().strip() if snippet_div else ""
                        
                        results.append({
                            "title": title,
                            "url": href,
                            "snippet": snippet
                        })
                        if len(results) >= max_results:
                            break
                    
                    if not results:
                        # Fallback parsing for links if selectors changed
                        for a in soup.select("a"):
                            href = a.get("href", "")
                            title = a.get_text().strip()
                            if href.startswith("http") and not any(x in href for x in ["brave.com", "google.com", "yahoo.com"]):
                                results.append({
                                    "title": title,
                                    "url": href,
                                    "snippet": ""
                                })
                                if len(results) >= max_results:
                                    break
                                    
                    if not results:
                        error_msg = "No results found in HTML response"
            else:
                error_msg = f"HTTP error {status_code}"
                if status_code == 429:
                    error_msg = "Rate limited / Bot detected (429 Too Many Requests)"
        except Exception as e:
            error_msg = f"Request failed: {str(e)}"
            logger.error(f"Brave search error: {e}", exc_info=True)

        return {
            "results": results,
            "status_code": status_code,
            "error": error_msg,
            "duration": time.time() - start_time
        }


class AOLProvider(SearchProvider):
    name = "AOL"

    def search(self, query: str, max_results: int = 7) -> dict:
        start_time = time.time()
        results = []
        status_code = 0
        error_msg = "success"

        try:
            url = "https://search.aol.com/aol/search"
            headers = self._get_headers()
            response = requests.get(url, params={"q": query}, headers=headers, timeout=5)
            status_code = response.status_code

            if status_code == 200:
                soup = BeautifulSoup(response.text, "html.parser")
                algo_elements = soup.find_all("div", class_="algo")
                
                for item in algo_elements:
                    a_tag = item.find("a")
                    if not a_tag:
                        continue
                    href = a_tag.get("href", "")
                    title = a_tag.get_text().strip()
                    
                    if "/RU=" in href:
                        try:
                            parts = href.split("/RU=")
                            if len(parts) > 1:
                                target_url = parse_qs(urlparse(href).query).get("RU", [None])[0]
                                if not target_url:
                                    target_url = parts[1].split("/RK=")[0]
                                    target_url = unquote(target_url)
                                href = target_url
                        except Exception as e:
                            logger.error(f"Error resolving AOL redirect: {e}")
                            
                    if "aol.com" in href or "yahoo.com" in href or href.startswith("#") or not href:
                        continue
                        
                    snippet_div = item.find("div", class_="compText") or item.find("div", class_="abstr")
                    snippet = snippet_div.get_text().strip() if snippet_div else ""
                    
                    results.append({
                        "title": title,
                        "url": href,
                        "snippet": snippet
                    })
                    if len(results) >= max_results:
                        break
                        
                if not results:
                    error_msg = "No results found in HTML response"
            else:
                error_msg = f"HTTP error {status_code}"
                if status_code == 404:
                    error_msg = "AOL search endpoint unavailable (404 Not Found)"
        except Exception as e:
            error_msg = f"Request failed: {str(e)}"
            logger.error(f"AOL search error: {e}", exc_info=True)

        return {
            "results": results,
            "status_code": status_code,
            "error": error_msg,
            "duration": time.time() - start_time
        }
