import io
import json
from pathlib import Path
import random
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import marsi


class TerminalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "state.json"
        self.now = time.time()
        self.cfg = marsi.load_settings(marsi.ROOT / "marsi.json")
        self.persona = marsi.Persona(rng=random.Random(42))
        self.state = marsi.State(self.path, "https://example.test/70", self.cfg, self.now)
        self.bot = marsi.TerminalBot(self.cfg, self.persona, self.state)
        self.args = SimpleNamespace(terminal=False, state=self.path,
                                    zuliprc=Path(self.tmp.name) / "no-zuliprc")

    def test_terminal_reuses_schedule_and_account_without_changing_state(self):
        before = self.path.read_bytes()
        local = marsi.State(self.path, None, self.cfg, self.now + 100)
        self.assertEqual(local.data, self.state.data)
        self.assertEqual(before, self.path.read_bytes())
        with self.assertRaises(ValueError):
            marsi.State(self.path, "another-account", self.cfg, self.now)

    def test_fresh_terminal_timer_can_later_bind_to_zulip(self):
        fresh = self.path.with_name("fresh.json")
        local = marsi.State(fresh, None, self.cfg, self.now)
        online = marsi.State(fresh, "https://example.test/70", self.cfg, self.now + 10)
        self.assertEqual(online.data["next_post_at"], local.data["next_post_at"])
        self.assertEqual(online.data["account"], "https://example.test/70")

    def test_text_mentions_and_commands_work_without_cooldown(self):
        with patch.object(self.persona, "reply", return_value="beep") as reply, \
                patch("sys.stdout", io.StringIO()):
            for text in ("coffee", "@Marsi coffee", "@marsi: coffee", "@**Marsi|70** coffee"):
                self.assertTrue(self.bot.handle_input(text))
            self.assertEqual([c.args[0] for c in reply.call_args_list], ["coffee"] * 4)
            for text in ("", "   ", "/help", "/status", "/sermon"):
                self.assertTrue(self.bot.handle_input(text))
            self.assertFalse(self.bot.handle_input("/quit"))
            self.assertEqual(reply.call_count, 4)

    def test_bonus_sermon_does_not_move_timer(self):
        before = self.path.read_bytes()
        with patch("sys.stdout", io.StringIO()) as output:
            self.bot.handle_input("/sermon")
        self.assertIn("Marsi [sermon]", output.getvalue())
        self.assertEqual(before, self.path.read_bytes())

    def test_overdue_sermon_prints_once_and_stays_saved_after_restart(self):
        later = self.now + 20 * 86400
        with patch("sys.stdout", io.StringIO()) as output:
            self.bot.maybe_post(later)
            self.bot.state = marsi.State(self.path, None, self.cfg, later)
            self.bot.maybe_post(later)
        self.assertEqual(output.getvalue().count("Marsi [sermon]"), 1)
        self.assertGreaterEqual(self.bot.state.data["next_post_at"], later + 48 * 3600)

    def test_disabled_sermons_stay_quiet(self):
        self.cfg["automatic_posts"] = False
        with patch("sys.stdout", io.StringIO()) as output:
            self.bot.maybe_post(self.now + 20 * 86400)
        self.assertEqual(output.getvalue(), "")

    def test_sermon_timer_runs_while_input_is_blocked_and_stops_on_exit(self):
        fired = threading.Event()
        maybe_post = self.bot.maybe_post
        calls = []

        def observe(now):
            calls.append(now)
            maybe_post(now)
            if len(calls) > 1:
                fired.set()

        def idle_input(prompt):
            # First synchronous check is done; become due while waiting for input.
            with self.bot.lock:
                self.state.data["next_post_at"] = time.time() - 1
            self.assertTrue(fired.wait(3), "Sermon timer blocked behind input()")
            return "/quit"

        with patch.object(self.bot, "maybe_post", side_effect=observe), \
                patch("builtins.input", side_effect=idle_input), patch("sys.stdout", io.StringIO()) as output:
            self.bot.run()
        self.assertIn("Marsi [sermon]", output.getvalue())
        self.assertFalse(any(t.name == "marsi-sermon-timer" for t in threading.enumerate()))

    def test_eof_and_ctrl_c_stop_timer_cleanly(self):
        for error in (EOFError, KeyboardInterrupt):
            bot = marsi.TerminalBot(self.cfg, self.persona, self.state)
            with patch("builtins.input", side_effect=error), patch("sys.stdout", io.StringIO()):
                if error is KeyboardInterrupt:
                    with self.assertRaises(KeyboardInterrupt):
                        bot.run()
                else:
                    bot.run()
            self.assertTrue(bot.stop.is_set())
            self.assertFalse(any(t.name == "marsi-sermon-timer" for t in threading.enumerate()))

    def test_terminal_chats_use_existing_log_and_review(self):
        journal = marsi.ConversationLog(self.path.parent)
        self.bot.conversations = journal
        try:
            with patch("sys.stdout", io.StringIO()):
                self.bot.handle_input("@Marsi coffee please")
        finally:
            journal.close()
        rows = [json.loads(s) for s in (self.path.parent / "conversations.jsonl").read_text(
            encoding="utf-8").splitlines()]
        self.assertEqual(rows[0]["conversation_type"], "terminal")
        self.assertEqual(rows[0]["categories"], ["coffee"])
        self.assertTrue(rows[0]["message_id"].startswith("terminal-"))
        self.assertEqual(rows[1]["outcome"], "printed")
        with patch("sys.stdout", io.StringIO()) as output:
            marsi.review_conversations(self.path.parent)
        self.assertIn("coffee: 1", output.getvalue())

    def test_terminal_flag_never_creates_api_client(self):
        with patch("sys.argv", ["marsi.py", "--terminal", "--state", str(self.path)]), \
                patch("marsi.ZulipAPI") as api, patch("builtins.input", return_value="/quit"), \
                patch("sys.stdout", io.StringIO()):
            self.assertEqual(marsi.main(), 0)
        api.assert_not_called()

    def test_startup_access_denied_or_network_failure_switches_to_local(self):
        for error in (marsi.ApiError("FORBIDDEN", 403), marsi.ApiError("UNAUTHORIZED", 401),
                      marsi.ApiError("NETWORK_OR_RESPONSE_ERROR")):
            api = Mock()
            api.identify.side_effect = error
            before = self.path.read_bytes()
            with patch("marsi.ZulipAPI", return_value=api), \
                    patch.object(marsi.TerminalBot, "run") as run:
                marsi.run_session(self.args, self.cfg, self.persona, None)
            run.assert_called_once()
            api.identify.assert_called_once()
            self.assertEqual(before, self.path.read_bytes())

    def test_missing_credentials_start_local(self):
        with patch.object(marsi.TerminalBot, "run") as run:
            marsi.run_session(self.args, self.cfg, self.persona, None)
        run.assert_called_once()

    def test_poll_access_loss_switches_once_without_more_server_calls(self):
        api = Mock(site="https://example.test")
        api.identify.return_value = {"user_id": 70, "full_name": "Marsi"}
        api.request.side_effect = [{"queue_id": "test", "last_event_id": -1},
                                   marsi.ApiError("FORBIDDEN", 403)]
        with patch("marsi.ZulipAPI", return_value=api), patch("marsi.find_channel", return_value=29), \
                patch.object(marsi.TerminalBot, "run") as run:
            marsi.run_session(self.args, self.cfg, self.persona, None)
        run.assert_called_once()
        self.assertEqual(api.request.call_count, 2)

    def test_failed_zulip_sermon_prints_locally_without_scheduling_twice(self):
        self.state.data["next_post_at"] = self.now - 1
        self.state.save()
        api = Mock(site="https://example.test")
        api.identify.return_value = {"user_id": 70, "full_name": "Marsi"}
        api.request.side_effect = [{"queue_id": "test", "last_event_id": -1},
                                   {"events": []}, marsi.ApiError("FORBIDDEN", 403)]
        with patch("marsi.ZulipAPI", return_value=api), patch("marsi.find_channel", return_value=29), \
                patch("builtins.input", return_value="/quit"), patch("sys.stdout", io.StringIO()) as output:
            marsi.run_session(self.args, self.cfg, self.persona, None)
        self.assertEqual(api.request.call_count, 3)
        self.assertEqual(output.getvalue().count("Marsi [sermon]"), 1)
        sent_text = api.request.call_args.kwargs["content"]
        saved = marsi.read_json(self.path)
        self.assertIn(saved["last_post"], sent_text)
        self.assertIn(saved["last_post"], " ".join(output.getvalue().split()))
        self.assertEqual(sent_text.count(chr(96) * 3), 4)
        self.assertGreater(saved["next_post_at"], self.now + 48 * 3600)


if __name__ == "__main__":
    unittest.main()
