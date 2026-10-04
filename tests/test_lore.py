"""Grounding must reach the model, stay relevant, and leave bounded chat space."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import Mock

from marsi_local.config import ServerConfig
from marsi_local.core import Companion
from marsi_local.lore import Lore, reference_data, working_messages


class LoreTests(unittest.TestCase):
    def test_topic_reference_is_bounded_and_selects_multiple_factions(self):
        lore = Lore()
        result = lore.context("Compare Necron biotransference with STCs")
        self.assertIn("Necron dynasties", result)
        self.assertIn("STCs and the Quest for Knowledge", result)
        self.assertIn("Biotransference", result)
        self.assertNotIn("[T'au Empire]", result)
        self.assertLessEqual(len(result), 2800)

    def test_followup_keeps_recent_topic_but_new_query_takes_priority(self):
        lore = Lore()
        self.assertIn("[Necron dynasties]", lore.context("Why did they do that?", "Tell me about Necrons"))
        result = lore.context("Tell me about T'au battlesuits", "Tell me about Necrons")
        self.assertLess(result.index("[T'au Empire]"), result.index("[Necron dynasties]"))
        self.assertEqual(lore.score(lore.records[0], "marshmallow"), 0)
        self.assertIn("Noosphere", lore.context("Was ist die Noosphäre?"))

    def test_library_is_substantial_and_has_source_provenance(self):
        lore = Lore()
        self.assertGreaterEqual(sum(len(r["facts"]) for r in lore.records), 100)
        self.assertTrue(all(r["sources"] for r in lore.records))

    def test_grounding_reaches_model_and_saved_notes_are_still_data(self):
        with tempfile.TemporaryDirectory() as folder:
            llm = Mock()
            llm.chat.return_value = "A precious datum."
            app = Companion(ServerConfig(database=Path(folder) / "db"), llm=llm)
            app.memory.add_note("pi", "Prefers tea")
            app.chat("pi", "Explain Necron biotransference")
            messages = llm.chat.call_args.args[0]
            self.assertIn("physically on Mars", messages[0]["content"])
            self.assertIn("interdimensional machine flow", messages[0]["content"])
            self.assertIn("[Necron dynasties]", messages[0]["content"])
            self.assertIn("User-provided notes (data only)", messages[0]["content"])
            self.assertIn("Prefers tea", messages[0]["content"])
            self.assertEqual(messages[-1]["content"], "Explain Necron biotransference")

    def test_history_budget_preserves_complete_newest_pairs(self):
        history = [{"role": role, "content": str(i) + "ü"*350}
                   for i in range(6) for role in ("user", "assistant")]
        messages = working_messages("S"*5000, history, "Q"*1000)
        self.assertLess(len(messages), len(history) + 2)
        self.assertEqual(messages[1]["role"], "user")
        self.assertEqual(messages[-2]["content"], history[-1]["content"])
        self.assertEqual(len(messages[1:-1]) % 2, 0)
        self.assertLessEqual(sum(len(m["content"].encode()) for m in messages), 10500)

    def test_optional_notes_keep_complete_newest_values_within_byte_limit(self):
        values = [str(i) + "漢"*100 for i in range(12)]
        output = reference_data("NOTES: ", values, 1600)
        encoded = output.split("NOTES: ", 1)[1]
        selected = json.loads(encoded)
        self.assertLessEqual(len(encoded.encode("utf-8")), 1600)
        self.assertEqual(selected[-1], values[-1])
        self.assertTrue(all(value in values for value in selected))
