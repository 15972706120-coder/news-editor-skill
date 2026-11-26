#!/usr/bin/env python3
"""Rebuild the layout reference stills under assets/references/v3-reference/.

The stills are ordinary renders of the brief renderer on example copy and openly licensed video
frames (see frames/SOURCES.md): every cover is a frame cut from the footage, the way a production
frame cover is, and the footage band shows real pictures. Run this on Windows with Microsoft YaHei
Bold (the production font) whenever the lock, the renderer, the frames or the example copy
changes, then review the images and commit them:

    python scripts/build_layout_reference.py --font C:/Windows/Fonts/msyhbd.ttc
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

SCRIPTS = Path(__file__).resolve().parent
from production_contract import (body_geometry, caption_arrival, config, fullchar_units, load_timeline, lock,
                                 reading_seconds, sha256)
from caption_continuity import caption_problems
from render_news_brief import Type, draw_text

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

FPS, WIDTH, HEIGHT = 30, 1080, 1920
FRAMES = SCRIPTS.parent/'assets/references/v3-reference/frames'
COVER = (1080, 1440)  # the 3:4 window a cover is cut from, at its final size
# Bottom-right small caption of every still, so an example is never mistaken for real output.
LABEL = '示例文案（开放授权画面）'
OUTPUTS = ('reference-contact-sheet.png', 'caption-slide-in-strip.png', 'typography-detail.png',
           'cover-style-reference.png', 'cover-grid-good-bad-reference.jpg', 'platform-overlay-reference.webp')
# Covers drawn outside the rules on purpose, for the bottom row of the good/bad grid: (text, font px,
# colour) per line, then the stored frame and its left edge for the 3:4 window. Too many characters leave
# both lines tiny; a subline larger than its headline inverts the hierarchy.
FAILING_COVERS = (
    ((('列车运行图调整后旅客要注意什么', 56, 'cover_cyan'), ('多趟列车到发时刻变化记得及时改签', 50, 'white')),
     'anshan-west-t06.jpg', 1120),
    ((('调图提醒', 80, 'white'), ('出行先查车次', 136, 'cover_cyan')), 'anshan-west-t13.jpg', 1000))

# Video form with the full header: tag line, dark tag, twist line, result line and b-roll disclaimer.
FULL_HEADER = {
    'headline': [('买短乘长被拦后乘客起诉', 'yellow_tag'), ('铁路方称按购票规则补票', 'black_tag'),
                 ('称规则在购票页面早已写明', 'yellow_italic')],
    'result': '法院：驳回全部诉讼请求',
    'blocks': [['小张买的是短途票', '却一路坐到了终点站', '出站时被工作人员拦下补票', '他认为不该加价把铁路方告了'],
               ['法院审理后认为', '补票规则在购票页面写得清楚', '铁路方按章处置没有过错'],
               ['法院驳回小张的全部诉求', '案件受理费由他自行承担', '判决之后双方都没有上诉']],
    'shots': ('anshan-west-t05.jpg', 'tongguanyi-t06.jpg', 'anshan-west-t40.jpg'),
    'cover': ('买短乘长后起诉', '法院驳回全部诉讼请求'), 'cover_frame': 'anshan-west-t05.jpg', 'cover_x': 1380}
# Two-line header whose first line is white; the footage is the story itself, so no result line or disclaimer.
TWO_LINE = {
    'headline': [('新一轮列车运行图实行', 'white_italic'), ('多趟列车到发时刻有变化', 'yellow_italic')],
    'result': None,
    'blocks': [['运行图调整之后', '部分列车的到发时刻变了', '候车的旅客比平时多了一些'],
               ['站台上旅客排队等候', '列车缓缓驶入车站', '车门打开后大家依次上车'],
               ['出行前先查好自己的车次', '发车时间和车站都要核对', '车次有变化的记得及时改签']],
    'shots': ('anshan-west-t06.jpg', 'anshan-west-t13.jpg', 'anshan-west-t32.jpg'),
    'cover': ('列车运行图调整', '多趟列车时刻有变化'), 'cover_frame': 'anshan-west-t32.jpg', 'cover_x': 1460}
# Text-led form: lines accumulate down the panel and key words are highlighted.
TEXT_ONLY = {
    'headline': [('调图后 出行记住这几步', 'yellow_italic')],
    'blocks': [['调图后先查车次', '发车时间和车站', '都可能和以前不一样'],
               ['已经买好票的旅客', '留意有没有改签通知', '车次变了要及时处理'],
               ['出行当天早点到站', '高峰时段进站要排队']],
    'highlights': [['车次'], ['改签通知'], ['早点到站']],
    'cover': ('调图后出行指南', '这几步先做再出门'), 'cover_frame': 'anshan-west-t13.jpg', 'cover_x': 1000}


def still_clip(source: Path, path: Path, frames: int, fps: int, ffmpeg: str) -> Path:
    """A clip made from one stored video frame with a slow push-in: the reference renders go through
    the ordinary video path, frame-sourced covers included, without shipping any video file."""
    with Image.open(source) as image:
        width, height = image.size
    motion = (f"scale={2*width}:{2*height},zoompan=z='1+0.05*on/{max(frames-1, 1)}':d={frames}:"
              f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={width}x{height}:fps={fps},format=yuv420p")
    result = subprocess.run([ffmpeg, '-v', 'error', '-y', '-loop', '1', '-framerate', str(fps), '-i', str(source),
                             '-vf', motion, '-frames:v', str(frames), '-an', '-c:v', 'libx264', '-crf', '14',
                             '-pix_fmt', 'yuv420p', '-colorspace', 'bt709', '-color_primaries', 'bt709',
                             '-color_trc', 'bt709', str(path)], capture_output=True)
    if result.returncode:
        raise RuntimeError(f'ffmpeg exited with {result.returncode} while building the clip for {source.name}\n'
                           + result.stderr.decode('utf-8', 'replace')[-1200:])
    return path


def cover_clip(name: str, spec: dict, work: Path, ffmpeg: str) -> Path:
    return still_clip(FRAMES/spec['cover_frame'], work/f'{name}-cover-source.mp4', FPS, FPS, ffmpeg)


def cover_entry(path: Path, texts: tuple[str, str], left: int) -> dict:
    """A cover cut from a frame of the footage, which is how a production frame cover is sourced."""
    return {'source_kind': 'video_frame', 'derived_from_video': True, 'path': str(path), 'source_sha256': sha256(path),
            'source_in_seconds': 0, 'crop': [left, 0, *COVER], 'headline': texts[0], 'subline': texts[1],
            'acquisition_method': 'downloaded_video_frame', 'rights_basis': 'CC BY-SA 4.0, see frames/SOURCES.md',
            'selection_reason': 'layout reference example', 'clean_image_reviewed': True, 'frame_sharpness_reviewed': True}


def video_plan(name: str, spec: dict, work: Path, ffmpeg: str) -> dict:
    blocks, cursor, delay = [], 1, math.ceil(0.2*FPS)
    for index, lines in enumerate(spec['blocks']):
        hold = reading_seconds(fullchar_units(''.join(lines)))
        need = (delay if index == 0 else 0)+math.ceil(hold*FPS)
        blocks.append({'id': f'{name}{index+1}', 'start_frame': cursor, 'end_frame': cursor+need, 'lines': lines,
                       'timing': {'information_task': 'layout reference example', 'reading_basis': 'reading floor',
                                  'cut_reason': 'next information point', 'reading_hold_seconds': hold}})
        cursor += need
    # Reading time is counted from the moment a caption has landed, so the last block also carries its slide-in.
    blocks[-1]['end_frame'] += lock()['body']['caption']['slide_frames']
    clips = []
    for index, (block, shot) in enumerate(zip(blocks, spec['shots'])):
        need, source = block['end_frame']-block['start_frame'], FRAMES/shot
        clip = still_clip(source, work/f'{name}-clip-{index+1}.mp4', need, FPS, ffmpeg)
        with Image.open(source) as image:
            size = image.size
        clips.append({'media_type': 'video', 'path': str(clip), 'source_sha256': sha256(clip), 'source_in_seconds': 0,
                      'start_frame': block['start_frame'], 'end_frame': block['end_frame'], 'crop': [0, 0, *size],
                      'supports_claim': 'layout reference example',
                      'footage_role': 'contextual_broll' if spec['result'] else 'direct_evidence',
                      'keeps_source_subtitles': False, 'use_original_sound': False})
    plan = {'schema_version': 2, 'format': 'brief_video', 'fps': FPS, 'total_frames': blocks[-1]['end_frame'], 'cover_frames': 1,
            'audio_mode': 'bgm_only', 'headline_lines': [{'text': text, 'style': style} for text, style in spec['headline']],
            'source_label': LABEL, 'caption_blocks': blocks, 'clips': clips,
            'cover': cover_entry(cover_clip(name, spec, work, ffmpeg), spec['cover'], spec['cover_x'])}
    if spec['result']:
        plan.update(result_line=spec['result'], disclaimer_label=lock()['body']['disclaimer']['text'])
    return plan


def text_plan(name: str, spec: dict, work: Path, ffmpeg: str) -> dict:
    reveal, final, paragraph, delay = (math.ceil(seconds*FPS) for seconds in (0.75, 2.5, 0.9, 0.2))
    slide, blocks, cursor = lock()['body']['caption']['slide_frames'], [], 1
    for index, lines in enumerate(spec['blocks']):
        last = index == len(spec['blocks'])-1
        lead = (delay if index == 0 else 0)+slide
        span = lead+len(lines)*reveal+(final if last else paragraph)
        blocks.append({'id': f'{name}{index+1}', 'start_frame': cursor, 'end_frame': cursor+span, 'lines': lines,
                       'highlights': spec['highlights'][index],
                       'timing': {'information_task': 'layout reference example', 'reading_basis': 'text pacing',
                                  'cut_reason': 'next paragraph', 'reading_hold_seconds': (span-lead)/FPS}})
        cursor += span
    return {'schema_version': 2, 'format': 'brief_text', 'fps': FPS, 'total_frames': cursor, 'cover_frames': 1,
            'audio_mode': 'bgm_only', 'headline_lines': [{'text': spec['headline'][0][0], 'style': 'yellow_italic'}],
            'source_label': LABEL, 'caption_blocks': blocks,
            'cover': cover_entry(cover_clip(name, spec, work, ffmpeg), spec['cover'], spec['cover_x'])}


def frame(video: Path, index: int, ffmpeg: str) -> Image.Image:
    """Frame `index` of the encoded draft; body frame b sits at index b+1 because frame 0 is the cover."""
    result = subprocess.run([ffmpeg, '-v', 'error', '-i', str(video), '-vf', f'select=eq(n\\,{index})', '-vframes', '1',
                             '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], capture_output=True)
    if result.returncode:
        raise RuntimeError(' '.join(('ffmpeg', str(video), str(index)))+'\n'+result.stderr.decode('utf-8', 'replace')[-1200:])
    return Image.frombytes('RGB', (WIDTH, HEIGHT), result.stdout)


def render(name: str, plan: dict, work: Path, args) -> tuple[dict, Path]:
    path, out = work/f'{name}.json', work/name
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')
    plan = load_timeline(path)
    command = [sys.executable, str(SCRIPTS/'render_news_brief.py'), '--timeline', str(path), '--out-dir', str(out),
               '--font', str(args.font), '--font-index', str(args.font_index),
               *(['--ffmpeg', args.ffmpeg] if args.ffmpeg else []), *(['--ffprobe', args.ffprobe] if args.ffprobe else []), '--render']
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace')
    if result.returncode:
        raise RuntimeError(f'render {name} failed\n'+(result.stderr or result.stdout)[-1800:])
    report = json.loads((out/'render-report.json').read_text(encoding='utf-8'))
    if report['status'] != 'NEEDS_VISUAL_REVIEW':
        raise RuntimeError(f'render {name} ended as {report["status"]}')
    continuity = caption_problems(args.ffmpeg or 'ffmpeg', out/'draft.mp4', report, plan, out)
    if continuity:
        raise RuntimeError(f'caption continuity in {name}: '+'; '.join(continuity))
    return plan, out


def cover_of(out: Path) -> Image.Image:
    return Image.open(out/'cover-master.png').convert('RGB')


def settled_body(plan: dict, index: int, pad: int = 10) -> int:
    """Body frame on which caption block `index` has landed and been readable for `pad` frames."""
    return caption_arrival(plan['caption_blocks'][index], index, FPS)+pad


def contact_sheet(stills: dict) -> Image.Image:
    rows = (('full cover', 'full caption 1', 'full final'), ('two-line cover', 'two-line caption 1', 'two-line final'),
            ('text cover', 'text middle', 'text final'))
    tile, gap = (270, 480), 12
    sheet = Image.new('RGB', (len(rows[0])*(tile[0]+gap)+gap, len(rows)*(tile[1]+gap)+gap), (245, 245, 245))
    for y, row in enumerate(rows):
        for x, key in enumerate(row):
            sheet.paste(stills[key].resize(tile, Image.Resampling.LANCZOS), (gap+x*(tile[0]+gap), gap+y*(tile[1]+gap)))
    return sheet


def slide_in_strip(video: Path, plan: dict, ffmpeg: str) -> Image.Image:
    """The six frames during which the first caption slides in, cropped to the caption zone."""
    slide = lock()['body']['caption']['slide_frames']
    begin = caption_arrival(plan['caption_blocks'][0], 0, FPS)-slide
    sheet = Image.new('RGB', (3*(540+8)+8, 2*(245+8)+8), (245, 245, 245))
    for k in range(slide):
        item = frame(video, begin+1+k, ffmpeg).crop((0, 1150, WIDTH, 1640)).resize((540, 245), Image.Resampling.LANCZOS)
        sheet.paste(item, (8+(k % 3)*548, 8+(k//3)*253))
    return sheet


def typography_detail(stills: dict) -> Image.Image:
    left = [stills['full caption 1'].crop((0, 110, WIDTH, 560)), stills['two-line caption 1'].crop((0, 240, WIDTH, 450)),
            stills['full caption 1'].crop((60, 1195, 1020, 1470))]
    right = [stills['text middle'].crop((45, 430, 1035, 1300)), stills['text cover'].crop((40, 1100, 1040, 1440))]
    gap, margin = 24, 24
    width = left[0].width+gap+right[0].width+2*margin
    height = max(sum(item.height for item in column)+gap*(len(column)-1) for column in (left, right))+2*margin
    sheet = Image.new('RGB', (width, height), (245, 245, 245))
    for x, column in ((margin, left), (margin+left[0].width+gap, right)):
        y = margin
        for item in column:
            sheet.paste(item, (x, y))
            y += item.height+gap
    return sheet


def failing_cover(background: Image.Image, lines: tuple, type_: Type) -> Image.Image:
    """A cover drawn outside the rules on purpose: fixed size and colour per line, no auto-fit."""
    canvas = Image.new('RGBA', (WIDTH, HEIGHT), (0, 0, 0, 255))
    bx, by, bw, bh = lock()['cover']['background_video']
    canvas.paste(background.resize((bw, bh), Image.Resampling.LANCZOS), (bx, by))
    canvas.alpha_composite(Image.open(SCRIPTS.parent/config()['cover']['frame_overlay']).convert('RGBA'))
    for (text_, size, colour), anchor in zip(lines, ('headline_visible_bbox', 'subline_visible_bbox')):
        bounds = lock()['cover'][anchor]
        draw_text(canvas, text_, type_.font(size), [40, (bounds[1]+bounds[3])/2-size*.6, WIDTH-80, size*1.2],
                  lock()['colors'][colour], 'center', 4)
    return canvas.convert('RGB')


def cover_grid(stills: dict, type_: Type) -> Image.Image:
    """Top row: covers the renderer accepted. Bottom row: the same frame drawn outside the rules."""
    tile, gap = (270, 480), 16
    sheet = Image.new('RGB', (2*tile[0]+3*gap, 2*tile[1]+3*gap), (245, 245, 245))
    for x, key in enumerate(('full cover', 'text cover')):
        sheet.paste(stills[key].resize(tile, Image.Resampling.LANCZOS), (gap+x*(tile[0]+gap), gap))
    for x, (lines, shot, left) in enumerate(FAILING_COVERS):
        with Image.open(FRAMES/shot) as source:
            background = source.convert('RGB').crop((left, 0, left+COVER[0], COVER[1]))
        sheet.paste(failing_cover(background, lines, type_).resize(tile, Image.Resampling.LANCZOS),
                    (gap+x*(tile[0]+gap), 2*gap+tile[1]))
    return sheet


def platform_overlay(base: Image.Image, plan: dict, type_: Type) -> Image.Image:
    """What the platform interface may cover, drawn from the lock over a rendered body frame."""
    zones = lock()['platform']
    layer = Image.new('RGBA', base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    ink, paper = (255, 255, 255, 255), (0, 0, 0, 255)
    font, line = type_.font(44), 56
    boxes = (((0, 0, WIDTH, zones['top_title_bar_until_y']), ('顶部标题栏', '可能盖住标题块第 1 行')),
             ((zones['right_reserved_from_x'], zones['right_reserved_y'][0], WIDTH, zones['right_reserved_y'][1]),
              ('右侧操作栏',)),
             ((0, zones['bottom_reserved_from_y'], WIDTH, HEIGHT), ('底部标题与评论入口',)))
    for (x0, y0, x1, y1), labels in boxes:
        draw.rectangle((x0+3, y0+3, x1-4, y1-4), fill=(235, 60, 60, 92), outline=ink, width=6)
        if x1-x0 >= 600:  # a full-width band: one label per line, centred
            first = (y0+y1)/2-(len(labels)-1)*line/2
            for index, label in enumerate(labels):
                draw.text(((x0+x1)/2, first+index*line), label, font=font, fill=ink, anchor='mm', stroke_width=3, stroke_fill=paper)
        else:  # the side column: one character per line
            chars = ''.join(labels)
            first = (y0+y1)/2-len(chars)*line/2+line/2
            for index, char in enumerate(chars):
                draw.text(((x0+x1)/2, first+index*line), char, font=font, fill=ink, anchor='mm', stroke_width=3, stroke_fill=paper)
    # Captions must stay inside this width so the side column never covers them.
    caption, half = body_geometry(lock(), plan)['caption'], zones['caption_max_width']//2
    edge = (10, 234, 230, 255)
    draw.rectangle((WIDTH/2-half, caption[1], WIDTH/2+half, HEIGHT), outline=edge, width=4)
    chars = '字幕宽度上限'
    for index, char in enumerate(chars):
        draw.text((WIDTH/2-half+40, caption[1]+90+index*line), char, font=font, fill=edge, anchor='mm',
                  stroke_width=3, stroke_fill=paper)
    return Image.alpha_composite(base.convert('RGBA'), layer).convert('RGB').resize((540, 960), Image.Resampling.LANCZOS)


def build(work: Path, args) -> dict:
    ff = args.ffmpeg or 'ffmpeg'
    type_ = Type(args.font, args.font_index)
    full, full_out = render('full', video_plan('full', FULL_HEADER, work, ff), work, args)
    two, two_out = render('two', video_plan('two', TWO_LINE, work, ff), work, args)
    text, text_out = render('text', text_plan('text', TEXT_ONLY, work, ff), work, args)
    # The middle text frame shows the first two paragraphs complete and the last one not yet revealed.
    text_middle = (settled_body(text, 1, 0)+(len(text['caption_blocks'][1]['lines'])-1)*math.ceil(0.75*FPS)+15)
    stills = {'full cover': cover_of(full_out), 'full caption 1': frame(full_out/'draft.mp4', settled_body(full, 0)+1, ff),
              'full final': frame(full_out/'draft.mp4', full['total_frames']-1, ff),
              'two-line cover': cover_of(two_out), 'two-line caption 1': frame(two_out/'draft.mp4', settled_body(two, 0)+1, ff),
              'two-line final': frame(two_out/'draft.mp4', two['total_frames']-1, ff),
              'text cover': cover_of(text_out), 'text middle': frame(text_out/'draft.mp4', text_middle+1, ff),
              'text final': frame(text_out/'draft.mp4', text['total_frames']-1, ff)}
    images = {'reference-contact-sheet.png': contact_sheet(stills),
              'caption-slide-in-strip.png': slide_in_strip(full_out/'draft.mp4', full, ff),
              'typography-detail.png': typography_detail(stills),
              'cover-style-reference.png': stills['two-line cover'],
              'cover-grid-good-bad-reference.jpg': cover_grid(stills, type_),
              'platform-overlay-reference.webp': platform_overlay(stills['full caption 1'], full, type_)}
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name, image in images.items():
        if name.endswith('.jpg'):
            image.save(args.out_dir/name, quality=92, optimize=True)
        elif name.endswith('.webp'):
            image.save(args.out_dir/name, quality=90, method=6)
        else:
            image.save(args.out_dir/name, optimize=True)
    # Renders differ slightly between machines and font versions, so only the dimensions are compared:
    # a layout change that nobody rebuilt the committed stills for shows up as a different size.
    compared = []
    if args.compare_dir:
        for name in OUTPUTS:
            committed = args.compare_dir/name
            if not committed.is_file():
                compared.append(f'{name}: not committed yet')
                continue
            with Image.open(committed) as previous:
                state = 'same size' if previous.size == images[name].size else f'different size {previous.size} -> {images[name].size}'
            compared.append(f'{name}: {state}')
    return {'status': 'REFERENCE_BUILT', 'out_dir': str(args.out_dir), 'outputs': list(OUTPUTS), 'compared': compared,
            'frames': {'full': full['total_frames'], 'two-line': two['total_frames'], 'text': text['total_frames']},
            'note': 'Openly licensed video frames and example copy; a layout reference only, never a source of editorial facts.'}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--font', required=True, type=Path, help='bold CJK font, normally C:/Windows/Fonts/msyhbd.ttc')
    parser.add_argument('--font-index', type=int, default=0)
    # Left unset, the renderer resolves the tools from PATH itself.
    parser.add_argument('--ffmpeg')
    parser.add_argument('--ffprobe')
    parser.add_argument('--out-dir', type=Path, default=SCRIPTS.parent/'assets/references/v3-reference')
    parser.add_argument('--work-dir', type=Path, help='keep the intermediate renders here; default is a temporary directory')
    parser.add_argument('--compare-dir', type=Path, help='report whether the committed stills here have the same dimensions')
    args = parser.parse_args()
    if not args.font.is_file():
        print(json.dumps({'status': 'REFERENCE_FAILED', 'error': f'font missing: {args.font}'}, ensure_ascii=False))
        return 1
    temporary = None if args.work_dir else Path(tempfile.mkdtemp(prefix='news-editor-reference-'))
    work = args.work_dir or temporary
    work.mkdir(parents=True, exist_ok=True)
    try:
        print(json.dumps(build(work, args), ensure_ascii=False))
        return 0
    except (RuntimeError, OSError, ValueError, KeyError) as error:
        print(json.dumps({'status': 'REFERENCE_FAILED', 'error': str(error)[-1500:]}, ensure_ascii=False))
        return 1
    finally:
        if temporary:
            shutil.rmtree(temporary, ignore_errors=True)


if __name__ == '__main__':
    raise SystemExit(main())
