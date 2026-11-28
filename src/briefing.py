"""Approved-summary narration and local audio caching."""

from contextlib import closing
import fcntl
import hashlib
import json
import logging
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import review_store
from bger import local_config
from summaries import ask, passages, resolve_passages

PROMPT_VERSION = "briefing-2"
VOICE_MODEL = "eleven_multilingual_v2"
LANGUAGES = {"de": "Swiss Standard German", "fr": "Swiss French"}
MAX_AUDIO = 4 * 1024 * 1024
log = logging.getLogger(__name__)


def voice_config(lang):
    name = f"ELEVENLABS_VOICE_ID_{lang.upper()}"
    config = local_config("ELEVENLABS_API_KEY", name)
    voice = config[name] or ""
    if re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", voice):
        return config["ELEVENLABS_API_KEY"], voice
    return config["ELEVENLABS_API_KEY"], None


def context(record, summary, model, version, lang, voice):
    if not summary or not summary["approved"] or not voice:
        return None
    approved = summary["approved"]
    inputs = (record["key"], lang, version, model, PROMPT_VERSION,
              VOICE_MODEL, voice, hashlib.sha256(approved.encode()).hexdigest())
    key = hashlib.sha256(json.dumps(inputs).encode()).hexdigest()
    return key, approved


def write_script(config, record, approved, lang):
    source = passages(record["full_text"])
    result = ask(config, f"""Write a spoken, 110–140 word briefing in {LANGUAGES[lang]}.
Use natural short sentences. Cover what the court decided, the concrete
relevance to Suva (or say the implications are limited), and the source
passage to consult. Use the approved summary as the scope. Ground all claims
in the original judgment. Expand legal acronyms for listening. Output exactly
one JSON object with two keys: "script" (a single narration string in the
requested language) and "evidence_ids" (an array of one or two line numbers
from judgment_lines). Keep the JSON keys in English. Do not copy the input
object or add an explanation outside the JSON.""",
                 {"approved_summary": approved, "judgment_lines": source}, 750)
    script = result.get("script")
    ids = result.get("evidence_ids")
    if not isinstance(script, str) or not 40 <= len(script.split()) <= 175:
        raise ValueError("Apertus did not return a short narration script")
    if not isinstance(ids, list) or not ids:
        raise ValueError("Apertus did not identify a supporting passage")
    excerpts = list(dict.fromkeys(resolve_passages(source, ids)))[:2]
    if not excerpts:
        raise ValueError("Apertus returned an invalid supporting passage")
    return script.strip(), excerpts


def narrate(script, key, voice):
    request = Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=mp3_44100_128",
        data=json.dumps({"text": script, "model_id": VOICE_MODEL}).encode(),
        headers={"xi-api-key": key, "Content-Type": "application/json", "Accept": "audio/mpeg"},
    )
    with urlopen(request, timeout=60) as response:
        audio = response.read(MAX_AUDIO + 1)
        if response.headers.get_content_type() != "audio/mpeg" or not 0 < len(audio) <= MAX_AUDIO:
            raise ValueError("ElevenLabs did not return a valid MP3 response")
    return audio


def generate(connect, database, key, record, approved, lang, config, api_key, voice):
    """Lock only this cache entry, including across the CLI and web processes."""
    lock_dir = Path(database).parent / "briefing-locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    with (lock_dir / key).open("a") as lock, closing(connect()) as db:
        fcntl.flock(lock, fcntl.LOCK_EX)
        row = review_store.get_briefing(db, key)
        if row and row["mp3"]:
            return True
        script = row["script"] if row else None
        stage = "Apertus"
        try:
            if not script:
                script, excerpts = write_script(config, record, approved, lang)
                review_store.save_briefing_script(db, key, script, json.dumps(excerpts, ensure_ascii=False))
            stage = "ElevenLabs"
            audio = narrate(script, api_key, voice)
            review_store.save_briefing_audio(db, key, audio)
            log.info("briefing_ready case=%s lang=%s cache_key=%s bytes=%d", record["key"], lang, key[:12], len(audio))
            return True
        except (OSError, ValueError, TypeError, KeyError) as exc:
            error = (f"{stage} returned HTTP {exc.code}" if isinstance(exc, HTTPError)
                     else f"{stage} could not generate the briefing")
            log.warning("briefing_failed case=%s lang=%s cache_key=%s stage=%s error=%s http=%s", record["key"],
                        lang, key[:12], stage, type(exc).__name__, getattr(exc, "code", None))
            review_store.save_briefing_error(db, key, error)
            return False
