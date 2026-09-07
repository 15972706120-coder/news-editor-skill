#!/usr/bin/env python3
"""Measured, timeline-bound MiniMax + BGM mix. Never trim a voice to fit a page."""
from __future__ import annotations
import argparse
import json
import math
import re
import subprocess
import time
import wave
from pathlib import Path
from production_contract import load_timeline, read_json, resolve, sha256, require

ROOT = Path(__file__).resolve().parent.parent


def run(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120)
    if result.returncode:
        raise RuntimeError(result.stderr[-1200:])
    return result


def measure(ffmpeg, path, start=None, duration=None):
    selection = ([] if start is None else ['-ss', str(start)]) + ([] if duration is None else ['-t', str(duration)])
    result = run([ffmpeg, '-hide_banner', '-nostats', *selection, '-i', str(path), '-vn',
                  '-af', 'loudnorm=I=-16:TP=-3:LRA=7:print_format=json', '-f', 'null', '-'])
    raw = json.loads(re.findall(r'\{\s*"input_i"[\s\S]*?\}', result.stderr)[-1])
    values = float(raw['input_i']), float(raw['input_tp'])
    require(all(math.isfinite(v) for v in values), f'Non-finite loudness: {path}')
    return values


def quality_ok(lufs, peak, deltas, cfg):
    lo, hi = cfg['integrated_lufs_range']
    return (math.isfinite(lufs) and math.isfinite(peak) and lo <= lufs <= hi
            and peak <= cfg['max_true_peak_dbtp'] and bool(deltas)
            and all(math.isfinite(d) and abs(d-cfg['voice_bgm_delta_db']) <= cfg['delta_tolerance_db'] for d in deltas))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeline', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--bgm', type=Path)
    parser.add_argument('--report', type=Path)
    parser.add_argument('--ffmpeg', default='ffmpeg')
    args = parser.parse_args()
    started = time.perf_counter()
    plan = load_timeline(args.timeline)
    require(plan.get('audio_mode', 'tts_bgm') == 'tts_bgm', 'This mixer requires TTS pages')
    cfg = read_json(ROOT/'config.json')['mix']
    bgm = (args.bgm or ROOT/cfg['bgm_source']).resolve()
    out = args.output.resolve()
    report_path = args.report or out.with_suffix('.mix.json')
    require(out not in {bgm, args.timeline.resolve()}, 'Output must not overwrite an input')
    out.parent.mkdir(parents=True, exist_ok=True)
    work = out.parent/(out.stem+'-stems')
    work.mkdir(exist_ok=True)
    fps, total = plan['fps'], plan['total_frames']/plan['fps']
    pages, base, ff = plan['pages'], args.timeline.resolve().parent, args.ffmpeg
    normal, levels = [], []
    for i, page in enumerate(pages):
        source = resolve(base, page['voice']['path'])
        require(out != source.resolve(), 'Output must not overwrite voice')
        target = work/f'voice-{i:02}.wav'
        # Dynamics applied before separating stems; final mix remains linear.
        run([ff, '-v', 'error', '-y', '-i', str(source), '-af',
             f"loudnorm=I={cfg['voice_target_segment_lufs']}:TP=-6:LRA=7,aresample=48000",
             '-ac', '2', '-c:a', 'pcm_s24le', str(target)])
        normal.append(target)
        levels.append(measure(ff, target)[0])
    bgm_raw = work/'bgm-selected.wav'
    run([ff, '-v', 'error', '-y', '-ss', str(cfg['bgm_segment_seconds'][0]), '-i', str(bgm),
         '-t', str(total), '-ar', '48000', '-ac', '2', '-c:a', 'pcm_s24le', str(bgm_raw)])
    with wave.open(str(bgm_raw), 'rb') as audio:
        require(audio.getnframes()/audio.getframerate() >= total-1/48000, 'BGM segment too short')
    gains = []
    for p, level in zip(pages, levels):
        v = p['voice']
        music_level = measure(ff, bgm_raw, v['start_frame']/fps, v['duration_seconds'])[0]
        gains.append(level-cfg['voice_bgm_delta_db']-music_level)
    expression = str(10**(gains[-1]/20))
    for p, gain in reversed(list(zip(pages[:-1], gains[:-1]))):
        expression = f"if(lt(t,{p['end_frame']/fps}),{10**(gain/20)},{expression})"
    iterations, trim = [], 0.0
    voice_stem, bgm_stem = work/'voice.wav', work/'bgm.wav'
    for attempt in range(cfg['gain_iteration_max']):
        parts, labels = [], []
        for i, p in enumerate(pages):
            delay = round(p['voice']['start_frame']/fps*48000)
            parts.append(f'[{i}:a]volume={trim}dB,adelay={delay}S:all=1[v{i}]')
            labels.append(f'[v{i}]')
        parts.append(''.join(labels)+f'amix=inputs={len(pages)}:normalize=0,apad,atrim=duration={total}[v]')
        inputs = [a for path in normal for a in ('-i', str(path))]
        run([ff, '-v', 'error', '-y', *inputs, '-filter_complex', ';'.join(parts), '-map', '[v]',
             '-ar', '48000', '-ac', '2', '-c:a', 'pcm_s24le', str(voice_stem)])
        run([ff, '-v', 'error', '-y', '-i', str(bgm_raw), '-af',
             f"volume='{expression}':eval=frame,volume={trim}dB,afade=t=in:d=0.1,afade=t=out:st={total-.15}:d=0.15",
             '-c:a', 'pcm_s24le', str(bgm_stem)])
        run([ff, '-v', 'error', '-y', '-i', str(voice_stem), '-i', str(bgm_stem),
             '-filter_complex', 'amix=inputs=2:normalize=0', '-ar', '48000', '-ac', '2',
             '-c:a', 'pcm_s24le', str(out)])
        lufs, peak = measure(ff, out)
        deltas = []
        for p in pages:
            start, duration = p['voice']['start_frame']/fps, p['voice']['duration_seconds']
            deltas.append(measure(ff, voice_stem, start, duration)[0]-measure(ff, bgm_stem, start, duration)[0])
        iterations.append({'attempt': attempt+1, 'trim_db': trim, 'lufs': lufs, 'true_peak_dbtp': peak, 'deltas_db': deltas})
        if quality_ok(lufs, peak, deltas, cfg) or attempt+1 == cfg['gain_iteration_max']:
            break
        correction = min(cfg['target_integrated_lufs']-lufs, cfg['max_true_peak_dbtp']-.2-peak)
        trim += correction
    passed = quality_ok(lufs, peak, deltas, cfg)
    report = {'status': 'PASS' if passed else 'BLOCKED_AUDIO', 'final_ready': False,
              'timeline_sha256': sha256(args.timeline), 'output': str(out), 'output_sha256': sha256(out),
              'config_sha256': sha256(ROOT/'config.json'), 'bgm_sha256': sha256(bgm),
              'voice_stem': str(voice_stem), 'bgm_stem': str(bgm_stem),
              'stem_sha256': [sha256(voice_stem), sha256(bgm_stem)],
              'tts_manifest_sha256': [sha256(resolve(base, p['voice']['manifest'])) for p in pages],
              'final_lufs': lufs, 'final_true_peak_dbtp': peak, 'page_voice_bgm_delta_db': deltas,
              'total_trim_db': trim, 'iterations': iterations, 'elapsed_seconds': time.perf_counter()-started,
              'listening_review': 'not_checked', 'measurement_note': 'Both post-gain stems measured on identical voiced intervals; final sum linear.'}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 3


if __name__ == '__main__':
    raise SystemExit(main())
