# Suva Judgment Review

This local Hack Apertus prototype helps legal reviewers shortlist Swiss Federal
Supreme Court decisions and draft German and French summaries. Run commands from
the repository root. Preserve existing working-tree changes.

- Set up with `python3 -m venv .venv` and `.venv/bin/python -m pip install -r requirements.txt`.
- The Flask entry point is `src/app.py`. Run `.venv/bin/python src/app.py serve`
  and open http://127.0.0.1:5050. Use `sync` for OpenCaseLaw, `import <file.jsonl>`
  for supplied data, `score` for triage, and `summarize-june` for the shortlist.
- Keep `.env` and `.suva-work/` out of Git. Never print credentials or put them
  in templates. Use Keera's `apertus-v1-5-8b` via the existing `LLM_*` settings.
- The Q2 target is **publication** from April 1 to June 30, 2026. Store decision
  date separately. The verified API listing originally returned publication dates
  from May 19 onward. Surface the observed coverage rather than claiming a
  complete quarter. The workbook and HTML files in `judgements/` are historical
  reference material, including positive selections and style examples.
- `src/triage.py` defines the evidence rubric. Use 45 as the shortlist cutoff and
  70 for the Relevant label. High scores need exact source passages and a
  verification pass. Test with `.venv/bin/python src/evaluate.py --positive-limit 3`.
- Draft German and French independently from the ruling and shared factual
  brief, with examples in the target language. Keep review and approval explicit.
- Audio requires a separate approved summary per language. Apertus authors the
  script and cites original lines. Send only the script to ElevenLabs. Store and
  serve audio locally; invalidate it when the approval, source, or generation
  settings change. See the README for configuration and the `briefing` command.
- Run local regression tests with `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests`.
- Case conversation uses Apertus for answers and recap, with original ruling
  citations. ElevenLabs handles transcription and narration. It is separate
  from approved summaries. Run `node --test tests/conversation-ui.test.cjs`
  for the simulated browser/media flows and keep recorded questions out of logs and storage.
