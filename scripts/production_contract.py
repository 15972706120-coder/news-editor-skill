#!/usr/bin/env python3
"""Single frame clock and text/audio binding for News-Editor. End frames are exclusive."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import unicodedata
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMAGE_SUFFIXES = {'.jpg', '.jpeg', '.png', '.webp'}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def resolve(base: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else base / path


def require(condition, message):
    if not condition:
        raise ValueError(message)


def normalized_copy(text):
    # Numbers and negation remain significant; only typography is ignored.
    return ''.join(c for c in unicodedata.normalize('NFKC', text)
                   if not c.isspace() and not unicodedata.category(c).startswith('P'))


def wav_duration(path: Path) -> float:
    with wave.open(str(path), 'rb') as audio:
        require(audio.getnframes() > 0, f'Empty WAV: {path.name}')
        return audio.getnframes() / audio.getframerate()


def load_timeline(path: Path, verify_assets: bool = True) -> dict:
    path = Path(path).resolve()
    plan = read_json(path)
    require(plan.get('schema_version') == 1, 'Unsupported timeline schema')
    for key in ('fps', 'total_frames', 'cover_frames'):
        require(type(plan.get(key)) is int and plan[key] > 0, f'{key} must be a positive integer')
    require(plan['cover_frames'] == 1, 'Cover must occupy exactly frame 0')
    fps, total = plan['fps'], plan['total_frames']
    if plan.get('duration_profile') is not None:
        profile = plan['duration_profile']
        profiles = read_json(ROOT/'config.json')['video']['duration_profiles']
        seconds = total/fps
        require(profile in ('standard','compact'), 'duration_profile must be standard or compact')
        if profile == 'standard':
            require(total == round(profiles['standard_seconds']*fps),
                    'Standard profile duration differs from config')
        else:
            low,high=profiles['compact_seconds_range']
            require(low-1/fps <= seconds <= high+1/fps, 'Compact profile must stay in configured 7–9 second range')
    pages = plan.get('pages')
    require(isinstance(pages, list) and bool(pages), 'No pages')
    cursor, ids = 1, set()
    for page in pages:
        pid = page.get('id')
        require(isinstance(pid, str) and pid and pid not in ids, 'Missing or duplicate page id')
        ids.add(pid)
        a, b = page.get('start_frame'), page.get('end_frame')
        require(type(a) is int and type(b) is int and a == cursor and a < b <= total,
                f'{pid}: page gap, overlap or invalid frame range')
        cursor = b
        white, red = page.get('white_lines'), page.get('red_emphasis')
        require(isinstance(white, list) and 1 <= len(white) <= 2
                and all(isinstance(x, str) and x.strip() for x in white), f'{pid}: need 1–2 white lines')
        require(isinstance(red, str) and red.strip(), f'{pid}: missing red emphasis')
        timing = page.get('timing')
        require(isinstance(timing, dict), f'{pid}: missing editorial timing plan')
        for key in ('information_task', 'reading_basis', 'cut_reason'):
            require(isinstance(timing.get(key), str) and bool(timing[key].strip()),
                    f'{pid}: missing timing.{key}')
        hold = timing.get('reading_hold_seconds')
        require(type(hold) in (int, float) and math.isfinite(hold) and hold > 0,
                f'{pid}: invalid reading hold')
        require(b-a >= math.ceil(hold*fps), f'{pid}: page shorter than editorial reading hold')
        narration = page.get('narration', '')
        if plan.get('audio_mode', 'tts_bgm') == 'bgm_only':
            require(not page.get('voice'), f'{pid}: bgm_only cannot include a voice')
            continue
        require(isinstance(narration, str) and narration.strip(), f'{pid}: no narration')
        if normalized_copy(''.join(white) + red) != normalized_copy(narration):
            require(bool(page.get('narration_override_reason')) and bool(page.get('claim_ids'))
                    and page.get('narration_reviewed') is True,
                    f'{pid}: display/narration mismatch; reviewed claim mapping required')
        voice = page.get('voice') or {}
        start, duration = voice.get('start_frame'), voice.get('duration_seconds')
        require(type(start) is int and start >= a + math.ceil(0.15 * fps), f'{pid}: missing voice lead-in')
        require(type(duration) in (int, float) and math.isfinite(duration) and duration > 0,
                f'{pid}: invalid voice duration')
        require(start + math.ceil(duration * fps) <= b - math.ceil(0.25 * fps),
                f'{pid}: voice overflow; shorten copy or explicitly extend plan, never truncate')
        require(voice.get('text_sha256') == text_hash(narration), f'{pid}: stale narration hash')
        if verify_assets:
            audio = resolve(path.parent, voice['path'])
            manifest_path = resolve(path.parent, voice['manifest'])
            manifest = read_json(manifest_path)
            require(manifest.get('status') == 'TTS_READY' and manifest.get('page_id') == pid,
                    f'{pid}: wrong TTS page or status')
            request, result = manifest['request'], manifest['audio']
            require(request['text'] == narration and request['text_sha256'] == text_hash(narration),
                    f'{pid}: TTS text differs from approved narration')
            require(resolve(manifest_path.parent, result['path']).resolve() == audio.resolve(),
                    f'{pid}: manifest points at another WAV')
            require(sha256(audio) == result['sha256'] == voice.get('audio_sha256'),
                    f'{pid}: WAV replaced, damaged or swapped')
            measured = wav_duration(audio)
            require(abs(measured - duration) < 1 / 44100 + 1e-6
                    and abs(measured - result['duration_seconds']) < 1 / 44100 + 1e-6,
                    f'{pid}: duration is not measured from current WAV')
    require(cursor == total, 'Pages do not cover the full timeline')
    require(plan.get('audio_mode', 'tts_bgm') in ('tts_bgm', 'bgm_only'), 'Invalid audio_mode')
    cover = plan.get('cover')
    if cover is not None:
        require(cover.get('source_kind') == 'image', 'Cover must use a separately acquired image, never a video frame')
        require(cover.get('derived_from_video') is False, 'Cover image must not be derived from video')
        require('source_in_seconds' not in cover, 'Image cover cannot contain a video timecode')
        require(cover.get('acquisition_method') in ('web_image_search', 'official_image_download', 'user_provided'),
                'Cover acquisition method is missing or invalid')
        require(isinstance(cover.get('selection_reason'), str) and bool(cover['selection_reason'].strip()),
                'Cover selection reason is required')
        require(isinstance(cover.get('rights_basis'), str) and bool(cover['rights_basis'].strip()),
                'Cover rights/usage basis is required')
        if cover['acquisition_method'] != 'user_provided':
            require(isinstance(cover.get('source_url'), str) and cover['source_url'].startswith(('https://','http://')),
                    'Searched cover image requires its source URL')
        require(type(cover.get('clean_image_reviewed')) is bool, 'Cover clean-image review must be explicit')
        image = resolve(path.parent, cover.get('path', ''))
        require(image.suffix.lower() in IMAGE_SUFFIXES, 'Cover source must be a supported image file')
        if verify_assets:
            require(image.is_file() and sha256(image) == cover.get('source_sha256'), 'Cover image missing or changed')
    # Editing/renderer fields may be absent when this contract is used for audio-only work.
    clips = plan.get('clips')
    if clips is not None:
        require(bool(clips), 'No clips')
        policy = read_json(ROOT/'config.json')['video']['still_media']
        cursor, still_frames, still_segments, video_frames = 1, 0, 0, 0
        used_ranges = {}
        for clip in clips:
            a, b = clip.get('start_frame'), clip.get('end_frame')
            require(type(a) is int and type(b) is int and a == cursor and a < b <= total,
                    'Clip ranges must be continuous half-open frame intervals')
            require(clip.get('page_id') in ids, 'Clip has unknown page')
            page = next(p for p in pages if p['id'] == clip['page_id'])
            require(page['start_frame'] <= a < b <= page['end_frame'], 'Clip crosses page boundary')
            media_type = clip.get('media_type')
            require(media_type in ('video','image','screenshot'), 'Clip media_type must be video, image or screenshot')
            require(isinstance(clip.get('supports_claim'), str) and bool(clip['supports_claim'].strip()),
                    'Every clip must state what claim it supports')
            source_key = str(resolve(path.parent, clip['path']).resolve())
            if media_type == 'video':
                video_frames += b-a
                require(type(clip.get('source_in_seconds')) in (int, float)
                        and math.isfinite(clip['source_in_seconds']) and clip['source_in_seconds'] >= 0,
                        'Invalid source in-point')
                require(clip.get('speed', 1) == 1, 'Standard renderer requires speed=1; do not hide frame duplication')
                source_a = clip['source_in_seconds']
                source_b = source_a + (b-a)/fps
                for old_a,old_b in used_ranges.get(source_key,[]):
                    require(min(source_b,old_b)-max(source_a,old_a) <= 1/fps or bool(clip.get('replay_reason')),
                            'Same source interval repeated; do not reset a clip at a page cut to pad duration')
                used_ranges.setdefault(source_key,[]).append((source_a,source_b))
            else:
                still_frames += b-a; still_segments += 1
                require('source_in_seconds' not in clip and 'blur' not in clip,
                        'Still media has no timecode and cannot use subtitle blur')
                require(resolve(path.parent, clip['path']).suffix.lower() in IMAGE_SUFFIXES,
                        'Still media must use a supported image file')
                require(clip.get('acquisition_method') in ('web_image_search','official_image_download','user_provided'),
                        'Still media acquisition method is missing')
                require(isinstance(clip.get('rights_basis'),str) and bool(clip['rights_basis'].strip()),
                        'Still media rights/usage basis is required')
                if clip['acquisition_method'] != 'user_provided':
                    require(isinstance(clip.get('source_url'),str) and clip['source_url'].startswith(('https://','http://')),
                            'Searched still media requires its source URL')
                motion = clip.get('motion')
                require(isinstance(motion,dict) and motion.get('easing') == 'linear',
                        'Still media requires explicit linear keyframe motion')
                sz,ez = motion.get('start_zoom'),motion.get('end_zoom')
                start,end = motion.get('start_anchor'),motion.get('end_anchor')
                require(all(type(v) in (int,float) and math.isfinite(v) for v in (sz,ez)), 'Invalid still zoom')
                require(1 <= sz <= policy['max_motion_zoom'] and 1 <= ez <= policy['max_motion_zoom']
                        and abs(ez-sz) >= policy['min_motion_zoom_delta'], 'Still zoom is absent or outside policy')
                require(isinstance(start,list) and isinstance(end,list) and len(start)==len(end)==2
                        and all(type(v) in (int,float) and math.isfinite(v) and 0 <= v <= 1 for v in start+end),
                        'Still anchors must be two normalized coordinate pairs')
                require(math.dist(start,end) <= policy['max_anchor_shift'], 'Still pan exceeds motion policy')
                if media_type == 'screenshot':
                    require(clip.get('intentional_text_reviewed') is True and bool(clip.get('claim_ids')),
                            'Information screenshot text and claims require explicit review')
            if verify_assets:
                require(sha256(resolve(path.parent, clip['path'])) == clip.get('source_sha256'), 'Source hash changed')
            cursor = b
        require(cursor == total, 'Clips do not cover body')
        require(video_frames > 0, 'Still images/screenshots may supplement but not replace all video footage')
        require(still_segments <= policy['max_segments'], 'Too many still-image segments')
        require(still_frames/(total-plan['cover_frames']) <= policy['max_body_ratio']+1e-9,
                'Still images/screenshots exceed the allowed body ratio')
    return plan


def allocate_pages(durations: list[float], fps: int, total_frames: int,
                   minimum_hold_seconds: list[float] | None = None) -> list[dict]:
    """Return a draft lower-bound allocation, not an approved editorial cut."""
    require(type(fps) is int and fps > 0 and type(total_frames) is int and total_frames > 1,
            'Invalid frame budget')
    require(bool(durations) and all(math.isfinite(d) and d > 0 for d in durations), 'Invalid durations')
    lead, tail = math.ceil(.15 * fps), math.ceil(.25 * fps)
    needed = [math.ceil(d * fps) + lead + tail for d in durations]
    if minimum_hold_seconds is not None:
        require(len(minimum_hold_seconds)==len(durations) and all(math.isfinite(x) and x>=0 for x in minimum_hold_seconds),
                'Invalid editorial minimum holds')
        needed = [max(n,math.ceil(hold*fps)) for n,hold in zip(needed,minimum_hold_seconds)]
    spare = total_frames - 1 - sum(needed)
    require(spare >= 0, f'Voice/reading need {sum(needed)+1} frames, only {total_frames} available')
    lengths = [n + spare // len(needed) + (i < spare % len(needed)) for i, n in enumerate(needed)]
    cursor, pages = 1, []
    for n in lengths:
        pages.append({'start_frame': cursor, 'end_frame': cursor + n, 'voice_start_frame': cursor + lead})
        cursor += n
    return pages


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('timeline', type=Path)
    args = parser.parse_args()
    result = load_timeline(args.timeline)
    print(json.dumps({'status': 'TIMELINE_VALID', 'sha256': sha256(args.timeline),
                      'total_frames': result['total_frames'], 'pages': len(result['pages'])}))
