#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Boundary tests for read-only CSV performance review and explicit mapping."""

from __future__ import annotations

import contextlib
import csv
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

try:
    import performance_review as performance
except ModuleNotFoundError:
    from scripts import performance_review as performance


class PerformanceReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="news-editor-performance-")
        self.work = Path(self.temp.name)
        self.csv = self.work / "original.csv"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _row(self, **updates: str) -> dict[str, str]:
        row = {
            "内容标题": "测试新闻", "发布时间": "2026-10-01", "内容类型": "短视频",
            "浏览量": "100", "5s完播率": "0.20", "完播率": "0.10",
            "人均浏览时长": "4.51", "吸粉": "1", "互动指数": "0.0",
            "评论": "2", "点赞": "3", "收藏": "4", "分享": "5",
            "服务点击量": "0", "商品点击量": "0", "券点击量": "0",
        }
        row.update(updates)
        return row

    def _csv(self, rows: list[dict[str, str]], encoding: str = "utf-8-sig", headers: list[str] | None = None) -> None:
        with self.csv.open("w", encoding=encoding, newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=headers or list(performance.FIELDS), extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    def _mapping(self, entries: list[dict], **updates) -> Path:
        data = {
            "schema": performance.MAPPING_SCHEMA,
            "csv_sha256": hashlib.sha256(self.csv.read_bytes()).hexdigest(),
            "records": entries,
        }
        data.update(updates)
        path = self.work / "mapping.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return path

    @staticmethod
    def _codes(result: dict) -> set[str]:
        return {warning["code"] for warning in result["warnings"]}

    def test_raw_counts_and_per_thousand_not_rounded_index(self) -> None:
        self._csv([self._row(), self._row(浏览量="300", 点赞="7")])
        result = performance.review(self.csv)
        metrics = result["metrics"]
        self.assertEqual(metrics["raw_counts"]["views"], 400)
        self.assertEqual(metrics["views_per_record_median"], 200)
        self.assertEqual(metrics["raw_counts"]["interactions"], 32)
        self.assertEqual(metrics["per_1000_csv_views"]["interactions"], 80)
        self.assertEqual(metrics["reported_zero_index_with_nonzero_interactions"], 2)
        self.assertIn("ROUNDED_ZERO_INTERACTION_INDEX", self._codes(result))

    def test_arbitrary_record_count_and_head_share(self) -> None:
        self._csv([self._row(浏览量="100"), self._row(浏览量="50"), self._row(浏览量="0"), self._row(浏览量="50")])
        result = performance.review(self.csv)
        self.assertEqual(result["source"]["record_count"], 4)
        top3, top5, _ = result["metrics"]["head_concentration"]
        self.assertEqual(top3["share_of_csv_views"], 1)
        self.assertEqual(top5["included_record_count"], 4)
        self.assertEqual(top5["requested_top_n"], 5)

    def test_same_title_and_sixty_rows_do_not_map(self) -> None:
        self._csv([self._row()] * 60)
        result = performance.review(self.csv)
        self.assertEqual(result["source"]["record_count"], 60)
        self.assertTrue(all(row["identity"]["production_content_id"] is None for row in result["records"]))
        self.assertTrue(all(row["identity"]["platform_content_id"] is None for row in result["records"]))
        self.assertEqual(result["mapping"]["mapped_record_count"], 0)

    def test_utf8_bom_and_gb18030(self) -> None:
        for encoding, expected in (("utf-8-sig", "UTF-8-BOM"), ("utf-8", "UTF-8"), ("gb18030", "GB18030")):
            with self.subTest(encoding=encoding):
                self._csv([self._row(内容标题="蹭网被扣钱，3笔扣850元")], encoding=encoding)
                result = performance.review(self.csv)
                self.assertEqual(result["source"]["encoding"], expected)
                self.assertEqual(result["records"][0]["title"], "蹭网被扣钱，3笔扣850元")

    def test_multiline_csv_record_keeps_physical_line_range(self) -> None:
        self._csv([self._row(内容标题="第一行\n第二行"), self._row(内容标题="独立记录")])
        mapping = self._mapping([{"csv_line_start": 2, "csv_line_end": 3, "production_content_id": "production-a"}])
        result = performance.review(self.csv, mapping_path=mapping)
        first, second = result["records"]
        self.assertEqual(first["source_record"]["csv_line_start"], 2)
        self.assertEqual(first["source_record"]["csv_line_end"], 3)
        self.assertEqual(second["source_record"]["csv_line_start"], 4)
        self.assertEqual(first["identity"]["production_content_id"], "production-a")
        self.assertIsNone(second["identity"]["production_content_id"])
        bad = self._mapping([{"csv_line_start": 3, "production_content_id": "wrong"}])
        with self.assertRaisesRegex(performance.ReviewError, "record start line"):
            performance.review(self.csv, mapping_path=bad)

    def test_vendor_tabs_after_quoted_fields_without_relaxing_bad_quotes(self) -> None:
        self._csv([self._row(内容标题='第一行\n含"引号"与\t制表符')])
        original = self.csv.read_text(encoding="utf-8-sig")
        # Produce the vendor export convention: all fields quoted, then a tab.
        output = io.StringIO(newline="")
        writer = csv.writer(output, quoting=csv.QUOTE_ALL)
        writer.writerow(performance.FIELDS)
        writer.writerow([self._row(内容标题='第一行\n含"引号"与\t制表符')[key] for key in performance.FIELDS])
        text, _ = performance._quoted_field_padding(output.getvalue())
        # Replace only the last quote before delimiters, not escaped quotes.
        text = text.replace('",', '"\t,').replace('"\r\n', '"\t\r\n')
        self.csv.write_bytes(text.encode("gb18030"))
        result = performance.review(self.csv)
        self.assertEqual(result["records"][0]["title"], '第一行\n含"引号"与\t制表符')
        self.assertEqual(result["records"][0]["source_record"]["csv_line_end"], 3)
        self.assertGreater(result["source"]["quoted_field_padding_characters_removed"], 0)
        self.csv.write_text(original.replace('",', '"bad,', 1), encoding="utf-8")
        with self.assertRaises(performance.ReviewError):
            performance.review(self.csv)

    def test_dates_stay_dates_and_missing_observed_at_stays_unknown(self) -> None:
        self._csv([self._row()])
        result = performance.review(self.csv)
        row = result["records"][0]
        self.assertEqual(row["csv_publication"]["precision"], "date")
        self.assertIsNone(row["observation"]["published_at"])
        self.assertIsNone(row["observation"]["age_hours"])
        self.assertIsNone(result["observation"]["observed_at"])
        self.assertTrue(result["observation"]["snapshot_not_fixed_window"])
        self.assertIn("OBSERVATION_TIME_UNKNOWN", self._codes(result))
        result = performance.review(self.csv, "2026-10-08T12:00:00+08:00")
        self.assertIsNone(result["records"][0]["observation"]["age_hours"])

    def test_mapped_time_and_mixed_ages_are_snapshots_only(self) -> None:
        self._csv([self._row(), self._row(发布时间="2026-10-02")])
        mapping = self._mapping([
            {"csv_line_start": 2, "published_at": "2026-10-01T12:00:00+08:00", "video_duration_seconds": 14, "platform_content_id": "platform-a"},
            {"csv_line_start": 3, "published_at": "2026-10-02T12:00:00+08:00", "production_content_id": "production-b"},
        ])
        result = performance.review(self.csv, "2026-10-03T12:00:00+08:00", mapping)
        self.assertEqual(result["records"][0]["observation"]["age_hours"], 48)
        self.assertEqual(result["records"][1]["observation"]["age_hours"], 24)
        self.assertEqual(result["observation"]["min_age_hours"], 24)
        self.assertEqual(result["observation"]["max_age_hours"], 48)
        self.assertTrue(result["observation"]["snapshot_not_fixed_window"])
        self.assertIn("MIXED_OBSERVATION_AGES", self._codes(result))
        self.assertNotIn("24h_views", result["metrics"])
        self.assertNotIn("period_growth", result["metrics"])

    def test_mapping_timestamp_without_observation_has_no_age(self) -> None:
        self._csv([self._row()])
        mapping = self._mapping([{"csv_line_start": 2, "published_at": "2026-10-01T12:00:00+08:00"}])
        result = performance.review(self.csv, mapping_path=mapping)
        self.assertIsNotNone(result["records"][0]["observation"]["published_at"])
        self.assertIsNone(result["records"][0]["observation"]["age_hours"])

    def test_timestamp_requires_timezone_and_seconds_not_invented(self) -> None:
        self._csv([self._row()])
        for value in ("2026-10-08", "2026-10-08T12:00:00", "2026-10-08T12+08:00", "2026-10-08T12:00+08:00", "2026-99-08T12:00:00+08:00"):
            with self.subTest(value=value), self.assertRaises(performance.ReviewError):
                performance.review(self.csv, value)
        result = performance.review(self.csv, "2026-10-08T04:00:00Z")
        self.assertEqual(result["observation"]["status"], "explicit_timestamp")

    def test_mapping_requires_original_csv_hash_and_no_title_matching(self) -> None:
        self._csv([self._row()])
        for updates in ({"csv_sha256": "0" * 64}, {"csv_sha256": None}):
            mapping = self._mapping([{"csv_line_start": 2, "production_content_id": "known"}], **updates)
            with self.assertRaisesRegex(performance.ReviewError, "csv_sha256"):
                performance.review(self.csv, mapping_path=mapping)
        mapping = self._mapping([{"title": "测试新闻", "production_content_id": "guessed"}])
        with self.assertRaisesRegex(performance.ReviewError, "documented fields"):
            performance.review(self.csv, mapping_path=mapping)

    def test_mapping_rejects_duplicate_unknown_line_bad_duration_and_naive_time(self) -> None:
        self._csv([self._row()])
        cases = [
            [{"csv_line_start": 2, "production_content_id": "a"}, {"csv_line_start": 2, "production_content_id": "b"}],
            [{"csv_line_start": 999, "production_content_id": "a"}],
            [{"csv_line_start": 2, "csv_line_end": 99, "production_content_id": "a"}],
            [{"csv_line_start": 2, "video_duration_seconds": 0}],
            [{"csv_line_start": 2, "video_duration_seconds": -1}],
            [{"csv_line_start": 2, "video_duration_seconds": True}],
            [{"csv_line_start": 2, "published_at": "2026-10-01T12:00:00"}],
            [{"csv_line_start": 2, "production_content_id": ""}],
        ]
        for entries in cases:
            with self.subTest(entries=entries), self.assertRaises(performance.ReviewError):
                performance.review(self.csv, mapping_path=self._mapping(entries))

    def test_future_mapped_publication_fails_and_date_disagreement_needs_review(self) -> None:
        self._csv([self._row()])
        mapping = self._mapping([{"csv_line_start": 2, "published_at": "2026-10-02T12:00:00+08:00"}])
        with self.assertRaisesRegex(performance.ReviewError, "precedes"):
            performance.review(self.csv, "2026-10-01T12:00:00+08:00", mapping)
        result = performance.review(self.csv, "2026-10-03T12:00:00+08:00", mapping)
        self.assertEqual(result["records"][0]["conditional_checks"][0]["code"], "PUBLICATION_DATE_MISMATCH")

    def test_average_watch_time_is_never_video_length(self) -> None:
        self._csv([self._row(人均浏览时长="14.26")])
        row = performance.review(self.csv)["records"][0]
        self.assertEqual(row["average_watch_seconds"], 14.26)
        self.assertIsNone(row["video_duration_seconds"])

    def test_retention_missing_values_not_zero_and_weighted_proxy_is_labelled(self) -> None:
        self._csv([
            self._row(浏览量="100", 完播率="0.1", **{"5s完播率": ""}),
            self._row(浏览量="300", 完播率="0.3", **{"5s完播率": "20%"}),
            self._row(浏览量="600", 完播率="", **{"5s完播率": ""}),
        ])
        result = performance.review(self.csv)
        summary = result["metrics"]["retention"]["completion"]
        self.assertEqual(summary["available_record_count"], 2)
        self.assertEqual(summary["missing_record_count"], 1)
        self.assertAlmostEqual(summary["unweighted_record_mean"], 0.2)
        self.assertAlmostEqual(summary["views_weighted_record_mean_proxy"], 0.25)
        self.assertEqual(summary["proxy_weight_views"], 400)
        self.assertIn("not a real", summary["interpretation"])
        self.assertIsNone(result["records"][2]["rates"]["completion"])

    def test_completion_above_five_seconds_is_only_conditional(self) -> None:
        self._csv([self._row(完播率="0.32", **{"5s完播率": "0.28"})])
        for duration in (None, 4, 14):
            mapping = self._mapping([{"csv_line_start": 2, "video_duration_seconds": duration}]) if duration else None
            result = performance.review(self.csv, mapping_path=mapping)
            check = result["records"][0]["conditional_checks"][0]
            self.assertEqual(check["code"], "COMPLETION_ABOVE_FIVE_SECOND")
            self.assertEqual(check["status"], "requires_context")
            self.assertIn("Not an automatic data error", check["message"])

    def test_zero_views_and_all_missing_rates_are_safe(self) -> None:
        self._csv([self._row(浏览量="0", 完播率="", **{"5s完播率": ""})])
        result = performance.review(self.csv)
        self.assertIsNone(result["metrics"]["per_1000_csv_views"]["interactions"])
        self.assertIsNone(result["metrics"]["head_concentration"][0]["share_of_csv_views"])
        self.assertIsNone(result["metrics"]["retention"]["completion"]["unweighted_record_mean"])
        self.assertIsNone(result["metrics"]["retention"]["completion"]["views_weighted_record_mean_proxy"])
        json.dumps(result, allow_nan=False)

    def test_zero_clicks_are_not_zero_conversion(self) -> None:
        self._csv([self._row()])
        result = performance.review(self.csv)
        commercial = result["metrics"]["commercial"]
        self.assertTrue(commercial["all_exported_click_counts_zero"])
        self.assertIsNone(commercial["conversion_rate"])
        self.assertEqual(commercial["mounting_status"], "unknown")
        self.assertIn("ZERO_CLICKS_CONVERSION_UNKNOWN", self._codes(result))

    def test_counts_rates_and_numeric_fields_reject_invalid_values(self) -> None:
        cases = [
            {"浏览量": "-1"}, {"浏览量": "1.5"}, {"浏览量": ""},
            {"浏览量": str(2**53)}, {"分享": "-2"}, {"完播率": "1.01"},
            {"完播率": "101%"}, {"完播率": "-1%"}, {"完播率": "NaN"},
            {"完播率": "Infinity"}, {"完播率": "%"}, {"人均浏览时长": "-1"},
            {"互动指数": "NaN"}, {"发布时间": "2026-02-30"},
            {"内容标题": ""}, {"内容类型": ""},
        ]
        for updates in cases:
            with self.subTest(updates=updates):
                self._csv([self._row(**updates)])
                with self.assertRaises(performance.ReviewError):
                    performance.review(self.csv)

    def test_missing_header_duplicate_header_wrong_width_empty_and_bad_quote(self) -> None:
        self._csv([self._row()], headers=list(performance.FIELDS)[:-1])
        with self.assertRaisesRegex(performance.ReviewError, "missing required fields"):
            performance.review(self.csv)
        self._csv([self._row()])
        good = self.csv.read_text(encoding="utf-8-sig")
        for value in ("", good.splitlines()[0] + "\n", good.splitlines()[0] + '\n"unclosed', good + "bad,row\n", good.replace("内容标题", "浏览量", 1)):
            with self.subTest(value=value[:30]):
                self.csv.write_text(value, encoding="utf-8")
                with self.assertRaises(performance.ReviewError):
                    performance.review(self.csv)

    def test_stdout_default_does_not_write_or_mutate_sources(self) -> None:
        self._csv([self._row()])
        before = self.csv.read_bytes()
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            status = performance.main(["--csv", str(self.csv)])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(stdout.getvalue())["schema"], performance.SCHEMA)
        self.assertEqual(list(self.work.iterdir()), [self.csv])
        self.assertEqual(self.csv.read_bytes(), before)

    def test_explicit_output_and_input_overwrite_rejected_including_hardlink(self) -> None:
        self._csv([self._row()])
        before = self.csv.read_bytes()
        output = self.work / "review.json"
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(performance.main(["--csv", str(self.csv), "--output", str(output)]), 0)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(json.loads(output.read_text())["source"]["record_count"], 1)
        aliases = [self.csv]
        link = self.work / "hardlink.csv"
        os.link(self.csv, link)
        aliases.append(link)
        for path in aliases:
            with self.subTest(path=path), contextlib.redirect_stderr(io.StringIO()) as error:
                self.assertEqual(performance.main(["--csv", str(self.csv), "--output", str(path)]), 2)
                self.assertIn("must not overwrite", error.getvalue())
        self.assertEqual(self.csv.read_bytes(), before)

    def test_cli_failure_is_explicit_and_writes_no_output(self) -> None:
        self._csv([self._row(浏览量="-2")])
        output = self.work / "failed.json"
        with contextlib.redirect_stderr(io.StringIO()) as error:
            status = performance.main(["--csv", str(self.csv), "--output", str(output)])
        self.assertEqual(status, 2)
        self.assertIn("performance review failed", error.getvalue())
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
