#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""News-Editor 自动混音：MiniMax 人声 + BGM，全部 FACT 参数读取仓库根 config.json 的 mix 段。

流程：测各页人声 stem 响度 → 增益归一到 voice_target_segment_lufs → BGM 增益按 12dB 避让差计算
→ 按页 adelay 混合并限幅 → 实测综合响度/真峰值，超出 config 区间时整体微调总增益重混（有限迭代）。
输出 48kHz 立体声 WAV 与 stdout JSON 指标（逐页人声/BGM 实测差值供 qa 记录）。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR.parent / "config.json"


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError("命令失败: " + " ".join(cmd) + "\n" + (result.stderr or "")[-800:])
    return result


def integrated_lufs(path: Path) -> float:
    result = run(["ffmpeg", "-v", "info", "-i", str(path), "-af", "ebur128=framelog=quiet", "-f", "null", "-"])
    match = re.search(r"I:\s+(-?\d+(?:\.\d+)?) LUFS", result.stderr)
    if not match:
        raise RuntimeError(f"无法从 ffmpeg 输出解析响度: {path}")
    return float(match.group(1))


def true_peak_dbtp(path: Path) -> float:
    result = run(["ffmpeg", "-v", "info", "-i", str(path), "-af", "ebur128=peak=true:framelog=quiet", "-f", "null", "-"])
    match = re.search(r"Peak:\s+(-?\d+(?:\.\d+)?) dBFS", result.stderr)
    return float(match.group(1)) if match else float("nan")


def segment_lufs(path: Path, start_s: float, dur_s: float) -> float:
    """测音频文件在 [start, start+dur] 区间的响度（先裁再测）。"""
    result = run(["ffmpeg", "-v", "info", "-ss", f"{start_s:.3f}", "-t", f"{dur_s:.3f}", "-i", str(path),
                  "-af", "ebur128=framelog=quiet", "-f", "null", "-"])
    match = re.search(r"I:\s+(-?\d+(?:\.\d+)?) LUFS", result.stderr)
    if not match:
        raise RuntimeError(f"无法解析区间响度: {path}")
    return float(match.group(1))


def mix_once(bgm: Path, voices: list[Path], voice_gains: list[float], bgm_gain: float,
             delays_ms: list[int], total_trim_db: float, limiter: float, out: Path) -> None:
    inputs = ["-i", str(bgm)] + sum((["-i", str(v)] for v in voices), [])
    bgm_start, bgm_end = CONFIG["mix"]["bgm_segment_seconds"]
    parts = [
        f"[0:a]atrim={bgm_start}:{bgm_end},asetpts=PTS-STARTPTS,"
        f"volume={bgm_gain + total_trim_db:.2f}dB,aresample=48000,pan=stereo|c0=c0|c1=c1[b]"
    ]
    labels = ["[b]"]
    for idx, (gain, delay) in enumerate(zip(voice_gains, delays_ms), start=1):
        parts.append(
            f"[{idx}:a]aresample=48000,volume={gain + total_trim_db:.2f}dB,"
            f"adelay={delay}:all=1,pan=stereo|c0=c0|c1=c0[v{idx}]"
        )
        labels.append(f"[v{idx}]")
    parts.append(
        "".join(labels) + "amix=inputs=" + str(len(labels)) +
        ":duration=longest:normalize=0,alimiter=limit=" + f"{limiter:.3f}" + ":level=false[a]"
    )
    run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", ";".join(parts), "-map", "[a]",
         "-t", str(CONFIG["video"]["duration_seconds"]), "-c:a", "pcm_s16le", str(out)])


CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--voice-p1", required=True, type=Path)
    parser.add_argument("--voice-p2", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--bgm", default=None, type=Path, help="缺省取 config.mix.bgm_source（相对仓库根）")
    args = parser.parse_args()

    mix_cfg = CONFIG["mix"]
    bgm = args.bgm if args.bgm else (SCRIPT_DIR.parent / mix_cfg["bgm_source"])
    voices = [args.voice_p1, args.voice_p2]
    delays = [int(d) for d in mix_cfg["voice_delay_ms"]]
    target = float(mix_cfg["voice_target_segment_lufs"])
    delta = float(mix_cfg["voice_bgm_delta_db"])
    tolerance = float(mix_cfg["delta_tolerance_db"])
    limiter = float(mix_cfg["limiter_threshold_linear"])
    lo, hi = (float(x) for x in mix_cfg["integrated_lufs_range"])
    max_iter = int(mix_cfg.get("gain_iteration_max", 3))

    # 1) 人声增益：各页归一到目标段响度
    stems = [integrated_lufs(v) for v in voices]
    voice_gains = [round(target - s, 2) for s in stems]

    # 2) BGM 增益：重叠区比人声低 delta dB（用整段 LUFS 近似，完成后实测校验）
    bgm_start, bgm_end = mix_cfg["bgm_segment_seconds"]
    bgm_seg = Path(str(args.output) + ".bgmseg.wav")
    run(["ffmpeg", "-v", "error", "-y", "-i", str(bgm), "-ss", str(bgm_start), "-t",
         str(bgm_end - bgm_start), "-c:a", "pcm_s16le", str(bgm_seg)])
    bgm_lufs = integrated_lufs(bgm_seg)
    bgm_gain = round(target - delta - bgm_lufs, 2)
    bgm_seg.unlink()

    # 3) 混音 + 响度自动校准（整体同调总增益，保持相对差）
    trim, iterations = 0.0, []
    out = args.output
    for attempt in range(1, max_iter + 1):
        mix_once(bgm, voices, voice_gains, bgm_gain, delays, trim, limiter, out)
        measured_i = integrated_lufs(out)
        measured_tp = true_peak_dbtp(out)
        iterations.append({"attempt": attempt, "trim_db": round(trim, 2),
                           "lufs": round(measured_i, 2), "true_peak_dbfs": round(measured_tp, 2)})
        if lo <= measured_i <= hi:
            break
        trim += round((target - measured_i) * 0.8, 2)  # 0.8 阻尼防过冲

    # 4) 逐页避让差实测（BGM 单轨在播报区间 vs 人声目标）
    check_bgm = Path(str(out) + ".deltachk.wav")
    bgm_start, bgm_end = mix_cfg["bgm_segment_seconds"]
    run(["ffmpeg", "-v", "error", "-y", "-i", str(bgm), "-ss", str(bgm_start), "-t",
         str(bgm_end - bgm_start), "-af", f"volume={bgm_gain + trim:.2f}dB", str(check_bgm)])
    page_deltas = []
    for idx, v in enumerate(voices):
        start = delays[idx] / 1000.0
        dur = float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(v)],
            capture_output=True, text=True).stdout.strip())
        b = segment_lufs(check_bgm, start, dur)
        page_deltas.append(round(target - b, 2))
    check_bgm.unlink()

    report = {
        "output": str(out.resolve()),
        "config": "config.json#mix",
        "voice_stem_lufs": [round(s, 2) for s in stems],
        "voice_gains_db": voice_gains,
        "bgm_gain_db": bgm_gain,
        "total_trim_db": round(trim, 2),
        "page_voice_bgm_delta_db": page_deltas,
        "final_lufs": iterations[-1]["lufs"],
        "final_true_peak_dbfs": iterations[-1]["true_peak_dbfs"],
        "iterations": iterations,
    }
    report["delta_in_tolerance"] = [abs(d - delta) <= tolerance for d in page_deltas]
    ok = (lo <= report["final_lufs"] <= hi and all(report["delta_in_tolerance"]))
    report["status"] = "PASS" if ok else "CHECK"
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
