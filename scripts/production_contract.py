#!/usr/bin/env python3
"""Single frame clock and text/audio binding for the News-Editor brief format. End frames are exclusive."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMAGE_SUFFIXES = {'.jpg', '.jpeg', '.png', '.webp'}
FORMATS = ('brief_video', 'brief_text')
AUDIO_MODES = ('bgm_only', 'original_bgm', 'tts_bgm')
HEADLINE_STYLES = ('yellow_tag', 'black_tag', 'yellow_italic', 'white_italic')
FOOTAGE_ROLES = ('direct_evidence', 'contextual_broll', 'first_hand_quote')
OVERLAY_TYPES = ('comment_screenshot',)
OUTLET_SPLIT = re.compile(r'[/／|、·,，;；\s]+')


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


def one_line(value, label):
    require(isinstance(value, str) and bool(value.strip()) and '\n' not in value, f'{label} must be one nonempty line')
    return value


def normalized_copy(text: str):
    # Numbers and negation remain significant; only typography is ignored.
    return ''.join(c for c in unicodedata.normalize('NFKC', text)
                   if not c.isspace() and not unicodedata.category(c).startswith('P'))


def fullchar_units(text: str) -> float:
    """Display width in full-width units: CJK/fullwidth 1, ASCII letters and digits 0.55, other ASCII 0.5."""
    total = 0.0
    for char in text:
        if unicodedata.east_asian_width(char) in ('W', 'F'):
            total += 1
        elif char.isalnum():
            total += 0.55
        else:
            total += 0.5
    return round(total, 2)


def config():
    return read_json(ROOT/'config.json')


def lock(cfg=None):
    return read_json(ROOT/(cfg or config())['layout']['active_lock_file'])


# Operating-system keys shared with config output.platform_roots; anything else counts as linux.
PLATFORM_KEYS = {'win32': 'win32', 'darwin': 'darwin'}


def platform_key(name=None):
    return PLATFORM_KEYS.get(name or sys.platform, 'linux')


def approved_bold_font(name=None):
    """The one bold font this OS may render with. Callers pass it to the renderer explicitly; nothing falls back."""
    entry = config()['layout']['bold_font'][platform_key(name)]
    return Path(entry['path']).expanduser(), int(entry['index'])


def wav_info(path: Path) -> dict:
    """RIFF header facts read directly, so 24-bit and WAVE_FORMAT_EXTENSIBLE files work on any Python.

    The standard `wave` module rejects format tag 65534 before Python 3.12.
    """
    import struct
    path = Path(path)
    with path.open('rb') as stream:
        head = stream.read(1024*1024)
    require(len(head) >= 12 and head[:4] == b'RIFF' and head[8:12] == b'WAVE', f'Not a WAV file: {path.name}')
    position, fmt, data_size, data_offset = 12, None, None, None
    while position+8 <= len(head):
        chunk, size = head[position:position+4], int.from_bytes(head[position+4:position+8], 'little')
        if chunk == b'fmt ':
            fmt = head[position+8:position+8+size]
        elif chunk == b'data':
            data_size, data_offset = size, position+8
            break
        position += 8+size+(size & 1)
    require(fmt is not None and len(fmt) >= 16 and data_offset is not None, f'Incomplete WAV header: {path.name}')
    if data_size in (None, 0, 0xFFFFFFFF):
        data_size = path.stat().st_size-data_offset
    _, channels, rate, byte_rate, block_align, bits = struct.unpack('<HHIIHH', fmt[:16])
    require(byte_rate > 0 and block_align > 0 and data_size > 0, f'Empty WAV: {path.name}')
    return {'channels': channels, 'sample_rate': rate, 'bits': bits, 'frames': data_size//block_align,
            'duration_seconds': (data_size//block_align)/rate}


def wav_duration(path: Path) -> float:
    return wav_info(path)['duration_seconds']


def body_geometry(lk: dict, plan: dict) -> dict:
    """Stacked body bands in canvas pixels (XYWH), derived only from the lock and the copy present."""
    canvas = lk['canvas']
    if plan.get('format') == 'brief_text':
        panel = lk['brief_text']['panel']
        return {'header': [0, 0, canvas['width'], panel[1]], 'panel': list(panel),
                'caption': [panel[0], panel[1], panel[2], panel[3]]}
    body, block = lk['body'], lk['body']['headline_block']
    height = sum(block['tag_line_height'] if line['style'].endswith('_tag') else block['italic_line_height']
                 for line in plan['headline_lines'])+block['line_gap']*(len(plan['headline_lines'])-1)
    zones = {'headline_block': [block['center_x']-block['max_width']//2, block['bottom_y']-height,
                                block['max_width'], height]}
    bottom = block['bottom_y']
    if plan.get('result_line'):
        line = body['result_line']
        h = line['font_px']+2
        zones['result_line'] = [line['center_x']-line['max_width']//2, bottom+line['gap_above'], line['max_width'], h]
        bottom += line['gap_above']+h
    if plan.get('disclaimer_label'):
        note = body['disclaimer']
        h = note['font_px']+1
        zones['disclaimer'] = [note['left_x'], bottom+note['gap_above'], 460, h]
        bottom += note['gap_above']+h+note['gap_below']
    else:
        bottom += body['footage']['gap_above']
    footage = [body['footage']['left_x'], bottom, body['footage']['width'], body['footage']['bottom_y']-bottom]
    zones.update(header=[0, 0, canvas['width'], bottom], footage=footage,
                 caption=[0, footage[1]+footage[3], canvas['width'], canvas['height']-footage[1]-footage[3]])
    return zones


def reading_seconds(units: float, cfg=None) -> float:
    """Machine lower bound for how long one caption block must stay readable; the editor may hold longer."""
    pace = (cfg or config())['video']['timing']
    return max(pace['min_block_seconds'], pace['reading_base_seconds']+pace['reading_per_unit_seconds']*units)


def caption_arrival(block: dict, index: int, fps: int, cfg=None, lk=None) -> int:
    """Frame on which a caption block has finished sliding in and is fully readable."""
    cfg, lk = cfg or config(), lk or lock()
    slide = lk['body']['caption']['slide_frames']
    if block.get('voice'):
        return block['voice']['start_frame']
    return block['start_frame']+(math.ceil(cfg['video']['timing']['first_caption_delay_seconds']*fps) if index == 0 else 0)+slide


def plan_reading_frames(block_units: list[float], fps: int, cfg=None, lk=None) -> tuple[list[dict], int]:
    """Sequential caption blocks paced for reading; a runnable first cut, not an approved edit."""
    cfg, lk = cfg or config(), lk or lock()
    slide = lk['body']['caption']['slide_frames']
    delay = math.ceil(cfg['video']['timing']['first_caption_delay_seconds']*fps)
    require(type(fps) is int and fps > 0 and bool(block_units) and all(math.isfinite(u) and u > 0 for u in block_units),
            'Invalid caption units')
    cursor, blocks = 1, []
    for index, units in enumerate(block_units):
        end = cursor+(delay if index == 0 else 0)+max(slide, math.ceil(reading_seconds(units, cfg)*fps))
        blocks.append({'start_frame': cursor, 'end_frame': end})
        cursor = end
    return blocks, cursor


def plan_voice_frames(voice_durations: list[float], fps: int, cfg=None, lk=None) -> tuple[list[dict], int]:
    """Sequential caption blocks from measured TTS durations (optional tts_bgm mode)."""
    cfg, lk = cfg or config(), lk or lock()
    timing, slide = cfg['video']['timing'], lk['body']['caption']['slide_frames']
    lead, gap, tail = (math.ceil(timing[key]*fps) for key in
                       ('first_voice_lead_seconds', 'voice_gap_seconds', 'tail_hold_seconds'))
    require(type(fps) is int and fps > 0 and bool(voice_durations)
            and all(math.isfinite(d) and d > 0 for d in voice_durations), 'Invalid durations')
    cursor, blocks = 1, []
    for index, duration in enumerate(voice_durations):
        start = cursor+(lead if index == 0 else slide+gap)
        end = start+math.ceil(duration*fps)+(tail if index == len(voice_durations)-1 else gap)
        blocks.append({'start_frame': cursor, 'voice_start_frame': start, 'end_frame': end})
        cursor = end
    return blocks, cursor


def load_timeline(path: Path, verify_assets: bool = True) -> dict:
    path = Path(path).resolve()
    plan = read_json(path)
    require(plan.get('schema_version') == 2, 'Unsupported timeline schema (expected 2, brief format)')
    fmt = plan.get('format')
    require(fmt in FORMATS, 'format must be brief_video or brief_text')
    for key in ('fps', 'total_frames', 'cover_frames'):
        require(type(plan.get(key)) is int and plan[key] > 0, f'{key} must be a positive integer')
    require(plan['cover_frames'] == 1, 'Cover must occupy exactly frame 0')
    cfg, lk = config(), lock()
    fps, total = plan['fps'], plan['total_frames']
    require(fps == lk['canvas']['fps'], 'FPS differs from active layout')
    profiles, limits, timing = cfg['video']['duration_profiles'], cfg['video']['copy_limits'], cfg['video']['timing']
    seconds = total/fps
    low, high = profiles['brief_seconds_range']
    require(seconds >= low-1/fps, f'A brief runs at least {low} seconds; add one more real information point instead of padding')
    if seconds > high+1/fps:
        require(plan.get('duration_extension_authorized') is True and isinstance(plan.get('duration_extension_reason'), str)
                and bool(plan['duration_extension_reason'].strip()),
                f'Brief exceeds {high} seconds; trim copy or record an explicit user extension authorization')
        require(seconds <= profiles['hard_max_seconds']+1/fps, 'Brief exceeds the hard duration ceiling')
    mode = plan.get('audio_mode', 'bgm_only')
    require(mode in AUDIO_MODES, 'Invalid audio_mode')
    # Headline block
    lines = plan.get('headline_lines')
    require(isinstance(lines, list) and all(isinstance(x, dict) for x in lines), 'headline_lines must be a list of objects')
    need = 3 if fmt == 'brief_video' else 1
    require((2 if fmt == 'brief_video' else 1) <= len(lines) <= need,
            'brief_video needs 2–3 headline lines; brief_text needs exactly 1')
    for index, line in enumerate(lines, 1):
        one_line(line.get('text'), f'headline line {index}')
        require(line.get('style') in HEADLINE_STYLES, f'headline line {index}: unknown style')
        require(fullchar_units(line['text']) <= limits['headline_line_units'],
                f'headline line {index} exceeds {limits["headline_line_units"]} full-width units; rewrite, never shrink')
    if fmt == 'brief_video':
        require(lines[-1]['style'] == 'yellow_italic', 'The last headline line (the twist) must use yellow_italic')
    else:
        require(lines[0]['style'] == 'yellow_italic', 'brief_text headline must use yellow_italic')
    result = plan.get('result_line')
    if result is not None:
        one_line(result, 'result_line')
        require(fullchar_units(result) <= limits['result_line_units'], 'result_line too long; rewrite')
    source_label = plan.get('source_label')
    require(isinstance(source_label, str) and source_label.strip(),
            'Missing top-level source_label; the news source is mandatory as the small bottom-right caption')
    outlets = [name for name in OUTLET_SPLIT.split(source_label) if name]
    for field, value in [('headline line', ' '.join(x['text'] for x in lines)), ('result_line', result or '')]:
        clash = [name for name in outlets if normalized_copy(name) and normalized_copy(name) in normalized_copy(value)]
        require(not clash, f'{field} must not contain news source outlets {clash}')
    publish_title = plan.get('publish_title')
    if publish_title is not None:
        one_line(publish_title, 'publish_title')
        require(fullchar_units(publish_title) <= limits['publish_title_units'], 'publish_title too long')
    # Caption blocks
    blocks = plan.get('caption_blocks')
    require(isinstance(blocks, list) and bool(blocks), 'No caption blocks')
    cap, slide = lk['body']['caption'], lk['body']['caption']['slide_frames']
    max_lines = cap['max_lines'] if fmt == 'brief_video' else 6
    lead = math.ceil(timing['first_voice_lead_seconds']*fps)
    gap, tail = math.ceil(timing['voice_gap_seconds']*fps), math.ceil(timing['tail_hold_seconds']*fps)
    cursor, ids, arrivals = 1, set(), []
    text_mode = fmt == 'brief_text'
    for index, block in enumerate(blocks):
        bid = block.get('id')
        require(isinstance(bid, str) and bid and bid not in ids, 'Missing or duplicate caption block id')
        ids.add(bid)
        a, b = block.get('start_frame'), block.get('end_frame')
        require(type(a) is int and type(b) is int and a == cursor and a < b <= total,
                f'{bid}: block gap, overlap or invalid frame range')
        cursor = b
        texts = block.get('lines')
        require(isinstance(texts, list) and 1 <= len(texts) <= max_lines
                and all(isinstance(x, str) and x.strip() and '\n' not in x for x in texts), f'{bid}: need 1–{max_lines} caption lines')
        for text in texts:
            require(fullchar_units(text) <= limits['caption_line_units'], f'{bid}: caption line exceeds {limits["caption_line_units"]} units: {text}')
        highlights = block.get('highlights') or []
        joined = ''.join(texts)
        require(isinstance(highlights, list) and all(isinstance(x, str) and x and x in joined for x in highlights),
                f'{bid}: every highlight must be a substring of the caption lines')
        require(text_mode or not highlights, f'{bid}: highlights are only available in brief_text')
        timing_plan = block.get('timing')
        require(isinstance(timing_plan, dict), f'{bid}: missing editorial timing plan')
        for key in ('information_task', 'reading_basis', 'cut_reason'):
            require(isinstance(timing_plan.get(key), str) and bool(timing_plan[key].strip()), f'{bid}: missing timing.{key}')
        hold = timing_plan.get('reading_hold_seconds')
        require(type(hold) in (int, float) and math.isfinite(hold) and hold > 0, f'{bid}: invalid reading hold')
        units = fullchar_units(joined)
        # brief_video replaces blocks, so each block must be readable on its own; brief_text accumulates
        # lines, so its floor is the line-by-line reveal cadence plus the final whole-text hold.
        floor = len(texts)*timing['text_line_reveal_seconds'] if text_mode else reading_seconds(units, cfg)
        require(hold >= floor-1e-6, f'{bid}: declared reading hold is below the pacing floor ({floor:.2f}s)')
        narration = block.get('narration')
        voice = block.get('voice')
        if mode == 'tts_bgm':
            require(isinstance(narration, str) and narration.strip(), f'{bid}: tts_bgm needs narration')
            if normalized_copy(joined) != normalized_copy(narration):
                require(bool(block.get('narration_override_reason')) and bool(block.get('claim_ids'))
                        and block.get('narration_reviewed') is True,
                        f'{bid}: caption/narration mismatch; reviewed claim mapping required')
            voice = voice or {}
            start, duration = voice.get('start_frame'), voice.get('duration_seconds')
            require(type(start) is int and start >= a+(lead if index == 0 else slide),
                    f'{bid}: voice must start after the caption slide-in (and the first-block lead-in)')
            require(type(duration) in (int, float) and math.isfinite(duration) and duration > 0, f'{bid}: invalid voice duration')
            require(start+math.ceil(duration*fps) <= b-(tail if index == len(blocks)-1 else gap),
                    f'{bid}: voice overflow; shorten copy or extend the plan, never truncate')
            require(voice.get('text_sha256') == text_hash(narration), f'{bid}: stale narration hash')
            if verify_assets:
                audio = resolve(path.parent, voice['path'])
                manifest_path = resolve(path.parent, voice['manifest'])
                manifest = read_json(manifest_path)
                require(manifest.get('status') == 'TTS_READY' and manifest.get('page_id') == bid, f'{bid}: wrong TTS block or status')
                request, outcome = manifest['request'], manifest['audio']
                require(request['text'] == narration and request['text_sha256'] == text_hash(narration),
                        f'{bid}: TTS text differs from approved narration')
                require(resolve(manifest_path.parent, outcome['path']).resolve() == audio.resolve(), f'{bid}: manifest points at another WAV')
                require(sha256(audio) == outcome['sha256'] == voice.get('audio_sha256'), f'{bid}: WAV replaced, damaged or swapped')
                measured = wav_duration(audio)
                require(abs(measured-duration) < 1/44100+1e-6 and abs(measured-outcome['duration_seconds']) < 1/44100+1e-6,
                        f'{bid}: duration is not measured from current WAV')
        else:
            require(not voice and not narration, f'{bid}: voice/narration exist only in tts_bgm mode')
        arrivals.append(caption_arrival(block, index, fps, cfg, lk))
    require(cursor == total, 'Caption blocks do not cover the full timeline')
    for index, block in enumerate(blocks):
        visible = (arrivals[index+1] if index+1 < len(blocks) else total)-arrivals[index]
        require(visible >= math.ceil(block['timing']['reading_hold_seconds']*fps),
                f'{block["id"]}: caption shorter than editorial reading hold')
    if text_mode:
        # brief_text lines accumulate down the panel; the stack must stay inside the text zone.
        note = lk['brief_text']['text']
        cursor = note['first_line_center_y']-note['line_pitch']
        for block in blocks:
            cursor += len(block['lines'])*note['line_pitch']+note['paragraph_gap']
        require(cursor-note['paragraph_gap'] <= note['last_line_center_max_y'],
                'brief_text lines overflow the panel; cut copy')
        reveal = math.ceil(timing['text_line_reveal_seconds']*fps)
        for index, block in enumerate(blocks):
            span = (blocks[index+1]['start_frame'] if index+1 < len(blocks) else total)-block['start_frame']
            need = len(block['lines'])*reveal+(math.ceil(timing['text_final_hold_seconds']*fps) if index == len(blocks)-1 else 0)
            require(span >= need, f'{block["id"]}: lines reveal faster than the text pacing')
    if plan.get('bgm'):
        require(resolve(ROOT, plan['bgm']).is_file(), 'timeline bgm file is missing')
    # Cover
    cover = plan.get('cover')
    require(isinstance(cover, dict), 'Missing cover')
    kind = cover.get('source_kind')
    require(kind in ('image', 'video_frame'), 'Cover source_kind must be image or video_frame')
    require(cover.get('derived_from_video') is (kind == 'video_frame'), 'derived_from_video must match the cover source kind')
    if kind == 'video_frame':
        require(type(cover.get('source_in_seconds')) in (int, float) and math.isfinite(cover['source_in_seconds'])
                and cover['source_in_seconds'] >= 0, 'Video-frame cover requires source_in_seconds')
        require(cover.get('frame_sharpness_reviewed') is True and cover.get('clean_image_reviewed') is True,
                'Video-frame cover needs explicit clean-frame and sharpness review')
    else:
        require('source_in_seconds' not in cover, 'Image cover cannot contain a video timecode')
        require(cover.get('clean_image_reviewed') is True, 'Cover clean-image review must be explicit')
    require(cover.get('acquisition_method') in ('web_image_search', 'official_image_download', 'user_provided', 'downloaded_video_frame'),
            'Cover acquisition method is missing or invalid')
    require(isinstance(cover.get('selection_reason'), str) and bool(cover['selection_reason'].strip()), 'Cover selection reason is required')
    require(isinstance(cover.get('rights_basis'), str) and bool(cover['rights_basis'].strip()), 'Cover rights/usage basis is required')
    if cover['acquisition_method'] not in ('user_provided', 'downloaded_video_frame'):
        require(isinstance(cover.get('source_url'), str) and cover['source_url'].startswith(('https://', 'http://')),
                'Searched cover image requires its source URL')
    for key, limit in (('headline', cfg['cover']['headline_hard_max']), ('subline', cfg['cover']['subline_hard_max'])):
        one_line(cover.get(key), f'cover {key}')
        require(fullchar_units(cover[key]) <= limit, f'cover {key} exceeds {limit} full-width units; rewrite, never shrink')
    source = resolve(path.parent, cover.get('path', ''))
    if kind == 'image':
        require(source.suffix.lower() in IMAGE_SUFFIXES, 'Cover source must be a supported image file')
    if verify_assets:
        require(source.is_file() and sha256(source) == cover.get('source_sha256'), 'Cover source missing or changed')
    # Footage clips
    clips = plan.get('clips')
    if fmt == 'brief_video':
        require(isinstance(clips, list) and bool(clips), 'No clips')
    else:
        require(not clips, 'brief_text carries no footage clips')
    zones = body_geometry(lk, plan)
    contextual, original = False, False
    if clips:
        policy = cfg['video']['still_media']
        require(type(policy.get('motion_supersample')) is int and policy['motion_supersample'] >= 2,
                'still_media.motion_supersample must be an integer >= 2; still motion is rendered '
                'supersampled to avoid whole-pixel stutter')
        cursor, still_frames, still_segments, video_frames = 1, 0, 0, 0
        used_ranges = {}
        for clip in clips:
            a, b = clip.get('start_frame'), clip.get('end_frame')
            require(type(a) is int and type(b) is int and a == cursor and a < b <= total,
                    'Clip ranges must be continuous half-open frame intervals')
            media_type = clip.get('media_type')
            require(media_type in ('video', 'image', 'screenshot'), 'Clip media_type must be video, image or screenshot')
            require(isinstance(clip.get('supports_claim'), str) and bool(clip['supports_claim'].strip()),
                    'Every clip must state what claim it supports')
            role = clip.get('footage_role')
            require(role in FOOTAGE_ROLES, 'Every clip needs footage_role direct_evidence, contextual_broll or first_hand_quote')
            contextual = contextual or role == 'contextual_broll'
            keeps = clip.get('keeps_source_subtitles', False)
            sound = clip.get('use_original_sound', False)
            require(type(keeps) is bool and type(sound) is bool, 'keeps_source_subtitles/use_original_sound must be explicit booleans')
            if keeps:
                require(media_type == 'video' and role == 'first_hand_quote' and clip.get('quote_reviewed') is True
                        and bool(clip.get('claim_ids')),
                        'Source subtitles may stay only on a reviewed first-hand quote clip mapped to claims')
            if sound:
                require(media_type == 'video' and role == 'first_hand_quote' and mode == 'original_bgm',
                        'Original sound is kept only for first-hand quote clips in original_bgm mode')
                original = True
            source_key = str(resolve(path.parent, clip['path']).resolve())
            if media_type == 'video':
                video_frames += b-a
                require(type(clip.get('source_in_seconds')) in (int, float)
                        and math.isfinite(clip['source_in_seconds']) and clip['source_in_seconds'] >= 0,
                        'Invalid source in-point')
                require(clip.get('speed', 1) == 1, 'Standard renderer requires speed=1; do not hide frame duplication')
                source_a = clip['source_in_seconds']
                source_b = source_a+(b-a)/fps
                for old_a, old_b in used_ranges.get(source_key, []):
                    require(min(source_b, old_b)-max(source_a, old_a) <= 1/fps or bool(clip.get('replay_reason')),
                            'Same source interval repeated; do not reset a clip to pad duration')
                used_ranges.setdefault(source_key, []).append((source_a, source_b))
            else:
                still_frames += b-a
                still_segments += 1
                require('source_in_seconds' not in clip and 'blur' not in clip and not keeps and not sound,
                        'Still media has no timecode, sound or subtitles')
                require(resolve(path.parent, clip['path']).suffix.lower() in IMAGE_SUFFIXES,
                        'Still media must use a supported image file')
                require(clip.get('acquisition_method') in ('web_image_search', 'official_image_download', 'user_provided'),
                        'Still media acquisition method is missing')
                require(isinstance(clip.get('rights_basis'), str) and bool(clip['rights_basis'].strip()),
                        'Still media rights/usage basis is required')
                if clip['acquisition_method'] != 'user_provided':
                    require(isinstance(clip.get('source_url'), str) and clip['source_url'].startswith(('https://', 'http://')),
                            'Searched still media requires its source URL')
                motion = clip.get('motion')
                require(isinstance(motion, dict) and motion.get('easing') == 'linear',
                        'Still media requires explicit linear keyframe motion')
                sz, ez = motion.get('start_zoom'), motion.get('end_zoom')
                start, end = motion.get('start_anchor'), motion.get('end_anchor')
                require(all(type(v) in (int, float) and math.isfinite(v) for v in (sz, ez)), 'Invalid still zoom')
                require(1 <= sz <= policy['max_motion_zoom'] and 1 <= ez <= policy['max_motion_zoom']
                        and abs(ez-sz) >= policy['min_motion_zoom_delta'], 'Still zoom is absent or outside policy')
                require(isinstance(start, list) and isinstance(end, list) and len(start) == len(end) == 2
                        and all(type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 1 for v in start+end),
                        'Still anchors must be two normalized coordinate pairs')
                require(math.dist(start, end) <= policy['max_anchor_shift'], 'Still pan exceeds motion policy')
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
    require((mode == 'original_bgm') == original,
            'original_bgm requires at least one quote clip with use_original_sound, and no other mode may carry it')
    expected_note = lk['body']['disclaimer']['text'] if contextual else None
    require(plan.get('disclaimer_label') == expected_note,
            'disclaimer_label must be the lock text exactly when contextual footage is used, and absent otherwise')
    # Floating overlays (comment screenshots)
    overlays = plan.get('overlays') or []
    require(isinstance(overlays, list) and len(overlays) <= cfg['video']['overlays']['max_count'], 'Too many overlays')
    fx, fy, fw, fh = zones['footage'] if fmt == 'brief_video' else zones['panel']
    for item in overlays:
        require(item.get('type') in OVERLAY_TYPES, 'Unsupported overlay type')
        a, b = item.get('start_frame'), item.get('end_frame')
        require(type(a) is int and type(b) is int and 1 <= a < b <= total, 'Overlay range must lie inside the body')
        box = item.get('box')
        require(isinstance(box, list) and len(box) == 4 and all(type(v) is int for v in box) and box[2] > 0 and box[3] > 0
                and box[0] >= fx and box[1] >= fy and box[0]+box[2] <= fx+fw and box[1]+box[3] <= fy+fh,
                'Overlay box must lie inside the footage band')
        require(isinstance(item.get('supports_claim'), str) and bool(item['supports_claim'].strip()), 'Overlay needs supports_claim')
        require(item.get('intentional_text_reviewed') is True and bool(item.get('claim_ids')),
                'Overlay text and claims require explicit review')
        require(item.get('acquisition_method') in ('web_image_search', 'official_image_download', 'user_provided'),
                'Overlay acquisition method is missing')
        require(isinstance(item.get('rights_basis'), str) and bool(item['rights_basis'].strip()), 'Overlay rights basis is required')
        if item['acquisition_method'] != 'user_provided':
            require(isinstance(item.get('source_url'), str) and item['source_url'].startswith(('https://', 'http://')),
                    'Searched overlay requires its source URL')
        require(resolve(path.parent, item['path']).suffix.lower() in IMAGE_SUFFIXES, 'Overlay must be a supported image file')
        if verify_assets:
            require(sha256(resolve(path.parent, item['path'])) == item.get('source_sha256'), 'Overlay hash changed')
    return plan


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('timeline', type=Path)
    args = parser.parse_args()
    result = load_timeline(args.timeline)
    print(json.dumps({'status': 'TIMELINE_VALID', 'sha256': sha256(args.timeline),
                      'total_frames': result['total_frames'], 'caption_blocks': len(result['caption_blocks'])}, ensure_ascii=False))
