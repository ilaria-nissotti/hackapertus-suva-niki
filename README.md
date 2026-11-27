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

The local judgment-review app uses React, TypeScript, Vite, Flask, SQLite, and Apertus via Keera. Building the frontend requires Node.js 20.19+ or 22.12+ on a supported release line. Flask serves the build on the same origin.
Set `LLM_BASE_URL=https://gateway.keera.ch/api/v1`,
`LLM_NAME=apertus-v1-5-8b` and your `LLM_API_KEY` in the ignored `.env` file
at the repo root.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm --prefix src/frontend ci
npm --prefix src/frontend run build
.venv/bin/python src/bger.py check-apertus
.venv/bin/python src/app.py sync
.venv/bin/python src/app.py serve
```

Rebuild the frontend after changing its source. Open [the local review dashboard](http://127.0.0.1:5050). You can score and
summarize individual judgments there. For offline or replacement data,
run `.venv/bin/python src/app.py import judgments.jsonl`. Each line needs
`docket_number` (or `docket`) and `full_text`; optional metadata includes
`title`, `source_url`, `decision_date`, `publication_date` and `date_source`.
The database lives in ignored `.suva-work/`. Sync resumes at missing cases.

```sh
.venv/bin/python src/evaluate.py --positive-limit 3
.venv/bin/python src/app.py score
.venv/bin/python src/app.py summarize-june
```

Run evaluation without the limit for the full historical benchmark. Run batch
jobs one at a time. Successful results are reused by source text, model and
prompt version. Failed items can be rerun. The score is an ordinal ranking
mapped to fixed points on a 0–100 scale. High scores require evidence from the
judgment and an independent model check. The benchmark selected 45 as the
shortlist threshold to retain Review cases. A score of 70 or above is needed
for the Relevant label. The two summary calls each use their
own language examples and a shared factual brief. Approved edits can be used
as style examples for future drafts.

The mirror's observed dates are shown in the UI. They reflect the imported
dataset's coverage; the April–June challenge window may contain more cases.
The app binds to loopback and sends the selected judgment to your configured
inference endpoint when triage or summaries are requested.

### Audio briefings

Set `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID_DE`, and
`ELEVENLABS_VOICE_ID_FR` in the local `.env`. Choose and audition a voice
for each language. Approve the language's summary in the dashboard,
then select **Generate briefing**. Apertus writes a short script and
ElevenLabs receives only that script for narration. Listen in the
dashboard or open the transcript and supporting passage.

For the demo, approve both summaries for `8C_354/2026`, then pre-generate:

```sh
.venv/bin/python src/app.py briefing --case 8C_354/2026 --lang both
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

```sh
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
node --test tests/conversation-ui.test.cjs
```

1. Click **Use this template** to create your own repository.

## License

All Hack Apertus projects are open-sourced. Please check our Terms & Conditions for specific licensing details (6. What you build is open source): https://hackapertus.ch/terms-and-conditions

## Support

### FAQ
💡 https://hackapertus.ch/faq

### Contact

💬 In case you have questions, join the conversation on Discord. Alternatively, send an email to “hello@hackapertus.ch”.
