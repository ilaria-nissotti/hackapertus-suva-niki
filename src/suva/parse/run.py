"""Parse every stored judgement: passages + citations, with a failure report."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field

from suva.hashing import sha256
from suva.parse.citations import extract
from suva.parse.segment import PARSER_VERSION, segment
from suva.storage.base import AuditEntry, Repository

WATCH_PREFIXES = ("8C", "9C")  # accident / social insurance chambers: failures here matter most


@dataclass
class ParseReport:
    parser_version: str = PARSER_VERSION
    judgements: int = 0
    parsed: int = 0
    skipped_unchanged: int = 0
    status: dict = field(default_factory=dict)
    issues: dict = field(default_factory=dict)
    passages: int = 0
    citations: int = 0
    picks_not_ok: list[str] = field(default_factory=list)
    watched_not_ok: list[str] = field(default_factory=list)  # 8C/9C decisions not cleanly split

    def as_dict(self) -> dict:
        return asdict(self)


def parse_all(repo: Repository, force: bool = False) -> ParseReport:
    rep = ParseReport()
    picks = {r["docket"] for r in repo.list_known_positives(court="bger") if r["docket"]}
    status, issues = Counter(), Counter()
    for row in repo.list_judgements():
        rep.judgements += 1
        j = repo.get_judgement(row["key"])
        text = j["text"]
        text_hash = sha256(text) if text else None
        prev = repo.get_parse(j["key"])
        if (not force and prev and prev["text_hash"] == text_hash
                and prev["parser_version"] == PARSER_VERSION):
            rep.skipped_unchanged += 1
            seg_status, seg_issues = prev["status"], prev["issues"]
        else:
            seg = segment(j["key"], text)
            cits = {p.pid: extract(p.text, self_docket=j["docket"]) for p in seg.passages}
            repo.save_parse(seg, cits, text_hash)
            rep.parsed += 1
            rep.passages += len(seg.passages)
            rep.citations += sum(len(v) for v in cits.values())
            seg_status, seg_issues = seg.status, seg.issues
        status[seg_status] += 1
        issues.update(seg_issues)
        if seg_status != "ok":
            label = f"{j['key']} ({', '.join(seg_issues)})"
            if j["docket"] in picks:
                rep.picks_not_ok.append(label)
            if (j["chamber_prefix"] or "") in WATCH_PREFIXES:
                rep.watched_not_ok.append(label)
    rep.status, rep.issues = dict(status), dict(issues)
    repo.append_audit(AuditEntry(kind="parse_run", actor="pipeline", payload={
        k: v for k, v in rep.as_dict().items() if k not in ("picks_not_ok", "watched_not_ok")
    } | {"picks_not_ok": len(rep.picks_not_ok), "watched_not_ok": len(rep.watched_not_ok)}))
    return rep
