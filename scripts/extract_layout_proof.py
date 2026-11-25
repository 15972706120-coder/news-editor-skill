#!/usr/bin/env python3
"""Extract locked News-Editor brief-layout evidence (cover, header, footage, caption zones) from an MP4."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from fractions import Fraction
from pathlib import Path
from PIL import Image
from production_contract import (load_timeline, lock, config, sha256, require,
                                 body_geometry, caption_arrival)
from validate_news_video import extract_qa_frames


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


SCRIPT_DIR = Path(__file__).resolve().parent


def locate(explicit: str | None, name: str) -> str:
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise FileNotFoundError(f"{name} not found: {path}")
        return str(path)
    found = shutil.which(name)
    if not found and os.name == "nt":
        # shutil.which only tries PATHEXT extensions; without PATHEXT it skips .exe tools that
        # every other stage resolves fine, so retry the native Windows extensions explicitly.
        for extension in (".exe", ".cmd", ".bat"):
            found = shutil.which(name + extension)
            if found:
                break
    if not found:
        raise FileNotFoundError(f"{name} is not on PATH; pass --{name}")
    return found


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8", timeout=120)


def probe(ffprobe: str, source: Path) -> dict[str, object]:
    result = run([ffprobe, "-v", "error", "-count_frames", "-select_streams", "v:0",
                  "-show_entries", "stream=width,height,r_frame_rate,nb_read_frames", "-of", "json", str(source)])
    payload = json.loads(result.stdout)
    if not payload.get("streams"):
        raise RuntimeError("no video stream found")
    stream = payload["streams"][0]
    return {"width": int(stream["width"]), "height": int(stream["height"]),
            "fps": float(Fraction(stream["r_frame_rate"])), "frames": int(stream["nb_read_frames"])}


def crop_image(source: Path, box: list[int], target: Path, scale: str | None = None) -> None:
    x, y, width, height = box
    with Image.open(source) as image:
        cropped = image.crop((x, y, x+width, y+height))
        if scale:
            cropped = cropped.resize(tuple(map(int, scale.split(':'))), Image.Resampling.LANCZOS)
        cropped.save(target)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument('--timeline', type=Path, required=True)
    parser.add_argument('--qa-report', type=Path, help='Reuse exact-hash final video probe and extracted frames')
    parser.add_argument('--render-report', type=Path, help='Caption slide frames and placements from the render')
    parser.add_argument("--cover-frames", type=int, default=1)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--contract", type=Path)
    parser.add_argument("--ffmpeg")
    parser.add_argument("--ffprobe")
    args = parser.parse_args()

    if not args.source.is_file():
        raise FileNotFoundError(args.source)
    ffmpeg, ffprobe = locate(args.ffmpeg, "ffmpeg"), locate(args.ffprobe, "ffprobe")
    plan = load_timeline(args.timeline)
    lk = lock()
    contract_path = args.contract or (SCRIPT_DIR.parent/config()['layout']['active_lock_file'])
    zones = body_geometry(lk, plan)
    text_mode = plan['format'] == 'brief_text'
    band = 'panel' if text_mode else 'footage'
    render = json.loads(args.render_report.read_text(encoding='utf-8')) if args.render_report else {}
    units = render.get('caption_units') or [
        {'id': block['id'], 'begin': caption_arrival(block, index, plan['fps'])-lk['body']['caption']['slide_frames'],
         'arrival': caption_arrival(block, index, plan['fps']), 'exit': None}
        for index, block in enumerate(plan['caption_blocks'])]
    verified = None
    if args.qa_report:
        verified = json.loads(args.qa_report.read_text(encoding='utf-8'))
        require(verified.get('sha256') == sha256(args.source), 'QA report refers to another video')
        require(verified.get('timeline_sha256') == sha256(args.timeline), 'QA timeline changed')
        require(verified.get('passed') is True, 'Cannot reuse failed machine report as structural approval')
        media = dict(verified['video'], frames=verified['video']['decoded_frames'])
    else:
        media = probe(ffprobe, args.source)
    expected = lk["canvas"]
    structural_checks = {
        "width": media["width"] == expected["width"],
        "height": media["height"] == expected["height"],
        "fps": abs(media["fps"]-expected["fps"]) < 0.001,
        "frames_match_plan": media["frames"] == plan['total_frames'],
    }
    if not all(structural_checks.values()):
        raise RuntimeError("structural layout proof failed: "+json.dumps(
            {"actual": media, "expected_frames": plan['total_frames'], "checks": structural_checks}, ensure_ascii=False))

    args.out_dir.mkdir(parents=True, exist_ok=True)
    frames: dict[str, int] = {"cover": 0, "first_body": args.cover_frames, "final": media["frames"]-1}
    for index, unit in enumerate(units, start=1):
        frames[f"caption_{index:02d}_settled"] = min(int(unit["arrival"])+1, media["frames"]-1)
    cuts = 0
    for clip in plan.get('clips') or []:
        if clip['start_frame'] > args.cover_frames:
            cuts += 1
            frames[f"cut_{cuts:02d}_before"] = clip['start_frame']-1
            frames[f"cut_{cuts:02d}_after"] = clip['start_frame']

    full_frame_files: dict[str, str] = {}
    reusable = {}
    if verified:
        for name in verified.get('extracted_frames', []):
            candidate = Path(name)
            if candidate.is_file() and verified.get('extracted_frame_sha256', {}).get(name) == sha256(candidate):
                reusable[int(candidate.stem.split('-')[-1])] = candidate
    missing = [n for n in sorted(set(frames.values())) if n not in reusable]
    if missing:
        paths, failed = extract_qa_frames(ffmpeg, args.source, args.out_dir/'frames', missing, media['frames'], 120)
        require(not failed, 'Cannot extract requested proof frames')
        reusable.update({int(Path(p).stem.split('-')[-1]): Path(p) for p in paths})
    for label, frame in frames.items():
        target = args.out_dir/f"{label.replace('_', '-')}.png"
        shutil.copyfile(reusable[frame], target)
        full_frame_files[label] = target.name

    bx, by, bw, bh = lk["cover"]["background_video"]
    cover = args.out_dir/full_frame_files["cover"]
    crop_image(cover, [bx, by, bw, bh], args.out_dir/"cover-3x4.png")
    crop_image(cover, [bx, by, bw, bh], args.out_dir/"cover-thumbnail-270x360.png", "270:360")

    zone_files: dict[str, dict[str, str]] = {}
    for index in range(1, len(units)+1):
        full = args.out_dir/full_frame_files[f"caption_{index:02d}_settled"]
        files: dict[str, str] = {}
        for zone in ("header", band, "caption"):
            target = args.out_dir/f"caption-{index:02d}-{zone}.png"
            crop_image(full, zones[zone], target)
            files[zone] = target.name
        zone_files[f"caption_{index:02d}"] = files

    report = {
        "source": str(args.source.resolve()),
        "source_sha256": sha256(args.source),
        "timeline_sha256": sha256(args.timeline),
        "lock_sha256": sha256(contract_path),
        "contract": str(contract_path.resolve()),
        "format": plan['format'],
        "probe": media,
        "zones": zones,
        "cover_frames": args.cover_frames,
        "cover_evidence_usage": "Encoded MP4 frame and derived crops/thumbnails are QA evidence only; never use them as the independent upload cover or source background. Use the lossless master.",
        "structural_checks": structural_checks,
        "frames": frames,
        "full_frame_files": full_frame_files,
        "caption_zone_files": zone_files,
        "manual_review_required": [
            "cover_full_and_3x4",
            "cover_thumbnail_270x360",
            "each_caption_header_against_reference",
            "each_caption_footage_or_panel_fill_and_source_text",
            "each_caption_text_legibility_and_width",
            "each_cut_before_after",
            "caption_slide_in_frames",
            "final_frame",
            "platform_overlay",
        ],
        "status": "NEEDS_VISUAL_REVIEW",
    }
    report_path = args.out_dir/"layout-proof.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"report": str(report_path), "frames": len(frames), "checks": structural_checks}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
