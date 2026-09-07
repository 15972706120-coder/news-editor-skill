#!/usr/bin/env python3
"""Encode an INTERNAL 7-second mixed video/still fixture to test FFmpeg wiring.

This is an engine smoke test only. Its bundled reference images are not news
assets and the output is deliberately diagnostic, so it can never be published.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command: list[str]) -> subprocess.CompletedProcess:
    result = subprocess.run(command, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=300)
    if result.returncode:
        raise RuntimeError(result.stderr[-2400:])
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--font", type=Path, default=Path("C:/Windows/Fonts/msyhbd.ttc"))
    args = parser.parse_args()
    cover = ROOT / "assets/references/cover-style-reference.png"
    video = ROOT / "assets/references/finished-video-reference.mp4"
    still = ROOT / "assets/references/locked-layout/reference-footage-page1.png"
    for path in (cover, video, still, args.font):
        if not path.is_file():
            raise FileNotFoundError(path)
    with tempfile.TemporaryDirectory(prefix="news-editor-mixed-") as temp:
        work = Path(temp)
        timeline = {
            "schema_version": 1,
            "fps": 30,
            "total_frames": 210,
            "cover_frames": 1,
            "duration_profile": "compact",
            "audio_mode": "bgm_only",
            "headline": "紧凑新闻测试",
            "subtitle": "视频加图片关键帧",
            "pages": [{
                "id": "p1", "start_frame": 1, "end_frame": 210,
                "white_lines": ["动态素材不足时使用短版"],
                "red_emphasis": "图片必须连续运动",
                "narration": "",
                "timing": {
                    "information_task": "验证七秒混合时间轴",
                    "reading_hold_seconds": 5.5,
                    "reading_basis": "两行短文案静音可读",
                    "cut_reason": "单页紧凑档无需换页"
                }
            }],
            "cover": {
                "source_kind": "image", "derived_from_video": False,
                "path": str(cover), "source_sha256": digest(cover),
                "crop": [0, 240, 1080, 1440],
                "headline": "紧凑新闻", "subline": "混合素材回归",
                "acquisition_method": "user_provided",
                "rights_basis": "随包回归资产，仅内部测试",
                "selection_reason": "验证独立图片输入而非视频抽帧",
                "clean_image_reviewed": False
            },
            "clips": [
                {
                    "media_type": "video", "path": str(video),
                    "source_sha256": digest(video), "source_in_seconds": 0,
                    "start_frame": 1, "end_frame": 151, "page_id": "p1",
                    "supports_claim": "验证视频主体输入", "speed": 1,
                    "crop": [0, 344, 1080, 1024]
                },
                {
                    "media_type": "image", "path": str(still),
                    "source_sha256": digest(still),
                    "start_frame": 151, "end_frame": 210, "page_id": "p1",
                    "supports_claim": "验证图片关键帧输入",
                    "acquisition_method": "user_provided",
                    "rights_basis": "随包回归资产，仅内部测试",
                    "crop": [0, 15, 1080, 1024],
                    "motion": {
                        "start_zoom": 1.0, "end_zoom": 1.05,
                        "start_anchor": [0.48, 0.5], "end_anchor": [0.52, 0.5],
                        "easing": "linear"
                    }
                }
            ]
        }
        timeline_path = work / "timeline.json"
        timeline_path.write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")
        render_dir = work / "render"
        run([
            sys.executable, str(Path(__file__).resolve().parent / "render_locked_news.py"),
            "--timeline", str(timeline_path), "--out-dir", str(render_dir),
            "--font", str(args.font), "--ffmpeg", args.ffmpeg,
            "--ffprobe", args.ffprobe, "--render", "--diagnostic"
        ])
        output = render_dir / "INTERNAL-DRAFT.mp4"
        probe = json.loads(run([
            args.ffprobe, "-v", "error", "-count_frames", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,nb_read_frames", "-of", "json", str(output)
        ]).stdout)["streams"][0]
        expected = {"width": 1080, "height": 1920, "nb_read_frames": "210"}
        if probe != expected:
            raise AssertionError(f"mixed renderer mismatch: {probe!r} != {expected!r}")
        report = json.loads((render_dir / "render-report.json").read_text(encoding="utf-8"))
        if report.get("body_media_types") != ["video", "image"] or not report.get("diagnostic"):
            raise AssertionError("render report lost mixed-media/diagnostic identity")
        print(json.dumps({"status": "PASS", "probe": probe,
                          "media_types": report["body_media_types"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
