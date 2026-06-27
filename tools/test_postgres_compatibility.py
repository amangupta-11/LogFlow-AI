import unittest
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import db_manager

class TestPostgresCompatibility(unittest.TestCase):
    def test_query_translation_placeholders(self):
        query = "SELECT * FROM jobs WHERE id = ? AND status = ?"
        translated = db_manager.translate_query(query)
        self.assertEqual(translated, "SELECT * FROM jobs WHERE id = %s AND status = %s")

    def test_query_translation_insert_ignore(self):
        # agent_status
        q1 = "INSERT OR IGNORE INTO agent_status (id, status, last_active) VALUES (1, 'idle', datetime('now'))"
        translated1 = db_manager.translate_query(q1)
        self.assertIn("ON CONFLICT (id) DO NOTHING", translated1)
        self.assertNotIn("INSERT OR IGNORE", translated1)
        self.assertIn("CURRENT_TIMESTAMP", translated1)

        # technology_aliases
        q2 = "INSERT OR IGNORE INTO technology_aliases (technology_name, alias) VALUES (?, ?)"
        translated2 = db_manager.translate_query(q2)
        self.assertIn("ON CONFLICT (alias) DO NOTHING", translated2)
        self.assertNotIn("INSERT OR IGNORE", translated2)

        # repository_metrics
        q3 = "INSERT OR IGNORE INTO repository_metrics (key, value) VALUES ('duplicates_skipped', 0)"
        translated3 = db_manager.translate_query(q3)
        self.assertIn("ON CONFLICT (key) DO NOTHING", translated3)
        self.assertNotIn("INSERT OR IGNORE", translated3)

    def test_query_translation_insert_replace(self):
        q = "INSERT OR REPLACE INTO domain_performance (domain, urls_crawled, logs_extracted, logs_validated, logs_inserted, yield_score) VALUES (?, ?, ?, ?, ?, ?)"
        translated = db_manager.translate_query(q)
        self.assertIn("ON CONFLICT (domain) DO UPDATE SET", translated)
        self.assertIn("urls_crawled = EXCLUDED.urls_crawled", translated)

    def test_postgres_row_lookup(self):
        description = [("id",), ("platform",), ("status",)]
        values = (123, "Nginx", "active")
        row = db_manager.PostgresRow(description, values)
        
        # Test key lookup
        self.assertEqual(row["id"], 123)
        self.assertEqual(row["platform"], "Nginx")
        
        # Test index lookup
        self.assertEqual(row[0], 123)
        self.assertEqual(row[1], "Nginx")
        self.assertEqual(row[2], "active")
        
        # Test dictionary conversion
        d = dict(row)
        self.assertEqual(d, {"id": 123, "platform": "Nginx", "status": "active"})
        
        # Test keys & values
        self.assertEqual(list(row.keys()), ["id", "platform", "status"])
        self.assertEqual(list(row.values()), [123, "Nginx", "active"])

if __name__ == "__main__":
    unittest.main()
