#!/usr/bin/env python3
"""Transactional editorial reservations and hash-bound publication records.

This records supplied editorial/QA/user evidence. It does not perform QA,
authenticate a human, query a platform, or publish a video.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import unicodedata
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATES = {"IN_PROGRESS", "DRAFT", "QA", "WAITING_ACCEPTANCE",
          "PENDING_AUTHORIZATION", "PENDING_PUBLISH", "PUBLISHING",
          "PUBLISHED", "BLOCKED"}
ACTIVE_STATES = {"IN_PROGRESS", "DRAFT", "QA"}


class LedgerError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def require(condition, code, message):
    if not condition:
        raise LedgerError(code, message)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def timestamp(value, field, allow_date=False):
    require(isinstance(value, str) and value.strip(), "INVALID_TIME", f"{field} required")
    if allow_date and len(value) == 10:
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError as exc:
            raise LedgerError("INVALID_TIME", f"{field} must be an ISO date or timezone-aware timestamp") from exc
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LedgerError("INVALID_TIME", f"{field} must be an ISO timestamp") from exc
    require(parsed.tzinfo is not None, "INVALID_TIME", f"{field} requires a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def nonempty(value, field):
    require(isinstance(value, str) and value.strip(), "INVALID_INPUT", f"{field} required")
    return value.strip()


def time_precision(value):
    """Retain input resolution instead of treating ISO normalization as evidence."""
    time_text = re.split(r"[Tt ]", value.strip(), maxsplit=1)[-1]
    time_text = re.sub(r"(?:Z|[+-]\d{2}(?::?\d{2})?(?::?\d{2}(?:[.,]\d+)?)?)$", "", time_text)
    fraction = re.search(r"[.,](\d+)", time_text)
    if fraction:
        return "subsecond", min(len(fraction.group(1)), 6)
    digits = time_text.replace(":", "")
    return ("second", 0) if len(digits) >= 6 else ("minute", 0)


def publication_time_conflict(item, observed):
    """Compare only the resolution both timezone-aware observations support."""
    original = item.get("published_at_original")
    if not original or not item.get("published_at_precision") or not isinstance(observed, str):
        return None
    try:
        left = datetime.fromisoformat(timestamp(original, "published_at"))
        right = datetime.fromisoformat(timestamp(observed.strip(), "observed published_at"))
    except LedgerError:
        return None
    lp, ld = time_precision(original); rp, rd = time_precision(observed)
    if "minute" in {lp, rp}:
        return left.replace(second=0, microsecond=0) != right.replace(second=0, microsecond=0)
    if "second" in {lp, rp}:
        return left.replace(microsecond=0) != right.replace(microsecond=0)
    unit = 10 ** (6 - min(ld, rd))
    return left.replace(microsecond=left.microsecond // unit * unit) != right.replace(microsecond=right.microsecond // unit * unit)


def normalized_fact(text):
    # Preserve decimal points, negation, digits, units and letter case. Semantic
    # equivalence must be reviewed by an editor; this is only a typography guard.
    return "".join(unicodedata.normalize("NFKC", text).split())


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


class Ledger:
    def __init__(self, db, workspace, readonly=False, max_in_progress=None):
        self.workspace = Path(workspace).resolve()
        self.path = Path(db).resolve()
        require(self.workspace.is_dir(), "INVALID_PATH", "workspace must be an existing directory")
        require(self.path.is_relative_to(self.workspace), "INVALID_PATH", "database must be inside workspace")
        require(not self.path.is_relative_to(ROOT), "INVALID_PATH", "database cannot be inside the skill repository")
        require("outputs" not in self.path.parts, "INVALID_PATH", "database cannot be a deliverable in outputs")
        if readonly:
            require(self.path.is_file(), "NOT_FOUND", "database does not exist")
            self.connection = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=15)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=15000")
        self.readonly = readonly
        configured_limit = read_json(ROOT / "config.json").get("editorial_feedback", {}).get("max_in_progress_topics")
        self.max_in_progress = configured_limit if max_in_progress is None else max_in_progress
        self.limit_source = "config" if max_in_progress is None else "explicit_override"
        require(type(self.max_in_progress) is int and self.max_in_progress > 0,
                "INVALID_CONFIG", "max_in_progress_topics must be a positive integer")
        if not readonly:
            self.connection.executescript("""
                CREATE TABLE IF NOT EXISTS items (
                    content_id TEXT PRIMARY KEY, event_id TEXT NOT NULL,
                    run_id TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS fact_ids (
                    event_id TEXT NOT NULL, fact_id TEXT NOT NULL, normalized TEXT NOT NULL,
                    PRIMARY KEY(event_id, fact_id)
                );
                CREATE TABLE IF NOT EXISTS expressed_facts (
                    event_id TEXT NOT NULL, normalized TEXT NOT NULL,
                    content_id TEXT NOT NULL REFERENCES items(content_id),
                    PRIMARY KEY(event_id, normalized, content_id)
                );
                CREATE TABLE IF NOT EXISTS publications (
                    platform_content_id TEXT PRIMARY KEY,
                    content_id TEXT UNIQUE NOT NULL REFERENCES items(content_id),
                    final_sha256 TEXT NOT NULL, published_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, content_id TEXT NOT NULL,
                    at TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL
                );
            """)

    def close(self):
        self.connection.close()

    @contextmanager
    def transaction(self):
        require(not self.readonly, "READ_ONLY", "read-only ledger")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def _get(self, content_id):
        row = self.connection.execute("SELECT data FROM items WHERE content_id=?", (content_id,)).fetchone()
        require(row is not None, "NOT_FOUND", f"unknown content_id: {content_id}")
        return json.loads(row["data"])

    def _save(self, item, action, detail=None, progress=True):
        at = now()
        if progress:
            item["last_progress_at"] = at
        self.connection.execute("UPDATE items SET status=?,run_id=?,data=? WHERE content_id=?",
                                (item["status"], item["run_id"], json.dumps(item, ensure_ascii=False), item["content_id"]))
        self.connection.execute("INSERT INTO audit(content_id,at,action,detail) VALUES(?,?,?,?)",
                                (item["content_id"], at, action, json.dumps(detail or {}, ensure_ascii=False)))

    def _state(self, item, allowed):
        require(item["status"] in allowed, "INVALID_STATE",
                f"{item['status']} cannot perform this action; expected {', '.join(allowed)}")

    def _current_final(self, item):
        final = item.get("final") or {}
        require(final.get("path") and final.get("sha256"), "MISSING_FINAL", "no final artifact recorded")
        require(Path(final["path"]).is_file(), "STALE_FINAL", "final artifact missing")
        require(sha256(final["path"]) == final["sha256"], "STALE_FINAL", "final artifact changed; record a new draft")
        return final["sha256"]

    def _verified_qa(self, item, digest):
        qa = item.get("qa") or {}
        require(qa.get("result") == "PASS" and qa.get("final_sha256") == digest,
                "STALE_QA", "current final lacks recorded QA pass")
        path = Path(qa.get("path", ""))
        require(path.is_file() and sha256(path) == qa.get("evidence_sha256"),
                "STALE_QA", "QA evidence file changed or is missing")

    def _capacity(self, content_id=None):
        count = self.connection.execute(
            "SELECT COUNT(*) FROM items WHERE status IN ('IN_PROGRESS','DRAFT','QA') AND content_id != ?",
            (content_id or "",)).fetchone()[0]
        require(count < self.max_in_progress, "CAPACITY_REACHED",
                f"in-progress topic limit reached ({self.max_in_progress}); finish or block existing work")
        return {"max_in_progress": self.max_in_progress, "limit_source": self.limit_source}

    def claim(self, request):
        require(isinstance(request, dict), "INVALID_INPUT", "item must be an object")
        identifiers = {k: nonempty(request.get(k), k) for k in ("content_id", "event_id", "run_id")}
        claims = request.get("claims")
        require(isinstance(claims, list) and bool(claims), "INVALID_INPUT", "nonempty claims list required")
        mapped, seen_ids, seen_texts = [], set(), set()
        for claim in claims:
            require(isinstance(claim, dict), "INVALID_INPUT", "claim must be an object")
            text = nonempty(claim.get("text"), "claims.text")
            kind = claim.get("kind", "fact")
            require(isinstance(kind, str) and kind in {"fact", "new", "correction"}, "INVALID_INPUT", "claim kind must be fact/new/correction")
            normalized = normalized_fact(text)
            fid = claim.get("id") or hashlib.sha256(normalized.encode()).hexdigest()
            fid = nonempty(fid, "claim.id")
            require(fid not in seen_ids and normalized not in seen_texts, "DUPLICATE_CLAIM", "duplicate fact ID or text in request")
            seen_ids.add(fid); seen_texts.add(normalized)
            mapped.append({"id": fid, "text": text, "kind": kind, "normalized": normalized,
                           "source": claim.get("source")})
        material_at = timestamp(request.get("latest_material_at"), "latest_material_at", allow_date=True)
        with self.transaction():
            require(self.connection.execute("SELECT 1 FROM items WHERE content_id=?", (identifiers["content_id"],)).fetchone() is None,
                    "ID_EXISTS", "content_id already exists; use resume, never overwrite it")
            existing = {r[0] for r in self.connection.execute("SELECT normalized FROM expressed_facts WHERE event_id=?", (identifiers["event_id"],))}
            for claim in mapped:
                known = self.connection.execute("SELECT normalized FROM fact_ids WHERE event_id=? AND fact_id=?",
                                                (identifiers["event_id"], claim["id"])).fetchone()
                require(known is None or known[0] == claim["normalized"], "FACT_ID_CONFLICT", "existing fact ID cannot point to different text")
            novelty = [c for c in mapped if c["normalized"] not in existing]
            require(bool(novelty), "NO_NEW_FACTS", "same event has no unreserved fact; resume the existing content_id")
            if existing:
                require(any(c["kind"] in {"new", "correction"} for c in novelty), "EDITORIAL_REVIEW_REQUIRED", "same-event novelty must be marked new or correction")
                nonempty(request.get("editorial_review"), "editorial_review for semantic novelty/correction")
            limit_record = self._capacity()
            at = now()
            item = dict(identifiers, status="IN_PROGRESS", claims=mapped, latest_material_at=material_at,
                        latest_material_at_precision="date" if len(material_at) == 10 else "timestamp",
                        editorial_review=request.get("editorial_review"), created_at=at,
                        production_started_at=at, production_completed_at=None,
                        published_at=None, platform_content_id=None, reason=None,
                        last_progress_at=at, progress_markers=[], final=None, qa=None,
                        acceptance=None, authorization=None, publish_prepared=None,
                        run_history=[{"run_id": identifiers["run_id"], "at": at, "action": "claim"}],
                        concurrency_policy=limit_record)
            self.connection.execute("INSERT INTO items VALUES(?,?,?,?,?)", (item["content_id"], item["event_id"], item["run_id"], item["status"], json.dumps(item, ensure_ascii=False)))
            for claim in mapped:
                self.connection.execute("INSERT OR IGNORE INTO fact_ids VALUES(?,?,?)", (item["event_id"], claim["id"], claim["normalized"]))
                self.connection.execute("INSERT INTO expressed_facts VALUES(?,?,?)", (item["event_id"], claim["normalized"], item["content_id"]))
            self._save(item, "claim", {"new_fact_ids": [c["id"] for c in novelty]})
            return item

    def draft(self, content_id, final_path):
        path = Path(final_path).resolve()
        require(path.is_file(), "INVALID_INPUT", "final path must be an existing file")
        digest = sha256(path)
        with self.transaction():
            item = self._get(content_id)
            self._state(item, {"IN_PROGRESS", "DRAFT", "QA", "WAITING_ACCEPTANCE", "PENDING_AUTHORIZATION", "PENDING_PUBLISH"})
            limit_record = self._capacity(content_id)
            item.update(status="DRAFT", final={"path": str(path), "sha256": digest}, qa=None,
                        acceptance=None, authorization=None, publish_prepared=None,
                        production_completed_at=None, reason=None, concurrency_policy=limit_record)
            self._save(item, "draft", {"final_sha256": digest})
            return item

    def qa_start(self, content_id):
        with self.transaction():
            item = self._get(content_id); self._state(item, {"DRAFT"}); self._current_final(item)
            item["status"] = "QA"; self._save(item, "qa-start"); return item

    def qa(self, content_id, evidence_path):
        path = Path(evidence_path).resolve()
        evidence = read_json(path)
        require(isinstance(evidence, dict) and evidence.get("result") == "PASS", "QA_NOT_PASSED", "QA evidence must explicitly record result PASS")
        nonempty(evidence.get("reviewer"), "QA reviewer")
        with self.transaction():
            item = self._get(content_id); self._state(item, {"DRAFT", "QA"})
            digest = self._current_final(item)
            require(evidence.get("final_sha256") == digest, "STALE_QA", "QA evidence does not match current final SHA-256")
            item.update(status="WAITING_ACCEPTANCE", qa={"path": str(path), "evidence_sha256": sha256(path),
                        "final_sha256": digest, "reviewer": evidence["reviewer"], "result": "PASS", "recorded_at": now()},
                        production_completed_at=now(), reason="awaiting user acceptance")
            self._save(item, "qa-pass", item["qa"]); return item

    def user_record(self, content_id, record_path, authorization=False):
        record = read_json(record_path)
        key = "authorized" if authorization else "accepted"
        require(isinstance(record, dict) and record.get(key) is True, "USER_RECORD_REQUIRED", f"record must explicitly set {key}: true")
        actor = nonempty(record.get("actor"), "user record actor")
        evidence = nonempty(record.get("evidence"), "user instruction evidence reference/text")
        record_at = timestamp(record.get("at"), "user record at")
        with self.transaction():
            item = self._get(content_id)
            self._state(item, {"PENDING_AUTHORIZATION"} if authorization else {"WAITING_ACCEPTANCE"})
            digest = self._current_final(item)
            require(record.get("final_sha256") == digest, "STALE_USER_RECORD", "user record does not match current final SHA-256")
            self._verified_qa(item, digest)
            if authorization:
                require(item.get("acceptance", {}).get("final_sha256") == digest, "MISSING_ACCEPTANCE", "current final lacks user acceptance")
            field = "authorization" if authorization else "acceptance"
            item[field] = {key: True, "actor": actor, "evidence": evidence, "at": record_at,
                           "final_sha256": digest, "record_sha256": sha256(record_path)}
            item["status"] = "PENDING_PUBLISH" if authorization else "PENDING_AUTHORIZATION"
            item["reason"] = "awaiting platform publication" if authorization else "awaiting explicit publication authorization"
            self._save(item, field, item[field]); return item

    def prepare_publish(self, content_id):
        with self.transaction():
            item = self._get(content_id); self._state(item, {"PENDING_PUBLISH"})
            digest = self._current_final(item)
            self._verified_qa(item, digest)
            for field in ("qa", "acceptance", "authorization"):
                require((item.get(field) or {}).get("final_sha256") == digest, "STALE_AUTHORIZATION", f"{field} missing or bound to another final")
            require(self.connection.execute("SELECT 1 FROM publications WHERE content_id=?", (content_id,)).fetchone() is None,
                    "ALREADY_PUBLISHED", "publication already recorded")
            item.update(status="PUBLISHING", publish_prepared={"final_sha256": digest, "at": now()},
                        reason="publication in progress; reconcile platform state before retrying")
            self._save(item, "prepare-publish", item["publish_prepared"]); return item

    def publish(self, content_id, platform_content_id, published_at):
        platform_content_id = nonempty(platform_content_id, "platform_content_id")
        published_at_original = nonempty(published_at, "published_at")
        precision, _ = time_precision(published_at_original)
        published_at = timestamp(published_at, "published_at")
        with self.transaction():
            item = self._get(content_id); self._state(item, {"PUBLISHING"})
            digest = self._current_final(item)
            self._verified_qa(item, digest)
            require((item.get("publish_prepared") or {}).get("final_sha256") == digest,
                    "NOT_PREPARED", "prepare-publish must bind current final")
            for field in ("qa", "acceptance", "authorization"):
                require((item.get(field) or {}).get("final_sha256") == digest, "STALE_AUTHORIZATION", f"{field} is stale")
            try:
                self.connection.execute("INSERT INTO publications VALUES(?,?,?,?)", (platform_content_id, content_id, digest, published_at))
            except sqlite3.IntegrityError as exc:
                raise LedgerError("PLATFORM_ID_EXISTS", "platform/content ID already recorded; reconcile, do not overwrite") from exc
            item.update(status="PUBLISHED", platform_content_id=platform_content_id, published_at=published_at,
                        published_at_original=published_at_original, published_at_precision=precision, reason=None)
            self._save(item, "publish-record", {"platform_content_id": platform_content_id, "published_at": published_at,
                       "published_at_original": published_at_original, "published_at_precision": precision, "final_sha256": digest})
            return item

    def retry_publish(self, content_id, evidence_path):
        """Record an operator's explicit absence confirmation, never auto-retry."""
        record = read_json(evidence_path)
        require(isinstance(record, dict) and record.get("confirmed_absent") is True,
                "ABSENCE_NOT_CONFIRMED", "explicit confirmed_absent: true required; an unknown result or empty list is insufficient")
        reviewer = nonempty(record.get("reviewer"), "absence confirmation reviewer")
        evidence = nonempty(record.get("evidence"), "platform absence evidence reference/text")
        checked_at = timestamp(record.get("checked_at"), "checked_at")
        with self.transaction():
            item = self._get(content_id); self._state(item, {"PUBLISHING"})
            digest = self._current_final(item); self._verified_qa(item, digest)
            require(record.get("final_sha256") == digest, "STALE_ABSENCE_RECORD", "absence record must identify the current authorized final SHA-256")
            for field in ("acceptance", "authorization"):
                require((item.get(field) or {}).get("final_sha256") == digest,
                        "STALE_AUTHORIZATION", f"{field} missing or bound to another final")
            prepared = item.get("publish_prepared") or {}
            require(prepared.get("final_sha256") == digest and prepared.get("at"),
                    "NOT_PREPARED", "no matching publication attempt to reconcile")
            require(datetime.fromisoformat(checked_at) >= datetime.fromisoformat(prepared["at"]),
                    "STALE_ABSENCE_RECORD", "absence check predates the current publication attempt")
            require(self.connection.execute("SELECT 1 FROM publications WHERE content_id=?", (content_id,)).fetchone() is None,
                    "ALREADY_PUBLISHED", "publication already recorded; never reset it for retry")
            confirmation = {"confirmed_absent": True, "reviewer": reviewer, "evidence": evidence,
                            "checked_at": checked_at, "final_sha256": digest,
                            "record_sha256": sha256(evidence_path), "previous_attempt": prepared}
            item.setdefault("publish_retry_history", []).append(confirmation)
            item.update(status="PENDING_PUBLISH", publish_prepared=None,
                        reason="explicit platform absence recorded; prepare-publish is required before retry")
            self._save(item, "retry-publish-authorized", confirmation)
            return item

    def block(self, content_id, reason):
        reason = nonempty(reason, "blocked reason")
        with self.transaction():
            item = self._get(content_id)
            require(item["status"] != "PUBLISHED", "INVALID_STATE", "published content cannot be blocked/replaced")
            if item["status"] != "BLOCKED":
                item["resume_status"] = item["status"]
            item.update(status="BLOCKED", reason=reason)
            self._save(item, "block", {"reason": reason}, progress=False); return item

    def resume(self, content_id, run_id, reason):
        run_id = nonempty(run_id, "run_id"); reason = nonempty(reason, "resume reason")
        with self.transaction():
            item = self._get(content_id); self._state(item, {"BLOCKED"})
            require(run_id != item["run_id"], "NEW_RUN_REQUIRED", "resume requires a fresh run_id and its version gate")
            status = item.get("resume_status", "IN_PROGRESS")
            if item.get("final"):
                try:
                    self._current_final(item)
                except LedgerError:
                    require(status != "PUBLISHING", "STALE_FINAL", "publication outcome uncertain; retain the authorized artifact and reconcile before recovery")
                    status = "DRAFT"
                    item.update(final=None, qa=None, acceptance=None, authorization=None,
                                publish_prepared=None, production_completed_at=None)
            if status in ACTIVE_STATES:
                item["concurrency_policy"] = self._capacity(content_id)
            item.setdefault("run_history", []).append({"run_id": run_id, "at": now(), "action": "resume", "reason": reason})
            item.update(status=status, run_id=run_id, reason=reason)
            self._save(item, "resume", {"reason": reason, "status": status}); return item

    def progress(self, content_id, node, artifact_path=None):
        node = nonempty(node, "new node")
        marker = {"node": node}
        if artifact_path:
            path = Path(artifact_path).resolve()
            require(path.is_file(), "INVALID_INPUT", "progress artifact must exist")
            marker.update(path=str(path), sha256=sha256(path))
        marker_key = hashlib.sha256(json.dumps(marker, sort_keys=True).encode()).hexdigest()
        with self.transaction():
            item = self._get(content_id)
            require(item["status"] not in {"BLOCKED", "PUBLISHED"}, "INVALID_STATE", "resume blocked content before recording progress")
            require(marker_key not in item["progress_markers"], "NO_PROGRESS", "same node/artifact is not new progress; heartbeat does not advance last_progress_at")
            item["progress_markers"].append(marker_key)
            self._save(item, "progress", marker); return item

    def show(self, content_id=None):
        if content_id:
            item = self._get(content_id)
            item["audit"] = [dict(row) for row in self.connection.execute("SELECT seq,at,action,detail FROM audit WHERE content_id=? ORDER BY seq", (content_id,))]
            return item
        return [json.loads(row[0]) for row in self.connection.execute("SELECT data FROM items ORDER BY content_id")]

    def reconcile(self, observed):
        require(isinstance(observed, list), "INVALID_INPUT", "observed platform data must be a list")
        results = []
        for item in self.show():
            matches = [row for row in observed if isinstance(row, dict) and (
                (item.get("platform_content_id") and row.get("platform_content_id") == item["platform_content_id"])
                or row.get("content_id") == item["content_id"]
                or (item.get("final") and row.get("final_sha256") == item["final"]["sha256"]))]
            conflicts = []
            for row in matches:
                expected = {"content_id": item["content_id"],
                            "final_sha256": (item.get("final") or {}).get("sha256"),
                            "platform_content_id": item.get("platform_content_id")}
                for field, local_value in expected.items():
                    supplied = row.get(field)
                    if isinstance(supplied, str):
                        supplied = supplied.strip()
                    if supplied not in (None, "") and local_value and supplied != local_value:
                        conflicts.append({"platform_content_id": row.get("platform_content_id"),
                                          "field": field, "expected": local_value, "observed": row.get(field)})
                if publication_time_conflict(item, row.get("published_at")) is True:
                    conflicts.append({"platform_content_id": row.get("platform_content_id"),
                                      "field": "published_at", "expected": item["published_at_original"],
                                      "observed": row["published_at"]})
            results.append({"content_id": item["content_id"], "local_status": item["status"],
                            "platform_candidates": matches, "conflicts": conflicts,
                            "needs_review": item["status"] != "PUBLISHED" or len(matches) != 1 or bool(conflicts)})
        return {"readonly": True, "automatic_republish": False, "results": results}


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise LedgerError("ARGUMENT_ERROR", message)


def parser():
    p = JsonArgumentParser(description=__doc__)
    p.add_argument("--workspace", required=True, help="existing production work directory, not the skill repository")
    p.add_argument("--db", required=True, help="explicit SQLite path inside workspace; no default data path")
    p.add_argument("--max-in-progress", type=int, help="explicit trial override of config editorial_feedback.max_in_progress_topics; recorded with claim/resume")
    sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("claim"); c.add_argument("--item", required=True)
    for name in ("draft", "qa-start", "qa", "accept", "authorize", "prepare-publish", "publish", "retry-publish", "block", "resume", "progress", "show"):
        c = sub.add_parser(name); c.add_argument("--content-id", required=name != "show")
        if name == "draft": c.add_argument("--final", required=True)
        if name == "qa": c.add_argument("--evidence", required=True)
        if name == "retry-publish": c.add_argument("--evidence", required=True)
        if name in {"accept", "authorize"}: c.add_argument("--record", required=True)
        if name == "publish":
            c.add_argument("--platform-content-id", required=True); c.add_argument("--published-at", required=True)
        if name in {"block", "resume"}: c.add_argument("--reason", required=True)
        if name == "resume": c.add_argument("--run-id", required=True)
        if name == "progress":
            c.add_argument("--node", required=True); c.add_argument("--artifact")
    c = sub.add_parser("reconcile"); c.add_argument("--platform-json", required=True)
    return p


def main(argv=None):
    ledger = None
    try:
        args = parser().parse_args(argv)
        ledger = Ledger(args.db, args.workspace, readonly=args.command in {"show", "reconcile"}, max_in_progress=args.max_in_progress)
        cid = getattr(args, "content_id", None)
        if args.command == "claim": result = ledger.claim(read_json(args.item))
        elif args.command == "draft": result = ledger.draft(cid, args.final)
        elif args.command == "qa-start": result = ledger.qa_start(cid)
        elif args.command == "qa": result = ledger.qa(cid, args.evidence)
        elif args.command in {"accept", "authorize"}: result = ledger.user_record(cid, args.record, args.command == "authorize")
        elif args.command == "prepare-publish": result = ledger.prepare_publish(cid)
        elif args.command == "publish": result = ledger.publish(cid, args.platform_content_id, args.published_at)
        elif args.command == "retry-publish": result = ledger.retry_publish(cid, args.evidence)
        elif args.command == "block": result = ledger.block(cid, args.reason)
        elif args.command == "resume": result = ledger.resume(cid, args.run_id, args.reason)
        elif args.command == "progress": result = ledger.progress(cid, args.node, args.artifact)
        elif args.command == "show": result = ledger.show(cid)
        else: result = ledger.reconcile(read_json(args.platform_json))
        print(json.dumps({"ok": True, "data": result}, ensure_ascii=False)); return 0
    except (LedgerError, OSError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": {"code": getattr(exc, "code", "IO_OR_DATABASE_ERROR"), "message": str(exc)}}, ensure_ascii=False)); return 2
    finally:
        if ledger is not None: ledger.close()


if __name__ == "__main__":
    sys.exit(main())
