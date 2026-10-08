#!/usr/bin/env python3
"""Verify reservations, evidence binding and publication retry safety."""
import concurrent.futures
import contextlib
import hashlib
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path

from editorial_ledger import Ledger, LedgerError, ROOT, main, now


class EditorialLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.work = Path(self.temp.name)
        self.db = self.work / "ledger.sqlite"
        self.ledger = Ledger(self.db, self.work, max_in_progress=5)
        self.final = self.work / "final.mp4"
        self.final.write_bytes(b"fixture final, not a real video")

    def tearDown(self):
        self.ledger.close(); self.temp.cleanup()

    def item(self, cid="c1", event="event-1", text="价格为1.5元", kind="fact", fid="f1"):
        return {"content_id": cid, "run_id": "run1", "event_id": event,
                "claims": [{"id": fid, "text": text, "kind": kind}],
                "latest_material_at": "2026-10-08T08:00:00+08:00", "editorial_review": "editor verified new facts/source"}

    def error(self, code, fn, *args):
        with self.assertRaises(LedgerError) as ctx: fn(*args)
        self.assertEqual(code, ctx.exception.code)

    def record(self, name, data):
        path = self.work / name; path.write_text(json.dumps(data)); return path

    def ready(self, cid="c1", event="event-1"):
        self.ledger.claim(self.item(cid, event))
        self.ledger.draft(cid, self.final)
        digest = hashlib.sha256(self.final.read_bytes()).hexdigest()
        evidence = self.record(cid+"-qa.json", {"result": "PASS", "reviewer": "independent-reviewer", "final_sha256": digest})
        self.ledger.qa_start(cid); self.ledger.qa(cid, evidence)
        self.ledger.user_record(cid, self.record(cid+"-accept.json", {"accepted": True, "actor": "user", "evidence": "user accepted this exact final", "at": "2026-10-08T09:00:00+08:00", "final_sha256": digest}))
        self.ledger.user_record(cid, self.record(cid+"-authorize.json", {"authorized": True, "actor": "user", "evidence": "user explicitly requested publication", "at": "2026-10-08T09:01:00+08:00", "final_sha256": digest}), True)
        return digest

    def test_same_fact_different_id_and_typography_rejected(self):
        self.ledger.claim(self.item())
        self.error("NO_NEW_FACTS", self.ledger.claim, self.item("c2", text="价格 为１.５元", fid="new-id", kind="new"))
        self.assertEqual(1, len(self.ledger.show()))

    def test_content_id_overwrite_rejected(self):
        self.ledger.claim(self.item())
        self.error("ID_EXISTS", self.ledger.claim, self.item(event="other", text="different"))

    def test_fact_id_cannot_be_remapped(self):
        self.ledger.claim(self.item())
        self.error("FACT_ID_CONFLICT", self.ledger.claim, self.item("c2", text="价格15元", kind="new"))

    def test_real_increment_and_correction_allowed(self):
        self.ledger.claim(self.item())
        self.ledger.claim(self.item("c2", text="条件改为实名用户", kind="new", fid="f2"))
        self.ledger.claim(self.item("c3", text="原图拍摄于九月而非今日", kind="correction", fid="f3"))
        self.assertEqual(3, len(self.ledger.show()))

    def test_novelty_requires_editorial_review(self):
        self.ledger.claim(self.item())
        request = self.item("c2", text="新信息", kind="new", fid="f2"); request.pop("editorial_review")
        self.error("INVALID_INPUT", self.ledger.claim, request)
        self.assertEqual(1, len(self.ledger.show()))

    def test_concurrent_claim_only_one_wins(self):
        barrier = threading.Barrier(2)
        def compete(i):
            ledger = Ledger(self.db, self.work)
            try:
                barrier.wait()
                try: ledger.claim(self.item(f"race{i}", event="race-event", fid=f"race-f{i}")); return "ok"
                except LedgerError as exc: return exc.code
            finally: ledger.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(compete, [1, 2]))
        self.assertCountEqual(["ok", "NO_NEW_FACTS"], results)

    def test_blocked_reservation_requires_resume_same_id(self):
        self.ledger.claim(self.item()); first = self.ledger.show("c1")["last_progress_at"]
        self.ledger.block("c1", "cover unavailable")
        self.assertEqual(first, self.ledger.show("c1")["last_progress_at"])
        self.error("NO_NEW_FACTS", self.ledger.claim, self.item("c2"))
        resumed = self.ledger.resume("c1", "run2", "cover now available")
        self.assertEqual("IN_PROGRESS", resumed["status"]); self.assertEqual("run2", resumed["run_id"])

    def test_progress_requires_new_node_or_artifact(self):
        self.ledger.claim(self.item()); self.ledger.progress("c1", "sources-verified")
        previous = self.ledger.show("c1")["last_progress_at"]
        self.error("NO_PROGRESS", self.ledger.progress, "c1", "sources-verified")
        self.assertEqual(previous, self.ledger.show("c1")["last_progress_at"])
        self.ledger.progress("c1", "sources-verified", self.final)

    def test_prepare_needs_explicit_qa_acceptance_authorization(self):
        self.ledger.claim(self.item()); self.ledger.draft("c1", self.final)
        self.error("INVALID_STATE", self.ledger.prepare_publish, "c1")

    def test_stale_qa_rejected(self):
        self.ledger.claim(self.item()); self.ledger.draft("c1", self.final)
        evidence = self.record("qa.json", {"result":"PASS", "reviewer":"reviewer", "final_sha256":"0"*64})
        self.error("STALE_QA", self.ledger.qa, "c1", evidence)

    def test_changed_hash_invalidates_old_acceptance_and_authorization(self):
        old = self.ready(); self.final.write_bytes(b"changed final")
        self.error("STALE_FINAL", self.ledger.prepare_publish, "c1")
        updated = self.ledger.draft("c1", self.final)
        self.assertIsNone(updated["acceptance"]); self.assertIsNone(updated["authorization"])
        new_digest = hashlib.sha256(self.final.read_bytes()).hexdigest()
        self.ledger.qa("c1", self.record("new-qa.json", {"result":"PASS", "reviewer":"reviewer", "final_sha256":new_digest}))
        record = self.record("old-accept.json", {"accepted":True, "actor":"user", "evidence":"prior version only", "at":"2026-10-08T09:00:00Z", "final_sha256":old})
        self.error("STALE_USER_RECORD", self.ledger.user_record, "c1", record)

    def test_publish_requires_prepare_and_unique_platform_id(self):
        self.ready(); self.error("INVALID_STATE", self.ledger.publish, "c1", "p1", "2026-10-08T09:00:00Z")
        self.ledger.prepare_publish("c1"); published = self.ledger.publish("c1", "p1", "2026-10-08T09:00:00Z")
        self.assertEqual("PUBLISHED", published["status"])
        self.assertNotEqual(published["published_at"], published["production_completed_at"])
        self.error("INVALID_STATE", self.ledger.prepare_publish, "c1")
        self.ready("c2", "event-2"); self.ledger.prepare_publish("c2")
        self.error("PLATFORM_ID_EXISTS", self.ledger.publish, "c2", "p1", "2026-10-08T10:00:00Z")
        self.assertEqual("PUBLISHING", self.ledger.show("c2")["status"])

    def test_publishing_block_resume_does_not_enable_reupload(self):
        self.ready(); self.ledger.prepare_publish("c1"); self.ledger.block("c1", "platform response unknown")
        self.assertEqual("PUBLISHING", self.ledger.resume("c1", "run2", "observed platform status")["status"])
        self.error("INVALID_STATE", self.ledger.prepare_publish, "c1")

    def test_unknown_or_stale_absence_cannot_enable_retry(self):
        digest = self.ready(); self.ledger.prepare_publish("c1")
        unknown = self.record("unknown.json", {"confirmed_absent": False, "reviewer": "operator", "evidence": "temporary empty list", "checked_at": now(), "final_sha256": digest})
        self.error("ABSENCE_NOT_CONFIRMED", self.ledger.retry_publish, "c1", unknown)
        missing = self.record("missing.json", {"confirmed_absent": True, "reviewer": "operator", "checked_at": now(), "final_sha256": digest})
        self.error("INVALID_INPUT", self.ledger.retry_publish, "c1", missing)
        old = self.record("old-absence.json", {"confirmed_absent": True, "reviewer": "operator", "evidence": "prior attempt checked", "checked_at": "2000-01-01T00:00:00Z", "final_sha256": digest})
        self.error("STALE_ABSENCE_RECORD", self.ledger.retry_publish, "c1", old)
        self.assertEqual("PUBLISHING", self.ledger.show("c1")["status"])

    def test_explicit_absence_after_resume_allows_prepared_retry_only(self):
        digest = self.ready(); self.ledger.prepare_publish("c1")
        self.ledger.block("c1", "upload outcome unknown")
        evidence = self.record("confirmed-absent.json", {"confirmed_absent": True, "reviewer": "operator", "evidence": "target account full content list and upload status checked; matching work absent", "checked_at": now(), "final_sha256": digest})
        self.error("INVALID_STATE", self.ledger.retry_publish, "c1", evidence)
        self.ledger.resume("c1", "new-gated-run", "platform absence checked")
        item = self.ledger.retry_publish("c1", evidence)
        self.assertEqual("PENDING_PUBLISH", item["status"]); self.assertIsNone(item["publish_prepared"])
        self.assertTrue(item["publish_retry_history"][0]["confirmed_absent"])
        self.error("INVALID_STATE", self.ledger.publish, "c1", "p1", now())
        self.ledger.prepare_publish("c1"); self.ledger.publish("c1", "p1", now())
        self.error("INVALID_STATE", self.ledger.retry_publish, "c1", evidence)

    def test_blocked_draft_with_changed_final_resumes_without_old_evidence(self):
        self.ready(); self.ledger.block("c1", "need revised final")
        self.final.write_bytes(b"revised final")
        resumed = self.ledger.resume("c1", "run2", "artifact revised")
        self.assertEqual("DRAFT", resumed["status"])
        self.assertIsNone(resumed["final"]); self.assertIsNone(resumed["authorization"])
        self.ledger.draft("c1", self.final)
        self.error("INVALID_STATE", self.ledger.prepare_publish, "c1")

    def test_publishing_changed_artifact_cannot_reset_to_reupload(self):
        self.ready(); self.ledger.prepare_publish("c1"); self.ledger.block("c1", "unknown upload result")
        self.final.write_bytes(b"different final")
        self.error("STALE_FINAL", self.ledger.resume, "c1", "run2", "retry")
        self.assertEqual("BLOCKED", self.ledger.show("c1")["status"])

    def test_changed_qa_evidence_prevents_publication(self):
        self.ready(); (self.work/"c1-qa.json").write_text('{"result":"FAIL"}')
        self.error("STALE_QA", self.ledger.prepare_publish, "c1")

    def test_cli_argument_errors_are_structured_json(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output): self.assertEqual(2, main([]))
        self.assertEqual("ARGUMENT_ERROR", json.loads(output.getvalue())["error"]["code"])

    def test_show_reconcile_are_readonly_and_never_publish(self):
        self.ready(); before = self.ledger.show("c1")
        ro = Ledger(self.db, self.work, readonly=True)
        try:
            result = ro.reconcile([{"content_id":"c1", "platform_content_id":"p1"}])
            self.assertTrue(result["readonly"]); self.assertFalse(result["automatic_republish"])
            self.error("READ_ONLY", ro.block, "c1", "reason")
        finally: ro.close()
        self.assertEqual(before, self.ledger.show("c1"))

    def test_matching_platform_id_with_conflicting_identity_hash_or_time_requires_review(self):
        self.ready(); self.ledger.prepare_publish("c1")
        self.ledger.publish("c1", "p1", "2026-10-08T09:00:00+08:00")
        before = self.ledger.show("c1")
        observed = [{"platform_content_id": "p1", "content_id": "another-content", "final_sha256": "0"*64,
                     "published_at": "2026-10-09T09:00:00+08:00"}]
        result = self.ledger.reconcile(observed)["results"][0]
        self.assertTrue(result["needs_review"])
        self.assertEqual(observed, result["platform_candidates"])
        self.assertEqual({"content_id", "final_sha256", "published_at"}, {c["field"] for c in result["conflicts"]})
        self.assertEqual(before, self.ledger.show("c1"))

    def test_publication_minute_precision_is_preserved_and_comparison_is_coarse(self):
        digest = self.ready(); self.ledger.prepare_publish("c1")
        item = self.ledger.publish("c1", "p1", "2026-10-08T09:00+08:00")
        self.assertEqual("minute", item["published_at_precision"])
        self.assertEqual("2026-10-08T09:00+08:00", item["published_at_original"])
        audit = self.ledger.show("c1")["audit"][-1]
        self.assertEqual("minute", json.loads(audit["detail"])["published_at_precision"])
        same_minute = [{"platform_content_id": "p1", "content_id": "c1", "final_sha256": digest,
                        "published_at": "2026-10-08T01:00:59Z"}]
        self.assertFalse(self.ledger.reconcile(same_minute)["results"][0]["needs_review"])
        same_minute[0]["published_at"] = "2026-10-08T01:01:00Z"
        self.assertTrue(self.ledger.reconcile(same_minute)["results"][0]["needs_review"])
        same_minute[0]["published_at"] = "2026-10-08"
        self.assertEqual([], self.ledger.reconcile(same_minute)["results"][0]["conflicts"])

    def test_invalid_path_and_naive_timestamps(self):
        self.error("INVALID_PATH", Ledger, self.work.parent / "outside.sqlite", self.work)
        self.error("INVALID_PATH", Ledger, ROOT / "ledger.sqlite", ROOT)
        request = self.item(); request["latest_material_at"] = "2026-10-08T09:00:00"
        self.error("INVALID_TIME", self.ledger.claim, request)

    def test_date_precision_is_preserved_and_resume_requires_fresh_run(self):
        request = self.item(); request["latest_material_at"] = "2026-10-08"
        claimed = self.ledger.claim(request)
        self.assertEqual("2026-10-08", claimed["latest_material_at"])
        self.assertEqual("date", claimed["latest_material_at_precision"])
        self.ledger.block("c1", "waiting for cover")
        self.error("NEW_RUN_REQUIRED", self.ledger.resume, "c1", "run1", "resume")
        resumed = self.ledger.resume("c1", "run2", "new gated run")
        self.assertEqual(["run1", "run2"], [r["run_id"] for r in resumed["run_history"]])

    def test_capacity_claim_block_and_resume_are_transactional(self):
        limit = Ledger(self.db, self.work, max_in_progress=1)
        try:
            limit.claim(self.item())
            self.error("CAPACITY_REACHED", limit.claim, self.item("c2", event="event-2"))
            limit.block("c1", "no usable cover")
            self.error("NO_NEW_FACTS", limit.claim, self.item("duplicate"))
            limit.claim(self.item("c2", event="event-2"))
            self.error("CAPACITY_REACHED", limit.resume, "c1", "run2", "cover available")
            self.assertEqual("BLOCKED", limit.show("c1")["status"])
            limit.block("c2", "pause")
            resumed = limit.resume("c1", "run2", "cover available")
            self.assertEqual(1, resumed["concurrency_policy"]["max_in_progress"])
            self.assertEqual("explicit_override", resumed["concurrency_policy"]["limit_source"])
        finally: limit.close()

    def test_illegal_transition_rolls_back_audit(self):
        self.ledger.claim(self.item()); before = self.ledger.show("c1")
        self.error("INVALID_STATE", self.ledger.qa_start, "c1")
        self.assertEqual(before, self.ledger.show("c1"))


if __name__ == "__main__":
    unittest.main()
