import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
"""Quick test of the fixed DuckDuckGoProvider."""
import time
from backend.search_providers import DuckDuckGoProvider

p = DuckDuckGoProvider()

# Test 1: exact-phrase query (previously failed due to quotes)
q1 = 'Docker 25 daemon "error logs"'
t0 = time.time()
res1 = p.search(q1, max_results=5)
elapsed1 = time.time() - t0
print(f'Test 1 (quoted): {len(res1["results"])} results in {elapsed1:.1f}s | error={res1["error"]}')
for r in res1["results"][:3]:
    print(f'  {r["url"]}')

# Test 2: plain query
q2 = "Docker daemon error logs"
t0 = time.time()
res2 = p.search(q2, max_results=5)
elapsed2 = time.time() - t0
print(f'Test 2 (plain):  {len(res2["results"])} results in {elapsed2:.1f}s | error={res2["error"]}')
for r in res2["results"][:3]:
    print(f'  {r["url"]}')

print("DONE")
