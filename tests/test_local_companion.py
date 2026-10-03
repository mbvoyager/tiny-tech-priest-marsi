"""Exercise the actual LAN API and durable memory without model downloads."""
import base64
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import wave

from marsi_local.ambient import Ambient, quiet_hour
from marsi_local.client import Client, ClientError
from marsi_local.config import ServerConfig, load_env, validate_bind
from marsi_local.core import Companion, Memory, Ollama, ServiceError
from marsi_local.server import Server
from marsi_local.speech import Speech, validate_wav


def wav_bytes(seconds=0.1, channels=1, rate=16000):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\0\0" * int(seconds * rate) * channels)
    return buffer.getvalue()


class FakeLLM:
    def __init__(self):
        self.messages = []

    def chat(self, messages):
        self.messages = messages
        return "The tiny forge is ready."


class FakeSpeech:
    def __init__(self):
        self.spoken = 0

    def transcribe(self, data):
        validate_wav(data)
        return "Hello Marsi"

    def synthesize(self, text):
        self.spoken += 1
        return wav_bytes()


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "memory.sqlite3"
        self.memory = Memory(self.path)

    def test_restart_retains_context_and_explicit_notes(self):
        self.memory.remember_turn("pi", "My name is Ada", "Hello Ada")
        self.memory.add_note("pi", "Ada prefers tea")
        reopened = Memory(self.path)
        self.assertEqual(reopened.notes("pi"), ["Ada prefers tea"])
        self.assertEqual(reopened.context("pi")[0]["content"], "My name is Ada")
        self.assertEqual(reopened.context("other"), [])
        self.assertEqual(reopened.notes("other"), [])

    def test_history_is_bounded_and_chronological(self):
        for number in range(40):
            self.memory.remember_turn("pi", str(number), f"Reply {number}")
        context = self.memory.context("pi")
        self.assertEqual(len(context), 12)
        self.assertEqual(context[0]["content"], "34")
        self.assertEqual(context[-1]["content"], "Reply 39")
        with self.memory.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM turns").fetchone()[0], 30)

    def test_forget_only_removes_selected_session(self):
        for session in ("pi", "other"):
            self.memory.remember_turn(session, "Hello", "Hi")
            self.memory.add_note(session, "Likes cogs")
        self.memory.forget("pi")
        self.assertEqual(self.memory.context("pi"), [])
        self.assertEqual(self.memory.notes("pi"), [])
        self.assertTrue(self.memory.context("other"))
        self.assertEqual(self.memory.notes("other"), ["Likes cogs"])

    def test_session_limit_removes_old_histories_and_notes(self):
        for number in range(101):
            session = f"session{number}"
            self.memory.remember_turn(session, "Hi", "Hello")
            self.memory.add_note(session, "A cog")
        self.assertEqual(self.memory.context("session0"), [])
        self.assertEqual(self.memory.notes("session0"), [])
        with self.memory.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 100)

    def test_prompt_includes_saved_context_but_rituals_do_not_use_private_memory(self):
        config = ServerConfig(database=self.path)
        llm = FakeLLM()
        app = Companion(config, llm=llm)
        app.memory.add_note("pi", "Prefers tea")
        app.memory.remember_turn("pi", "I am learning Python", "One little step at a time")
        result = app.chat("pi", "What next?")
        self.assertIn("Prefers tea", llm.messages[0]["content"])
        self.assertEqual(llm.messages[-3]["content"], "I am learning Python")
        self.assertEqual(llm.messages[-1]["content"], "What next?")
        self.assertEqual(result["source"], "qwen")

    def test_failed_inference_is_not_saved_as_a_successful_turn(self):
        llm = FakeLLM()
        app = Companion(ServerConfig(database=self.path), llm=llm)
        with patch.object(llm, "chat", side_effect=ServiceError("Offline")):
            with self.assertRaises(ServiceError):
                app.chat("pi", "Hello")
        self.assertEqual(app.memory.context("pi"), [])


