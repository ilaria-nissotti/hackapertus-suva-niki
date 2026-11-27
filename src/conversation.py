"""Case-specific question answering with optional speech input and output."""

import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from flask import Blueprint, Response, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from bger import model_config
from briefing import LANGUAGES, narrate, voice_config
from summaries import ask, passages

MAX_UPLOAD = 3 * 1024 * 1024
FORMATS = {"audio/webm": "webm", "video/webm": "webm", "audio/mp4": "mp4",
           "video/mp4": "mp4", "audio/ogg": "ogg", "audio/wav": "wav", "audio/mpeg": "mp3"}
MAX_QUESTION = 1000
MAX_ANSWER = 1800
MAX_HISTORY = 6
SCORE_QUESTION = re.compile(r"\b(?:score|bewertung|punktzahl|einstufung|classement|note)\b|/100", re.I)
# A number right after an article, paragraph or consideration marker ("Art. 6", "E. 6") is a legal reference.
LEGAL_REFERENCE = re.compile(r"(?:§|\b(?:art|abs|al|ziff|lit|let|e|consid|bge|atf|dtf|sr|rs|nr|no)\b\.?)"
                             r"\s*(?:\d+\w*\s*(?:,|und|et|bis|-)\s*)*$", re.I)


def bounded(value, maximum):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise ValueError(f"Text must contain 1–{maximum} characters.")
    return value.strip()


def evidence_lines(text):
    """Keep original line IDs while excluding empty numbering fragments."""
    return {key: line for key, line in passages(text).items() if len(line.strip()) >= 12}


def citations(source, ids):
    if not isinstance(ids, list) or len(ids) > 3:
        raise ValueError("Invalid passage references.")
    result = {}
    for value in ids:
        if type(value) not in (str, int) or str(value) not in source:
            raise ValueError("Passage reference is outside this judgment.")
        key = str(value)
        result[key] = source[key].strip()
    return [{"id": key, "text": text} for key, text in result.items()]


def history_context(history, source):
    if not isinstance(history, list):
        raise ValueError("Invalid conversation history.")
    result = []
    for item in history[-MAX_HISTORY:]:
        if not isinstance(item, dict):
            raise ValueError("Invalid conversation turn.")
        result.append({"question": bounded(item.get("question"), MAX_QUESTION),
                       "answer": bounded(item.get("answer"), MAX_ANSWER),
                       "citations": citations(source, item.get("evidence_ids", []))})
    return result


