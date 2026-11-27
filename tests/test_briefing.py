from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from email.message import Message
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from urllib.error import HTTPError

from datetime import date

import app as dashboard
import briefing
from conftest import seed_kb


class BriefingTests(TestCase):
    docket = "8C_354/2026"
    evidence = "The court decided the right to benefits under statutory accident insurance."

    def setUp(self):
        workspace = self.enter(TemporaryDirectory())
        self.case = seed_kb(Path(workspace) / "kb.sqlite", self.docket, self.evidence)
        self.enter(patch.dict(dashboard.app.config, DATABASE=Path(workspace) / "review.sqlite",
                              KB=Path(workspace) / "kb.sqlite", TODAY=date(2026, 11, 27)))
        self.enter(patch("app.model_config", return_value={"LLM_NAME": "test-model"}))
        self.enter(patch("briefing.voice_config", side_effect=lambda lang: ("secret", f"voice-{lang}")))
        self.client = dashboard.app.test_client()
        record = dashboard.load_record(self.case)
        with closing(dashboard.connect()) as db, db:
            for lang in ("de", "fr"):
                db.execute("INSERT INTO results VALUES (?, ?, ?, ?, NULL, NULL)",
                           (self.case, lang, dashboard.version(record, "test-model"),
                            json.dumps({"summary": f"Draft {lang}", "quotes": [self.evidence]})))

    def enter(self, manager):
        result = manager.__enter__()
        self.addCleanup(manager.__exit__, None, None, None)
        return result

    def approve(self, lang, value):
        selected = self.client.get("/api/dashboard", query_string={"case": self.case}).json["selected"]
        response = self.client.post("/approve", data={"case": self.case, "version": selected[lang]["version"],
                                                      "lang": lang, "summary": value,
                                                      "previous_approved": selected[lang]["approved"] or ""})
        self.assertEqual(response.status_code, 303)

    def prepared(self, lang):
        return dashboard.prepare_briefing(self.case, lang)

    def play(self, key, lang, **kwargs):
        return self.client.get(f"/briefing/{key}.mp3",
                               query_string={"case": self.case, "lang": lang}, **kwargs)

    def test_approval_and_invalid_passage_gate(self):
        with self.assertRaisesRegex(ValueError, "Approve"):
            self.prepared("de")
        self.approve("de", "Approved DE")
        with self.assertRaisesRegex(ValueError, "Approve"):
            self.prepared("fr")
        _, args = self.prepared("de")
        with patch("briefing.ask", return_value={"script": "Wort " * 120,
                                                  "evidence_ids": [999]}), patch("briefing.narrate") as speech:
            self.assertFalse(briefing.generate(*args))
            speech.assert_not_called()
        result = self.client.get("/api/dashboard", query_string={"case": self.case})
        self.assertTrue(result.json["audio"]["error"])
        self.assertFalse(result.json["audio"]["ready"])

    def test_independent_languages_retry_cache_and_invalidation(self):
        self.approve("de", "Approved DE")
        self.approve("fr", "Approved FR")
        de_key, de = self.prepared("de")
        fr_key, fr = self.prepared("fr")
        self.assertNotEqual(de_key, fr_key)
        script = "Wort " * 119 + "Ende"
        with patch("briefing.ask", return_value={"script": script, "evidence_ids": [1]}) as llm, \
                patch("briefing.narrate", side_effect=[
                    HTTPError("https://api.elevenlabs.io", 429, "Busy", {}, None),
                    b"mp3de", b"mp3fr"]) as speech:
            self.assertFalse(briefing.generate(*de))
            self.assertTrue(briefing.generate(*de))
            self.assertTrue(briefing.generate(*fr))
            self.assertTrue(briefing.generate(*de))
            self.assertEqual([c.args[2]["approved_summary"] for c in llm.call_args_list],
                             ["Approved DE", "Approved FR"])
            self.assertEqual([c.args[2] for c in speech.call_args_list],
                             ["voice-de", "voice-de", "voice-fr"])
        self.assertEqual(self.play(de_key, "de").data, b"mp3de")
        self.assertEqual(self.play(fr_key, "fr", headers={"Range": "bytes=0-2"}).status_code, 206)
        result = self.client.get("/api/dashboard", query_string={"case": self.case})
        self.assertEqual(result.json["audio"]["script"], script)
        self.assertTrue(result.json["audio"]["excerpts"])
        self.assertNotIn(b"secret", result.data)
        self.approve("de", "Edited DE")
        self.assertEqual(self.play(de_key, "de").status_code, 404)
        self.assertEqual(self.play(fr_key, "fr").status_code, 200)

    def test_concurrent_generation_uses_one_provider_call(self):
        self.approve("de", "Approved")
        _, args = self.prepared("de")
        with patch("briefing.write_script", return_value=("script", [self.evidence])) as llm, \
                patch("briefing.narrate", return_value=b"mp3") as speech:
            with ThreadPoolExecutor(2) as pool:
                self.assertEqual(list(pool.map(lambda _: briefing.generate(*args), range(2))),
                                 [True, True])
            llm.assert_called_once()
            speech.assert_called_once_with("script", "secret", "voice-de")

    def test_elevenlabs_wire_request_only_sends_narration(self):
        response = BytesIO(b"mp3")
        response.headers = Message()
        response.headers["Content-Type"] = "audio/mpeg"
        with patch("briefing.urlopen", return_value=response) as outbound:
            self.assertEqual(briefing.narrate("Narration", "secret", "voice-de"), b"mp3")
        request = outbound.call_args.args[0]
        self.assertIn("/v1/text-to-speech/voice-de?", request.full_url)
        self.assertEqual(json.loads(request.data),
                         {"text": "Narration", "model_id": "eleven_multilingual_v2"})
