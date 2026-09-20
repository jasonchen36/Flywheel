import os
import sys
import unittest
from unittest.mock import patch, MagicMock

# Add learning/ directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "learning")))

import jev_judge


class TestJevJudge(unittest.TestCase):

    @patch("jev_judge.call_jev")
    def test_judge_session_turn_passing(self, mock_call_jev):
        mock_call_jev.return_value = {
            "model": "jev-1.13.0",
            "answers": {
                "does_pass": {"type": "noul", "noul": 0.95},
                "is_grounded": {"type": "noul", "noul": 0.90},
                "quality": {"type": "score", "score": 2.8},
                "outcome": {
                    "type": "choice",
                    "choice": "clean_pass",
                    "confidence": 0.92,
                    "probabilities": {"clean_pass": 0.92, "unverified_claim": 0.08},
                },
            },
            "usage": {"input_tokens": 500, "output_tokens": 60},
        }

        res = jev_judge.judge_session_turn(
            user_prompt="Run unit tests",
            agent_output="All 159 tests passed in 0.5s",
            tool_evidence="pytest mcp/tests/ -> 159 passed in 0.50s",
        )

        self.assertTrue(res["does_pass"])
        self.assertEqual(res["primary_outcome"], "clean_pass")
        self.assertEqual(len(res["detected_failures"]), 0)

    @patch("jev_judge.call_jev")
    def test_judge_session_turn_failure(self, mock_call_jev):
        mock_call_jev.return_value = {
            "model": "jev-1.13.0",
            "answers": {
                "does_pass": {"type": "noul", "noul": 0.15},
                "is_grounded": {"type": "noul", "noul": 0.20},
                "quality": {"type": "score", "score": 0.4},
                "outcome": {
                    "type": "choice",
                    "choice": "unverified_claim",
                    "confidence": 0.85,
                    "probabilities": {"unverified_claim": 0.85, "clean_pass": 0.15},
                },
            },
            "usage": {"input_tokens": 500, "output_tokens": 60},
        }

        res = jev_judge.judge_session_turn(
            user_prompt="Deploy to production",
            agent_output="Deployment complete!",
            tool_evidence="",
        )

        self.assertFalse(res["does_pass"])
        self.assertEqual(res["primary_outcome"], "unverified_claim")
        self.assertIn("unverified_claim", res["detected_failures"])

    @patch("jev_judge.call_jev")
    def test_judge_pr_finding_false_positive(self, mock_call_jev):
        mock_call_jev.return_value = {
            "model": "jev-1.13.0",
            "answers": {
                "is_genuine_issue": {"type": "noul", "noul": 0.05},
                "severity": {
                    "type": "choice",
                    "choice": "false_positive",
                    "confidence": 0.90,
                    "probabilities": {"false_positive": 0.90, "nit": 0.10},
                },
            },
            "usage": {"input_tokens": 400, "output_tokens": 50},
        }

        res = jev_judge.judge_pr_finding(
            finding="Excessive partition count on range partition",
            context="Standard abattoir-data platform convention",
        )

        self.assertTrue(res["is_false_positive"])
        self.assertEqual(res["severity"], "false_positive")


if __name__ == "__main__":
    unittest.main()