def transcribe(audio, media_type, lang, key):
    boundary = uuid4().hex
    chunks = []
    for field, value in (("model_id", "scribe_v2"), ("language_code", lang)):
        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"\r\n\r\n{value}\r\n'.encode())
    chunks.extend([
        (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
         f'filename="question.{FORMATS[media_type]}"\r\nContent-Type: {media_type}\r\n\r\n').encode(),
        audio, f"\r\n--{boundary}--\r\n".encode(),
    ])
    upstream = Request("https://api.elevenlabs.io/v1/speech-to-text", data=b"".join(chunks),
                       headers={"xi-api-key": key, "Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urlopen(upstream, timeout=45) as response:
        data = json.load(response)
    if not isinstance(data, dict):
        raise ValueError("Invalid transcription.")
    return bounded(data.get("text"), MAX_QUESTION)


def app_score(record):
    """The rules score the app shows, as the model may explain it (None if missing or stale)."""
    if record.get("score") is None or not record.get("score_current"):
        return None
    return {"score": round(record["score"]), "band": record["band"],
            "reasons": [f"{f['points']:+g} {f['explanation']}" for f in record["fired"]]}


def discuss(config, record, lang, question, history, rating):
    source = evidence_lines(record["full_text"])
    score_question = bool(rating and SCORE_QUESTION.search(question))
    if rating and any(not LEGAL_REFERENCE.search(question[:m.start()])
                      for m in re.finditer(rf"\b{rating['score']}\b", question)):
        score_question = True
    result = ask(config, f"""Answer in {LANGUAGES[lang]} using the current court ruling.
Return exactly {{"kind":"court","answer":"…","evidence_ids":[]}} with English
keys and the answer in {LANGUAGES[lang]}. Use kind "court", "score", or "uncertain".
List at most three line numbers, strongest first. Cite the dispositive or
reasoning rather than headings or party arguments.
"court" requires source lines that directly support the answer.
"score" explains the app's rules-based score in app_score (the reasons are the
rules that fired) and clearly attributes it to the app. For a court finding in a score explanation cite the ruling too.
When the source does not establish the answer, use "uncertain". No legal advice,
invented findings, cross-case claims, or actions. History is context only.""",
                 {"question": question, "judgment_lines": source, "history": history,
                  "app_score": rating if score_question else None}, 550)
    kind = result.get("kind")
    try:
        if kind not in ("court", "score", "uncertain") or (kind == "score" and not score_question):
            raise ValueError("Unexpected answer classification.")
        answer = bounded(result.get("answer"), MAX_ANSWER)
        ids = result.get("evidence_ids")
        evidence = citations(source, ids[:3] if isinstance(ids, list) else ids)
        if kind == "court" and not evidence:
            raise ValueError("Court answer without evidence.")
        if kind == "uncertain":
            evidence = []
        if kind == "score":
            label = "App-Bewertung" if lang == "de" else "Score de l’application"
            answer = bounded(f"{label} ({rating['score']}/100). {answer}", MAX_ANSWER)
    except (ValueError, KeyError):
        answer = ("Das lässt sich anhand dieses Urteils nicht verlässlich beantworten."
                  if lang == "de" else "Ce jugement ne permet pas de répondre de manière fiable.")
        kind, evidence = "uncertain", []
    return {"answer": answer, "kind": kind, "citations": evidence}


def recap(config, record, lang, history):
    allowed = {c["id"]: c["text"] for turn in history for c in turn["citations"]}
    result = ask(config, f"""Summarize the conversation in {LANGUAGES[lang]}.
Return exactly {{"points":[],"takeaways":[],"open_questions":[],"evidence_ids":[]}}.
Write the first three arrays in {LANGUAGES[lang]}, up to four short strings
per array. Copy at most three IDs from allowed_evidence_ids. If there is no
source-backed takeaway, return empty takeaways and evidence_ids. State only
what was discussed. Treat unresolved
questions as open. Include court takeaways only when those passages support them.""",
                 {"case": record["docket"], "turns": history, "allowed_evidence_ids": list(allowed)}, 550)
    output = {}
    for field in ("points", "takeaways", "open_questions"):
        items = result.get(field)
        if not isinstance(items, list) or len(items) > 4:
            raise ValueError("Invalid conversation summary.")
        output[field] = [bounded(item, 400) for item in items]
    if not output["points"]:
        raise ValueError("Conversation summary is empty.")
    output["citations"] = citations(allowed, result.get("evidence_ids")) if output["takeaways"] else []
    if output["takeaways"] and not output["citations"]:
        raise ValueError("Conversation takeaways need source evidence.")
    return output


def routes(load_record):
    """load_record(key) returns the app's record for a judgement key, or None."""
    blueprint = Blueprint("conversation", __name__, url_prefix="/conversation")

    @blueprint.errorhandler(RequestEntityTooLarge)
    def too_large(_error):
        return jsonify(error="The recording exceeds 3 MB."), 413

    @blueprint.after_request
    def no_cache(response):
        response.headers["Cache-Control"] = "no-store"
        return response

    def dispatch(action):
        form = request.form if action == "transcribe" else request.get_json(silent=True)
        if not hasattr(form, "get"):
            return jsonify(error="Invalid request."), 400
        key, lang = form.get("case"), form.get("lang")
        if not isinstance(key, str) or not isinstance(lang, str) or lang not in LANGUAGES:
            return jsonify(error="Select a case and language."), 400
        record = load_record(key)
        if record is None:
            return jsonify(error="Judgment not found."), 404
        try:
            if action == "transcribe":
                upload = request.files.get("audio")
                media_type = upload.mimetype if upload else None
                if media_type not in FORMATS:
                    return jsonify(error="Use a supported audio recording or type your question."), 415
                audio = upload.stream.read(MAX_UPLOAD + 1)
                if len(audio) > MAX_UPLOAD:
                    return too_large(None)
                if not audio:
                    raise ValueError("The recording is empty.")
            elif action == "speak":
                answer = bounded(form.get("answer"), MAX_ANSWER)
            else:
                history = history_context(form.get("history", []), evidence_lines(record["full_text"]))
                if action == "ask":
                    question = bounded(form.get("question"), MAX_QUESTION)
                elif not history:
                    raise ValueError("Ask a question before generating a summary.")
        except ValueError as exc:
            return jsonify(error=str(exc)), 400

        provider = "ElevenLabs" if action in ("transcribe", "speak") else "Apertus"
        try:
            if provider == "ElevenLabs":
                key, voice = voice_config(lang)
                if not key or (action == "speak" and not voice):
                    return jsonify(error="Configure ElevenLabs for this feature."), 503
                if action == "transcribe":
                    return jsonify(transcript=transcribe(audio, media_type, lang, key))
                return Response(narrate(answer, key, voice), mimetype="audio/mpeg")
            config = model_config()
            if action == "recap":
                return jsonify(recap(config, record, lang, history))
            return jsonify(discuss(config, record, lang, question, history, app_score(record)))
        except HTTPError as exc:
            if action == "transcribe" and exc.code in (401, 403):
                return jsonify(error="ElevenLabs denied transcription. Check Speech to Text access on the API key."), 502
            return jsonify(error=f"{provider} returned HTTP {exc.code}. Try again."), 502
        except (TimeoutError, URLError, OSError, ValueError, TypeError, KeyError):
            message = ("Could not transcribe that recording. Try again or type your question."
                       if action == "transcribe" else f"{provider} could not complete this request. Try again.")
            return jsonify(error=message), 502

    for action in ("transcribe", "ask", "speak", "recap"):
        blueprint.add_url_rule(f"/{action}", action, lambda action=action: dispatch(action),
                               methods=["POST"])
    return blueprint
