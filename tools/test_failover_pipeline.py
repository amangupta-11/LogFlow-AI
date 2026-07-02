import sys
import os
import unittest
import json
from unittest.mock import patch, MagicMock

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))
from backend import db_manager, validator

class TestFailoverPipeline(unittest.TestCase):
    def setUp(self):
        # We clean the database of any LLM API entries to start fresh
        db_manager.execute_repo_write("DELETE FROM agent_health_history WHERE component = 'llm_api'")

    @patch('backend.validator.validate_with_gemini_provider')
    @patch('backend.validator.validate_with_openai_provider')
    @patch('backend.validator.validate_with_anthropic_provider')
    @patch('backend.validator.post_validate_logs', side_effect=lambda logs, raw_text, platform: logs)
    def test_pipeline_failover_order(self, mock_post_validate, mock_anthropic, mock_openai, mock_gemini):
        # 1. Setup mock returns
        # Gemini fails
        mock_gemini.return_value = None
        # OpenAI fails
        mock_openai.return_value = None
        # Anthropic succeeds
        mock_anthropic.return_value = [{"validation": {"valid": True, "reason": "Claude success"}}]
        
        # 2. Call validation
        logs = [{"message": "test log"}]
        res = validator.validate_logs_with_claude(logs, "test raw text", "nginx", "")
        
        # 3. Assert mock calls in order
        mock_gemini.assert_called_once()
        mock_openai.assert_called_once()
        mock_anthropic.assert_called_once()
        
        # 4. Check if results are returned correctly
        self.assertTrue(res[0]["validation"]["valid"])

    def test_check_llm_api_health_failover_path(self):
        import time
        from datetime import datetime
        
        # We will manually seed attempts to simulate failover cycles
        # Cycle 1: Gemini failed, OpenAI failed, Anthropic succeeded (within 5 seconds)
        t1 = datetime.utcnow().isoformat() + "Z"
        db_manager.execute_repo_insert(
            "INSERT INTO agent_health_history (timestamp, component, status, details) VALUES (?, 'llm_api', 'unhealthy', ?)",
            (t1, json.dumps({"provider": "Gemini", "success": False, "error_message": "Blocked", "response_time": 0.5}))
        )
        t2 = datetime.utcnow().isoformat() + "Z"
        db_manager.execute_repo_insert(
            "INSERT INTO agent_health_history (timestamp, component, status, details) VALUES (?, 'llm_api', 'unhealthy', ?)",
            (t2, json.dumps({"provider": "OpenAI", "success": False, "error_message": "Rate limit", "response_time": 0.4}))
        )
        t3 = datetime.utcnow().isoformat() + "Z"
        db_manager.execute_repo_insert(
            "INSERT INTO agent_health_history (timestamp, component, status, details) VALUES (?, 'llm_api', 'healthy', ?)",
            (t3, json.dumps({"provider": "Anthropic", "success": True, "error_message": "", "response_time": 1.2}))
        )
        
        # Call health check with mocked environment variables
        with patch.dict(os.environ, {
            "GEMINI_API_KEY": "sk-test",
            "OPENAI_API_KEY": "sk-test",
            "ANTHROPIC_API_KEY": "sk-test"
        }):
            status, reason, active = db_manager.check_llm_api_health()
            
            # Assertions
            self.assertEqual(active, "Anthropic")
            self.assertEqual(status, "Healthy")
            self.assertIn("Provider Failover Path: Gemini -> OpenAI -> Anthropic", reason)
            self.assertIn("Success Rate: 100.0% (1/1)", reason)
            
        # Cycle 2: All failed (fell back to Regex)
        # Clear DB and seed
        db_manager.execute_repo_write("DELETE FROM agent_health_history WHERE component = 'llm_api'")
        
        t1 = datetime.utcnow().isoformat() + "Z"
        db_manager.execute_repo_insert(
            "INSERT INTO agent_health_history (timestamp, component, status, details) VALUES (?, 'llm_api', 'unhealthy', ?)",
            (t1, json.dumps({"provider": "Gemini", "success": False, "error_message": "Timeout", "response_time": 10.0}))
        )
        t2 = datetime.utcnow().isoformat() + "Z"
        db_manager.execute_repo_insert(
            "INSERT INTO agent_health_history (timestamp, component, status, details) VALUES (?, 'llm_api', 'unhealthy', ?)",
            (t2, json.dumps({"provider": "OpenAI", "success": False, "error_message": "HTTP 402", "response_time": 0.3}))
        )
        t3 = datetime.utcnow().isoformat() + "Z"
        db_manager.execute_repo_insert(
            "INSERT INTO agent_health_history (timestamp, component, status, details) VALUES (?, 'llm_api', 'unhealthy', ?)",
            (t3, json.dumps({"provider": "Anthropic", "success": False, "error_message": "HTTP 401", "response_time": 0.2}))
        )
        
        with patch.dict(os.environ, {
            "GEMINI_API_KEY": "sk-test",
            "OPENAI_API_KEY": "sk-test",
            "ANTHROPIC_API_KEY": "sk-test"
        }):
            status, reason, active = db_manager.check_llm_api_health()
            
            # Assertions
            self.assertEqual(active, "None")
            self.assertEqual(status, "Fallback Validator Active")
            self.assertIn("Provider Failover Path: Gemini -> OpenAI -> Anthropic -> Regex", reason)
            self.assertIn("Success Rate: 0.0% (0/1)", reason)

if __name__ == "__main__":
    unittest.main()
