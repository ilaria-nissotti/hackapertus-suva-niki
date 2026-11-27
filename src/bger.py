"""Collect Swiss Federal Supreme Court judgments and check Apertus."""

import argparse
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sys
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
CASE_ID = re.compile(r"\b\d+[A-Z]+_\d+/\d{4}\b")


class JudgmentExport(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_body = False
        self.in_title = False
        self.language = None
        self.title = []
        self.body = []

    def handle_starttag(self, tag, attrs):
        if tag == "html":
            self.language = dict(attrs).get("lang")
        elif tag == "title":
            self.in_title = True
        elif tag == "body":
            self.in_body = True
        elif self.in_body and tag in {"br", "hr"}:
            self.body.append("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        elif tag == "body":
            self.in_body = False
        elif self.in_body and tag in {"p", "div", "h1", "h2", "h3", "li", "tr"}:
            self.body.append("\n")

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)
        if self.in_body:
            self.body.append(data)


def import_exports(directory):
    paths = sorted(directory.glob("Export_*.html"))
    if not paths:
        raise ValueError(f"No Export_*.html files found in {directory}")
    for path in paths:
        parser = JudgmentExport()
        parser.feed(path.read_text(encoding="utf-8-sig"))
        parser.close()
        title = "".join(parser.title).strip()
        case_ids = list(dict.fromkeys(CASE_ID.findall(title)))
        if not case_ids:
            filename_id = re.match(r"Export_(\d[A-Z]+_\d+)_(\d{4})\b", path.stem)
            if filename_id:
                case_ids = ["/".join(filename_id.groups())]
        body = "\n".join(
            line.strip() for line in "".join(parser.body).splitlines() if line.strip()
        )
        if not case_ids or not body:
            raise ValueError(f"Missing case ID or body in {path.name}")
        print(json.dumps({
            "case_ids": case_ids,
            "title": title,
            "export_language": parser.language,
            "source_file": path.name,
            "text": body,
        }, ensure_ascii=False))


def fetch_opencaselaw(path):
    with urlopen(f"https://mcp.opencaselaw.ch/api/{path}", timeout=30) as response:
        result = json.load(response)
    sleep(0.25)
    return result


def iter_judgments(args):
    offset = 0
    seen = set()
    while True:
        params = urlencode({
            "court": "bger",
            "published_from": args.published_from,
            "published_to": args.published_to,
            "sort": "published_desc",
            "limit": min(args.limit - len(seen), 100) if args.limit else 100,
            "offset": offset,
            "include_pinpoint": "false",
        })
        page = fetch_opencaselaw(f"decisions?{params}")
        for record in page["results"]:
            decision_id = record["decision_id"]
            if decision_id in seen:
                continue
            if args.full_text:
                detail = fetch_opencaselaw(f"decisions/{quote(decision_id, safe='')}")
                if not (detail.get("full_text") or "").strip():
                    raise ValueError(f"No full text returned for {decision_id}")
                record = {**record, **detail}
            yield record
            seen.add(decision_id)
            if args.limit and len(seen) >= args.limit:
                return
        if not page["has_more"]:
            if not page["total_is_lower_bound"] and len(seen) != page["total"]:
                raise ValueError(f"Collected {len(seen)} of {page['total']} records")
            return
        next_offset = page["next_offset"]
        if not isinstance(next_offset, int) or next_offset <= offset:
            raise ValueError("OpenCaseLaw returned an invalid continuation offset")
        offset = next_offset


def collect_judgments(args):
    for record in iter_judgments(args):
        print(json.dumps(record, ensure_ascii=False), flush=True)


def local_config(*names):
    config = {}
    env_file = ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            name, _, value = line.partition("=")
            if name.strip() in names:
                config[name.strip()] = value.strip().strip("'\"")
    return {name: os.environ.get(name, config.get(name)) for name in names}


def model_config():
    names = ("LLM_API_KEY", "LLM_BASE_URL", "LLM_NAME")
    config = local_config(*names)
    missing = [name for name in names if not config[name]]
    if missing:
        raise ValueError("Missing configuration: " + ", ".join(missing))
    base_url = config["LLM_BASE_URL"].rstrip("/")
    if urlsplit(base_url).scheme != "https" or not urlsplit(base_url).hostname:
        raise ValueError("LLM_BASE_URL must be an HTTPS API base URL")
    return config


def chat(config, messages, max_tokens):
    base_url = config["LLM_BASE_URL"].rstrip("/")
    payload = dict(model=config["LLM_NAME"], messages=messages, max_tokens=max_tokens,
                   temperature=0)
    request = Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {config['LLM_API_KEY']}",
            "Content-Type": "application/json",
        },
    )
    with urlopen(request, timeout=90) as response:
        result = json.load(response)
    if not result.get("choices"):
        raise ValueError("Apertus returned no choices")
    choice = result["choices"][0]
    if choice.get("finish_reason") == "length":
        raise ValueError("Apertus response exceeded the output budget")
    return choice["message"]["content"]


def check_apertus():
    config = model_config()
    chat(config, [{"role": "user", "content": "Reply with the single word READY."}], 32)
    print(f"Apertus responded successfully using {config['LLM_NAME']}.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    importer = commands.add_parser("import-exports", help="Emit one JSON record per export")
    importer.add_argument("--directory", type=Path, default=ROOT / "judgements")
    crawler = commands.add_parser("collect", help="Fetch judgments from OpenCaseLaw as JSONL")
    crawler.add_argument("--published-from", default="2026-04-01")
    crawler.add_argument("--published-to", default="2026-06-30")
    crawler.add_argument("--limit", type=int, help="Stop after this many judgments")
    crawler.add_argument("--full-text", action="store_true")
    commands.add_parser("check-apertus", help="Send a short test prompt")
    args = parser.parse_args()
    try:
        if args.command == "import-exports":
            import_exports(args.directory)
        elif args.command == "collect":
            if args.limit is not None and args.limit < 1:
                parser.error("--limit must be positive")
            collect_judgments(args)
        else:
            check_apertus()
    except HTTPError as error:
        service = "OpenCaseLaw" if args.command == "collect" else "Apertus"
        print(f"{service} API returned HTTP {error.code}.", file=sys.stderr)
        return 1
    except (OSError, URLError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
