"""Parse every stored judgement: passages + citations, with a failure report."""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import asdict, dataclass, field

from suva.hashing import sha256
from suva.parse.citations import bge_header_reference, extract
from suva.parse.segment import PARSER_VERSION, segment
from suva.sources.base import parse_iso_date
from suva.storage.base import AuditEntry, Repository

log = logging.getLogger(__name__)
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


def parse_all(repo: Repository, force: bool = False, *, key: str | None = None) -> ParseReport:
    rep = ParseReport()
    rows = repo.list_judgements() if key is None else [repo.get_judgement(key)]
    rows = [row for row in rows if row is not None]
    docket_counts = Counter(row["docket"] for row in rows)
    labels = repo.list_known_positives(court="bger")
    exact_picks = {label["ref"] for label in labels if parse_iso_date(label["ref"].partition("@")[2])}
    unique_pick_dockets = {label["docket"] for label in labels
                           if key is None and label["ref"] not in exact_picks and docket_counts[label["docket"]] == 1}
    status, issues = Counter(), Counter()
    for row in rows:
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
            header = next((p.text for p in seg.passages if p.kind == "header"), "")
            own_bge = bge_header_reference(header) if "bge_extract" in seg.issues else None
            cits = {
                p.pid: extract(p.text, self_docket=j["docket"], self_bge=own_bge)
                for p in seg.passages
            }
            if not repo.save_parse(seg, cits, text_hash):
                status["stale"] += 1
                issues["text_changed_during_parse"] += 1
                log.warning("parse_stale key=%s reason=text_changed_during_parse", j["key"])
                continue
            rep.parsed += 1
            rep.passages += len(seg.passages)
            rep.citations += sum(len(v) for v in cits.values())
            seg_status, seg_issues = seg.status, seg.issues
        status[seg_status] += 1
        issues.update(seg_issues)
        if seg_status != "ok":
            label = f"{j['key']} ({', '.join(seg_issues)})"
            if j["key"] in exact_picks or j["docket"] in unique_pick_dockets:
                rep.picks_not_ok.append(label)
            if (j["chamber_prefix"] or "") in WATCH_PREFIXES:
                rep.watched_not_ok.append(label)
    rep.status, rep.issues = dict(status), dict(issues)
    log.info("parse_done judgements=%d parsed=%d skipped=%d status=%s picks_not_ok=%d", rep.judgements,
             rep.parsed, rep.skipped_unchanged, rep.status, len(rep.picks_not_ok))
    repo.append_audit(AuditEntry(kind="parse_run", actor="pipeline", payload={
        k: v for k, v in rep.as_dict().items() if k not in ("picks_not_ok", "watched_not_ok")
    } | {"picks_not_ok": len(rep.picks_not_ok), "watched_not_ok": len(rep.watched_not_ok)}))
    return rep
