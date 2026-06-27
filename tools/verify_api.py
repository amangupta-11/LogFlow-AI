import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import requests
import json
import time

BASE_URL = "http://127.0.0.1:8000"

def test_generate():
    print("Testing /api/generate endpoint...")
    payload = {
        "platform": "Nginx",
        "version": "1.25",
        "service": "upstream",
        "severity": "ALL",
        "count": 5,
        "scenario": "database connection timeout error"
    }
    
    try:
        response = requests.post(f"{BASE_URL}/api/generate", json=payload)
        print(f"Status Code: {response.status_code}")
        data = response.json()
        print(f"Status: {data.get('status')}")
        logs = data.get("logs", [])
        print(f"Generated {len(logs)} logs:")
        for log in logs:
            msg = log['original_log'].encode('ascii', 'replace').decode('ascii')
            print(f"  {msg}")
        return len(logs) == 5
    except Exception as e:
        print(f"Error calling /api/generate: {e}")
        return False

def test_collect():
    print("\nTesting /api/collect endpoint...")
    payload = {
        "platform": "Docker",
        "version": "25",
        "service": "daemon",
        "count": 5
    }
    
    try:
        response = requests.post(f"{BASE_URL}/api/collect", json=payload)
        print(f"Status Code: {response.status_code}")
        data = response.json()
        print(f"Status: {data.get('status')}")
        print(f"Source: {data.get('source')}")
        logs = data.get("logs", [])
        print(f"Collected {len(logs)} logs:")
        for log in logs:
            msg = log['original_log'].encode('ascii', 'replace').decode('ascii')
            print(f"  {msg}")
        return len(logs) > 0
    except Exception as e:
        print(f"Error calling /api/collect: {e}")
        return False

def test_search():
    print("\nTesting /api/search endpoint...")
    try:
        response = requests.get(f"{BASE_URL}/api/search?query=limit&limit=3")
        print(f"Status Code: {response.status_code}")
        data = response.json()
        results = data.get("results", [])
        print(f"Found {len(results)} matching logs in semantic DB:")
        for idx, res in enumerate(results):
            print(f"  {idx+1}. [{res['platform']}] {res['severity']}: {res['message']} (Score: {res.get('score')})")
        return True
    except Exception as e:
        print(f"Error calling /api/search: {e}")
        return False

if __name__ == "__main__":
    print("Waiting 2 seconds for server to be fully responsive...")
    time.sleep(2)
    
    gen_success = test_generate()
    collect_success = test_collect()
    search_success = test_search()
    
    if gen_success and collect_success and search_success:
        print("\nAll integration checks PASSED successfully!")
    else:
        print("\nSome integration checks FAILED.")
