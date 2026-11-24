#!/usr/bin/env python3
"""Measured, timeline-bound brief mix: BGM, optional first-hand original sound, optional per-block TTS.

audio_mode is read from the timeline. Prominent audio (original quotes or TTS) is normalized
per unit and the BGM is kept config.voice_bgm_delta_db below it on every unit interval.
Nothing is ever trimmed to fit; an overflowing unit is a timeline error, not a mix edit.
"""
from __future__ import annotations
import argparse
import json
import math
import re
import subprocess
import time
from pathlib import Path
from production_contract import load_timeline, read_json, resolve, sha256, require, wav_info

ROOT = Path(__file__).resolve().parent.parent


def run(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180)
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


def quality_ok(lufs, peak, deltas, target, capped=False):
    lo, hi = target['integrated_lufs_range']
    level_ok = (lo <= lufs <= hi) or (capped and lufs >= lo-target.get('cap_slack_db', 0))
    return (math.isfinite(lufs) and math.isfinite(peak) and level_ok
            and peak <= target['max_true_peak_dbtp']
            and all(math.isfinite(d) and abs(d-12.0) <= 1.0 for d in deltas))


def prominent_units(plan, base):
    """Audio that must stay intelligible: quote-clip original sound, or per-caption-block TTS."""
    fps, mode, units = plan['fps'], plan.get('audio_mode', 'bgm_only'), []
    if mode == 'original_bgm':
        for clip in plan.get('clips') or []:
            if clip.get('use_original_sound'):
                units.append({'id': f'clip-{clip["start_frame"]}', 'start_frame': clip['start_frame'],
                              'duration_seconds': (clip['end_frame']-clip['start_frame'])/fps,
                              'source': resolve(base, clip['path']), 'source_in_seconds': clip['source_in_seconds']})
    elif mode == 'tts_bgm':
        for block in plan['caption_blocks']:
            units.append({'id': block['id'], 'start_frame': block['voice']['start_frame'],
                          'duration_seconds': block['voice']['duration_seconds'],
                          'source': resolve(base, block['voice']['path']), 'source_in_seconds': None})
    return units


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
    cfg = read_json(ROOT/'config.json')['mix']
    mode = plan.get('audio_mode', 'bgm_only')
    target = cfg['loudness_targets'][mode]
    cap = target.get('prominent_max_lufs')
    bgm = (args.bgm or ROOT/(plan.get('bgm') or cfg['bgm_source'])).resolve()
    out = args.output.resolve()
    report_path = args.report or out.with_suffix('.mix.json')
    require(out not in {bgm, args.timeline.resolve()}, 'Output must not overwrite an input')
    out.parent.mkdir(parents=True, exist_ok=True)
    work = out.parent/(out.stem+'-stems')
    work.mkdir(exist_ok=True)
    fps, total = plan['fps'], plan['total_frames']/plan['fps']
    base, ff = args.timeline.resolve().parent, args.ffmpeg
    units = prominent_units(plan, base)
    normal, levels = [], []
    for index, unit in enumerate(units):
        require(out != unit['source'], 'Output must not overwrite prominent audio')
        unit_wav, raw = work/f'prominent-{index:02}.wav', work/f'prominent-{index:02}-raw.wav'
        selection = ['-i', str(unit['source'])] if unit['source_in_seconds'] is None else \
            ['-ss', str(unit['source_in_seconds']), '-t', str(unit['duration_seconds']), '-i', str(unit['source'])]
        run([ff, '-v', 'error', '-y', *selection, '-vn', '-ar', '48000', '-ac', '2', '-c:a', 'pcm_s16le', str(raw)])
        # Measure, then apply plain gain: single-pass loudnorm swallows its tail buffer at EOF and
        # leaves the unit shorter than its clip; apad only covers the few milliseconds by which a
        # source audio track may end before its video track.
        gain = cfg['voice_target_segment_lufs']-measure(ff, raw)[0]
        run([ff, '-v', 'error', '-y', '-i', str(raw), '-af', f'volume={gain:.2f}dB,apad',
             '-t', str(unit['duration_seconds']), '-ac', '2', '-c:a', 'pcm_s24le', str(unit_wav)])
        require(wav_info(unit_wav)['duration_seconds'] >= unit['duration_seconds']-1/48000,
                f'{unit["id"]}: prominent audio shorter than its clip interval')
        normal.append(unit_wav)
        levels.append(measure(ff, unit_wav)[0])
    bgm_raw = work/'bgm-selected.wav'
    run([ff, '-v', 'error', '-y', '-ss', str(cfg['bgm_segment_seconds'][0]), '-i', str(bgm),
         '-t', str(total), '-ar', '48000', '-ac', '2', '-c:a', 'pcm_s24le', str(bgm_raw)])
    require(wav_info(bgm_raw)['duration_seconds'] >= total-1/48000, 'BGM segment too short')
    # BGM gain: constant when nothing is prominent, otherwise stepped per prominent unit interval
    # (idle level between and around units keeps the ducked level of the nearest units).
    def linear(db):
        return 10**(db/20)
    if units:
        gains = [level-cfg['voice_bgm_delta_db']-measure(ff, bgm_raw, u['start_frame']/fps, u['duration_seconds'])[0]
                 for u, level in zip(units, levels)]
        idle, segments, cursor = target['bgm_solo_lufs']-measure(ff, bgm_raw)[0], [], 0.0
        for unit, gain in zip(units, gains):
            start = unit['start_frame']/fps
            if start > cursor:
                segments.append((cursor, idle))
            segments.append((start, gain))
            cursor = start+unit['duration_seconds']
        segments.append((cursor, idle))
        expression = f'{linear(segments[-1][1])}'
        for index in range(len(segments)-1, 0, -1):
            expression = f"if(lt(t,{segments[index][0]}),{linear(segments[index-1][1])},{expression})"
    else:
        expression = f"{linear(target['target_integrated_lufs']-measure(ff, bgm_raw)[0])}"
    iterations, trim = [], 0.0
    prominent_stem, bgm_stem = work/'prominent.wav', work/'bgm.wav'
    for attempt in range(cfg['gain_iteration_max']):
        if units:
            parts, labels = [], []
            for index, unit in enumerate(units):
                parts.append(f"[{index}:a]volume={trim}dB,adelay={round(unit['start_frame']/fps*48000)}S:all=1[v{index}]")
                labels.append(f'[v{index}]')
            parts.append(''.join(labels)+f'amix=inputs={len(units)}:normalize=0,apad,atrim=duration={total}[v]')
            inputs = [a for path in normal for a in ('-i', str(path))]
            run([ff, '-v', 'error', '-y', *inputs, '-filter_complex', ';'.join(parts), '-map', '[v]',
                 '-ar', '48000', '-ac', '2', '-c:a', 'pcm_s24le', str(prominent_stem)])
        else:
            run([ff, '-v', 'error', '-y', '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo', '-t', str(total),
                 '-c:a', 'pcm_s24le', str(prominent_stem)])
        run([ff, '-v', 'error', '-y', '-i', str(bgm_raw), '-af',
             f"volume='{expression}':eval=frame,volume={trim}dB,afade=t=in:d=0.1,afade=t=out:st={total-.15}:d=0.15",
             '-c:a', 'pcm_s24le', str(bgm_stem)])
        run([ff, '-v', 'error', '-y', '-i', str(prominent_stem), '-i', str(bgm_stem),
             '-filter_complex', 'amix=inputs=2:normalize=0', '-ar', '48000', '-ac', '2',
             '-c:a', 'pcm_s24le', str(out)])
        lufs, peak = measure(ff, out)
        deltas = [measure(ff, prominent_stem, u['start_frame']/fps, u['duration_seconds'])[0]
                  - measure(ff, bgm_stem, u['start_frame']/fps, u['duration_seconds'])[0] for u in units]
        iterations.append({'attempt': attempt+1, 'trim_db': trim, 'lufs': lufs, 'true_peak_dbtp': peak, 'deltas_db': deltas})
        capped = cap is not None and trim >= cap-max(levels)-1e-6
        if quality_ok(lufs, peak, deltas, target, capped) or attempt+1 == cfg['gain_iteration_max'] or capped:
            break
        correction = min(target['target_integrated_lufs']-lufs, target['max_true_peak_dbtp']-.2-peak)
        if cap is not None:
            correction = min(correction, cap-max(levels)-trim)
        trim += correction
    capped = cap is not None and trim >= cap-max(levels)-1e-6
    passed = quality_ok(lufs, peak, deltas, target, capped)
    report = {'status': 'PASS' if passed else 'BLOCKED_AUDIO', 'final_ready': False,
              'audio_mode': plan.get('audio_mode', 'bgm_only'),
              'timeline_sha256': sha256(args.timeline), 'output': str(out), 'output_sha256': sha256(out),
              'config_sha256': sha256(ROOT/'config.json'), 'bgm': str(bgm), 'bgm_sha256': sha256(bgm),
              'prominent_stem': str(prominent_stem), 'bgm_stem': str(bgm_stem),
              'stem_sha256': [sha256(prominent_stem), sha256(bgm_stem)],
              'prominent_units': [dict(u, source=str(u['source']), lufs=level) for u, level in zip(units, levels)],
              'tts_manifest_sha256': [sha256(resolve(base, b['voice']['manifest'])) for b in plan['caption_blocks']
                                      if plan.get('audio_mode') == 'tts_bgm'],
              'final_lufs': lufs, 'final_true_peak_dbtp': peak, 'unit_prominent_bgm_delta_db': deltas,
              'total_trim_db': trim, 'prominent_cap_reached': capped, 'loudness_target': target,
              'iterations': iterations, 'elapsed_seconds': time.perf_counter()-started,
              'listening_review': 'not_checked',
              'measurement_note': 'Both post-gain stems measured on identical prominent intervals; final sum linear.'}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('status', 'audio_mode', 'final_lufs', 'final_true_peak_dbtp',
                                             'unit_prominent_bgm_delta_db')}, ensure_ascii=False))
    return 0 if passed else 3


if __name__ == '__main__':
    raise SystemExit(main())
