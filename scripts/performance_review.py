#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read a Life Account CSV snapshot without inferring fixed observation windows.

Default output is JSON on stdout. Files are written only with --output. Mapping
is explicit, by the original CSV physical record start line plus its SHA-256:
{
  "schema": "news-editor-performance-mapping/v1",
  "csv_sha256": "<original file hash>",
  "records": [{"csv_line_start": 2, "production_content_id": "content-01",
               "platform_content_id": "platform-01",
               "published_at": "2026-10-07T03:19:00+08:00",
               "video_duration_seconds": 14}]
}
csv_line_end is optional and, when supplied, must match the physical line range.
Dates, titles, row counts, and average watch time never establish an identity or
an exact publication timestamp. The script accesses no platform or network API.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import re
import statistics
import sys
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


FIELDS = (
    "内容标题", "发布时间", "内容类型", "浏览量", "5s完播率", "完播率",
    "人均浏览时长", "吸粉", "互动指数", "评论", "点赞", "收藏", "分享",
    "服务点击量", "商品点击量", "券点击量",
)
COUNT_FIELDS = {
    "浏览量": "views", "吸粉": "follows", "评论": "comments",
    "点赞": "likes", "收藏": "favorites", "分享": "shares",
    "服务点击量": "service_clicks", "商品点击量": "product_clicks",
    "券点击量": "coupon_clicks",
}
RATE_FIELDS = {"5s完播率": "five_second_completion", "完播率": "completion"}
INTERACTION_FIELDS = ("likes", "comments", "favorites", "shares")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$")
SCHEMA = "news-editor-performance-review/v1"
MAPPING_SCHEMA = "news-editor-performance-mapping/v1"


class ReviewError(ValueError):
    """Malformed input or incompatible explicitly supplied evidence."""


def _timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not TIMESTAMP_RE.fullmatch(value):
        raise ReviewError(f"{label} must be an ISO 8601 timestamp through seconds with explicit timezone")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ReviewError(f"{label} is not a valid ISO 8601 timestamp") from error
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ReviewError(f"{label} must include an explicit timezone")
    return stamp


def _publication(value: str, label: str) -> dict[str, Any]:
    if not value:
        raise ReviewError(f"{label} is required")
    if DATE_RE.fullmatch(value):
        try:
            day = date.fromisoformat(value)
        except ValueError as error:
            raise ReviewError(f"{label} is not a valid date") from error
        return {"value": value, "precision": "date", "date": day.isoformat()}
    stamp = _timestamp(value, label)
    return {"value": value, "precision": "timestamp", "date": stamp.date().isoformat()}


def _number(value: Any, label: str, *, optional: bool = False) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        if optional:
            return None
        raise ReviewError(f"{label} is required")
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ReviewError(f"{label} must be a finite non-negative number")
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation as error:
        raise ReviewError(f"{label} must be numeric") from error
    if not number.is_finite() or number < 0:
        raise ReviewError(f"{label} must be a finite non-negative number")
    converted = float(number)
    if not math.isfinite(converted):
        raise ReviewError(f"{label} exceeds the supported numeric range")
    return converted


def _count(value: str, label: str) -> int:
    if not re.fullmatch(r"\d+", value):
        raise ReviewError(f"{label} must be a non-negative integer")
    try:
        number = int(value)
    except ValueError as error:
        raise ReviewError(f"{label} must be a non-negative integer") from error
    if number > 2**53 - 1:
        raise ReviewError(f"{label} exceeds the exact JSON integer range")
    return number


def _rate(value: str, label: str) -> float | None:
    if not value:
        return None
    percent = value.endswith("%")
    number = _number(value[:-1] if percent else value, label)
    if percent:
        number /= 100
    if not 0 <= number <= 1:
        raise ReviewError(f"{label} must be in [0, 1], or an explicit percentage in [0%, 100%]")
    return number


