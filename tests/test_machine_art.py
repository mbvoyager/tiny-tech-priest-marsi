import io
from pathlib import Path
import random
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import marsi
from machine_art import MachineArt


class ArtworkTests(unittest.TestCase):
    def test_ascii_panels_keep_geometry_at_multiple_console_widths(self):
        for width in (40, 55, 72, 76):
            art = MachineArt(random.Random(3), width)
            for _ in range(12):
                output = art.canticle()
                self.assertTrue(output.isascii())
                self.assertTrue(all(len(line) == width for line in output.splitlines()))
                self.assertIn("PURITY SEAL", output)
                self.assertIn("MECHANICUS", output)

    def test_generation_varies_seals_binary_and_motifs(self):
        art = MachineArt(random.Random(4), 72)
        outputs = [art.canticle() for _ in range(15)]
        self.assertEqual(len(set(outputs)), len(outputs))
        names = [next(name for name, _ in art.MOTIFS if name in out) for out in outputs]
        self.assertTrue(all(left != right for left, right in zip(names, names[1:])))

    def test_sermon_is_between_top_and_bottom_artwork(self):
        text = "The toaster has requested a small robe."
        art = MachineArt(random.Random(7), 72)
        rendered = art.sermon(text)
        self.assertLess(rendered.index("TINY SERMON"), rendered.index(text))
        self.assertGreater(rendered.index("PURITY SEAL"), rendered.index(text))
        markdown = art.sermon(text, markdown=True)
        fence = chr(96) * 3
        self.assertEqual(markdown.count(fence), 4)
        self.assertIn(f"{fence}\n\n{text}\n\n{fence}", markdown)


class HourlyArtTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "state.json"
        self.cfg = marsi.load_settings(marsi.ROOT / "marsi.json")
        self.now = time.time()
        self.state = marsi.State(self.path, "test", self.cfg, self.now)
        self.sermon_due = self.state.data["next_post_at"]
        self.persona = marsi.Persona(rng=random.Random(10))
        with patch("marsi.time.time", return_value=self.now):
            self.bot = marsi.TerminalBot(self.cfg, self.persona, self.state)

    def test_old_state_gains_hourly_timer_without_moving_sermon(self):
        self.assertEqual(self.state.data["next_machine_art_at"], self.now + 3600)
        self.assertEqual(self.state.data["next_post_at"], self.sermon_due)
        before = self.path.read_bytes()
        reloaded = marsi.State(self.path, None, self.cfg, self.now + 1800)
        marsi.TerminalBot(self.cfg, self.persona, reloaded)
        self.assertEqual(self.path.read_bytes(), before)

    def test_hourly_event_prints_once_and_is_saved_across_restart(self):
        with patch("sys.stdout", io.StringIO()) as output:
            self.bot.maybe_machine_art(self.now + 3599)
            self.assertEqual(output.getvalue(), "")
            self.bot.maybe_machine_art(self.now + 3600)
            self.bot.state = marsi.State(self.path, None, self.cfg, self.now + 3601)
            self.bot.maybe_machine_art(self.now + 3601)
        self.assertEqual(output.getvalue().count("Marsi [machine canticle]"), 1)
        self.assertEqual(self.bot.state.data["next_machine_art_at"], self.now + 7200)
        self.assertEqual(self.bot.state.data["next_post_at"], self.sermon_due)

    def test_long_absence_produces_one_display_instead_of_a_backlog(self):
        later = self.now + 14 * 86400
        with patch("sys.stdout", io.StringIO()) as output:
            self.bot.maybe_machine_art(later)
            self.bot.maybe_machine_art(later)
        self.assertEqual(output.getvalue().count("Marsi [machine canticle]"), 1)
        self.assertEqual(self.state.data["next_machine_art_at"], later + 3600)

    def test_art_can_be_disabled_independently_from_sermons(self):
        self.cfg["hourly_machine_art"] = False
        with patch("sys.stdout", io.StringIO()) as output:
            self.bot.maybe_machine_art(self.now + 7200)
        self.assertEqual(output.getvalue(), "")
        self.cfg["hourly_machine_art"] = True
        self.cfg["automatic_posts"] = False
        with patch("sys.stdout", io.StringIO()) as output:
            self.bot.maybe_machine_art(self.now + 7200)
        self.assertIn("Marsi [machine canticle]", output.getvalue())

    def test_plain_commands_and_aliases_preserve_scheduled_times(self):
        before = self.path.read_bytes()
        with patch.object(self.persona, "reply", return_value="beep") as reply, \
                patch("sys.stdout", io.StringIO()) as output:
            for command in ("help", "HELP", "status", "sermon", "art",
                            "@Marsi art", "/art", "/status", "/sermon"):
                self.assertTrue(self.bot.handle_input(command))
            for command in ("quit", "exit", "/quit", "/exit"):
                self.assertFalse(self.bot.handle_input(command))
            reply.assert_not_called()
            self.assertTrue(self.bot.handle_input("can we exit the forge?"))
            reply.assert_called_once_with("can we exit the forge?")
        self.assertIn("PURITY SEAL", output.getvalue())
        self.assertEqual(self.path.read_bytes(), before)

    def test_hourly_display_runs_while_waiting_for_input(self):
        fired = threading.Event()
        original = self.bot.print_machine_art

        def display():
            original()
            fired.set()

        def idle_input(prompt):
            with self.bot.lock:
                self.state.data["next_machine_art_at"] = time.time() - 1
            self.assertTrue(fired.wait(3), "Hourly display blocked by input")
            return "quit"

        with patch.object(self.bot, "print_machine_art", side_effect=display), \
                patch("builtins.input", side_effect=idle_input), patch("sys.stdout", io.StringIO()) as output:
            self.bot.run()
        self.assertIn("NOOSPHERIC CANTICLE", output.getvalue())
        self.assertTrue(self.bot.stop.is_set())

    def test_corrupt_art_timer_is_not_reset(self):
        self.state.data["next_machine_art_at"] = "broken"
        self.state.save()
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            marsi.TerminalBot(self.cfg, self.persona, self.state)
        self.assertEqual(self.path.read_bytes(), before)

    def test_art_preview_is_offline_without_state_changes(self):
        before = self.path.read_bytes()
        with patch("sys.argv", ["marsi.py", "--preview-art", "--state", str(self.path)]), \
                patch("marsi.ZulipAPI") as api, patch("sys.stdout", io.StringIO()) as output:
            self.assertEqual(marsi.main(), 0)
        api.assert_not_called()
        self.assertIn("CULT MECHANICUS", output.getvalue())
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
