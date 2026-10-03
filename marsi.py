"""Marsi: a tiny, template-powered Zulip tech-priest. Python 3.10+, stdlib only."""
from __future__ import annotations

import argparse
import base64
from collections import Counter, deque
import configparser
from contextlib import contextmanager
from datetime import datetime
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import os
from pathlib import Path
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from machine_art import MachineArt

ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger("marsi")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_settings(path):
    cfg = read_json(path)
    cfg.setdefault("log_conversations", True)
    cfg.setdefault("hourly_machine_art", True)
    cfg.setdefault("machine_art_interval_minutes", 60)
    for key in ("automatic_posts", "reply_to_mentions", "log_conversations", "hourly_machine_art"):
        if type(cfg.get(key)) is not bool:
            raise ValueError(f"{key} must be true or false in marsi.json")
    for key in ("channel", "topic"):
        if not isinstance(cfg.get(key), str) or not cfg[key].strip():
            raise ValueError(f"{key} must be nonempty in marsi.json")
    limits = {"post_interval_min_hours": 1, "post_interval_max_hours": 1,
              "reply_cooldown_seconds": 1, "max_replies_per_minute": 1,
              "max_message_age_seconds": 1, "poll_seconds": 1, "machine_art_interval_minutes": 1}
    for key, minimum in limits.items():
        value = cfg.get(key)
        if type(value) not in (int, float) or not math.isfinite(value) or value < minimum:
            raise ValueError(f"{key} must be a number >= {minimum}")
    if cfg["post_interval_min_hours"] > cfg["post_interval_max_hours"]:
        raise ValueError("Minimum post interval must not exceed maximum")
    return cfg


class Persona:
    def __init__(self, path=ROOT / "phrases.json", rng=None):
        self.data = read_json(path)
        self.rng = rng or random.Random()
        self.recent = deque(maxlen=12)
        for key in ("openers", "closers", "fallbacks", "posts"):
            self.validate_lines(self.data.get(key), key)
        self.topics = []
        for topic in self.data["topics"]:
            self.validate_lines(topic.get("keywords"), "keywords")
            self.validate_lines(topic.get("replies"), "replies")
            pattern = r"(?<!\w)(?:" + "|".join(map(re.escape, topic["keywords"])) + r")(?!\w)"
            self.topics.append((topic["name"], re.compile(pattern, re.I), topic["replies"]))

    @staticmethod
    def validate_lines(lines, label):
        if not isinstance(lines, list) or not lines or not all(isinstance(s, str) and s.strip() for s in lines):
            raise ValueError(f"phrases.json: {label} must be a nonempty list of text")

    def choose(self, lines):
        line = self.rng.choice([s for s in lines if s not in self.recent] or lines)
        self.recent.append(line)
        return line

    @staticmethod
    def clean_content(content):
        return re.sub(r"@_?\*\*.*?\*\*", "", content).strip()

    def matching_topics(self, content):
        clean = self.clean_content(content)
        return [(name, replies) for name, pattern, replies in self.topics if pattern.search(clean)]

    def categories(self, content):
        if self.clean_content(content).casefold() in ("help", "/help", "!help"):
            return ["help"]
        return [name for name, _ in self.matching_topics(content)]

    def reply(self, content):
        # Never echo user text: no accidental mentions, links, or copied instructions.
        clean = self.clean_content(content)
        if clean.casefold() in ("help", "/help", "!help"):
            return ("I am Marsi, a tiny tech-priest with a very large hat. ⚙️\n\n"
                    "DM me, or directly @mention me in a channel I have joined. "
                    "Try `bless my laptop`, `the printer is broken`, or `I need coffee`. "
                    "My wisdom is handcrafted nonsense. Allow a few seconds between messages. uwu")
        matches = [replies for _, replies in self.matching_topics(content)]
        pool = self.rng.choice(matches) if matches else self.data["fallbacks"]
        body = self.choose(pool)
        return f"{self.rng.choice(self.data['openers'])} {body} {self.rng.choice(self.data['closers'])}"

    def post(self, previous=None):
        return self.choose([s for s in self.data["posts"] if s != previous] or self.data["posts"])


