#!/usr/bin/env python3
"""Engine smoke test for the brief pipeline: reproduce one bundled account sample and run every stage.

Footage is cut from the account's own sample under assets/references/v3-reference/, so no download,
TTS key or platform access is needed. The test cover is an upscaled frame and is deliberately held
back by the cover geometry gate; the render therefore stays --diagnostic and every output is INTERNAL.
Passing this test proves wiring and frame accounting only, never editorial or visual approval.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
SAMPLE = ROOT/'assets/references/v3-reference/sample-unmarried-couple.mp4'
HEADLINE = [{'text': '情侣同居生子未领证', 'style': 'yellow_tag'},
            {'text': '分手后男方起诉索要100万元', 'style': 'black_tag'},
            {'text': '还辩称两人只是同事', 'style': 'yellow_italic'}]
BLOCKS = [('b1', ['湖南一对情侣办酒席同居生子', '始终未领证 分手后男方起诉女方', '男方索要百余万元财物并争夺抚养权', '还称二人只是同事关系'], 4.03),
          ('b2', ['衡阳中院二审维持原判', '已满8岁的孩子自愿随母亲生活', '男方按月支付抚养费'], 3.07),
          ('b3', ['判定吉普车归男方 房产双方各占一半', '38万余元转账、彩礼不予返还', '但以结婚为前提赠送的钻剑 判令女方返还'], 3.77)]
CLIPS = [(1, 94, 0.04, 3.12), (94, 204, 3.16, 3.68), (204, 303, 6.84, 3.32), (303, 390, 10.16, 2.92)]
NOTE = '视频资料与新闻事件无关'


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace')
    if result.returncode:
        raise RuntimeError(' '.join(str(part) for part in command[:3])+'\n'+(result.stderr or result.stdout)[-1800:])
    return result


def stage(script: str, arguments: list, tools: dict) -> subprocess.CompletedProcess[str]:
    return run([sys.executable, str(SCRIPTS/script), *arguments, *tools[script.split('.')[0]]])


def build_timeline(work: Path, ffmpeg: str, fps: int) -> Path:
    import hashlib
    clips = []
    for index, (start, end, source_in, seconds) in enumerate(CLIPS, 1):
        clip = work/f'clip-{index}.mp4'
        run([ffmpeg, '-v', 'error', '-y', '-ss', f'{source_in:.3f}', '-i', str(SAMPLE), '-t', f'{seconds:.3f}',
             '-vf', 'crop=720:444:0:366', '-an', '-c:v', 'libx264', '-crf', '12', '-pix_fmt', 'yuv420p', str(clip)])
        clips.append({'media_type': 'video', 'path': str(clip), 'source_sha256': hashlib.sha256(clip.read_bytes()).hexdigest(),
                      'source_in_seconds': 0, 'start_frame': start, 'end_frame': end, 'crop': [0, 0, 720, 444],
                      'supports_claim': 'INTERNAL smoke-test fixture', 'footage_role': 'contextual_broll',
                      'keeps_source_subtitles': False, 'use_original_sound': False})
    total = CLIPS[-1][1]
    # Block ends follow the reading pace floor, so the fixture also exercises the pacing gate.
    from production_contract import reading_seconds, fullchar_units
    blocks, cursor, delay = [], 1, math.ceil(0.2*fps)
    for index, (bid, lines, hold) in enumerate(BLOCKS):
        need = (delay if index == 0 else 0)+math.ceil(max(hold, reading_seconds(fullchar_units(''.join(lines))))*fps)
        blocks.append({'id': bid, 'start_frame': cursor, 'end_frame': cursor+need, 'lines': lines,
                       'timing': {'information_task': 'INTERNAL smoke-test fixture', 'reading_basis': 'sample pacing',
                                  'cut_reason': 'next information point', 'reading_hold_seconds': hold}})
        cursor += need
    if cursor < total:  # give any remainder to the last block
        blocks[-1]['end_frame'] = total
        clips[-1]['end_frame'] = total
    else:
        total = cursor
        clips[-1]['end_frame'] = total
    plan = {'schema_version': 2, 'format': 'brief_video', 'fps': fps, 'total_frames': blocks[-1]['end_frame'],
            'cover_frames': 1, 'audio_mode': 'bgm_only', 'headline_lines': HEADLINE,
            'result_line': '法院：孩子由女方抚养 车归男方 房子各一半', 'disclaimer_label': NOTE,
            'source_label': '内部冒烟测试', 'caption_blocks': blocks, 'clips': clips,
            'cover': {'source_kind': 'video_frame', 'derived_from_video': True, 'path': str(clips[1]['path']),
                      'source_sha256': clips[1]['source_sha256'], 'source_in_seconds': 1.0, 'crop': [180, 111, 222, 296],
                      'headline': '未婚情侣同居生子', 'subline': '分手后男方起诉100万',
                      'acquisition_method': 'downloaded_video_frame', 'rights_basis': 'INTERNAL smoke-test fixture',
                      'selection_reason': 'engine wiring only', 'clean_image_reviewed': True, 'frame_sharpness_reviewed': True}}
    path = work/'timeline.json'
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--font", type=Path, default=Path("C:/Windows/Fonts/msyhbd.ttc"))
    parser.add_argument("--font-index", type=int, default=0)
    parser.add_argument("--work-dir", type=Path, help="defaults to a fresh temporary directory")
    parser.add_argument("--keep", action="store_true", help="keep the work directory after success")
    args = parser.parse_args()
    if not SAMPLE.is_file() or not args.font.is_file():
        print(json.dumps({"status": "SMOKE_SKIPPED", "reason": "bundled sample or font missing"}))
        return 0
    media = ['--ffmpeg', args.ffmpeg]
    probing = ['--ffmpeg', args.ffmpeg, '--ffprobe', args.ffprobe]
    tools = {'production_contract': [], 'render_news_brief': ['--font', str(args.font), '--font-index', str(args.font_index), *probing],
             'mix_timeline_audio': media, 'assemble_news_video': media, 'validate_news_video': probing,
             'extract_layout_proof': probing}
    work = (args.work_dir or Path(tempfile.mkdtemp(prefix='news-editor-smoke-')))/'smoke'
    work.mkdir(parents=True, exist_ok=True)
    try:
        timeline = build_timeline(work, args.ffmpeg, 30)
        stage('production_contract.py', [str(timeline)], tools)
        render = work/'render'
        stage('render_news_brief.py', ['--timeline', str(timeline), '--out-dir', str(render), '--render', '--diagnostic'], tools)
        stage('mix_timeline_audio.py', ['--timeline', str(timeline), '--output', str(render/'mix.wav'), '--report', str(render/'mix.json')], tools)
        stage('assemble_news_video.py', ['--timeline', str(timeline), '--render-report', str(render/'render-report.json'),
                                         '--mix-report', str(render/'mix.json'), '--output', str(render/'candidate.mp4'),
                                         '--report', str(render/'assembly.json'), '--diagnostic'], tools)
        stage('validate_news_video.py', [str(render/'candidate.mp4'), '--timeline', str(timeline),
                                         '--assembly-report', str(render/'assembly.json'), '--qa-dir', str(render/'qa'),
                                         '--qa-frames', 'auto', '--report', str(render/'qa.json')], tools)
        stage('extract_layout_proof.py', [str(render/'candidate.mp4'), '--timeline', str(timeline), '--qa-report', str(render/'qa.json'),
                                          '--render-report', str(render/'render-report.json'), '--out-dir', str(render/'proof')], tools)
        qa = json.loads((render/'qa.json').read_text(encoding='utf-8'))
        plan = json.loads(timeline.read_text(encoding='utf-8'))
        failed = [item['name'] for item in qa['checks'] if not item['passed']]
        ok = qa['passed'] and qa['video']['decoded_frames'] == plan['total_frames'] and not failed
        print(json.dumps({'status': 'SMOKE_PASSED' if ok else 'SMOKE_FAILED', 'work_dir': str(work),
                          'decoded_frames': qa['video']['decoded_frames'], 'planned_frames': plan['total_frames'],
                          'failed_checks': failed, 'final_lufs': (qa.get('loudness') or {}).get('integrated_lufs'),
                          'scope': 'INTERNAL engine wiring and frame accounting only; outputs are never publishable'}, ensure_ascii=False))
        return 0 if ok else 1
    except (RuntimeError, OSError, ValueError, KeyError) as error:
        print(json.dumps({'status': 'SMOKE_FAILED', 'work_dir': str(work), 'error': str(error)[-1500:]}, ensure_ascii=False))
        return 1
    finally:
        if not args.keep and not args.work_dir:
            import shutil
            shutil.rmtree(work.parent, ignore_errors=True)


if __name__ == '__main__':
    raise SystemExit(main())
