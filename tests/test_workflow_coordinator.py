import json
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "agents" / "shared" / "workflow-coordinator.py"
MINH = "620891893659598850"
WIEN = "859783610625556480"

spec = importlib.util.spec_from_file_location("workflow_coordinator", SCRIPT)
coordinator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coordinator)


class WorkflowCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / "workflow"
        self.input_path = Path(self.tempdir.name) / "project.json"
        self.project = {
            "project_id": "acme-demo",
            "status": "review",
            "business_name": "Acme Demo",
            "pages": [
                {
                    "slug": "homepage",
                    "status": "planned",
                    "owner_id": MINH,
                    "target_day": 1,
                    "checklist": [{"id": "copy", "status": "done"}],
                }
            ],
        }
        self.input_path.write_text(json.dumps(self.project), encoding="utf-8")

    def tearDown(self):
        self.tempdir.cleanup()

    def run_cmd(self, *args):
        env = os.environ.copy()
        env["OPENCLAW_WORKFLOW_ROOT"] = str(self.root)
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            env=env,
            text=True,
            capture_output=True,
        )

    def load_project(self):
        return json.loads((self.root / "projects" / "acme-demo" / "project.json").read_text(encoding="utf-8"))

    def test_happy_path_requires_both_final_confirmations(self):
        self.assertEqual(self.run_cmd("init", "--input", str(self.input_path)).returncode, 0)
        self.assertNotEqual(self.run_cmd("approve", "acme-demo", "--actor", "999999999999999999").returncode, 0)
        self.assertEqual(self.run_cmd("approve", "acme-demo", "--actor", WIEN).returncode, 0)
        self.assertEqual(self.load_project()["approved_by"], WIEN)
        self.assertNotEqual(self.run_cmd("page-done", "acme-demo", "homepage", "--actor", WIEN).returncode, 0)
        self.assertEqual(self.run_cmd("page-done", "acme-demo", "homepage", "--actor", MINH).returncode, 0)
        self.assertEqual(self.run_cmd("page-approve", "acme-demo", "homepage", "--actor", MINH).returncode, 0)
        self.assertEqual(self.run_cmd("final-confirm", "acme-demo", "--actor", MINH).returncode, 0)
        self.assertEqual(self.load_project()["status"], "stakeholder-review")
        self.assertEqual(self.run_cmd("final-confirm", "acme-demo", "--actor", WIEN).returncode, 0)
        project = self.load_project()
        self.assertEqual(project["status"], "offer-ready")
        self.assertEqual(project["offer_channel"], "1536659097649422356")

    def test_page_approval_rejects_incomplete_checklist(self):
        self.project["pages"][0]["checklist"][0]["status"] = "pending"
        self.input_path.write_text(json.dumps(self.project), encoding="utf-8")
        self.assertEqual(self.run_cmd("init", "--input", str(self.input_path)).returncode, 0)
        self.assertEqual(self.run_cmd("approve", "acme-demo", "--actor", MINH).returncode, 0)
        self.assertEqual(self.run_cmd("page-done", "acme-demo", "homepage", "--actor", MINH).returncode, 0)
        result = self.run_cmd("page-approve", "acme-demo", "homepage", "--actor", MINH)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checklist is not complete", result.stderr)

    def test_due_reminders_lists_pending_pages(self):
        self.assertEqual(self.run_cmd("init", "--input", str(self.input_path)).returncode, 0)
        result = self.run_cmd("due-reminders", "--stale-minutes", "30")
        self.assertEqual(result.returncode, 0)
        due = json.loads(result.stdout)
        self.assertEqual(due[0]["project_id"], "acme-demo")
        self.assertEqual(due[0]["pending_pages"][0]["slug"], "homepage")

    def test_reminder_message_is_readable_for_review_without_pages(self):
        project = {
            "project_id": "acme-demo",
            "business_name": "Acme Demo",
            "status": "review",
            "last_update": "2026-08-11T10:18:27+00:00",
            "pages": [],
            "message_tracking": {
                "review_message_url": "https://discord.com/channels/1446612692910739637/1536658476288450630/200",
            },
        }
        message = coordinator.format_reminder(project, [])
        self.assertIn("NHẮC VIỆC", message)
        self.assertIn("Acme Demo", message)
        self.assertIn("/approve acme-demo", message)
        self.assertIn("https://discord.com/channels/1446612692910739637/1536658476288450630/200", message)
        self.assertNotIn("None", message)

    def test_reminder_message_groups_page_action_details(self):
        project = {
            "project_id": "acme-demo",
            "business_name": "Acme Demo",
            "status": "task",
            "last_update": "2026-08-11T10:18:27+00:00",
            "pages": [],
        }
        pending = [{
            "slug": "homepage",
            "status": "content-draft",
            "owner": "Minh",
            "next_action": "Hoàn thiện hero và CTA",
            "blocked_reason": None,
        }]
        message = coordinator.format_reminder(project, pending)
        self.assertIn("HOMEPAGE", message)
        self.assertIn("Hoàn thiện hero và CTA", message)
        self.assertIn("/page-status acme-demo homepage", message)

    def test_reminder_signature_ignores_last_update_timestamp(self):
        project = {"project_id": "acme-demo", "status": "review", "last_update": "old"}
        pending = [{"slug": "homepage", "status": "todo", "next_action": "Write copy"}]
        first = coordinator.reminder_signature(project, pending)
        project["last_update"] = "new"
        self.assertEqual(first, coordinator.reminder_signature(project, pending))

    def test_recent_unchanged_reminder_is_suppressed(self):
        project = {
            "project_id": "acme-demo",
            "status": "review",
            "reminder_state": {
                "signature": "same",
                "last_sent_at": coordinator.now(),
            },
        }
        self.assertFalse(coordinator.should_send_reminder(project, "same", 120))
        self.assertTrue(coordinator.should_send_reminder(project, "changed", 120))

    def test_discord_message_url_uses_guild_channel_and_message_ids(self):
        url = coordinator.discord_message_url("1536658476288450630", "1536692489602338917")
        self.assertEqual(
            url,
            "https://discord.com/channels/1446612692910739637/1536658476288450630/1536692489602338917",
        )

    def test_message_tracking_payload_contains_only_bot_message_targets(self):
        tracking = coordinator.message_tracking_payload(
            discuss_ack_message_id="100",
            review_message_id="200",
            search_started_message_id="300",
        )
        self.assertEqual(tracking["discuss_ack_message_id"], "100")
        self.assertEqual(tracking["review_message_id"], "200")
        self.assertEqual(tracking["review_message_ids"], ["200"])
        self.assertEqual(tracking["search_started_message_id"], "300")
        self.assertEqual(tracking["review_message_url"], coordinator.discord_message_url("1536658476288450630", "200"))

    def test_discard_dry_run_does_not_change_project_state(self):
        self.assertEqual(self.run_cmd("init", "--input", str(self.input_path)).returncode, 0)
        self.assertEqual(
            self.run_cmd(
                "record-messages",
                "acme-demo",
                "--actor",
                MINH,
                "--discuss-ack-message-id",
                "100",
                "--review-message-id",
                "200",
            ).returncode,
            0,
        )
        self.assertEqual(self.run_cmd("discard", "acme-demo", "--actor", MINH, "--dry-run").returncode, 0)
        self.assertEqual(self.load_project()["status"], "review")


if __name__ == "__main__":
    unittest.main()
