"""OKF frontmatter serialization quality (the cleanup raised 2026-06-25):

* ``type`` describes CONTENT, not provenance (provenance lives in ``x_source``), so a
  memory stays meaningful dropped into another OKF knowledge base;
* ``description`` is a real first-sentence summary, not a mid-word char-chop;
* ``x_metadata_json`` carries only NEW metadata — never a field that already has a
  dedicated ``x_`` key (e.g. daydream's ``extracted_from == x_session_id``);
* ``x_item_id`` is RETAINED (graph_store + the loader fast-path read it).

All changes stay round-trip safe for our own docs. unittest-style to match the rest of
the stores suite (picked up by ``unittest discover``).
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_BASE = Path(__file__).resolve().parents[3]  # .../eval
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from memeval.okf import memory_item_to_doc, doc_to_memory_item, split_doc  # noqa: E402
from memeval.schema import MemoryItem  # noqa: E402


def _fm(item: MemoryItem) -> dict:
    fm, _ = split_doc(memory_item_to_doc(item))
    return fm


class OKFFrontmatterTests(unittest.TestCase):
    # -- type: content, not provenance ------------------------------------- #
    def test_type_is_not_provenance(self):
        fm = _fm(MemoryItem(item_id="mem_1", content="hello world.", source="daydream"))
        self.assertEqual(fm["type"], "Memory")        # honest generic — NOT the source
        self.assertEqual(fm["x_source"], "daydream")  # provenance preserved here instead

    def test_okf_type_override_still_wins(self):
        fm = _fm(MemoryItem(item_id="mem_2", content="x", source="daydream",
                            metadata={"okf_type": "Issue"}))
        self.assertEqual(fm["type"], "Issue")

    # -- description: real first sentence, not a char-chop ----------------- #
    def test_description_is_first_sentence(self):
        fm = _fm(MemoryItem(item_id="m3", content="First sentence here. Second is ignored."))
        self.assertEqual(fm["description"], "First sentence here.")

    def test_description_does_not_split_on_domain_dot(self):
        fm = _fm(MemoryItem(item_id="m4",
                            content="Posting to bpaste.net fails with HTTP 400 and no end period"))
        self.assertIn("bpaste.net fails", fm["description"])  # the dot must not end it

    def test_okf_description_override_still_wins(self):
        fm = _fm(MemoryItem(item_id="m5", content="a. b. c.",
                            metadata={"okf_description": "a real summary"}))
        self.assertEqual(fm["description"], "a real summary")

    # -- x_metadata_json: no redundant fields ------------------------------ #
    def test_x_metadata_json_dropped_when_only_redundant(self):
        fm = _fm(MemoryItem(item_id="m6", content="x", session_id="S1",
                            metadata={"extracted_from": "S1"}))
        self.assertNotIn("x_metadata_json", fm)

    def test_x_metadata_json_keeps_new_metadata_only(self):
        fm = _fm(MemoryItem(item_id="m7", content="x", session_id="S1",
                            metadata={"extracted_from": "S1", "note": "keep me"}))
        self.assertEqual(json.loads(fm["x_metadata_json"]), {"note": "keep me"})

    # -- x_item_id retained (regression) ----------------------------------- #
    def test_x_item_id_retained(self):
        fm = _fm(MemoryItem(item_id="m8", content="x", source="daydream"))
        self.assertEqual(fm["x_item_id"], "m8")

    def test_description_keeps_lowercase_abbreviations(self):
        # e.g./i.e. are followed by lowercase, so they must NOT end the sentence
        fm = _fm(MemoryItem(item_id="m10",
                            content="Use e.g. the fallback values. Second is ignored."))
        self.assertEqual(fm["description"], "Use e.g. the fallback values.")
        fm2 = _fm(MemoryItem(item_id="m11",
                             content="Prefer i.e. the default path. Next sentence."))
        self.assertEqual(fm2["description"], "Prefer i.e. the default path.")

    def test_description_empty_content(self):
        self.assertEqual(_fm(MemoryItem(item_id="m12", content=""))["description"], "(empty)")

    def test_description_caps_long_unbroken_content(self):
        d = _fm(MemoryItem(item_id="m13",
                           content=("word " * 80).strip() + " ends with no period"))["description"]
        self.assertTrue(d.endswith("…"))
        self.assertLessEqual(len(d), 200)

    # -- x_metadata_json dedup is KEY-AWARE, not value-based ---------------- #
    def test_x_metadata_json_keeps_nonredundant_even_if_value_equals_id(self):
        # different keys whose values equal item_id/source must be KEPT (not value-stripped)
        fm = _fm(MemoryItem(item_id="ID1", content="x", session_id="S1", source="daydream",
                            metadata={"related_to": "ID1", "via": "daydream"}))
        self.assertEqual(json.loads(fm["x_metadata_json"]),
                         {"related_to": "ID1", "via": "daydream"})

    def test_x_metadata_json_keeps_extracted_from_when_not_session(self):
        fm = _fm(MemoryItem(item_id="m14", content="x", session_id="S1",
                            metadata={"extracted_from": "OTHER"}))
        self.assertEqual(json.loads(fm["x_metadata_json"]), {"extracted_from": "OTHER"})

    # -- round-trip stays faithful for our own docs ------------------------ #
    def test_round_trip_preserves_substance(self):
        it = MemoryItem(item_id="m9", content="Fix the bug. More detail.", source="daydream",
                        session_id="S9", tags=["bug"], relevancy=0.9, version=1,
                        metadata={"extracted_from": "S9"})
        back = doc_to_memory_item(memory_item_to_doc(it))
        self.assertEqual(back.item_id, "m9")
        self.assertEqual(back.source, "daydream")   # recovered from x_source, not type
        self.assertEqual(back.session_id, "S9")
        self.assertEqual(back.content, "Fix the bug. More detail.")
        self.assertEqual(back.tags, ["bug"])


if __name__ == "__main__":
    unittest.main()