class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.token = "a" * 64
        cls.speech = FakeSpeech()
        cls.app = Companion(ServerConfig(database=Path(cls.folder.name) / "db", token=cls.token),
                            llm=FakeLLM(), speech=cls.speech)
        cls.server = Server(("127.0.0.1", 0), cls.app)
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.client = Client(cls.url, cls.token, "pi")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.folder.cleanup()

    def setUp(self):
        self.app.memory.forget("pi")

    def test_health_is_public_but_chat_requires_token(self):
        visitor = Client(self.url)
        self.assertEqual(visitor.request("/health", method="GET")["status"], "ok")
        with self.assertRaisesRegex(ClientError, "token"):
            visitor.chat("Hello")

    def test_chat_and_memory_roundtrip_then_forget(self):
        self.client.remember("My name is Ada")
        self.assertEqual(self.client.notes()["notes"], ["My name is Ada"])
        self.assertEqual(self.client.chat("Hello")["text"], "The tiny forge is ready.")
        self.assertTrue(self.app.memory.context("pi"))
        self.client.forget()
        self.assertEqual(self.client.notes()["notes"], [])
        self.assertEqual(self.app.memory.context("pi"), [])

    def test_voice_upload_and_spoken_reply(self):
        result = self.client.voice(wav_bytes())
        self.assertEqual(result["heard"], "Hello Marsi")
        validate_wav(base64.b64decode(result["audio_base64"]))
        self.assertEqual(self.app.memory.context("pi")[0]["content"], "Hello Marsi")

    def test_mute_skips_synthesis(self):
        before = self.speech.spoken
        result = self.client.voice(wav_bytes(), speak=False)
        self.assertNotIn("audio_base64", result)
        self.assertEqual(self.speech.spoken, before)

    def test_invalid_recording_does_not_enter_memory(self):
        with self.assertRaisesRegex(ClientError, "WAV"):
            self.client.voice(b"not a recording")
        self.assertEqual(self.app.memory.context("pi"), [])

    def test_voice_failure_preserves_text(self):
        with patch.object(self.speech, "synthesize", side_effect=ServiceError("No voice configured")):
            result = self.client.chat("Hello", speak=True)
        self.assertEqual(result["text"], "The tiny forge is ready.")
        self.assertIn("No voice", result["voice_error"])

    def test_busy_returns_error_instead_of_overloading_cpu(self):
        with self.app.exclusive():
            with self.assertRaisesRegex(ClientError, "finishing another task"):
                self.client.chat("Hello")

    def test_bad_session_and_json_are_rejected(self):
        with self.assertRaisesRegex(ClientError, "session_id"):
            self.client.request("/v1/chat", {"text": "Hi", "session_id": "../secret"})
        request = urllib.request.Request(self.url + "/v1/chat", data=b"[]", headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + self.token})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.client.http.open(request)
        self.assertEqual(caught.exception.code, 400)
        caught.exception.close()

    def test_oversized_upload_is_rejected(self):
        with self.assertRaisesRegex(ClientError, "allowed size"):
            self.client.voice(b"x" * 2_000_001)

    def test_ritual_never_calls_llm_or_retains_conversation(self):
        with patch.object(self.app.llm, "chat", side_effect=AssertionError("No inference for rituals")):
            result = self.client.ritual()
        self.assertEqual(result["source"], "ritual")
        self.assertEqual(self.app.memory.context("pi"), [])


class AudioAndConfigTests(unittest.TestCase):
    def test_wave_validation(self):
        validate_wav(wav_bytes())
        for audio in (wav_bytes(seconds=0), wav_bytes(seconds=21), wav_bytes(channels=2),
                      wav_bytes(rate=8000), wav_bytes()[:-20]):
            with self.subTest(length=len(audio)), self.assertRaises(ValueError):
                validate_wav(audio)

    def test_lan_requires_a_long_token(self):
        validate_bind("127.0.0.1", "")
        validate_bind("0.0.0.0", "a" * 32)
        with self.assertRaises(ValueError):
            validate_bind("0.0.0.0", "short")
        with self.assertRaises(ValueError):
            validate_bind("marsi.local", "")

    def test_env_file_does_not_execute_or_override_shell_environment(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "env"
            path.write_text('MARSI_MODEL=file-value\nMARSI_TOKEN=$(do-not-execute)\n')
            with patch.dict("os.environ", {"MARSI_MODEL": "shell-value"}, clear=True):
                load_env(path)
                import os
                self.assertEqual(os.getenv("MARSI_MODEL"), "shell-value")
                self.assertEqual(os.getenv("MARSI_TOKEN"), "$(do-not-execute)")

    def test_ollama_receives_cpu_limits_and_no_thinking(self):
        llm = Ollama(ServerConfig())
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, limit): return b'{"message":{"content":"<think>secret</think>Hello human"}}'
        with patch.object(llm.http, "open", return_value=Response()) as opened:
            self.assertEqual(llm.chat([{"role": "user", "content": "Hello"}]), "Hello human")
        body = json.loads(opened.call_args.args[0].data)
        self.assertFalse(body["think"])
        self.assertEqual(body["options"]["num_thread"], 3)
        self.assertEqual(body["options"]["num_ctx"], 4096)

    def test_unconfigured_voice_reports_actionable_error(self):
        with self.assertRaisesRegex(ServiceError, "MARSI_PIPER_MODEL"):
            Speech(ServerConfig()).synthesize("Hi")


class AmbientTests(unittest.TestCase):
    def test_quiet_hours_across_midnight(self):
        self.assertTrue(quiet_hour(23, 22, 8))
        self.assertTrue(quiet_hour(7, 22, 8))
        self.assertFalse(quiet_hour(12, 22, 8))
        self.assertFalse(quiet_hour(12, 8, 8))

    def test_no_interruption_or_backlog(self):
        timer = Ambient(minimum=60, maximum=60)
        timer.next_at = 100
        self.assertFalse(timer.due(200, 12, 190, False, True))
        self.assertEqual(timer.next_at, 260)
        self.assertFalse(timer.due(260, 12, 0, True, True))
        self.assertFalse(timer.due(320, 23, 0, False, True))
        self.assertFalse(timer.due(380, 12, 0, False, False))
        self.assertTrue(timer.due(440, 12, 0, False, True))
        self.assertFalse(timer.due(441, 12, 0, False, True))


if __name__ == "__main__":
    unittest.main()
