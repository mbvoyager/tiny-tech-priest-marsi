import copy
import io
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse

import marsi


ME = {"user_id": 70, "full_name": "Marsi"}
NOW = 1_800_000_000


def event(message_id=100, sender_id=9, content="hello", kind="private", flags=None):
    return {"type": "message", "id": message_id, "flags": flags or [], "message": {
        "id": message_id, "sender_id": sender_id, "timestamp": NOW, "content": content,
        "type": kind, "display_recipient": [{"id": 70}, {"id": sender_id}],
        "stream_id": 29, "subject": "coffee break"}}


class FakeAPI:
    def __init__(self):
        self.sent = []
        self.bots = {70, 88}
        self.fail = False

    def is_bot(self, user_id):
        return user_id in self.bots

    def request(self, method, endpoint, **params):
        self.sent.append((method, endpoint, params))
        if self.fail:
            raise marsi.ApiError("NETWORK_OR_RESPONSE_ERROR")
        return {"result": "success", "id": 1000}


class BehaviorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "state.json"
        self.cfg = marsi.load_settings(marsi.ROOT / "marsi.json")
        self.persona = marsi.Persona(rng=random.Random(42))
        self.api = FakeAPI()
        self.state = self.load_state()
        self.bot = marsi.Bot(self.api, self.cfg, self.persona, self.state, ME, 29, NOW)

    def load_state(self, now=NOW):
        return marsi.State(self.path, "test/70", self.cfg, now, random.Random(10))

    def test_dm_stays_private(self):
        self.bot.handle(event(), NOW)
        sent = self.api.sent[0][2]
        self.assertEqual(sent["type"], "direct")
        self.assertEqual(sent["to"], [9])
        self.assertNotIn("topic", sent)

    def test_channel_only_direct_mentions_same_topic(self):
        for content, flags in (("hello Marsi", []), ("@**all**", ["wildcard_mentioned"]),
                               ("@**Marsi**", []), ("@**Other|123**", ["mentioned"]),
                               ("@*Ops*", ["mentioned"])):
            self.bot.handle(event(content=content, kind="stream", flags=flags), NOW)
        self.assertEqual(self.api.sent, [])
        self.bot.handle(event(content="@**Marsi|70** coffee please", kind="stream",
                              flags=["mentioned"]), NOW)
        sent = self.api.sent[0][2]
        self.assertEqual((sent["type"], sent["to"], sent["topic"]),
                         ("stream", 29, "coffee break"))
        simple = event(content="@**Marsi** hello", kind="stream", flags=["mentioned"])
        self.assertIsNotNone(marsi.reply_target(simple, ME, self.cfg))

    def test_group_dm_requires_mention_and_keeps_recipients(self):
        group = event()
        group["message"]["display_recipient"].append({"id": 12})
        self.bot.handle(group, NOW)
        self.assertEqual(self.api.sent, [])
        group["message"]["content"] = "@**Marsi** bless us"
        group["flags"] = ["mentioned"]
        self.bot.handle(group, NOW)
        self.assertEqual(self.api.sent[0][2]["to"], [9, 12])

    def test_self_other_bots_old_messages_and_edits_ignored(self):
        self.bot.handle(event(sender_id=70), NOW)
        self.bot.handle(event(sender_id=88), NOW)
        stale = event()
        stale["message"]["timestamp"] = NOW - 1
        self.bot.handle(stale, NOW)
        self.bot.handle(event(), NOW + 301)
        self.bot.handle({"type": "update_message"}, NOW)
        self.assertEqual(self.api.sent, [])

    def test_duplicate_and_cooldowns_survive_restart(self):
        self.bot.handle(event(), NOW)
        self.bot.state = self.load_state(NOW + 1)
        self.bot.handle(event(message_id=101), NOW + 1)
        self.bot.handle(event(), NOW + 10)
        self.assertEqual(len(self.api.sent), 1)
        self.bot.handle(event(message_id=102), NOW + 10)
        self.assertEqual(len(self.api.sent), 2)

    def test_global_reply_cap(self):
        for i in range(20):
            self.bot.handle(event(message_id=i, sender_id=100 + i), NOW)
        self.assertEqual(len(self.api.sent), self.cfg["max_replies_per_minute"])

    def test_failed_reply_is_not_retried_after_restart(self):
        self.api.fail = True
        with self.assertRaises(marsi.ApiError):
            self.bot.handle(event(), NOW)
        self.api.fail = False
        self.bot.state = self.load_state(NOW + 10)
        self.bot.handle(event(), NOW + 10)
        self.assertEqual(len(self.api.sent), 1)

    def test_first_post_and_persisted_due_time(self):
        due = self.state.data["next_post_at"]
        self.assertGreaterEqual(due, NOW + 48 * 3600)
        self.assertLessEqual(due, NOW + 72 * 3600)
        self.assertEqual(self.load_state(NOW + 100).data["next_post_at"], due)
        self.bot.maybe_post(NOW)
        self.assertEqual(self.api.sent, [])

    def test_overdue_post_once_no_catchup_burst(self):
        later = NOW + 20 * 86400
        self.bot.maybe_post(later)
        self.bot.state = self.load_state(later)
        self.bot.maybe_post(later)
        self.assertEqual(len(self.api.sent), 1)
        sent = self.api.sent[0][2]
        self.assertEqual((sent["type"], sent["to"], sent["topic"]),
                         ("stream", 29, self.cfg["topic"]))
        self.assertGreaterEqual(self.bot.state.data["next_post_at"], later + 48 * 3600)

    def test_disabled_and_failed_posts_do_not_repeat(self):
        later = NOW + 20 * 86400
        self.cfg["automatic_posts"] = False
        self.bot.maybe_post(later)
        self.assertEqual(self.api.sent, [])
        self.cfg["automatic_posts"] = True
        self.api.fail = True
        with self.assertRaises(marsi.ApiError):
            self.bot.maybe_post(later)
        self.api.fail = False
        self.bot.state = self.load_state(later)
        self.bot.maybe_post(later)
        self.assertEqual(len(self.api.sent), 1)

    def test_corrupt_state_fails_without_resetting(self):
        self.path.write_text('{"unfinished":', encoding="utf-8")
        with self.assertRaises(ValueError):
            self.load_state()
        self.assertEqual(self.path.read_text(), '{"unfinished":')

    def test_duplicate_process_lock_released_on_exit(self):
        lock = self.path.with_suffix(".lock")
        with marsi.single_instance(lock):
            with self.assertRaises(ValueError):
                with marsi.single_instance(lock):
                    self.fail("Second process acquired the lock")
        with marsi.single_instance(lock):
            pass

    def test_keyword_context_no_user_text_echo(self):
        text = self.persona.reply("@**Marsi|70** COFFEE @**all** https://example.org/private-token")
        coffee = next(t for t in self.persona.data["topics"] if t["name"] == "coffee")
        self.assertTrue(any(line in text for line in coffee["replies"]))
        self.assertNotIn("@**", text)
        self.assertNotIn("private-token", text)
        unmatched = self.persona.reply("This is a chair")
        self.assertTrue(any(line in unmatched for line in self.persona.data["fallbacks"]))

    def test_post_never_repeats_previous(self):
        previous = self.persona.post()
        for _ in range(50):
            result = self.persona.post(previous)
            self.assertNotEqual(result, previous)
            previous = result

    def test_invalid_settings_rejected(self):
        cfgpath = Path(self.tmp.name) / "marsi.json"
        for key, value in (("automatic_posts", "false"), ("poll_seconds", 0),
                           ("post_interval_min_hours", 1000)):
            bad = copy.deepcopy(self.cfg)
            bad[key] = value
            cfgpath.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaises(ValueError):
                marsi.load_settings(cfgpath)

    def test_expired_queue_is_reregistered_without_history_replay(self):
        class StopLoop(Exception):
            pass

        replies = [{"queue_id": "old", "last_event_id": -1},
                   marsi.ApiError("BAD_EVENT_QUEUE_ID", 400),
                   {"queue_id": "new", "last_event_id": -1},
                   {"events": []}]
        with patch.object(self.api, "request", side_effect=replies) as call:
            with patch("marsi.time.sleep", side_effect=[None, StopLoop()]):
                with self.assertRaises(StopLoop):
                    self.bot.run()
        endpoints = [c.args[1] for c in call.call_args_list]
        self.assertEqual(endpoints, ["register", "events", "register", "events"])
        self.assertEqual(call.call_args_list[-1].kwargs["queue_id"], "new")


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        config = Path(self.tmp.name) / "zuliprc"
        config.write_text("[api]\nsite=https://zulip.example.test\nemail=marsi@example.test\nkey=test-key\n")
        self.api = marsi.ZulipAPI(config)

    def test_api_form_encoding_and_raw_events(self):
        response = io.BytesIO(b'{"result":"success","queue_id":"test","last_event_id":-1}')
        with patch.object(self.api.opener, "open", return_value=response) as call:
            self.api.request("POST", "register", event_types=["message"], apply_markdown=False)
        req = call.call_args.args[0]
        fields = urllib.parse.parse_qs(req.data.decode())
        self.assertEqual(json.loads(fields["event_types"][0]), ["message"])
        self.assertEqual(fields["apply_markdown"], ["false"])
        self.assertTrue(req.get_header("Authorization").startswith("Basic "))

    def test_api_error_preserves_code_but_not_response_secrets(self):
        error = urllib.error.HTTPError("https://zulip.example.test", 429, "Rate limited",
                                       {"Retry-After": "12"},
                                       io.BytesIO(b'{"code":"RATE_LIMIT_HIT","msg":"secret"}'))
        with patch.object(self.api.opener, "open", side_effect=error):
            with self.assertRaises(marsi.ApiError) as caught:
                self.api.request("POST", "messages", content="hello")
        self.assertEqual(caught.exception.retry_after, 12)
        self.assertEqual(caught.exception.code, "RATE_LIMIT_HIT")
        self.assertNotIn("secret", str(caught.exception))


class LoggingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.path = self.directory / "state.json"
        self.cfg = marsi.load_settings(marsi.ROOT / "marsi.json")
        self.state = marsi.State(self.path, "test/70", self.cfg, NOW)
        self.persona = marsi.Persona(rng=random.Random(42))
        self.api = FakeAPI()
        self.journal = marsi.ConversationLog(self.directory)
        self.addCleanup(self.journal.close)
        self.bot = marsi.Bot(self.api, self.cfg, self.persona, self.state, ME, 29, NOW, self.journal)

    def rows(self):
        return [json.loads(line) for line in (self.directory / "conversations.jsonl").read_text(
            encoding="utf-8").splitlines()]

    def test_records_message_reply_and_categories_once(self):
        msg = event(content="coffee please\nwith milk")
        self.bot.handle(msg, NOW)
        self.bot.handle(msg, NOW + 10)
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["content"], "coffee please\nwith milk")
        self.assertEqual(rows[0]["categories"], ["coffee"])
        self.assertEqual(rows[0]["sender_id"], 9)
        self.assertIsNone(rows[0]["channel_id"])
        self.assertEqual(rows[1]["outcome"], "sent")
        self.assertEqual(rows[1]["reply"], self.api.sent[0][2]["content"])

    def test_excludes_unaddressed_bot_and_stale_content(self):
        self.bot.handle(event(kind="stream", content="private project details"), NOW)
        self.bot.handle(event(sender_id=88), NOW)
        self.bot.handle(event(), NOW + 301)
        group = event()
        group["message"]["display_recipient"].append({"id": 12})
        self.bot.handle(group, NOW)
        self.assertEqual(self.rows(), [])

    def test_logs_cooldown_and_uncertain_delivery(self):
        self.bot.handle(event(), NOW)
        self.bot.handle(event(message_id=101, content="gardening"), NOW + 1)
        self.assertEqual(self.rows()[-1]["outcome"], "skipped_cooldown")
        self.api.fail = True
        with self.assertRaises(marsi.ApiError):
            self.bot.handle(event(message_id=102), NOW + 10)
        self.assertEqual(self.rows()[-1]["outcome"], "failed_or_unconfirmed")

    def test_logging_can_be_disabled_without_affecting_replies(self):
        self.bot.conversations = None
        self.bot.handle(event(), NOW)
        self.assertEqual(self.rows(), [])
        self.assertEqual(len(self.api.sent), 1)

    def test_rotation_review_and_incomplete_lines(self):
        self.journal.handler.maxBytes = 700
        self.bot.handle(event(content="coffee"), NOW)
        self.bot.handle(event(message_id=101, content="gardening\n\x1b[31m"), NOW + 10)
        self.assertTrue((self.directory / "conversations.jsonl.1").exists())
        with (self.directory / "conversations.jsonl").open("a", encoding="utf-8") as target:
            target.write('{"interrupted":\n')
        output = io.StringIO()
        with patch("sys.stdout", output):
            marsi.review_conversations(self.directory)
        text = output.getvalue()
        self.assertIn("Reviewing 2 recent messages", text)
        self.assertIn("coffee: 1", text)
        self.assertIn("Without a matching keyword category: 1", text)
        self.assertIn("gardening", text)
        self.assertNotIn("\x1b", text)
        self.assertIn("Skipped 1 incomplete", text)

    def test_activity_log_contains_preserved_and_rescheduled_due_time(self):
        due = self.state.data["next_post_at"]
        with marsi.activity_log(self.directory):
            self.bot.log_due_time()
            self.bot.maybe_post(NOW + 20 * 86400)
        text = (self.directory / "marsi.log").read_text(encoding="utf-8")
        self.assertIn(marsi.local_time(due), text)
        self.assertIn(marsi.local_time(self.state.data["next_post_at"]), text)
        self.assertNotIn("coffee", text)

    def test_status_is_offline_and_does_not_change_timer(self):
        original = self.path.read_bytes()
        output = io.StringIO()
        with patch("sys.argv", ["marsi.py", "--status", "--state", str(self.path)]), \
                patch("sys.stdout", output), patch("marsi.ZulipAPI") as api:
            self.assertEqual(marsi.main(), 0)
        api.assert_not_called()
        self.assertIn(marsi.local_time(self.state.data["next_post_at"]), output.getvalue())
        self.assertEqual(self.path.read_bytes(), original)
        missing = self.directory / "unused.json"
        with patch("sys.stdout", io.StringIO()):
            marsi.show_status(missing, self.cfg, NOW)
        self.assertFalse(missing.exists())

    def test_overdue_status_reports_single_attempt(self):
        output = io.StringIO()
        with patch("sys.stdout", output):
            marsi.show_status(self.path, self.cfg, NOW + 20 * 86400)
        self.assertIn("Overdue: one sermon", output.getvalue())


if __name__ == "__main__":
    unittest.main()
