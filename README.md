# Hack Apertus Zurich — Challenge Partner: Suva

## 🎯 The Challenge

### Identification of relevant Federal Court judgements and preparation of a legal summary

`Add your team name`
<br>
<br>

Template repository for [Hack Apertus](https://hackapertus.ch/) submissions.
Every project keeps the same layout, so organizers and judges find the same
things in the same place.

Submissions must use the v1.5 Apertus model family.
The submission must comply with the [HackApertus T&C](https://hackapertus.ch/terms-and-conditions) and the [model and challenge requirements](https://hackapertus.notion.site/zurich-challenges-model-requirements)


## 🔧 Resources & Tools

Check our resources & tools page for more information:
[Resources & Tools](https://hackapertus.notion.site/zurich-resources-tools)

| Models           | URL                                      |
|------------------|------------------------------------------|
| Apertus v1.5 8B  | [huggingface.co/swiss-ai/Apertus-v1.5-8B](https://huggingface.co/swiss-ai/Apertus-v1.5-8B)  |
| Apertus v1.5 70B | [huggingface.co/swiss-ai/Apertus-v1.5-70B](https://huggingface.co/swiss-ai/Apertus-v1.5-70B) |

## The structure

Keep the files and directories as shown below — don't rename or move them.

| Path | What it is |
| --- | --- |
| `README.md` | The challenge description and submission requirements |
| `technical_report.md` | The deeper write-up: architecture, evaluation, limitations |
| `challenge_description.md` | Complete Challenge description incl. deliverables 
| `LICENSE` | Apache-2.0 |
| `src/` | Your code |

## Environment

- Include instructions for running the project in your technical report.
- Use the following environment variables:
```
LLM_NAME — name and version of the model
LLM_BASE_URL — endpoint base URL
LLM_API_KEY — your API key
```

## 📦 Submission Requirements & Deliverables

The submission must:
1. follow the template repo and include all prerequisite files and definitions
2. run end-to-end when judges try to run it

### Git repo (URL)
- Create your repo from this template (**Use this template**).
- Keep the layout as it is: don't rename or move its files.
- Set the repo to PUBLIC (Settings --> Collaborators --> Manage Visibility)
- Submit the URL of YOUR Git repo (keep it to 'public').

### Technical Report (pdf)
- Update [technical_report.md](technical_report.md) in this repository with all the details for your submission.
- Upload a pdf of your technical report to the root of the repo, named as `TeamName_Report.pdf`.
- Format: pdf, max. 6 pages w/o references
- Submit the pdf of the technical report.

### Demo video (URL)
- Max. 2 min demo video of your prototype
- Add the link to the **Demo** field at the top of [technical_report.md](technical_report.md)
- Submit the URL of the video.

### Pitch deck (pdf)
- Max. 10 slides

## Getting started

The pipeline collects judgments into a local knowledge base, parses them and
scores each one with expert-editable rules. A local review app (React,
TypeScript, Vite, Flask) shows the ranked queue with the reasons for each score
and drafts German and French summaries with Apertus via Keera. Building the
frontend requires Node.js 20.19+ or 22.12+.

Copy `.env.example` to the ignored `.env` at the repo root and fill it in.
`LLM_BASE_URL`, `LLM_NAME` and `LLM_API_KEY` (Apertus) are required. The
ElevenLabs settings are optional and enable audio and voice questions.
`DATA_DIR`, `DATABASE_URL`, `LLM_CACHE_MODE`, `LLM_TIMEOUT`,
`LLM_MAX_RETRIES` and `LOG_LEVEL` have working defaults. Shell variables take
precedence over `.env`.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e . pytest
cp .env.example .env                                # then fill in the values
.venv/bin/python -m suva llm-ping                   # checks the Apertus endpoint (uncached)
.venv/bin/python -m suva init-db
.venv/bin/python -m suva import-supplied            # Suva's summaries and picks
.venv/bin/python -m suva split-examples --manifest data/split-manifest.json
.venv/bin/python -m suva fetch --window challenge   # judgments published in the last 3 months
.venv/bin/python -m suva parse
.venv/bin/python -m suva score                      # rules score + explanation per judgment
npm --prefix src/frontend ci
npm --prefix src/frontend run build
.venv/bin/python src/app.py serve --strict          # refuses to start without LLM config, frontend build or KB
```

On startup the app logs its configuration and one line per missing setting to
stderr (never keys). Without `--strict` it starts anyway and shows the problem
on the drafts.

Open [the local review dashboard](http://127.0.0.1:5050). It first asks who is
reviewing: name, initials and, optionally, function (no account, no password;
the browser remembers them). Every summary approved and every assessment saved
is signed with that name, as in Suva's exports ("Autor: Frei Thomas (FRT),
Rechtsanwalt"), and "Change" in the menu switches reviewer. The session cookie
lasts 12 hours and is signed with `SUVA_SECRET_KEY` or, if unset, a random key
kept in `data/session-secret`.

The queue lists the
judgments of the window, sorted by score, each with the rules that fired
("why it was selected" and "what counts against it"). Edit `rules/rules.yaml` and
run `suva score` again to change the ranking; the app flags scores that are out
of date. `--today YYYY-MM-DD` (on `fetch`, `score` and `app.py`) pins the window.

### Feedback loop: retraining the rule weights

Open a case and answer **Your assessment** (Relevant / Not relevant). After 30
new verdicts the queue suggests an update; **Rule weights** can calculate one at
any time. It refits the weights on Suva's spreadsheet picks plus your verdicts
(about a minute, in the background) and shows, before anything changes: how many
verdicts the current and new weights agree with (the new weights are tested on
verdicts they were not trained on), the share of Suva's past picks in the reading
list, which judgements would join or leave the shortlist, and every rule's old and
new points. **Accept** writes the numbers into `rules/rules.yaml` (comments kept)
and rescores the queue; **Discard** keeps the current weights. It needs
`data/scoring/dataset.jsonl` (`python -m suva dataset`; built on first use if missing).

```sh
.venv/bin/python src/app.py summarize-top --limit 10   # DE/FR drafts for the 10 best-scored
```

Drafts use the judgment, a shared factual brief, and same-language Suva
examples from the frozen few-shot split. Successful results are reused for the
same source text, inference endpoint, model, and prompt version. Review and
approve each language in the dashboard. If the draft changes while you are
editing, reload it before approving. After upgrading an existing review
database to this version, rerun `suva score` and generate fresh drafts.

Use **Add judgment** to select a case from the knowledge base or upload usable
judgment text with its decision date. To queue a case that already has source
text, select it from the knowledge base. See `src/suva/scoring/README.md` for
ranking evaluation and rule tuning.

See [Tests and smoke test](#tests-and-smoke-test) below.

The observed publication dates are shown in the UI. They reflect the sources'
coverage; the window may contain more cases. The app binds to loopback and
sends a judgment to your configured inference endpoint only when you request
summaries, a briefing or an answer.

### Audio briefings

Set `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID_DE`, and
`ELEVENLABS_VOICE_ID_FR` in the local `.env`. Choose and audition a voice
for each language. Approve the language's summary in the dashboard,
then select **Generate briefing**. Apertus writes a short script and
ElevenLabs receives only that script for narration. Listen in the
dashboard or open the transcript and supporting passage.

For the demo, approve both summaries for a case, then pre-generate audio with
its key (docket@decision date, shown in the dashboard URL):

```sh
.venv/bin/python src/app.py briefing --case "8C_92/2026@2026-10-13" --lang both
```

Replaying cached audio does not call either provider. Editing the approved
summary invalidates its recording.

### Ask this judgment

Open a case, select **Ask this judgment**, and choose Deutsch or Français.
Select **Start case call**, speak, then select **Send question**. ElevenLabs
transcribes the recording using Scribe v2. Apertus answers with source
passages, and ElevenLabs reads the answer aloud. Select **Ask follow-up**
for the next question. The **Conversation summary** view asks Apertus to
recap the discussion. The six most recent turns per case and language stay in
the page's memory until reload or close.

To add a portrait, export a PNG or JPG of your avatar and save it as
`.suva-work/avatar.png` or `.suva-work/avatar.jpg`. Reload the page to see it.
The portrait's ring animates while the answer plays.

Voice questions require **Speech to Text** on the ElevenLabs key. Spoken
answers require **Text to Speech** and the existing language voice IDs. The
app does not save microphone recordings. For the demo use public judgments
and avoid including nonpublic information in questions.

### Tests and smoke test

All tests are offline and use test doubles for HTTP, Apertus and ElevenLabs.

```sh
.venv/bin/python -m pytest -q                                   # Python: pipeline, app, stress and logging tests
node --test tests/conversation-ui.test.cjs                      # simulated browser: voice conversation
.venv/bin/python -m pytest -q tests/test_review_pipeline.py     # one judgment from scraper to approved drafts
.venv/bin/python scripts/smoke.py                               # end to end against a real server, offline
```

`scripts/smoke.py` builds a scratch knowledge base, starts `src/app.py serve
--strict` and checks every step and four deliberate failures. `--live` uses the
real Apertus and ElevenLabs settings and spends quota. See
[SMOKE_TEST.md](SMOKE_TEST.md) for the production-like start and the manual
checks.

1. Click **Use this template** to create your own repository.

## License

All Hack Apertus projects are open-sourced. Please check our Terms & Conditions for specific licensing details (6. What you build is open source): https://hackapertus.ch/terms-and-conditions

## Support

### FAQ
💡 https://hackapertus.ch/faq

### Contact

💬 In case you have questions, join the conversation on Discord. Alternatively, send an email to “hello@hackapertus.ch”.
