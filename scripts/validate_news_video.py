#!/usr/bin/env python3
"""Probe a news video, validate delivery specs, and optionally extract QA frames."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from fractions import Fraction
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8")


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
    return float(Fraction(value))


def parse_float(value: object) -> float | None:
    if value is None or value == "N/A":
        return None
    try:
        return float(value)
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


def measure_loudness(ffmpeg: str, source: Path) -> dict[str, float]:
    measured = run(
        [
            ffmpeg,
            "-hide_banner",
            "-nostats",
            "-i",
            str(source),
            "-af",
            "loudnorm=I=-16:TP=-3:LRA=7:print_format=json",
            "-f",
            "null",
            "-",
        ]
    )
    matches = re.findall(r'\{\s*"input_i"[\s\S]*?\}', measured.stderr)
    if not matches:
        raise RuntimeError("unable to parse loudnorm analysis")
    raw = json.loads(matches[-1])
    return {
        "integrated_lufs": float(raw["input_i"]),
        "true_peak_dbtp": float(raw["input_tp"]),
        "loudness_range_lu": float(raw["input_lra"]),
        "threshold_lufs": float(raw["input_thresh"]),
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
    return sorted(frame for frame in selected if frame >= 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--ffprobe")
    parser.add_argument("--ffmpeg")
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
    args = parser.parse_args()

    source = args.input.resolve()
    if not source.is_file():
        parser.error(f"input file does not exist: {source}")

    ffprobe = locate(args.ffprobe, "ffprobe")
    probe = run(
        [
            ffprobe,
            "-v",
            "error",
            "-count_frames",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(source),
        ]
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
    actual_frames = parse_int(video.get("nb_read_frames"))
    checks: list[dict[str, object]] = []

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
        checks.append(check("audio_stream", len(audio_streams), ">=1", len(audio_streams) >= 1))

    timeline_fps = args.expected_fps or fps
    expected_frames = args.expected_frames
    expected_pages = args.expected_pages
    page_frame_counts: list[int] | None = None
    if args.page_frame_counts:
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
    if audio_streams and video_duration is not None:
        audio_duration = parse_float(audio_streams[0].get("duration"))
        if audio_duration is not None:
            av_tolerance = args.av_duration_tolerance
            if av_tolerance is None:
                av_tolerance = 1.0 / timeline_fps if timeline_fps else 0.05
            checks.append(
                check(
                    "audio_video_duration_difference_seconds",
                    abs(audio_duration - video_duration),
                    f"<= {av_tolerance}",
                    abs(audio_duration - video_duration) <= av_tolerance,
                )
            )

    loudness: dict[str, float] | None = None
    if args.check_loudness:
        checks.append(check("audio_stream_for_loudness", len(audio_streams), ">=1", len(audio_streams) >= 1))
        if audio_streams:
            ffmpeg = locate(args.ffmpeg, "ffmpeg")
            loudness = measure_loudness(ffmpeg, source)
            integrated = loudness["integrated_lufs"]
            true_peak = loudness["true_peak_dbtp"]
            checks.append(
                check(
                    "integrated_loudness_lufs",
                    integrated,
                    f"{args.loudness_min} to {args.loudness_max}",
                    args.loudness_min <= integrated <= args.loudness_max,
                )
            )
            checks.append(
                check(
                    "true_peak_dbtp",
                    true_peak,
                    f"<= {args.max_true_peak}",
                    true_peak <= args.max_true_peak,
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
                parser.error("--qa-frames auto requires --page-frame-counts or --expected-pages")
            frame_numbers = automatic_qa_frames(
                page_frame_counts,
                args.cover_frames,
                expected_frames,
            )
        else:
            frame_numbers = [int(item.strip()) for item in args.qa_frames.split(",") if item.strip()]
        for frame_number in frame_numbers:
            target = qa_dir / f"frame-{frame_number:06d}.png"
            run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(source),
                    "-vf",
                    f"select=eq(n\\,{frame_number})",
                    "-frames:v",
                    "1",
                    "-fps_mode",
                    "passthrough",
                    "-y",
                    str(target),
                ]
            )
            if target.is_file():
                extracted.append(str(target))
            else:
                missing_extractions.append(frame_number)
        checks.append(
            check(
                "qa_frame_extraction",
                {"extracted": len(extracted), "missing_frames": missing_extractions},
                {"requested": len(frame_numbers), "missing_frames": []},
                not missing_extractions,
            )
        )

    passed = all(item["passed"] for item in checks)
    result = {
        "passed": passed,
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
            "expected_pages": expected_pages,
            "cover_frames": args.cover_frames,
            "seconds_per_page": None if args.page_frame_counts else args.seconds_per_page,
            "page_frame_counts": page_frame_counts,
            "expected_frames": expected_frames,
        },
        "checks": checks,
        "extracted_frames": extracted,
        "missing_extracted_frames": missing_extractions,
    }

    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.report:
        report = args.report.resolve()
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(rendered + "\n", encoding="utf-8")
    return 0 if passed else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"validation error: {exc}", file=sys.stderr)
        raise SystemExit(3)