def local_time(timestamp):
    return datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="seconds")


@contextmanager
def activity_log(directory):
    directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(directory / "marsi.log", maxBytes=1_000_000,
                                  backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    previous_level = LOG.level
    LOG.setLevel(logging.INFO)
    LOG.addHandler(handler)
    try:
        yield
    finally:
        LOG.removeHandler(handler)
        handler.close()
        LOG.setLevel(previous_level)


class ConversationLog:
    """Local JSONL journal of addressed human messages, plus reply outcomes."""
    def __init__(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        self.handler = RotatingFileHandler(directory / "conversations.jsonl", maxBytes=2_000_000,
                                           backupCount=3, encoding="utf-8")
        self.handler.setFormatter(logging.Formatter("%(message)s"))
        self.logger = logging.Logger("marsi.conversations", level=logging.INFO)
        self.logger.addHandler(self.handler)

    def write(self, kind, now, **fields):
        self.logger.info(json.dumps({"event": kind, "recorded_at": local_time(now), **fields},
                                    ensure_ascii=False))

    def close(self):
        self.handler.close()
        self.logger.removeHandler(self.handler)


def review_conversations(directory, limit=200):
    # Oldest backup first. Deduplicate events and keep the most recent sample.
    messages = {}
    malformed = 0
    for suffix in (".3", ".2", ".1", ""):
        path = directory / ("conversations.jsonl" + suffix)
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as source:
            for line in source:
                try:
                    record = json.loads(line)
                    if record.get("event") == "interaction":
                        if not isinstance(record.get("content"), str):
                            raise ValueError("Invalid content")
                        if not isinstance(record.get("categories"), list) or not all(
                                isinstance(c, str) for c in record["categories"]):
                            raise ValueError("Invalid categories")
                        messages[record["message_id"]] = record
                except (ValueError, KeyError, TypeError, AttributeError):
                    malformed += 1
    recent = list(messages.values())[-limit:]
    if not recent:
        print("No logged conversations yet. Logging begins after launching the updated Marsi.")
    else:
        counts = Counter(category for row in recent for category in row["categories"])
        unmatched = [row for row in recent if not row["categories"]]
        print(f"Reviewing {len(recent)} recent messages addressed to Marsi.")
        print(f"Without a matching keyword category: {len(unmatched)}")
        print("Matched categories: " + (", ".join(f"{k}: {v}" for k, v in counts.most_common()) or "none"))
        print("Recent unmatched messages (ideas for new keywords and jokes):")
        for row in unmatched[-10:]:
            # JSON quoting also escapes newlines and terminal control characters.
            print(f"  #{row['message_id']}: {json.dumps(row['content'], ensure_ascii=False)}")
        print("Review these themes and add handwritten replies to phrases.json, then restart Marsi.")
    if malformed:
        print(f"Skipped {malformed} incomplete or invalid log lines.")


def show_status(path, cfg, now):
    print(f"Automatic sermons: {'enabled' if cfg['automatic_posts'] else 'disabled'}")
    if not path.exists():
        print("No saved timer yet. The first launch will choose a due time.")
        return
    due = read_json(path)["next_post_at"]
    print(f"Next sermon due: {local_time(due)} (local time)")
    if not cfg["automatic_posts"]:
        print("The due time is saved, but automatic sermons are disabled.")
    elif due <= now:
        print("Overdue: one sermon will be attempted on the next run (Zulip or terminal).")
    else:
        print("Marsi will deliver it when due, or on the next run after that (Zulip or terminal).")
    print(f"Saved schedule: {path}")
    art_due = read_json(path).get("next_machine_art_at")
    if cfg["hourly_machine_art"] and art_due is not None:
        print(f"Next terminal machine canticle due: {local_time(art_due)}")


class ApiError(Exception):
    def __init__(self, code, status=0, retry_after=5):
        self.code, self.status, self.retry_after = code, status, retry_after
        super().__init__(f"Zulip request failed ({code}, HTTP {status})")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Do not forward the Authorization header to a redirect destination.
        return None


class ZulipAPI:
    def __init__(self, path):
        cfg = configparser.ConfigParser(interpolation=None)
        if not cfg.read(path, encoding="utf-8-sig"):
            raise ValueError("Missing zuliprc; download the bot's configuration into this folder")
        api = cfg["api"]
        self.site = api["site"].rstrip("/")
        if urllib.parse.urlsplit(self.site).scheme != "https":
            raise ValueError("zuliprc site must use https://")
        self.auth = "Basic " + base64.b64encode(f"{api['email']}:{api['key']}".encode()).decode()
        self.opener = urllib.request.build_opener(NoRedirect())
        self.sender_cache = {}

    def request(self, method, endpoint, **params):
        fields = {k: json.dumps(v) if isinstance(v, (list, dict, bool)) else str(v)
                  for k, v in params.items()}
        encoded = urllib.parse.urlencode(fields).encode()
        url = self.site + "/api/v1/" + endpoint
        if method == "GET" and fields:
            url += "?" + encoded.decode()
        req = urllib.request.Request(url, data=encoded if method != "GET" else None,
                                     method=method, headers={"Authorization": self.auth,
                                                            "User-Agent": "Marsi/1.0"})
        try:
            with self.opener.open(req, timeout=20) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                body = json.load(exc)
            except (ValueError, OSError):
                body = {}
            retry = exc.headers.get("Retry-After", "5")
            raise ApiError(body.get("code", "HTTP_ERROR"), exc.code,
                           float(retry) if retry.isdigit() else 5) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            raise ApiError("NETWORK_OR_RESPONSE_ERROR") from None
        if result.get("result") != "success":
            raise ApiError(result.get("code", "API_ERROR"))
        return result

    def identify(self):
        user = self.request("GET", "users/me")
        if not user.get("is_bot") or user.get("bot_type") != 1:
            raise ValueError("Use the zuliprc of a Generic bot, not a personal account or webhook bot")
        return user

    def is_bot(self, user_id):
        cached = self.sender_cache.get(user_id)
        if cached is None or cached[1] < time.time():
            user = self.request("GET", f"users/{user_id}")["user"]
            cached = (user["is_bot"], time.time() + 3600)
            self.sender_cache[user_id] = cached
        return cached[0]


def find_channel(api, cfg):
    channels = api.request("GET", "users/me/subscriptions")["subscriptions"]
    for channel in channels:
        if channel["name"].casefold() == cfg["channel"].casefold():
            return channel["stream_id"]
    if cfg["automatic_posts"]:
        raise ValueError(f"Subscribe Marsi to '{cfg['channel']}' in Zulip, or disable automatic_posts")
    return None


class State:
    def __init__(self, path, account, cfg, now, rng=None):
        self.path, self.cfg = path, cfg
        self.rng = rng or random.Random()
        if path.exists():
            self.data = read_json(path)
            saved_account = self.data.get("account")
            if not isinstance(saved_account, str) or not saved_account:
                raise ValueError("Invalid account in state file")
            # Terminal sessions reuse the saved timer without needing server identity.
            if account is not None and saved_account not in (account, "terminal"):
                raise ValueError("Saved state belongs to another bot; use a separate --state file")
            due = self.data.get("next_post_at")
            if type(due) not in (int, float) or not math.isfinite(due) or due <= 0:
                raise ValueError("Invalid schedule in state file; restore a valid backup")
            for key, kind in (("handled", list), ("reply_times", list), ("sender_last", dict)):
                if not isinstance(self.data.get(key), kind):
                    raise ValueError(f"Invalid {key} in state file")
            if account is not None and saved_account == "terminal":
                self.data["account"] = account
                self.save()
        else:
            self.data = {"account": account or "terminal", "next_post_at": now + self.interval(),
                         "handled": [], "reply_times": [], "sender_last": {}, "last_post": None}
            self.save()

    def interval(self):
        return self.rng.uniform(self.cfg["post_interval_min_hours"],
                                self.cfg["post_interval_max_hours"]) * 3600

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        with temp.open("w", encoding="utf-8") as out:
            json.dump(self.data, out, indent=2)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, self.path)

    def claim_reply(self, message_id, sender_id, now):
        if message_id in self.data["handled"]:
            return False
        recent = [t for t in self.data["reply_times"] if t > now - 60]
        senders = {k: t for k, t in self.data["sender_last"].items()
                   if t > now - self.cfg["reply_cooldown_seconds"]}
        if str(sender_id) in senders or len(recent) >= self.cfg["max_replies_per_minute"]:
            return False
        self.data["handled"] = (self.data["handled"] + [message_id])[-500:]
        self.data["reply_times"] = recent + [now]
        self.data["sender_last"] = {**senders, str(sender_id): now}
        self.save()  # Claim before sending: a timeout must not cause duplicate replies.
        return True

    def claim_post(self, now, content):
        if not self.cfg["automatic_posts"] or now < self.data["next_post_at"]:
            return False
        self.data["next_post_at"] = now + self.interval()
        self.data["last_post"] = content
        self.save()
        return True

    def ensure_machine_art_schedule(self, now):
        if "next_machine_art_at" not in self.data:
            self.data["next_machine_art_at"] = now + self.cfg["machine_art_interval_minutes"] * 60
            self.save()
        due = self.data["next_machine_art_at"]
        if type(due) not in (int, float) or not math.isfinite(due) or due <= 0:
            raise ValueError("Invalid machine art schedule in state file")

    def claim_machine_art(self, now):
        if not self.cfg["hourly_machine_art"]:
            return False
        self.ensure_machine_art_schedule(now)
        if now < self.data["next_machine_art_at"]:
            return False
        self.data["next_machine_art_at"] = now + self.cfg["machine_art_interval_minutes"] * 60
        self.save()
        return True


@contextmanager
def single_instance(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as lock:
        lock.seek(0, os.SEEK_END)
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError("Marsi is already running with this state file") from None
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def reply_target(event, me, cfg):
    msg = event["message"]
    if msg["sender_id"] == me["user_id"]:
        return None
    flags = event.get("flags", [])
    # The server's mentioned flag excludes text that merely looks like a mention
    # inside code. Inspect the actual mention too, to exclude group/wildcard pings.
    mentions = re.findall(r"@\*\*([^*]+)\*\*", msg.get("content", ""))
    direct_mention = "mentioned" in flags and any(
        (token.rsplit("|", 1)[-1] == str(me["user_id"]) if "|" in token
        else token.casefold() == me["full_name"].casefold()) for token in mentions)
    if msg["type"] == "private":
        recipients = sorted({u["id"] for u in msg["display_recipient"]} - {me["user_id"]})
        if recipients and (len(recipients) == 1 or (cfg["reply_to_mentions"] and direct_mention)):
            return {"type": "direct", "to": recipients}
    if msg["type"] == "stream" and cfg["reply_to_mentions"] and direct_mention:
        return {"type": "stream", "to": msg["stream_id"], "topic": msg["subject"]}
    return None


class Bot:
    def __init__(self, api, cfg, persona, state, me, channel, started_at, conversations=None):
        self.api, self.cfg, self.persona, self.state = api, cfg, persona, state
        self.me, self.channel, self.started_at = me, channel, started_at
        self.conversations = conversations
        self.logged_messages = deque(maxlen=500)
        self.undelivered_post = None
        self.art = MachineArt(width=72)

    def log_due_time(self):
        LOG.info("Next automatic post due: %s (local time; only while running)",
                 local_time(self.state.data["next_post_at"]))

    def handle(self, event, now):
        if event.get("type") != "message":
            return
        msg = event["message"]
        if msg["timestamp"] < max(self.started_at, now - self.cfg["max_message_age_seconds"]):
            return
        target = reply_target(event, self.me, self.cfg)
        if target is None or self.api.is_bot(msg["sender_id"]):
            return
        if msg["id"] in self.state.data["handled"]:
            return
        if self.conversations and msg["id"] not in self.logged_messages:
            self.conversations.write("interaction", now, message_id=msg["id"],
                                     sender_id=msg["sender_id"], sent_at=local_time(msg["timestamp"]),
                                     conversation_type=msg["type"],
                                     channel_id=msg.get("stream_id") if msg["type"] == "stream" else None,
                                     topic=msg.get("subject") if msg["type"] == "stream" else None,
                                     content=msg["content"], categories=self.persona.categories(msg["content"]))
            self.logged_messages.append(msg["id"])
        content = self.persona.reply(msg["content"])
        if self.state.claim_reply(msg["id"], msg["sender_id"], now):
            try:
                result = self.api.request("POST", "messages", **target, content=content)
            except ApiError as exc:
                if self.conversations:
                    self.conversations.write("reply_result", time.time(), message_id=msg["id"],
                                             reply=content, outcome="failed_or_unconfirmed", error_code=exc.code)
                raise
            if self.conversations:
                self.conversations.write("reply_result", time.time(), message_id=msg["id"],
                                         reply=content, outcome="sent", reply_message_id=result.get("id"))
            LOG.info("Replied to message %s", msg["id"])
        elif self.conversations:
            self.conversations.write("reply_result", now, message_id=msg["id"],
                                     outcome="skipped_cooldown")

    def maybe_post(self, now):
        if self.cfg["automatic_posts"] and now >= self.state.data["next_post_at"]:
            content = self.persona.post(self.state.data.get("last_post"))
            if self.state.claim_post(now, content):
                # Persisted and logged before the attempt, including when sending fails.
                self.log_due_time()
                try:
                    self.api.request("POST", "messages", type="stream", to=self.channel,
                                     topic=self.cfg["topic"], content=self.art.sermon(content, markdown=True))
                except ApiError:
                    # Keep this attempt for terminal fallback; never resend it to Zulip.
                    self.undelivered_post = content
                    raise
                LOG.info("Posted a tiny machine sermon")

    def run(self):
        queue, last_event = None, -1
        retry = 5
        LOG.info("Marsi is awake. Ctrl+C to stop. Replies and automatic posts are enabled by your settings.")
        if self.cfg["automatic_posts"]:
            self.log_due_time()
        LOG.info("Local conversation logging: %s", "enabled" if self.conversations else "disabled")
        while True:
            try:
                if queue is None:
                    result = self.api.request("POST", "register", event_types=["message"],
                                              fetch_event_types=[], apply_markdown=False,
                                              all_public_streams=False,
                                              client_capabilities={"notification_settings_null": True,
                                                                   "empty_topic_name": True})
                    queue, last_event = result["queue_id"], result["last_event_id"]
                    LOG.info("Connected to Zulip; listening for new messages")
                # Nonblocking event polling keeps the timer and Ctrl+C responsive.
                result = self.api.request("GET", "events", queue_id=queue,
                                          last_event_id=last_event, dont_block=True)
                for event in result["events"]:
                    try:
                        self.handle(event, time.time())
                    finally:
                        last_event = event["id"]
                self.maybe_post(time.time())
                retry = 5
                time.sleep(self.cfg["poll_seconds"])
            except ApiError as exc:
                if self.undelivered_post is not None:
                    raise
                if exc.code == "BAD_EVENT_QUEUE_ID":
                    queue = None
                elif exc.status != 429:
                    raise
                LOG.warning("%s; reconnecting in %.0fs. Claimed messages are not resent.",
                            exc, max(retry, exc.retry_after))
                time.sleep(max(retry, exc.retry_after))
                retry = min(retry * 2, 60)


class TerminalBot:
    """Local conversation in the foreground, with an independent sermon timer."""
    def __init__(self, cfg, persona, state, conversations=None):
        self.cfg, self.persona, self.state = cfg, persona, state
        self.conversations = conversations
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.art = MachineArt()
        if self.cfg["hourly_machine_art"]:
            self.state.ensure_machine_art_schedule(time.time())

    def say(self, content, kind="reply"):
        label = "Marsi [sermon]" if kind == "sermon" else "Marsi"
        if kind == "sermon":
            content = "\n" + self.art.sermon(content)
        print(f"\n{label}: {content}\n", flush=True)

    def print_sermon(self, content):
        with self.lock:
            self.say(content, "sermon")
            if self.conversations:
                self.conversations.write("sermon", time.time(), content=content,
                                         conversation_type="terminal", outcome="printed",
                                         next_post_at=self.state.data["next_post_at"])
            LOG.info("Printed a tiny machine sermon in the terminal")
            LOG.info("Next automatic post due: %s (local time; only while running)",
                     local_time(self.state.data["next_post_at"]))

    def maybe_post(self, now):
        with self.lock:
            if self.cfg["automatic_posts"] and now >= self.state.data["next_post_at"]:
                content = self.persona.post(self.state.data.get("last_post"))
                if self.state.claim_post(now, content):
                    self.print_sermon(content)

    def print_machine_art(self):
        with self.lock:
            print("\nMarsi [machine canticle]:\n" + self.art.canticle() + "\n", flush=True)
            LOG.info("Printed a machine canticle in the terminal")

    def maybe_machine_art(self, now):
        with self.lock:
            if self.state.claim_machine_art(now):
                self.print_machine_art()
                LOG.info("Next terminal machine canticle due: %s",
                         local_time(self.state.data["next_machine_art_at"]))

    def handle_input(self, text):
        with self.lock:
            # A mention is optional in the console; accept both plain and Zulip forms.
            clean = self.persona.clean_content(text)
            clean = re.sub(r"^@Marsi\b[\s,:]*", "", clean, flags=re.I).strip()
            if not clean:
                return True
            command = clean.casefold()
            # Plain words are primary; keep old slash commands as aliases.
            if command.startswith("/"):
                command = command[1:]
            if command in ("quit", "exit"):
                return False
            if command in ("help", "!help"):
                self.say("Local forge operational, flesh-friend! Type anything and press Enter. "
                         "@Marsi is optional. sermon gives you a bonus sermon; art summons a "
                         "machine canticle; status shows the saved timers; quit or Ctrl+C "
                         "tucks me into my charging alcove. uwu")
                return True
            if command == "status":
                show_status(self.state.path, self.cfg, time.time())
                return True
            if command == "sermon":
                # Bonus sermons are previews: the automatic timer is unchanged.
                self.say(self.persona.post(self.state.data.get("last_post")), "sermon")
                return True
            if command == "art":
                self.print_machine_art()
                return True
            now, message_id = time.time(), "terminal-" + uuid.uuid4().hex
            if self.conversations:
                self.conversations.write("interaction", now, message_id=message_id,
                                         sender_id="terminal", sent_at=local_time(now),
                                         conversation_type="terminal", channel_id=None, topic=None,
                                         content=text, categories=self.persona.categories(clean))
            reply = self.persona.reply(clean)
            self.say(reply)
            if self.conversations:
                self.conversations.write("reply_result", time.time(), message_id=message_id,
                                         reply=reply, outcome="printed")
            LOG.info("Replied in the terminal")
            return True

    def schedule(self):
        # input() stays on the main thread, so Ctrl+C and Windows console editing work.
        # Only this short timer runs in the background, including while input is idle.
        try:
            while not self.stop.wait(1):
                now = time.time()
                self.maybe_post(now)
                self.maybe_machine_art(now)
        except Exception as exc:
            LOG.error("Terminal display timers stopped (%s). Restart Marsi after checking the state file.",
                      type(exc).__name__)

    def run(self):
        LOG.info("Marsi is in terminal mode for this run. No further Zulip connections will be made.")
        LOG.info("Local conversation logging: %s", "enabled" if self.conversations else "disabled")
        self.say("My forge fits inside this terminal now! Type a message and press Enter. "
                 "Try 'I need coffee' or '@Marsi bless my laptop'. Type help for the local commands.")
        if self.cfg["automatic_posts"]:
            LOG.info("Next automatic post due: %s (local time; only while running)",
                     local_time(self.state.data["next_post_at"]))
        else:
            LOG.info("Automatic sermons are disabled; sermon still prints a bonus sermon")
        if self.cfg["hourly_machine_art"]:
            LOG.info("Next terminal machine canticle due: %s",
                     local_time(self.state.data["next_machine_art_at"]))
        self.maybe_post(time.time())
        self.maybe_machine_art(time.time())
        timer = threading.Thread(target=self.schedule, name="marsi-sermon-timer", daemon=True)
        timer.start()
        try:
            while True:
                try:
                    text = input("You > ")
                except EOFError:
                    break
                if not self.handle_input(text):
                    break
        finally:
            self.stop.set()
            timer.join()
            LOG.info("Marsi stopped")
            self.say("Curling up in my charging alcove. Goodbye, flesh-friend.")


def run_session(args, cfg, persona, conversations):
    state, failed_sermon = None, None
    if not args.terminal:
        try:
            api = ZulipAPI(args.zuliprc)
            me = api.identify()
            channel = find_channel(api, cfg)
        except (ApiError, ValueError, OSError, KeyError, configparser.Error) as exc:
            # Avoid echoing configuration-parser details, which can contain credentials.
            reason = str(exc) if isinstance(exc, ApiError) else type(exc).__name__
            LOG.warning("Zulip unavailable (%s); switching to terminal mode.", reason)
        else:
            started_at = int(time.time())
            # Account/state validation errors must still fail closed, not reset the timer.
            state = State(args.state, f"{api.site}/{me['user_id']}", cfg, started_at)
            bot = Bot(api, cfg, persona, state, me, channel, started_at, conversations)
            try:
                bot.run()
                return
            except ApiError as exc:
                LOG.warning("Zulip unavailable (%s); switching to terminal mode.", exc)
                failed_sermon = bot.undelivered_post
    if state is None:
        state = State(args.state, None, cfg, time.time())
    terminal = TerminalBot(cfg, persona, state, conversations)
    if failed_sermon is not None:
        LOG.info("Zulip delivery was not confirmed; displaying that sermon locally.")
        terminal.print_sermon(failed_sermon)
    terminal.run()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--run", action="store_true", help="Start on Zulip, falling back to terminal if unavailable")
    mode.add_argument("--terminal", action="store_true", help="Chat and hear sermons locally; never connect to Zulip")
    mode.add_argument("--check", action="store_true", help="Read-only credentials and channel check")
    mode.add_argument("--status", action="store_true", help="Show the saved sermon due time offline")
    mode.add_argument("--review", action="store_true", help="Review local conversation themes offline")
    mode.add_argument("--preview", metavar="MESSAGE", help="Preview a reply offline; nothing is sent")
    mode.add_argument("--preview-post", action="store_true", help="Preview a sermon offline")
    mode.add_argument("--preview-art", action="store_true", help="Preview a generated ASCII machine canticle offline")
    parser.add_argument("--config", type=Path, default=ROOT / "marsi.json")
    parser.add_argument("--zuliprc", type=Path, default=ROOT / "zuliprc")
    parser.add_argument("--state", type=Path, default=ROOT / "data" / "state.json")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        cfg = load_settings(args.config)
        if args.status:
            show_status(args.state, cfg, time.time())
            return 0
        if args.review:
            review_conversations(args.state.parent)
            return 0
        if args.preview_art:
            print(MachineArt().canticle())
            return 0
        persona = Persona()
        if args.preview is not None or args.preview_post:
            print(MachineArt().sermon(persona.post()) if args.preview_post else persona.reply(args.preview))
            return 0
        if args.check:
            api = ZulipAPI(args.zuliprc)
            me = api.identify()
            channel = find_channel(api, cfg)
            print(f"Connected as {me['full_name']} (Generic bot, ID {me['user_id']}).")
            print(f"Channel: {cfg['channel']} (ID {channel}). Automatic posts: {cfg['automatic_posts']}.")
            print("Check passed. No messages sent; Marsi has not been started.")
            return 0
        with single_instance(args.state.with_suffix(".lock")):
            with activity_log(args.state.parent):
                conversations = ConversationLog(args.state.parent) if cfg["log_conversations"] else None
                try:
                    run_session(args, cfg, persona, conversations)
                except KeyboardInterrupt:
                    LOG.info("Marsi stopped")
                    raise
                except Exception:
                    LOG.error("Marsi stopped due to an error; see the console for details")
                    raise
                finally:
                    if conversations:
                        conversations.close()
        return 0
    except KeyboardInterrupt:
        print("\nMarsi has curled up in his charging alcove. Goodbye, flesh-friend.")
        return 0
    except (ApiError, ValueError, OSError, KeyError, TypeError, configparser.Error) as exc:
        LOG.error("%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
