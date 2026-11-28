from datetime import date
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from urllib.error import HTTPError

import app as dashboard
import conversation as convo
from conftest import seed_kb


class ConversationTests(TestCase):
    docket = "8C_354/2026"
    source = "The court dismissed the appeal because the statutory requirements were not met."

    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.case = seed_kb(Path(tmp.name) / "kb.sqlite", self.docket, self.source, score=95, band="high")
        config = patch.dict(dashboard.app.config, DATABASE=Path(tmp.name) / "review.sqlite",
                            KB=Path(tmp.name) / "kb.sqlite", TODAY=date(2026, 11, 27))
        config.start()
        self.addCleanup(config.stop)
        self.client = dashboard.app.test_client()
        for name, value in [("conversation.model_config", {"LLM_NAME": "model"}),
                            ("conversation.voice_config", ("server-key", "voice"))]:
            mock = patch(name, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)

    def post(self, action, lang="de", **values):
        payload = {"case": self.case, "lang": lang, **values}
        return self.client.post(f"/conversation/{action}", json=payload)

    def upload(self, audio, mime="audio/webm", lang="de"):
        return self.client.post("/conversation/transcribe", data={
            "case": self.case, "lang": lang, "audio": (BytesIO(audio), "clip", mime)})

    def test_transcription_request_and_languages(self):
        for mime in ("audio/webm", "audio/mp4", "audio/ogg", "audio/wav", "audio/mpeg"):
            with self.subTest(mime=mime), patch("conversation.urlopen", return_value=BytesIO(b'{"text":" Question " }')) as send:
                response = self.upload(b"recording", mime, "fr")
                self.assertEqual(response.json, {"transcript": "Question"})
                sent = send.call_args.args[0]
                self.assertEqual(sent.full_url, "https://api.elevenlabs.io/v1/speech-to-text")
                for marker in (b"scribe_v2", b"\r\nfr\r\n", b"recording", mime.encode()):
                    self.assertIn(marker, sent.data)
                self.assertNotIn(b"server-key", response.data)

    def test_transcription_validation_and_errors(self):
        with patch("conversation.urlopen") as send:
            self.assertEqual(self.upload(b"x", "image/png").status_code, 415)
            self.assertEqual(self.upload(b"").status_code, 400)
            self.assertEqual(self.upload(b"x" * (convo.MAX_UPLOAD + 1)).status_code, 413)
            with patch("conversation.voice_config", return_value=(None, None)):
                self.assertEqual(self.upload(b"x").status_code, 503)
            send.assert_not_called()
            for bad in (b'{"text":""}', b'[]', b'not-json'):
                send.return_value = BytesIO(bad)
                self.assertEqual(self.upload(b"x").status_code, 502)
            send.side_effect = TimeoutError()
            self.assertEqual(self.upload(b"x").status_code, 502)
            send.side_effect = HTTPError("https://api.elevenlabs.io", 401, "Denied", {}, None)
            self.assertIn("Speech to Text", self.upload(b"x").json["error"])

    def test_answer_grounding_history_and_score(self):
        old = [{"question": f"Question {i}", "answer": "Answer", "evidence_ids": [1]} for i in range(8)]
        with patch("conversation.ask", return_value={"kind": "court", "answer": "Dismissed", "evidence_ids": [1]}) as model:
            for lang, prompt in (("de", "German"), ("fr", "French")):
                response = self.post("ask", lang, question="What happened?", history=old)
                self.assertEqual(response.json["citations"], [{"id": "1", "text": self.source}])
                self.assertIn(prompt, model.call_args.args[1])
                sent = model.call_args.args[2]
                self.assertEqual(len(sent["history"]), 6)
                self.assertEqual(sent["history"][0]["question"], "Question 2")
            model.return_value = {"kind": "score", "answer": "Statutory insurance.", "evidence_ids": []}
            self.assertIn("App-Bewertung (95/100)", self.post("ask", question="Why 95?").json["answer"])
            for invalid in ([], [999], ["9C_10/2026:1"], [True]):
                model.return_value = {"kind": "court", "answer": "Invented", "evidence_ids": invalid}
                self.assertEqual(self.post("ask", question="What?").json["kind"], "uncertain")
            model.return_value = {"kind": "uncertain", "answer": "Insufficient evidence", "evidence_ids": [1]}
            self.assertEqual(self.post("ask", question="Other case?").json["citations"], [])
            model.side_effect = ValueError("malformed JSON")
            self.assertEqual(self.post("ask", question="What?").status_code, 502)

    def test_recap_only_uses_discussed_evidence(self):
        history = [{"question": "What?", "answer": "Dismissed", "evidence_ids": [1]}]
        with patch("conversation.ask") as model:
            self.assertEqual(self.post("recap", history=[]).status_code, 400)
            model.assert_not_called()
            model.return_value = {"points": ["Outcome"], "takeaways": ["Appeal dismissed"],
                                  "open_questions": ["Does the ruling address costs?"], "evidence_ids": [1]}
            for lang, prompt in (("de", "German"), ("fr", "French")):
                self.assertEqual(self.post("recap", lang, history=history).json["citations"][0]["text"], self.source)
                self.assertIn(prompt, model.call_args.args[1])
            model.return_value["evidence_ids"] = [999]
            self.assertEqual(self.post("recap", history=history).status_code, 502)
            model.return_value["evidence_ids"] = []
            self.assertEqual(self.post("recap", history=history).status_code, 502)

    def test_narration_voice_failure_retry_and_payload(self):
        with patch("conversation.voice_config", side_effect=lambda lang: ("server-key", f"voice-{lang}")), patch(
            "conversation.narrate", side_effect=[HTTPError("https://api.elevenlabs.io", 429, "Busy", {}, None),
                                                  b"mp3", b"mp3"]) as speech:
            self.assertEqual(self.post("speak", "fr", answer="Résumé").status_code, 502)
            response = self.post("speak", "fr", answer="Résumé")
            self.assertEqual(response.mimetype, "audio/mpeg")
            self.assertEqual(response.data, b"mp3")
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            speech.assert_called_with("Résumé", "server-key", "voice-fr")
            self.assertEqual(self.post("speak", answer="Text").status_code, 200)
            speech.assert_called_with("Text", "server-key", "voice-de")

    def test_route_boundaries_and_dashboard_regression(self):
        self.assertEqual(self.post("ask", question="x" * 1001).status_code, 400)
        self.assertEqual(self.post("ask", question="valid", history="invalid").status_code, 400)
        self.assertEqual(self.post("ask", "it", question="valid").status_code, 400)
        self.assertEqual(self.post("ask", case="unknown", question="valid").status_code, 404)
        self.assertEqual(self.post("speak", answer="").status_code, 400)
        self.assertEqual(self.client.post("/conversation/ask", json={}, headers={
            "Origin": "https://external.example"}).status_code, 403)
        empty = self.client.get("/api/dashboard", query_string={"q": "missing"}).json
        self.assertIsNone(empty["selected"])
        self.assertEqual(empty["rows"], [])
        with patch("jobs.current_config", return_value={"LLM_NAME": "model"}):
            response = self.client.get("/api/dashboard", query_string={"case": self.case, "q": self.docket})
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        page = response.json
        self.assertEqual(page["selected"]["key"], self.case)
        self.assertEqual(page["selected"]["docket"], self.docket)
        self.assertEqual(page["selected"]["score"], 95)
        self.assertTrue(page["selected"]["score_current"])
        self.assertEqual(page["rows"][0]["status"], "Relevant")
        self.assertEqual(page["window"], ["2026-08-27", "2026-11-27"])
        self.assertNotIn("full_text", page["rows"][0])
