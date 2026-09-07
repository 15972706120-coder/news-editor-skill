#!/usr/bin/env python3
"""Probe a news video, validate delivery specs, and optionally extract QA frames."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.json"


def run(command: list[str], timeout: float = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, check=True, capture_output=True, text=True, encoding="utf-8", timeout=timeout
    )


def locate(explicit: str | None, name: str) -> str:
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise FileNotFoundError(f"{name} not found: {path}")
        return str(path)
    found = shutil.which(name)
    if not found:
        raise FileNotFoundError(f"{name} is not on PATH; pass --{name}")
    return found


def parse_fps(value: str | None) -> float | None:
    if not value or value == "0/0":
        return None
    try:
        parsed = float(Fraction(value))
        return parsed if math.isfinite(parsed) and parsed > 0 else None
    except (ValueError, ZeroDivisionError):
        return None


def parse_float(value: object) -> float | None:
    if value is None or value == "N/A":
        return None
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def parse_int(value: object) -> int | None:
    if value is None or value == "N/A":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def check(name: str, actual: object, expected: object, passed: bool) -> dict[str, object]:
    return {"name": name, "actual": actual, "expected": expected, "passed": passed}


def measure_loudness(ffmpeg: str, source: Path, timeout: float = 120) -> dict[str, float | None]:
    measured = run(
        [
            ffmpeg,
            "-hide_banner",
            "-nostats",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-vn",
            "-af",
            "loudnorm=I=-16:TP=-3:LRA=7:print_format=json",
            "-f",
            "null",
            "-",
        ],
        timeout=timeout,
    )
    matches = re.findall(r'\{\s*"input_i"[\s\S]*?\}', measured.stderr)
    if not matches:
        raise RuntimeError("unable to parse loudnorm analysis")
    raw = json.loads(matches[-1])
    return {
        "integrated_lufs": parse_float(raw["input_i"]),
        "true_peak_dbtp": parse_float(raw["input_tp"]),
        "loudness_range_lu": parse_float(raw["input_lra"]),
        "threshold_lufs": parse_float(raw["input_thresh"]),
    }


def automatic_qa_frames(
    page_frame_counts: list[int],
    cover_frames: int,
    expected_frames: int,
) -> list[int]:
    selected = {0, 1}
    start = cover_frames
    for page_index, page_frames in enumerate(page_frame_counts):
        end = start + page_frames
        selected.update({start, start + page_frames // 2, end - 1})
        if page_index > 0:
            selected.update({start - 1, start})
        start = end
    selected.update(range(max(0, expected_frames - 3), expected_frames))
    return sorted(frame for frame in selected if 0 <= frame < expected_frames)


def validate_timeline_schema(data: object) -> dict:
    """Validate global, half-open frame intervals without inferring missing frames."""
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ValueError("timeline schema_version must be 1")
    fps = data.get("fps")
    if isinstance(fps, bool) or not isinstance(fps, (int, float)) or not math.isfinite(fps) or fps <= 0:
        raise ValueError("timeline fps must be a finite positive number")
    if type(data.get("cover_frames")) is not int or data["cover_frames"] != 1:
        raise ValueError("timeline cover_frames must be 1")
    if type(data.get("total_frames")) is not int or data["total_frames"] <= 1:
        raise ValueError("timeline total_frames must be an integer greater than 1")
    pages = data.get("pages")
    if not isinstance(pages, list) or not pages:
        raise ValueError("timeline pages must be a nonempty list")
    cursor = data["cover_frames"]
    identifiers = set()
    for page in pages:
        if not isinstance(page, dict):
            raise ValueError("timeline page must be an object")
        identifier = page.get("id")
        if not isinstance(identifier, str) or not identifier.strip() or identifier in identifiers:
            raise ValueError("timeline page ids must be nonempty unique strings")
        identifiers.add(identifier)
        start, end = page.get("start_frame"), page.get("end_frame")
        if type(start) is not int or type(end) is not int or start != cursor or end <= start:
            raise ValueError("timeline pages must be contiguous positive global half-open frame intervals")
        cursor = end
    if cursor != data["total_frames"]:
        raise ValueError("timeline pages plus cover must exactly equal total_frames")
    return data


def timestamp_checks(frames: list[dict], fps: float | None, time_base: str | None) -> list[dict]:
    """Check real presentation timestamps; best-effort timestamps must not mask missing PTS."""
    pts = [parse_float(frame.get("pts_time")) for frame in frames]
    complete = bool(pts) and all(value is not None for value in pts)
    result = [check("frame_pts_complete", sum(value is not None for value in pts), len(pts), complete)]
    if not complete or fps is None:
        result.append(check("frame_pts_cfr", None, "complete PTS and a positive frame rate", False))
        return result
    interval = 1.0 / fps
    tick = parse_fps(time_base) or 0.000001
    tolerance = min(max(0.000001, 2 * tick), interval * 0.05)
    deltas = [right - left for left, right in zip(pts, pts[1:])]
    bad_deltas = [index + 1 for index, delta in enumerate(deltas) if abs(delta - interval) > tolerance]
    bad_grid = [index for index, value in enumerate(pts) if abs(value - index * interval) > tolerance]
    result.extend([
        check("frame_pts_start_zero", pts[0], f"0 +/- {tolerance}", abs(pts[0]) <= tolerance),
        check("frame_pts_strictly_increasing", min(deltas) if deltas else None, "> 0", all(delta > 0 for delta in deltas)),
        check("frame_pts_cfr_interval", {"bad_frame_count": len(bad_deltas), "first_bad_frames": bad_deltas[:20]},
              {"interval_seconds": interval, "tolerance_seconds": tolerance}, not bad_deltas),
        check("frame_pts_timeline_grid", {"bad_frame_count": len(bad_grid), "first_bad_frames": bad_grid[:20], "last_pts": pts[-1]},
              {"pts_seconds": "frame_index / fps", "tolerance_seconds": tolerance}, not bad_grid),
    ])
    return result


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def extract_qa_frames(ffmpeg: str, source: Path, qa_dir: Path, frame_numbers: list[int],
                      actual_frames: int, timeout: float) -> tuple[list[str], list[int]]:
    """Decode once using select; unique temporary outputs cannot reuse stale QA images."""
    qa_dir.mkdir(parents=True, exist_ok=True)
    valid = [number for number in frame_numbers if number < actual_frames]
    missing = [number for number in frame_numbers if number >= actual_frames]
    extracted = []
    if not valid:
        return extracted, missing
    with tempfile.TemporaryDirectory(prefix="qa-extract-", dir=qa_dir) as temporary:
        staging = Path(temporary)
        selection = "+".join(f"eq(n\\,{number})" for number in valid)
        run([ffmpeg, "-hide_banner", "-loglevel", "error", "-xerror", "-i", str(source),
             "-map", "0:v:0", "-an", "-vf", f"select={selection}", "-frames:v", str(len(valid)),
             "-fps_mode", "passthrough", "-start_number", "0", str(staging / "frame-%06d.png")], timeout=timeout)
        for index, frame_number in enumerate(valid):
            staged = staging / f"frame-{index:06d}.png"
            if not staged.is_file():
                missing.append(frame_number)
                continue
            target = qa_dir / f"frame-{frame_number:06d}.png"
            staged.replace(target)
            extracted.append(str(target))
    return extracted, sorted(missing)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--ffprobe")
    parser.add_argument("--ffmpeg")
    parser.add_argument("--timeout", type=float, default=120, help="timeout in seconds for each media subprocess")
    parser.add_argument("--timeline", type=Path, help="production timeline JSON; enables all production machine checks")
    parser.add_argument('--assembly-report',type=Path,help='Required with --timeline; binds this MP4 to the checked render and mix')
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--expected-video-codec")
    parser.add_argument("--expected-audio-codec")
    parser.add_argument("--expected-pixel-format")
    parser.add_argument("--expected-duration", type=float)
    parser.add_argument("--duration-tolerance", type=float, default=0.05)
    parser.add_argument("--expected-width", type=int)
    parser.add_argument("--expected-height", type=int)
    parser.add_argument("--expected-fps", type=float)
    parser.add_argument("--fps-tolerance", type=float, default=0.01)
    parser.add_argument("--expected-pages", type=int)
    parser.add_argument("--expected-frames", type=int)
    parser.add_argument(
        "--page-frame-counts",
        help="comma-separated frame count for each content page, for example 210,209",
    )
    parser.add_argument("--cover-frames", type=int, default=1)
    parser.add_argument(
        "--seconds-per-page",
        type=float,
        default=4.0,
        help="legacy uniform page duration; prefer --page-frame-counts for new projects",
    )
    parser.add_argument("--require-audio", action="store_true")
    parser.add_argument("--expected-audio-sample-rate", type=int)
    parser.add_argument("--expected-audio-channels", type=int)
    parser.add_argument(
        "--av-duration-tolerance",
        type=float,
        help="maximum audio/video stream duration difference; defaults to one video frame",
    )
    parser.add_argument("--check-loudness", action="store_true")
    parser.add_argument("--loudness-min", type=float, default=-17.0)
    parser.add_argument("--loudness-max", type=float, default=-15.0)
    parser.add_argument("--max-true-peak", type=float, default=-3.0)
    parser.add_argument("--qa-dir", type=Path)
    parser.add_argument("--qa-frames", default="0,1", help="comma-separated frame numbers or 'auto'")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)

    expectation_names = ["timeline", "expected_duration", "expected_width", "expected_height", "expected_fps",
                         "expected_pages", "expected_frames", "page_frame_counts", "require_audio", "check_loudness",
                         "expected_audio_sample_rate", "expected_audio_channels", "expected_video_codec",
                         "expected_audio_codec", "expected_pixel_format"]
    if not any(getattr(args, name) is not None and getattr(args, name) is not False for name in expectation_names):
        parser.error("no validation expectations: provide --timeline or at least one explicit expected specification")
    if not math.isfinite(args.timeout) or not 0 < args.timeout <= 3600:
        parser.error("--timeout must be finite and between 0 and 3600 seconds")
    for name in ("expected_duration", "expected_fps", "seconds_per_page", "duration_tolerance", "fps_tolerance",
                 "av_duration_tolerance", "expected_width", "expected_height", "expected_frames", "expected_pages",
                 "expected_audio_sample_rate", "expected_audio_channels"):
        value = getattr(args, name)
        if value is not None and (not math.isfinite(value) or value <= 0):
            parser.error(f"--{name.replace('_', '-')} must be finite and positive")
    if args.cover_frames < 0:
        parser.error("--cover-frames cannot be negative")

    timeline = None
    if args.timeline:
        if not args.assembly_report:
            parser.error('--timeline requires --assembly-report from assemble_news_video.py')
        if args.page_frame_counts is not None:
            parser.error("--timeline cannot be combined with --page-frame-counts; use one frame source")
        try:
            from production_contract import load_timeline
            timeline = validate_timeline_schema(load_timeline(args.timeline, verify_assets=True))
            config = json.loads(args.config.read_text(encoding="utf-8-sig"))
            production = {
                "expected_fps": timeline["fps"], "expected_frames": timeline["total_frames"],
                "expected_duration": timeline["total_frames"] / timeline["fps"],
                "expected_pages": len(timeline["pages"]), "cover_frames": timeline["cover_frames"],
                "expected_width": config["video"]["width"], "expected_height": config["video"]["height"],
                "expected_video_codec": config["video"].get("codec", "h264"),
                "expected_pixel_format": config["video"]["pixel_format"],
                "expected_audio_codec": config["mix"]["final_format"]["codec"],
                "expected_audio_sample_rate": config["mix"]["final_format"]["sample_rate_hz"],
                "expected_audio_channels": config["mix"]["final_format"]["channels"],
            }
            for name, value in production.items():
                explicit = getattr(args, name)
                if explicit is not None and explicit != value:
                    parser.error(f"--{name.replace('_', '-')} conflicts with the production timeline/config")
                setattr(args, name, value)
            args.require_audio = True
            args.check_loudness = True
            args.loudness_min, args.loudness_max = config["mix"]["integrated_lufs_range"]
            args.max_true_peak = config["mix"]["max_true_peak_dbtp"]
            args.duration_tolerance = min(args.duration_tolerance, 1.0 / timeline["fps"])
        except (OSError, ValueError, KeyError, TypeError, ImportError) as exc:
            parser.error(f"invalid production timeline/config: {exc}")

    source = args.input.resolve()
    if not source.is_file():
        parser.error(f"input file does not exist: {source}")
    if args.report and args.report.resolve() in {source, args.config.resolve(), args.timeline.resolve() if args.timeline else source}:
        parser.error("--report must not overwrite the input, timeline, or config")
    source_hash = sha256_file(source)
    assembly = None
    if timeline:
        assembly = json.loads(args.assembly_report.read_text(encoding='utf-8'))
        if assembly.get('output_sha256') != source_hash or assembly.get('timeline_sha256') != sha256_file(args.timeline):
            parser.error('Assembly evidence belongs to another MP4 or timeline')
        for key in ('mix_report','render_report'):
            if sha256_file(Path(assembly[key])) != assembly[key+'_sha256']:
                parser.error('Assembly source report changed: '+key)

    ffprobe = locate(args.ffprobe, "ffprobe")
    probe = run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_frames",
            "-show_entries",
            "frame=media_type,stream_index,pts_time:stream:format",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(source),
        ],
        timeout=args.timeout,
    )
    data = json.loads(probe.stdout)
    video_streams = [s for s in data.get("streams", []) if s.get("codec_type") == "video"]
    audio_streams = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
    if not video_streams:
        raise RuntimeError("no video stream found")

    video = video_streams[0]
    container_duration = float(data.get("format", {}).get("duration", 0.0))
    video_duration = parse_float(video.get("duration"))
    fps = parse_fps(video.get("avg_frame_rate") or video.get("r_frame_rate"))
    decoded_frames = [frame for frame in data.get("frames", [])
                      if frame.get("media_type") == "video" and frame.get("stream_index") == video.get("index")]
    actual_frames = len(decoded_frames)
    checks: list[dict[str, object]] = [
        check("video_stream_count", len(video_streams), 1, len(video_streams) == 1),
        check("audio_stream_count", len(audio_streams), "<=1", len(audio_streams) <= 1),
        check("decode_errors", probe.stderr.strip(), "", not probe.stderr.strip()),
        check("decoded_video_present", actual_frames, ">0", actual_frames > 0),
    ]
    checks.extend(timestamp_checks(decoded_frames, args.expected_fps or fps, video.get("time_base")))
    for name, field, expected in (("video_codec", "codec_name", args.expected_video_codec),
                                 ("pixel_format", "pix_fmt", args.expected_pixel_format)):
        if expected is not None:
            checks.append(check(name, video.get(field), expected, video.get(field) == expected))
    if timeline:
        checks.append(check("mp4_container", data.get("format", {}).get("format_name"), "mp4",
                            "mp4" in data.get("format", {}).get("format_name", "").split(",") and source.suffix.lower() == ".mp4"))
        checks.append(check("timeline_page_intervals", timeline["pages"], "contiguous [1,total_frames) coverage", True))

    if args.expected_duration is not None:
        checks.append(
            check(
                "container_duration_seconds",
                container_duration,
                args.expected_duration,
                abs(container_duration - args.expected_duration) <= args.duration_tolerance,
            )
        )
        checks.append(
            check(
                "video_stream_duration_seconds",
                video_duration,
                args.expected_duration,
                video_duration is not None
                and abs(video_duration - args.expected_duration) <= args.duration_tolerance,
            )
        )
    if args.expected_width is not None:
        checks.append(check("width", video.get("width"), args.expected_width, video.get("width") == args.expected_width))
    if args.expected_height is not None:
        checks.append(
            check("height", video.get("height"), args.expected_height, video.get("height") == args.expected_height)
        )
    if args.expected_fps is not None:
        checks.append(
            check(
                "fps",
                fps,
                args.expected_fps,
                fps is not None and abs(fps - args.expected_fps) <= args.fps_tolerance,
            )
        )
    if args.require_audio:
        checks.append(check("audio_stream", len(audio_streams), 1, len(audio_streams) == 1))
    if args.expected_audio_codec is not None:
        codec = audio_streams[0].get("codec_name") if len(audio_streams) == 1 else None
        checks.append(check("audio_codec", codec, args.expected_audio_codec, codec == args.expected_audio_codec))

    timeline_fps = args.expected_fps or fps
    expected_frames = args.expected_frames
    expected_pages = args.expected_pages
    page_frame_counts: list[int] | None = None
    if timeline:
        page_frame_counts = [page["end_frame"] - page["start_frame"] for page in timeline["pages"]]
    elif args.page_frame_counts:
        try:
            page_frame_counts = [int(item.strip()) for item in args.page_frame_counts.split(",") if item.strip()]
        except ValueError:
            parser.error("--page-frame-counts must contain comma-separated integers")
        if not page_frame_counts or any(count < 1 for count in page_frame_counts):
            parser.error("--page-frame-counts must contain positive integers")
        if expected_pages is not None and expected_pages != len(page_frame_counts):
            parser.error("--expected-pages conflicts with --page-frame-counts")
        expected_pages = len(page_frame_counts)
        calculated_frames = args.cover_frames + sum(page_frame_counts)
        if expected_frames is not None and expected_frames != calculated_frames:
            parser.error("--expected-frames conflicts with --page-frame-counts")
        expected_frames = calculated_frames
    elif expected_pages is not None:
        if expected_pages < 1:
            parser.error("--expected-pages must be positive")
        if timeline_fps is None:
            parser.error("--expected-pages requires --expected-fps or a readable video frame rate")
        page_frames = round(args.seconds_per_page * timeline_fps)
        page_frame_counts = [page_frames] * expected_pages
        calculated_frames = args.cover_frames + sum(page_frame_counts)
        if expected_frames is not None and expected_frames != calculated_frames:
            parser.error("--expected-frames conflicts with the page timeline formula")
        expected_frames = calculated_frames

    if expected_frames is not None:
        checks.append(check("decoded_video_frames", actual_frames, expected_frames, actual_frames == expected_frames))
        if timeline_fps is not None:
            expected_video_duration = expected_frames / timeline_fps
            checks.append(
                check(
                    "video_stream_duration_from_frames",
                    video_duration,
                    expected_video_duration,
                    video_duration is not None
                    and abs(video_duration - expected_video_duration) <= args.duration_tolerance,
                )
            )

    if args.expected_audio_sample_rate is not None:
        actual_sample_rate = parse_int(audio_streams[0].get("sample_rate")) if audio_streams else None
        checks.append(
            check(
                "audio_sample_rate_hz",
                actual_sample_rate,
                args.expected_audio_sample_rate,
                actual_sample_rate == args.expected_audio_sample_rate,
            )
        )
    if args.expected_audio_channels is not None:
        actual_channels = parse_int(audio_streams[0].get("channels")) if audio_streams else None
        checks.append(
            check(
                "audio_channels",
                actual_channels,
                args.expected_audio_channels,
                actual_channels == args.expected_audio_channels,
            )
        )
    if audio_streams:
        audio_duration = parse_float(audio_streams[0].get("duration"))
        av_tolerance = args.av_duration_tolerance
        if av_tolerance is None:
            av_tolerance = 1.0 / timeline_fps if timeline_fps else 0.05
        if timeline:
            av_tolerance = min(av_tolerance, 1.0 / timeline_fps)
        difference = abs(audio_duration - video_duration) if audio_duration is not None and video_duration is not None else None
        checks.append(
            check(
                "audio_video_duration_difference_seconds",
                difference,
                f"<= {av_tolerance}",
                difference is not None and difference <= av_tolerance,
            )
        )

    loudness: dict[str, float | None] | None = None
    if args.check_loudness:
        checks.append(check("audio_stream_for_loudness", len(audio_streams), ">=1", len(audio_streams) >= 1))
        if audio_streams:
            ffmpeg = locate(args.ffmpeg, "ffmpeg")
            loudness = measure_loudness(ffmpeg, source, timeout=args.timeout)
            integrated = loudness["integrated_lufs"]
            true_peak = loudness["true_peak_dbtp"]
            checks.append(
                check(
                    "integrated_loudness_lufs",
                    integrated,
                    f"{args.loudness_min} to {args.loudness_max}",
                    integrated is not None and args.loudness_min <= integrated <= args.loudness_max,
                )
            )
            checks.append(
                check(
                    "true_peak_dbtp",
                    true_peak,
                    f"<= {args.max_true_peak}",
                    true_peak is not None and true_peak <= args.max_true_peak,
                )
            )

    extracted: list[str] = []
    missing_extractions: list[int] = []
    if args.qa_dir:
        ffmpeg = locate(args.ffmpeg, "ffmpeg")
        qa_dir = args.qa_dir.resolve()
        qa_dir.mkdir(parents=True, exist_ok=True)
        if args.qa_frames.strip().lower() == "auto":
            if page_frame_counts is None or expected_frames is None:
                parser.error("--qa-frames auto requires --timeline, --page-frame-counts or --expected-pages")
            frame_numbers = automatic_qa_frames(
                page_frame_counts,
                args.cover_frames,
                expected_frames,
            )
            if timeline and timeline.get('clips'):
                selected = set(frame_numbers)
                for clip in timeline['clips']:
                    a,b = clip['start_frame'],clip['end_frame']
                    selected.update((a,max(1,a-1),(a+b)//2,b-1,min(expected_frames-1,b)))
                frame_numbers = sorted(selected)
        else:
            try:
                frame_numbers = sorted({int(item.strip()) for item in args.qa_frames.split(",") if item.strip()})
            except ValueError:
                parser.error("--qa-frames must be comma-separated integers or auto")
        if not frame_numbers or any(number < 0 for number in frame_numbers):
            parser.error("--qa-frames must contain nonnegative frame numbers")
        extracted, missing_extractions = extract_qa_frames(
            ffmpeg, source, qa_dir, frame_numbers, actual_frames, args.timeout
        )
        checks.append(
            check(
                "qa_frame_extraction",
                {"extracted": len(extracted), "missing_frames": missing_extractions},
                {"requested": len(frame_numbers), "missing_frames": []},
                not missing_extractions,
            )
        )

    checks.append(check("input_unchanged_during_validation", sha256_file(source), source_hash,
                        sha256_file(source) == source_hash))
    passed = bool(checks) and all(item["passed"] for item in checks)
    result = {
        "passed": passed,
        "status": "MACHINE_CHECKS_PASSED" if passed else "MACHINE_CHECKS_FAILED",
        "validation_scope": "production_machine_checks" if timeline else "legacy_explicit_machine_checks",
        "manual_review_required": True,
        "final_ready": False,
        "review_boundary": "Machine checks do not establish visual, factual, cover, or auditory approval. Review the final MP4 and separate cover; verify per-page voice/BGM evidence before FINAL_READY.",
        "sha256": source_hash,
        "timeline_sha256": sha256_file(args.timeline) if timeline else None,
        "assembly_sha256": sha256_file(args.assembly_report) if assembly else None,
        "diagnostic_only": bool(assembly and assembly.get('diagnostic')),
        "file": str(source),
        "format": {
            "duration": container_duration,
            "size": int(data.get("format", {}).get("size", 0)),
            "format_name": data.get("format", {}).get("format_name"),
        },
        "video": {
            "codec": video.get("codec_name"),
            "width": video.get("width"),
            "height": video.get("height"),
            "fps": fps,
            "duration": video_duration,
            "decoded_frames": actual_frames,
        },
        "audio": [
            {
                "codec": stream.get("codec_name"),
                "sample_rate": stream.get("sample_rate"),
                "channels": stream.get("channels"),
                "duration": parse_float(stream.get("duration")),
            }
            for stream in audio_streams
        ],
        "loudness": loudness,
        "timeline": {
            "source": str(args.timeline.resolve()) if timeline else None,
            "schema_version": timeline["schema_version"] if timeline else None,
            "fps": timeline_fps,
            "pages": timeline["pages"] if timeline else None,
            "expected_pages": expected_pages,
            "cover_frames": args.cover_frames,
            "seconds_per_page": None if timeline or args.page_frame_counts else args.seconds_per_page,
            "page_frame_counts": page_frame_counts,
            "expected_frames": expected_frames,
        },
        "checks": checks,
        "extracted_frames": extracted,
        "extracted_frame_sha256": {path:sha256_file(Path(path)) for path in extracted},
        "missing_extracted_frames": missing_extractions,
    }

    rendered = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    print(rendered)
    if args.report:
        report = args.report.resolve()
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(rendered + "\n", encoding="utf-8")
    return 0 if passed else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print(f"validation error: {exc}", file=sys.stderr)
        raise SystemExit(3)
