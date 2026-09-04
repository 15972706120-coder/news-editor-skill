#!/usr/bin/env python3
"""Extract locked News-Editor layout evidence from a rendered MP4."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from fractions import Fraction
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONTRACT = SCRIPT_DIR.parent / "assets" / "references" / "locked-layout" / "layout-lock-v2.json"


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


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8")


def parse_counts(value: str) -> list[int]:
    counts = [int(item.strip()) for item in value.split(",") if item.strip()]
    if not counts or any(item <= 0 for item in counts):
        raise ValueError("--page-frame-counts must contain positive comma-separated integers")
    return counts


def probe(ffprobe: str, source: Path) -> dict[str, object]:
    result = run(
        [
            ffprobe,
            "-v",
            "error",
            "-count_frames",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,r_frame_rate,nb_read_frames",
            "-of",
            "json",
            str(source),
        ]
    )
    payload = json.loads(result.stdout)
    if not payload.get("streams"):
        raise RuntimeError("no video stream found")
    stream = payload["streams"][0]
    return {
        "width": int(stream["width"]),
        "height": int(stream["height"]),
        "fps": float(Fraction(stream["r_frame_rate"])),
        "frames": int(stream["nb_read_frames"]),
    }


def extract_frame(ffmpeg: str, source: Path, frame: int, target: Path) -> None:
    run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-vf",
            f"select=eq(n\\,{frame})",
            "-frames:v",
            "1",
            "-y",
            str(target),
        ]
    )


def crop_image(ffmpeg: str, source: Path, box: list[int], target: Path, scale: str | None = None) -> None:
    x, y, width, height = box
    filters = [f"crop={width}:{height}:{x}:{y}"]
    if scale:
        filters.append(f"scale={scale}")
    run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-vf",
            ",".join(filters),
            "-frames:v",
            "1",
            "-y",
            str(target),
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--page-frame-counts", required=True, help="Comma-separated body page frame counts")
    parser.add_argument("--cover-frames", type=int, default=1)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--ffmpeg")
    parser.add_argument("--ffprobe")
    args = parser.parse_args()

    if not args.source.is_file():
        raise FileNotFoundError(args.source)
    if not args.contract.is_file():
        raise FileNotFoundError(args.contract)

    ffmpeg = locate(args.ffmpeg, "ffmpeg")
    ffprobe = locate(args.ffprobe, "ffprobe")
    counts = parse_counts(args.page_frame_counts)
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    media = probe(ffprobe, args.source)
    expected = contract["canvas"]
    expected_frames = args.cover_frames + sum(counts)

    structural_checks = {
        "width": media["width"] == expected["width"],
        "height": media["height"] == expected["height"],
        "fps": abs(media["fps"] - expected["fps"]) < 0.001,
        "frames_match_plan": media["frames"] == expected_frames,
    }
    if not all(structural_checks.values()):
        raise RuntimeError(
            "structural layout proof failed: "
            + json.dumps({"actual": media, "expected_frames": expected_frames, "checks": structural_checks}, ensure_ascii=False)
        )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    frames: dict[str, int] = {"cover": 0, "first_body": args.cover_frames, "final": media["frames"] - 1}
    page_start = args.cover_frames
    for index, count in enumerate(counts, start=1):
        frames[f"page_{index:02d}_mid"] = page_start + count // 2
        if index < len(counts):
            frames[f"cut_{index:02d}_before"] = page_start + count - 1
            frames[f"cut_{index:02d}_after"] = page_start + count
        page_start += count

    full_frame_files: dict[str, str] = {}
    for label, frame in frames.items():
        target = args.out_dir / f"{label.replace('_', '-')}.png"
        extract_frame(ffmpeg, args.source, frame, target)
        full_frame_files[label] = target.name

    cover = args.out_dir / full_frame_files["cover"]
    crop_image(ffmpeg, cover, contract["cover"]["background_video"], args.out_dir / "cover-3x4.png")
    crop_image(
        ffmpeg,
        cover,
        contract["cover"]["background_video"],
        args.out_dir / "cover-thumbnail-270x360.png",
        "270:360",
    )

    page_files: dict[str, dict[str, str]] = {}
    for index in range(1, len(counts) + 1):
        full = args.out_dir / full_frame_files[f"page_{index:02d}_mid"]
        files: dict[str, str] = {}
        for zone in ("header", "footage", "copy_board"):
            suffix = "body" if zone == "copy_board" else zone
            target = args.out_dir / f"page-{index:02d}-{suffix}.png"
            crop_image(ffmpeg, full, contract["body_page"][zone], target)
            files[zone] = target.name
        page_files[f"page_{index:02d}"] = files

    report = {
        "source": str(args.source.resolve()),
        "contract": str(args.contract.resolve()),
        "probe": media,
        "page_frame_counts": counts,
        "cover_frames": args.cover_frames,
        "structural_checks": structural_checks,
        "frames": frames,
        "full_frame_files": full_frame_files,
        "page_zone_files": page_files,
        "manual_review_required": [
            "cover_full_and_3x4",
            "cover_thumbnail_270x360",
            "each_page_header_against_reference_header",
            "each_page_footage_fill_and_source_text",
            "each_page_body_white_red_hierarchy",
            "each_cut_before_after",
            "final_frame",
            "platform_overlay",
        ],
        "status": "NEEDS_VISUAL_REVIEW",
    }
    report_path = args.out_dir / "layout-proof.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(report_path), "frames": frames, "checks": structural_checks}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
