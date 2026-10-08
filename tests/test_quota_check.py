import argparse
import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "agents" / "shared" / "quota_check.py"
spec = importlib.util.spec_from_file_location("quota_check", SCRIPT)
quota_check = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = quota_check
spec.loader.exec_module(quota_check)


class QuotaCheckTests(unittest.TestCase):
    def test_parse_json_uses_remaining_percent_or_computes_from_used_limit(self):
        payload = {
            "accounts": [
                {"provider": "codex", "used": 100, "limit": 100},
                {"provider": "codex", "used": 100, "limit": 100},
                {"provider": "codex", "used": 0, "limit": 100, "remaining_percent": 34},
                {"provider": "codex", "used": 57, "limit": 100},
                {"provider": "codex", "used": 46, "limit": 100},
                {"provider": "codex", "used": 26, "limit": 100},
                {"provider": "codex", "used": 100, "limit": 100},
                {"provider": "other", "used": 0, "limit": 100},
            ]
        }

        snapshot = quota_check.parse_quota_payload(payload, observed_at="2026-08-15T12:00:00+00:00")

        self.assertEqual(snapshot.total_remaining_percent, 205)
        self.assertEqual(snapshot.valid_accounts, 7)
        self.assertEqual(snapshot.source, "api")

    def test_parse_html_fallback_reads_session_ratio_and_remaining_percent(self):
        html = """
        <main>
          <div>codex session 100 / 100 0% in 5d</div>
          <div>codex session 100 / 100 0% in 5d</div>
          <div>codex session 66 / 100 34% in 4d</div>
          <div>codex session 57 / 100 43% in 4d</div>
          <div>codex session 46 / 100 54% in 5d</div>
          <div>codex session 26 / 100 74% in 4d</div>
          <div>codex session 100 / 100 0% in 5d</div>
        </main>
        """

        snapshot = quota_check.parse_html_quota(html, observed_at="2026-08-15T12:00:00+00:00")

        self.assertEqual(snapshot.total_remaining_percent, 205)
        self.assertEqual(snapshot.valid_accounts, 7)
        self.assertEqual(snapshot.source, "html")

    def test_collector_prefers_configured_api(self):
        calls = []
        payload = {"accounts": [{"provider": "codex", "used": 0, "limit": 100}]}

        def fetcher(url, **_kwargs):
            calls.append(url)
            return quota_check.HttpResponse(200, "application/json", json.dumps(payload).encode())

        collector = quota_check.QuotaCollector(
            base_url="http://router.invalid:20128",
            quota_path="/dashboard/quota",
            api_url="http://router.invalid:20128/api/quota",
            fetcher=fetcher,
        )

        snapshot = collector.collect("2026-08-15T12:00:00+00:00")

        self.assertEqual(snapshot.total_remaining_percent, 100)
        self.assertEqual(snapshot.source, "api")
        self.assertEqual(calls, ["http://router.invalid:20128/api/quota"])

    def test_collector_falls_back_to_html_after_api_payload_is_unusable(self):
        calls = []
        html = "<div>codex session 66 / 100 34%</div>"

        def fetcher(url, **_kwargs):
            calls.append(url)
            if url.endswith("/api/quota"):
                return quota_check.HttpResponse(200, "application/json", b"{}")
            return quota_check.HttpResponse(200, "text/html", html.encode())

        collector = quota_check.QuotaCollector(
            base_url="http://router.invalid:20128",
            quota_path="/dashboard/quota",
            api_url="http://router.invalid:20128/api/quota",
            fetcher=fetcher,
        )

        snapshot = collector.collect("2026-08-15T12:00:00+00:00")

        self.assertEqual(snapshot.total_remaining_percent, 34)
        self.assertEqual(snapshot.source, "html")
        self.assertEqual(calls, [
            "http://router.invalid:20128/api/quota",
            "http://router.invalid:20128/dashboard/quota",
        ])

    def test_collector_expands_codex_connection_list_into_usage_requests(self):
        calls = []

        def fetcher(url, **_kwargs):
            calls.append(url)
            if "/api/providers/client" in url:
                return quota_check.HttpResponse(
                    200,
                    "application/json",
                    json.dumps(
                        {
                            "connections": [
                                {"id": "codex-1", "provider": "codex"},
                                {"id": "other-1", "provider": "other"},
                            ]
                        }
                    ).encode(),
                )
            if url.endswith("/api/usage/codex-1"):
                return quota_check.HttpResponse(
                    200,
                    "application/json",
                    b'{"quotas":{"session":{"used":66,"total":100,"remaining":34}}}',
                )
            raise AssertionError(f"unexpected URL: {url}")

        collector = quota_check.QuotaCollector(
            base_url="http://router.invalid:20128",
            api_url="http://router.invalid:20128/api/providers/client?page=1&pageSize=100",
            fetcher=fetcher,
        )

        snapshot = collector.collect("2026-08-15T12:00:00+00:00")

        self.assertEqual(snapshot.total_remaining_percent, 34)
        self.assertEqual(snapshot.valid_accounts, 1)
        self.assertEqual(calls, [
            "http://router.invalid:20128/api/providers/client?page=1&pageSize=100",
            "http://router.invalid:20128/api/usage/codex-1",
        ])

    def test_collector_skips_an_unusable_account_quota_and_keeps_valid_total(self):
        calls = []

        def fetcher(url, **_kwargs):
            calls.append(url)
            if "/api/providers/client" in url:
                return quota_check.HttpResponse(
                    200,
                    "application/json",
                    b'{"connections":[{"id":"codex-1","provider":"codex"},{"id":"codex-2","provider":"codex"}]}',
                )
            if url.endswith("/api/usage/codex-1"):
                return quota_check.HttpResponse(
                    200,
                    "application/json",
                    b'{"quotas":{"session":{"used":66,"total":100,"remaining":34}}}',
                )
            if url.endswith("/api/usage/codex-2"):
                return quota_check.HttpResponse(200, "application/json", b'{"message":"quota unavailable"}')
            raise AssertionError(f"unexpected URL: {url}")

        collector = quota_check.QuotaCollector(
            base_url="http://router.invalid:20128",
            api_url="http://router.invalid:20128/api/providers/client?page=1&pageSize=100",
            fetcher=fetcher,
        )

        snapshot = collector.collect("2026-08-15T12:00:00+00:00")

        self.assertEqual(snapshot.total_remaining_percent, 34)
        self.assertEqual(snapshot.valid_accounts, 1)
        self.assertEqual(snapshot.source, "api")
        self.assertEqual(calls, [
            "http://router.invalid:20128/api/providers/client?page=1&pageSize=100",
            "http://router.invalid:20128/api/usage/codex-1",
            "http://router.invalid:20128/api/usage/codex-2",
        ])

    def test_collector_still_errors_when_every_codex_account_is_unusable(self):
        def fetcher(url, **_kwargs):
            if "/api/providers/client" in url:
                return quota_check.HttpResponse(
                    200,
                    "application/json",
                    b'{"connections":[{"id":"codex-1","provider":"codex"}]}',
                )
            return quota_check.HttpResponse(200, "application/json", b'{"message":"unavailable"}')

        collector = quota_check.QuotaCollector(
            base_url="http://router.invalid:20128",
            api_url="http://router.invalid:20128/api/providers/client?page=1&pageSize=100",
            fetcher=fetcher,
        )

        with self.assertRaises(quota_check.QuotaError) as raised:
            collector.collect("2026-08-15T12:00:00+00:00")

        self.assertEqual(raised.exception.code, "account_quota_error")

    def test_collector_passes_auth_cookie_without_logging_or_rewriting_it(self):
        seen = {}

        def fetcher(url, **kwargs):
            seen.update(kwargs)
            return quota_check.HttpResponse(200, "application/json", b'{"remaining_percent":34}')

        collector = quota_check.QuotaCollector(
            api_url="http://router.invalid:20128/api/quota",
            auth_cookie="auth_token=placeholder",
            fetcher=fetcher,
        )

        collector.collect("2026-08-15T12:00:00+00:00")

        self.assertEqual(seen["auth_cookie"], "auth_token=placeholder")

    def test_reminder_changes_at_100_percent(self):
        self.assertIn("timer 25 phút", quota_check.reminder_for(100))
        self.assertIn("dưới 100%", quota_check.reminder_for(99))
        self.assertNotEqual(quota_check.reminder_for(100), quota_check.reminder_for(99))

    def test_parse_interval_accepts_hours_and_minutes(self):
        self.assertEqual(quota_check.parse_interval("3h"), timedelta(hours=3))
        self.assertEqual(quota_check.parse_interval("180m"), timedelta(hours=3))
        with self.assertRaises(ValueError):
            quota_check.parse_interval("0h")

    def test_format_message_matches_discord_contract(self):
        next_run = datetime(2026, 8, 15, 15, 0, tzinfo=timezone(timedelta(hours=7)))

        message = quota_check.format_message(
            total_percent=205,
            reminder="Chọn một việc quan trọng.",
            quote="Một bước nhỏ vẫn đưa code tiến lên.",
            author="Codex Coach",
            next_run=next_run,
        )

        self.assertIn("**CODEX QUOTA CHECK**", message)
        self.assertIn("Còn **205%** để tiếp tục làm việc.", message)
        self.assertIn('> "Một bước nhỏ vẫn đưa code tiến lên."', message)
        self.assertIn("> — Codex Coach", message)
        self.assertIn("**Lần check tiếp theo:** 15:00 15/08/2026", message)

    def test_schedule_is_anchored_to_activation_and_advances_without_drift(self):
        state = {}
        activation = datetime(2026, 8, 15, 8, 30, tzinfo=UTC)
        first = quota_check.advance_schedule(state, activation)
        second = quota_check.advance_schedule(state, activation + timedelta(hours=3, minutes=1))

        self.assertEqual(first, activation + timedelta(hours=3))
        self.assertEqual(second, activation + timedelta(hours=6))
        self.assertEqual(state["activated_at"], activation.isoformat())

    def test_failure_alerts_once_on_second_consecutive_failure_and_resets_on_success(self):
        state = {}
        first = quota_check.record_failure(state)
        second = quota_check.record_failure(state)
        third = quota_check.record_failure(state)

        self.assertFalse(first.should_alert)
        self.assertTrue(second.should_alert)
        self.assertFalse(third.should_alert)
        self.assertEqual(state["consecutive_failures"], 3)
        self.assertTrue(state["technical_alert_sent"])

        quota_check.record_success(state, total_percent=205)
        self.assertEqual(state["consecutive_failures"], 0)
        self.assertFalse(state["technical_alert_sent"])
        self.assertEqual(state["last_total_percent"], 205)

    def test_quote_validation_rejects_secret_like_or_long_output(self):
        self.assertEqual(quota_check.validate_quote("Ship small, learn fast."), "Ship small, learn fast.")
        self.assertIsNone(quota_check.validate_quote("https://example.com"))
        self.assertIsNone(quota_check.validate_quote("password: do-not-share"))
        self.assertIsNone(quota_check.validate_quote("x" * 161))

    def test_quote_generation_falls_back_when_model_fails(self):
        fallback = [{"quote": "Một bước nhỏ vẫn đưa code tiến lên.", "author": "Codex Coach"}]

        def failing_model(_prompt):
            raise TimeoutError("model timeout")

        quote = quota_check.choose_quote(
            total_percent=205,
            observed_at="2026-08-15T12:00:00+00:00",
            model_runner=failing_model,
            fallback_quotes=fallback,
        )

        self.assertEqual(quote.quote, fallback[0]["quote"])
        self.assertEqual(quote.author, "Codex Coach")

    def test_discord_sender_retries_once_with_explicit_channel_target(self):
        calls = []

        def runner(args, **kwargs):
            calls.append(args)
            return type("Result", (), {"returncode": 1 if len(calls) == 1 else 0, "stderr": ""})()

        sent = quota_check.send_discord(
            "hello",
            channel_id="1533643473486348458",
            runner=runner,
        )

        self.assertTrue(sent)
        self.assertEqual(len(calls), 2)
        self.assertIn("channel:1533643473486348458", calls[0])
        self.assertIn("--channel", calls[0])
        self.assertIn("discord", calls[0])

    def test_run_lock_prevents_overlap_and_releases(self):
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "quota.lock"
            first = quota_check.RunLock(lock_path)
            second = quota_check.RunLock(lock_path)

            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            first.release()
            self.assertTrue(second.acquire())
            second.release()

    def test_execute_dry_run_formats_success_without_persisting_state_or_sending(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            args = argparse.Namespace(
                send=False,
                json_output=True,
                state_file=str(state_path),
                quotes_file=str(Path(directory) / "quotes.json"),
                base_url="http://router.invalid:20128",
                quota_path="/dashboard/quota",
                api_url=None,
                provider="codex",
                timeout=1.0,
                channel="1533643473486348458",
                interval=quota_check.DEFAULT_INTERVAL,
            )
            snapshot = quota_check.QuotaSnapshot(205, 7, "api", "2026-08-15T12:00:00+00:00")

            result = quota_check.execute(
                args,
                now=datetime(2026, 8, 15, 12, 0, tzinfo=timezone(timedelta(hours=7))),
                collector=type("Collector", (), {"collect": lambda _self, _observed: snapshot})(),
                model_runner=lambda _prompt: "Ship one small improvement.",
            )

            self.assertEqual(result["status"], "success")
            self.assertIn("205%", result["message"])
            self.assertFalse(state_path.exists())

    def test_execute_dry_run_does_not_call_quote_model(self):
        with tempfile.TemporaryDirectory() as directory:
            args = argparse.Namespace(
                send=False,
                json_output=True,
                state_file=str(Path(directory) / "state.json"),
                quotes_file=str(Path(directory) / "quotes.json"),
                base_url="http://router.invalid:20128",
                quota_path="/dashboard/quota",
                api_url=None,
                provider="codex",
                timeout=1.0,
                channel="1533643473486348458",
                interval=quota_check.DEFAULT_INTERVAL,
            )
            snapshot = quota_check.QuotaSnapshot(205, 7, "api", "2026-08-15T12:00:00+00:00")
            calls = []

            def model_runner(_prompt):
                calls.append(True)
                return "This model call should not happen in dry-run."

            result = quota_check.execute(
                args,
                now=datetime(2026, 8, 15, 12, 0, tzinfo=timezone(timedelta(hours=7))),
                collector=type("Collector", (), {"collect": lambda _self, _observed: snapshot})(),
                model_runner=model_runner,
            )

            self.assertEqual(result["status"], "success")
            self.assertEqual(calls, [])

    def test_parser_accepts_explicit_dry_run_mode(self):
        args = quota_check.build_parser().parse_args(["--dry-run"])

        self.assertFalse(args.send)

    def test_execute_sends_success_and_persists_sanitized_state(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            sent_messages = []
            args = argparse.Namespace(
                send=True,
                json_output=True,
                state_file=str(state_path),
                quotes_file=str(Path(directory) / "quotes.json"),
                base_url="http://router.invalid:20128",
                quota_path="/dashboard/quota",
                api_url=None,
                provider="codex",
                timeout=1.0,
                channel="1533643473486348458",
                interval=quota_check.DEFAULT_INTERVAL,
            )
            snapshot = quota_check.QuotaSnapshot(205, 7, "api", "2026-08-15T12:00:00+00:00")

            def sender(command, **_kwargs):
                sent_messages.append(command)
                return type("Result", (), {"returncode": 0})()

            result = quota_check.execute(
                args,
                now=datetime(2026, 8, 15, 12, 0, tzinfo=timezone(timedelta(hours=7))),
                collector=type("Collector", (), {"collect": lambda _self, _observed: snapshot})(),
                model_runner=lambda _prompt: "Ship one small improvement.",
                discord_runner=sender,
            )

            self.assertEqual(result["status"], "success")
            self.assertTrue(result["sent"])
            self.assertEqual(len(sent_messages), 1)
            self.assertIn("channel:1533643473486348458", sent_messages[0])
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["last_total_percent"], 205)
            self.assertNotIn("password", json.dumps(state).lower())
            self.assertNotIn("token", json.dumps(state).lower())

    def test_execute_sends_one_technical_alert_on_second_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            args = argparse.Namespace(
                send=True,
                json_output=True,
                state_file=str(state_path),
                quotes_file=str(Path(directory) / "quotes.json"),
                base_url="http://router.invalid:20128",
                quota_path="/dashboard/quota",
                api_url=None,
                provider="codex",
                timeout=1.0,
                channel="1533643473486348458",
                interval=quota_check.DEFAULT_INTERVAL,
            )
            messages = []
            collector = type(
                "FailingCollector",
                (),
                {
                    "collect": lambda _self, _observed: (_ for _ in ()).throw(
                        quota_check.QuotaError("offline", "connection_error")
                    )
                },
            )()

            def sender(args, **_kwargs):
                messages.append(args)
                return type("Result", (), {"returncode": 0})()

            first = quota_check.execute(
                args,
                now=datetime(2026, 8, 15, 12, 0, tzinfo=UTC),
                collector=collector,
                discord_runner=sender,
            )
            second = quota_check.execute(
                args,
                now=datetime(2026, 8, 15, 15, 0, tzinfo=UTC),
                collector=collector,
                discord_runner=sender,
            )

            self.assertFalse(first["sent"])
            self.assertTrue(second["technical_alert_attempted"])
            self.assertTrue(second["technical_alert_sent"])
            self.assertEqual(len(messages), 1)
            message_index = messages[0].index("--message") + 1
            self.assertIn("TECHNICAL ALERT", messages[0][message_index])

    def test_state_round_trip_does_not_store_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state = {"last_total_percent": 205, "consecutive_failures": 0}
            quota_check.save_state(state_path, state)
            loaded = quota_check.load_state(state_path)

            self.assertEqual(loaded, state)
            self.assertNotIn("password", json.dumps(loaded).lower())
            self.assertNotIn("token", json.dumps(loaded).lower())


if __name__ == "__main__":
    unittest.main()