def _quoted_field_padding(text: str) -> tuple[str, int]:
    """Accept the vendor's tab after a closing quote without relaxing CSV parsing.

    Only spaces/tabs between a genuine closing quote and a delimiter/end-of-line
    are removed. Quoted content, escaped quotes, and physical newlines stay intact;
    any other text after a closing quote is left for the strict reader to reject.
    """
    result = []
    removed = 0
    state = "start"
    index = 0
    while index < len(text):
        char = text[index]
        if state == "quoted" and char == '"':
            if index + 1 < len(text) and text[index + 1] == '"':
                result.extend(('"', '"'))
                index += 2
                continue
            state = "after_quote"
        elif state == "after_quote" and char in " \t":
            end = index
            while end < len(text) and text[end] in " \t":
                end += 1
            if end == len(text) or text[end] in ",\r\n":
                removed += end - index
                index = end
                continue
        elif state == "start":
            if char == '"':
                state = "quoted"
            elif char not in ",\r\n":
                state = "unquoted"
        if state != "quoted" and char in ",\r\n":
            state = "start"
        result.append(char)
        index += 1
    return "".join(result), removed


def _read_csv(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise ReviewError(f"cannot read CSV {path}: {error}") from error
    try:
        decoded = raw.decode("utf-8-sig")
        encoding = "UTF-8-BOM" if raw.startswith(b"\xef\xbb\xbf") else "UTF-8"
    except UnicodeDecodeError:
        try:
            decoded = raw.decode("gb18030")
            encoding = "GB18030"
        except UnicodeDecodeError as error:
            raise ReviewError("CSV must use UTF-8 (optional BOM) or GB18030") from error
    decoded, padding_removed = _quoted_field_padding(decoded)
    reader = csv.reader(io.StringIO(decoded, newline=""), strict=True)
    try:
        header = next(reader)
    except StopIteration as error:
        raise ReviewError("CSV is empty") from error
    except csv.Error as error:
        raise ReviewError(f"malformed CSV header: {error}") from error
    header = [cell.strip() for cell in header]
    if len(header) != len(set(header)):
        raise ReviewError("CSV has duplicate headers after whitespace normalization")
    missing = sorted(set(FIELDS) - set(header))
    if missing:
        raise ReviewError(f"CSV missing required fields: {', '.join(missing)}")
    rows = []
    while True:
        start = reader.line_num + 1
        try:
            cells = next(reader)
        except StopIteration:
            break
        except csv.Error as error:
            raise ReviewError(f"malformed CSV near physical line {start}: {error}") from error
        end = reader.line_num
        if not cells:
            continue
        if len(cells) != len(header):
            raise ReviewError(f"CSV lines {start}-{end}: expected {len(header)} fields, got {len(cells)}")
        values = {key: value.strip() for key, value in zip(header, cells)}
        label = f"CSV lines {start}-{end}"
        if not values["内容标题"] or not values["内容类型"]:
            raise ReviewError(f"{label}: 内容标题 and 内容类型 are required")
        counts = {key: _count(values[field], f"{label} {field}") for field, key in COUNT_FIELDS.items()}
        counts["interactions"] = sum(counts[key] for key in INTERACTION_FIELDS)
        rows.append({
            "source_record": {
                "record_number": len(rows) + 1,
                "csv_line_start": start, "csv_line_end": end,
            },
            "title": values["内容标题"], "content_type": values["内容类型"],
            "csv_publication": _publication(values["发布时间"], f"{label} 发布时间"),
            "counts": counts,
            "rates": {key: _rate(values[field], f"{label} {field}") for field, key in RATE_FIELDS.items()},
            "average_watch_seconds": _number(values["人均浏览时长"], f"{label} 人均浏览时长", optional=True),
            "reported_interaction_index": _number(values["互动指数"], f"{label} 互动指数", optional=True),
            "identity": {"platform_content_id": None, "production_content_id": None, "source": "unmapped"},
            "video_duration_seconds": None,
            "observation": {"published_at": None, "publication_source": "unknown", "age_hours": None, "age_status": "unknown"},
            "conditional_checks": [],
        })
    if not rows:
        raise ReviewError("CSV has no content records")
    if sum(row["counts"]["views"] for row in rows) > 2**53 - 1:
        raise ReviewError("total views exceed the exact JSON integer range")
    return rows, {
        "csv_path": str(path.resolve()), "csv_sha256": hashlib.sha256(raw).hexdigest(),
        "encoding": encoding, "headers": header,
        "quoted_field_padding_characters_removed": padding_removed,
        "record_row_scope": "all non-empty content records in this CSV; arbitrary N, not a fixed 60-record cohort",
        "record_count": len(rows),
        "scope": "CSV content-detail snapshot only; not the platform period dashboard or a production cohort",
    }


def _mapping(path: Path | None, source_hash: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    if path is None:
        return {"provided": False, "mapped_record_count": 0, "csv_sha256_verified": False}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReviewError(f"cannot read mapping JSON {path}: {error}") from error
    if not isinstance(data, dict) or data.get("schema") != MAPPING_SCHEMA:
        raise ReviewError(f"mapping schema must be {MAPPING_SCHEMA}")
    if data.get("csv_sha256") != source_hash:
        raise ReviewError("mapping csv_sha256 is required and must match the original CSV bytes")
    entries = data.get("records")
    if not isinstance(entries, list):
        raise ReviewError("mapping records must be an array")
    by_line = {row["source_record"]["csv_line_start"]: row for row in rows}
    seen = set()
    allowed = {"csv_line_start", "csv_line_end", "platform_content_id", "production_content_id", "published_at", "video_duration_seconds"}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) - allowed:
            raise ReviewError("mapping record must be an object using only documented fields")
        line = entry.get("csv_line_start")
        if isinstance(line, bool) or not isinstance(line, int) or line not in by_line:
            raise ReviewError("mapping csv_line_start must identify an original CSV record start line")
        if line in seen:
            raise ReviewError(f"duplicate mapping for CSV line {line}")
        seen.add(line)
        row = by_line[line]
        if "csv_line_end" in entry:
            line_end = entry["csv_line_end"]
            if isinstance(line_end, bool) or not isinstance(line_end, int) or line_end != row["source_record"]["csv_line_end"]:
                raise ReviewError(f"mapping csv_line_end does not match CSV line {line}")
        if not (set(entry) & {"platform_content_id", "production_content_id", "published_at", "video_duration_seconds"}):
            raise ReviewError(f"mapping CSV line {line} supplies no identity, timestamp, or duration evidence")
        for key in ("platform_content_id", "production_content_id"):
            if key in entry:
                if not isinstance(entry[key], str) or not entry[key].strip():
                    raise ReviewError(f"mapping {key} at CSV line {line} must be a non-empty string")
                row["identity"][key] = entry[key].strip()
        row["identity"]["source"] = "explicit_csv_line_mapping"
        if "published_at" in entry:
            stamp = _timestamp(entry["published_at"], f"mapping published_at at CSV line {line}")
            row["observation"]["published_at"] = stamp.isoformat()
            row["observation"]["publication_source"] = "explicit_csv_line_mapping"
        if "video_duration_seconds" in entry:
            duration = _number(entry["video_duration_seconds"], f"mapping video_duration_seconds at CSV line {line}")
            if duration <= 0:
                raise ReviewError(f"mapping video_duration_seconds at CSV line {line} must be positive")
            row["video_duration_seconds"] = duration
    return {"provided": True, "path": str(path.resolve()), "mapped_record_count": len(seen), "csv_sha256_verified": True}


def _per_thousand(count: int, views: int) -> float | None:
    return count * 1000 / views if views else None


def _retention(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    available = [row for row in rows if row["rates"][key] is not None]
    weight = sum(row["counts"]["views"] for row in available)
    return {
        "available_record_count": len(available), "missing_record_count": len(rows) - len(available),
        "unweighted_record_mean": statistics.mean(row["rates"][key] for row in available) if available else None,
        "views_weighted_record_mean_proxy": sum(row["rates"][key] * row["counts"]["views"] for row in available) / weight if weight else None,
        "proxy_weight_views": weight, "unit": "fraction from 0 to 1",
        "denominator_status": "unknown: platform rate denominators were not exported",
        "interpretation": "Both are summaries of reported record rates; the views-weighted proxy is not a real aggregate playback completion rate.",
    }


def review(csv_path: Path, observed_at: str | None = None, mapping_path: Path | None = None) -> dict[str, Any]:
    """Return an evidence-limited snapshot report; never write source or output files."""
    observed = _timestamp(observed_at, "observed_at") if observed_at is not None else None
    rows, source = _read_csv(csv_path)
    mapping = _mapping(mapping_path, source["csv_sha256"], rows)
    warnings = [{"code": "UNRECONCILED_SCOPE", "message": "This CSV snapshot is not reconciled with any platform period dashboard or production cohort; no period growth rate is calculated."}]
    ages = []
    for row in rows:
        observation = row["observation"]
        if observation["published_at"] is not None:
            published = _timestamp(observation["published_at"], "mapped published_at")
            if published.date().isoformat() != row["csv_publication"]["date"]:
                row["conditional_checks"].append({
                    "code": "PUBLICATION_DATE_MISMATCH", "status": "requires_context",
                    "message": "Mapped timestamp date differs from the CSV publication date; verify platform timezone, identity, and dates.",
                })
            if observed is not None:
                age = (observed - published).total_seconds() / 3600
                if age < 0:
                    raise ReviewError(f"observed_at precedes mapped published_at at CSV line {row['source_record']['csv_line_start']}")
                observation.update({"age_hours": age, "age_status": "known_snapshot_age"})
                ages.append(age)
        counts = row["counts"]
        row["per_1000_views"] = {key: _per_thousand(counts[key], counts["views"]) for key in (*INTERACTION_FIELDS, "interactions", "follows")}
        rates = row["rates"]
        if rates["completion"] is not None and rates["five_second_completion"] is not None and rates["completion"] > rates["five_second_completion"]:
            row["conditional_checks"].append({
                "code": "COMPLETION_ABOVE_FIVE_SECOND", "status": "requires_context",
                "video_duration_seconds": row["video_duration_seconds"],
                "message": "Not an automatic data error: check actual video duration, definitions, denominators, rounding, and observation windows; short videos may complete before five seconds.",
            })
    if observed is None:
        warnings.append({"code": "OBSERVATION_TIME_UNKNOWN", "message": "No observed_at was supplied; observation ages remain unknown. No retrieval time is invented."})
    if len(ages) < len(rows):
        warnings.append({"code": "PUBLICATION_AGE_INCOMPLETE", "message": "An age requires both explicit mapping publication timestamp and observed_at; dates, titles, and equal row counts do not provide that evidence."})
    if len(set(ages)) > 1:
        warnings.append({"code": "MIXED_OBSERVATION_AGES", "message": "Records have different ages at this snapshot; totals and comparisons do not represent matched 24-hour or 72-hour windows."})
    zero_index_nonzero = [row["source_record"] for row in rows if row["reported_interaction_index"] == 0 and row["counts"]["interactions"] > 0]
    if zero_index_nonzero:
        warnings.append({"code": "ROUNDED_ZERO_INTERACTION_INDEX", "record_count": len(zero_index_nonzero), "source_records": zero_index_nonzero, "message": "A reported zero interaction index does not mean zero interactions; use raw likes, comments, favorites, and shares."})
    if any(row["conditional_checks"] for row in rows):
        warnings.append({"code": "CONDITIONAL_CHECKS_PRESENT", "message": "Per-record checks need context; they are not automatic invalid-data verdicts."})
    totals = {key: sum(row["counts"][key] for row in rows) for key in rows[0]["counts"]}
    if any(total > 2**53 - 1 for total in totals.values()):
        raise ReviewError("aggregate counts exceed the exact JSON integer range")
    views = totals["views"]
    ranking = sorted(rows, key=lambda row: row["counts"]["views"], reverse=True)
    concentrations = []
    for limit in (3, 5, 10):
        top = ranking[:limit]
        top_views = sum(row["counts"]["views"] for row in top)
        concentrations.append({"requested_top_n": limit, "included_record_count": len(top), "views": top_views, "share_of_csv_views": top_views / views if views else None, "source_records": [row["source_record"] for row in top]})
    clicks_zero = all(totals[key] == 0 for key in ("service_clicks", "product_clicks", "coupon_clicks"))
    if clicks_zero:
        warnings.append({"code": "ZERO_CLICKS_CONVERSION_UNKNOWN", "message": "All exported click counts are zero; mounting status, click instrumentation, eligible impressions, and transaction data are unknown. This does not establish zero commercial conversion."})
    return {
        "schema": SCHEMA, "source": source, "mapping": mapping,
        "observation": {
            "observed_at": observed.isoformat() if observed else None,
            "status": "explicit_timestamp" if observed else "unknown",
            "snapshot_not_fixed_window": True,
            "known_age_record_count": len(ages), "unknown_age_record_count": len(rows) - len(ages),
            "min_age_hours": min(ages) if ages else None, "max_age_hours": max(ages) if ages else None,
            "mixed_ages": len(set(ages)) > 1,
        },
        "metrics": {
            "raw_counts": totals,
            "views_per_record_mean": views / len(rows),
            "views_per_record_median": statistics.median(row["counts"]["views"] for row in rows),
            "head_concentration": concentrations,
            "per_1000_csv_views": {key: _per_thousand(totals[key], views) for key in (*INTERACTION_FIELDS, "interactions", "follows")},
            "interaction_formula": "likes + comments + favorites + shares; counts are actions, not unique people",
            "retention": {key: _retention(rows, key) for key in RATE_FIELDS.values()},
            "reported_zero_index_with_nonzero_interactions": len(zero_index_nonzero),
            "commercial": {"all_exported_click_counts_zero": clicks_zero, "mounting_status": "unknown", "instrumentation_status": "unknown", "transaction_count": None, "conversion_rate": None, "status": "not_determined_from_this_csv"},
        },
        "limitations": [
            "No titles are matched automatically. Equal content counts never identify production and publication records.",
            "Average watch seconds are not total video duration and may include repeat viewing.",
            "A snapshot age is not a fixed 24-hour/72-hour result; cumulative counts cannot be split into those windows without historical snapshots.",
            "Missing reported rates stay null and are excluded, not converted to zero, in rate summaries.",
            "No exposure or playback denominators, historical comparator, restrictions, transaction evidence, or platform dashboard are supplied; no causal effect, period growth, or commercial conversion is inferred.",
        ],
        "warnings": warnings, "records": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv", required=True, type=Path, help="Original UTF-8/BOM or GB18030 CSV (read-only)")
    parser.add_argument("--observed-at", help="Actual snapshot timestamp, ISO 8601 with timezone; omitted means unknown")
    parser.add_argument("--mapping", type=Path, help="Explicit physical CSV line mapping JSON with matching csv_sha256")
    parser.add_argument("--output", type=Path, help="Explicit JSON destination; omitted writes only stdout")
    args = parser.parse_args(argv)
    try:
        if args.output is not None:
            inputs = {args.csv.resolve()}
            if args.mapping is not None:
                inputs.add(args.mapping.resolve())
            if args.output.resolve() in inputs or (
                args.output.exists() and any(args.output.samefile(path) for path in inputs)
            ):
                raise ReviewError("--output must not overwrite the CSV or mapping input")
        result = review(args.csv, args.observed_at, args.mapping)
        rendered = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.output is not None:
            args.output.write_text(rendered, encoding="utf-8")
        else:
            sys.stdout.write(rendered)
    except (ReviewError, OSError) as error:
        sys.stderr.write(f"performance review failed: {error}\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
