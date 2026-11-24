#!/usr/bin/env python3
"""Brief-format renderer for layout lock v3. One timeline, measured glyph boxes, one video encode.

Frame 0 is the cover. The body stacks a blurred copy of the current footage, the footage
band, a static headline block and caption blocks that slide in from the right with motion
blur. The text-led variant (brief_text) draws a blue background, a translucent panel and
lines that accumulate. Pillow draws packaging, never news imagery.
--diagnostic may render a rejected cover for an INTERNAL regression, never FINAL_READY.
"""
from __future__ import annotations
import argparse
import json
import math
import subprocess
import time
from pathlib import Path
import numpy as np
from PIL import Image, ImageColor, ImageDraw, ImageFont, ImageFilter
from production_contract import (load_timeline, read_json, resolve, require, sha256, fullchar_units,
                                 body_geometry, caption_arrival)
from check_cover_geometry import check_geometry

ROOT = Path(__file__).resolve().parent.parent
BLACK = (0, 0, 0, 255)


def run(cmd, timeout=240):
    p = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout)
    if p.returncode:
        raise RuntimeError(p.stderr[-1800:])
    return p


def run_loud(cmd, timeout=240):
    """Encode step: surface FFmpeg diagnostics instead of silently continuing."""
    p = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout)
    if p.returncode or not Path(cmd[-1]).is_file():
        raise RuntimeError('FFmpeg produced no output. ' + p.stderr[-1800:])
    return p


def geometry(crop, target):
    x, y, w, h = crop
    require(all(type(v) is int for v in crop) and min(x, y) >= 0 and min(w, h) > 0, 'Invalid crop')
    scale = max(target[0]/w, target[1]/h)
    return math.ceil(w*scale), math.ceil(h*scale)


def still_motion_filter(motion, frames, fps, width, height, supersample):
    """Deterministic linear Ken Burns motion in normalized anchor coordinates.

    zoompan samples the input at whole pixels; rendering the motion directly at
    output size snaps every step and visibly stutters. The input is therefore
    upscaled by a whole-number factor first, so each output step is sub-pixel.
    """
    require(type(supersample) is int and supersample >= 2,
            'Still motion requires whole-number supersampling >= 2 (config still_media.motion_supersample)')
    sz, ez = motion['start_zoom'], motion['end_zoom']
    sx, sy = motion['start_anchor']
    ex, ey = motion['end_anchor']
    denominator = max(frames-1, 1)
    z = f'{sz}+({ez-sz})*on/{denominator}'
    ax = f'{sx}+({ex-sx})*on/{denominator}'
    ay = f'{sy}+({ey-sy})*on/{denominator}'
    return (f'scale={width*supersample}:{height*supersample}:flags=lanczos,'
            f"zoompan=z='{z}':x='(iw-iw/zoom)*({ax})':y='(ih-ih/zoom)*({ay})'"
            f':d={frames}:s={width}x{height}:fps={fps}')


def ease_out(t):
    return 1-(1-t)**5


def rgba(value):
    if isinstance(value, list):
        return tuple(int(v) for v in value)
    return ImageColor.getrgb(value)+(255,)


class Type:
    """One approved bold font file; no silent fallback to another face."""

    def __init__(self, path: Path, index: int):
        require(path.is_file(), 'Required font missing; no silent fallback')
        self.path, self.index, self.cache = path, index, {}

    def font(self, size):
        if size not in self.cache:
            self.cache[size] = ImageFont.truetype(str(self.path), size, index=self.index)
        return self.cache[size]


def text_layer(text, font, fill, outline=0, outline_fill=BLACK, weight=0):
    """Tight RGBA layer of one line. `outline` is a contrasting ring; `weight` thickens the glyphs themselves."""
    measure = ImageDraw.Draw(Image.new('RGBA', (1, 1)))
    ring = outline+weight
    left, top, right, bottom = measure.textbbox((0, 0), text, font=font, stroke_width=ring)
    pad = 2
    layer = Image.new('RGBA', (right-left+2*pad, bottom-top+2*pad), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    x, y = pad-left, pad-top
    if ring:
        draw.text((x, y), text, font=font, fill=fill, stroke_width=ring, stroke_fill=outline_fill)
    if weight:
        draw.text((x, y), text, font=font, fill=fill, stroke_width=weight, stroke_fill=fill)
    draw.text((x, y), text, font=font, fill=fill)
    return layer


def rich_text_layer(text, font, highlights, fill, highlight_fill, outline):
    """One line with keyword runs recolored; used by the text-led variant."""
    runs, cursor = [], 0
    remaining = text
    while remaining:
        hits = [(remaining.find(h), h) for h in highlights if h in remaining]
        if not hits:
            runs.append((remaining, fill))
            break
        at, hit = min(hits, key=lambda item: item[0])
        if at:
            runs.append((remaining[:at], fill))
        runs.append((hit, highlight_fill))
        remaining = remaining[at+len(hit):]
    total = sum(font.getlength(run) for run, _ in runs)
    left, top, right, bottom = font.getbbox(text, stroke_width=outline)
    pad = 2
    layer = Image.new('RGBA', (math.ceil(total)+2*outline+2*pad, bottom-top+2*pad), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    x = pad-left
    for run, color in runs:
        draw.text((x, pad-top), run, font=font, fill=color, stroke_width=outline, stroke_fill=BLACK)
        x += font.getlength(run)
    return layer


def shear(layer, amount):
    """Right-leaning oblique: the top edge shifts right, the bottom edge stays."""
    if not amount:
        return layer
    w, h = layer.size
    extra = math.ceil(abs(amount)*h)+2
    canvas = Image.new('RGBA', (w+extra, h), (0, 0, 0, 0))
    canvas.paste(layer.transform((w+extra, h), Image.AFFINE, (1, amount, -amount*h, 0, 1, 0),
                                 resample=Image.BICUBIC))
    return canvas


def tag_layer(text, font, background, foreground, pad_x, pad_y, radius, weight, amount):
    body = text_layer(text, font, foreground, 0, weight=weight)
    w, h = body.width+2*pad_x, body.height+2*pad_y
    layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle((0, 0, w-1, h-1), radius=radius, fill=background)
    layer.alpha_composite(body, (pad_x, pad_y))
    return shear(layer, amount)


def fit(layer, max_width, label):
    require(layer.width <= max_width, f'{label} overflows the locked width ({layer.width} > {max_width}px); rewrite, never shrink')
    return layer


def fitted(builder, max_px, min_px, max_width, label):
    """Largest font step whose built layer fits the locked width; the floor still overflowing is a rewrite."""
    for size in range(max_px, min_px-1, -1):
        layer = builder(size)
        if layer.width <= max_width:
            return layer, size
    raise ValueError(f'{label} overflows the locked width even at the minimum font size '
                     f'({builder(min_px).width} > {max_width}px); rewrite, never squeeze')




def compose(canvas, layer, cx=None, cy=None, x=None, y=None, label=''):
    w, h = layer.size
    x = int(round(cx-w/2)) if x is None else int(x)
    y = int(round(cy-h/2)) if y is None else int(y)
    require(x >= 0 and y >= 0 and x+w <= canvas.width and y+h <= canvas.height, f'{label}: placed outside the canvas')
    canvas.alpha_composite(layer, (x, y))
    return [x, y, w, h]


def draw_text(canvas, value, font, box, color, align='left', stroke=0, stroke_fill='black'):
    """Box is XYWH. Align actual visible glyphs, not font ascent/baseline guesses."""
    require(isinstance(value, str) and value and '\n' not in value, 'Text must be one nonempty line')
    draw = ImageDraw.Draw(canvas)
    left, top, right, bottom = draw.textbbox((0, 0), value, font=font, stroke_width=stroke)
    x, y, w, h = box
    tw, th = right-left, bottom-top
    require(tw <= w and th <= h, f'Text overflow at fixed size: {value} ({tw}x{th} > {w}x{h}); rewrite, never shrink')
    ox = x+((w-tw)/2 if align == 'center' else (w-tw if align == 'right' else 0))
    oy = y+(h-th)/2
    draw.text((round(ox-left), round(oy-top)), value, font=font, fill=color,
              stroke_width=stroke, stroke_fill=stroke_fill)
    return [round(ox), round(oy), tw, th]


def headline_layers(plan, lock, type_):
    block, colors = lock['body']['headline_block'], lock['colors']
    fills = {'yellow_tag': (colors['title_yellow'], colors['black']),
             'black_tag': (colors['black'], colors['title_yellow']),
             'yellow_italic': (None, colors['title_yellow']),
             'white_italic': (None, colors['white'])}
    layers = []
    for index, line in enumerate(plan['headline_lines'], 1):
        background, foreground = fills[line['style']]
        if background:
            build = lambda size: tag_layer(line['text'], type_.font(size), rgba(background), rgba(foreground),
                                           block['box_pad_x'], block['box_pad_y'], block['box_corner_radius'],
                                           block['weight_stroke_px'], block['oblique_shear'])
        else:
            build = lambda size: shear(text_layer(line['text'], type_.font(size), rgba(foreground),
                                                 block['outline_px'], BLACK, block['weight_stroke_px']),
                                       block['oblique_shear'])
        layer, size = fitted(build, block['font_px'], block['min_font_px'], block['max_width'], f'headline line {index}')
        layers.append(layer)
    return layers


def header_layer(plan, lock, type_, zones, text_mode):
    """Static packaging above the footage band: headline block, optional result line, disclaimer, source."""
    colors, body, canvas_w, canvas_h = lock['colors'], lock['body'], lock['canvas']['width'], lock['canvas']['height']
    canvas = Image.new('RGBA', (canvas_w, canvas_h), (0, 0, 0, 0))
    if text_mode:
        ImageDraw.Draw(canvas).rectangle(tuple(zones['panel'][:2]+[zones['panel'][0]+zones['panel'][2]-1,
                                                               zones['panel'][1]+zones['panel'][3]-1]),
                                         fill=rgba(colors['brief_panel_rgba']))
    boxes = {}
    block = lock['brief_text']['headline'] if text_mode else body['headline_block']
    layers = headline_layers(plan, lock, type_)
    if text_mode:
        layer = shear(text_layer(plan['headline_lines'][0]['text'], type_.font(block['font_px']),
                                 rgba(colors['title_yellow']), block['outline_px'], BLACK, block['weight_stroke_px']),
                      block['oblique_shear'])
        boxes['headline_1'] = compose(canvas, layer, cx=block['center_x'], cy=block['center_y'], label='headline')
    else:
        centers, edge = [], block['bottom_y']
        for line in reversed(plan['headline_lines']):
            height = block['tag_line_height'] if line['style'].endswith('_tag') else block['italic_line_height']
            centers.append(edge-height/2)
            edge -= height+block['line_gap']
        for index, (layer, cy) in enumerate(zip(layers, reversed(centers))):
            boxes[f'headline_{index+1}'] = compose(canvas, layer, cx=block['center_x'], cy=cy,
                                                    label=f'headline line {index+1}')
    if plan.get('result_line'):
        line = body['result_line']
        build = lambda size: shear(text_layer(plan['result_line'], type_.font(size), rgba(colors['result_red']),
                                              line['outline_px'], rgba(colors['white']), line['weight_stroke_px']),
                                   line['oblique_shear'])
        layer, size = fitted(build, line['font_px'], line['min_font_px'], line['max_width'], 'result line')
        top = zones['result_line'][1]+zones['result_line'][3]/2
        boxes['result_line'] = compose(canvas, layer, cx=line['center_x'], cy=top, label='result line')
    if plan.get('disclaimer_label'):
        note = body['disclaimer']
        layer = text_layer(plan['disclaimer_label'], type_.font(note['font_px']), rgba(colors['white']),
                           note['outline_px'], BLACK, note['weight_stroke_px'])
        boxes['disclaimer'] = compose(canvas, layer, x=note['left_x'], y=zones['disclaimer'][1], label='disclaimer')
    if plan.get('source_label'):
        boxes['source_label'] = draw_text(canvas, plan['source_label'], type_.font(body['source_font_px']),
                                          body['source_safe_bbox'], colors['source_gray'], 'right')
    return canvas, boxes


def caption_lines(block, lock, type_, text_mode):
    """One RGBA layer per caption line, in order, sheared and fitted to the locked width."""
    colors, cap, note = lock['colors'], lock['body']['caption'], lock['brief_text']['text']
    outline = cap['outline_px']
    amount, layers = (note['oblique_shear'] if text_mode else cap['oblique_shear']), []
    for text in block['lines']:
        if text_mode:
            layer = fit(rich_text_layer(text, type_.font(note['font_px']), block.get('highlights') or [],
                                        rgba(colors['white']), rgba(colors['title_yellow']), 2),
                        cap['max_width'], f'caption {block["id"]}')
            layers.append(shear(layer, amount))
            continue
        build = lambda size: shear(text_layer(text, type_.font(size), rgba(colors['title_yellow']), outline,
                                              BLACK, cap['weight_stroke_px']), amount)
        layers.append(fitted(build, cap['font_px'], cap['min_font_px'], cap['max_width'], f'caption {block["id"]}')[0])
    return layers


def stack(lines, pitch):
    """Center-aligned stack of line layers with a fixed line pitch."""
    height = pitch*(len(lines)-1)+max(layer.height for layer in lines)
    canvas = Image.new('RGBA', (max(layer.width for layer in lines), height), (0, 0, 0, 0))
    for index, layer in enumerate(lines):
        compose(canvas, layer, cx=canvas.width/2, cy=height/2+pitch*(index-(len(lines)-1)/2), label='caption line')
    return canvas


def slide_frames(block_image, final_x, final_y, direction, canvas_size, frames, taps):
    """Full-canvas RGBA frames moving a caption in from the right (direction=1) or out to the left (-1).

    Each frame is a premultiplied average of weighted taps between the previous and the current
    offset; the leading edge carries the most weight, so the trailing side fades like real motion blur.
    """
    w, _ = canvas_size
    bw, bh = block_image.size
    distance = (w-final_x+8) if direction > 0 else (final_x+bw+8)
    arr = np.asarray(block_image).astype(np.float32)
    alpha = arr[..., 3:4]/255
    straight = arr.copy()
    arr[..., :3] *= alpha
    previous, output = 0.0, []
    for index in range(frames):
        reached = ease_out((index+1)/frames)
        start = distance*((1-previous) if direction > 0 else -previous)
        end = distance*((1-reached) if direction > 0 else -reached)
        frame = Image.new('RGBA', canvas_size, (0, 0, 0, 0))
        if abs(start-end) < 1:
            frame.alpha_composite(block_image, (int(round(final_x+end)), int(round(final_y))))
        else:
            band, weight_sum = np.zeros((bh, w, 4), np.float32), 0.0
            for tap in range(taps):
                u = tap/(taps-1)
                x = int(round(final_x+start+(end-start)*u))
                lo, hi = max(0, x), min(w, x+bw)
                if hi > lo:
                    band[:, lo:hi] += arr[:, lo-x:hi-x]*(0.25+u*u)
                weight_sum += 0.25+u*u
            band /= weight_sum
            band[..., :3] = np.where(band[..., 3:4] > 0.5, band[..., :3]*255/np.maximum(band[..., 3:4], 1), 0)
            frame.paste(Image.fromarray(band.clip(0, 255).astype(np.uint8)), (0, int(round(final_y))))
        output.append(frame)
        previous = reached
    return output


def brief_background(lock, extra):
    """Procedural blue tech background for the text-led variant: gradient stops plus soft light streaks."""
    w, h = lock['canvas']['width'], lock['canvas']['height']+extra
    stops = [(position, ImageColor.getrgb(color)) for position, color in lock['colors']['brief_background_stops']]
    ramp = np.zeros((h, 3), np.float32)
    for y in range(h):
        t = y/(h-1)
        for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
            if p0 <= t <= p1:
                k = (t-p0)/(p1-p0)
                ramp[y] = [a+(b-a)*k for a, b in zip(c0, c1)]
                break
    image = Image.fromarray(np.repeat(ramp[:, None, :], w, axis=1).astype(np.uint8))
    streaks = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(streaks)
    for index in range(9):
        base = 150+index*62
        amplitude = 70+index*9
        phase = index*0.9
        points = [(x, base+amplitude*math.sin(x/w*math.pi*2.3+phase)) for x in range(0, w+8, 8)]
        draw.line(points, fill=(150, 215, 255, 26 if index % 3 else 48), width=3)
    streaks = streaks.filter(ImageFilter.GaussianBlur(1.6))
    image = Image.alpha_composite(image.convert('RGBA'), streaks).convert('RGB')
    return image


def save_cover(plan, lock, config, type_, directory, ff, probe, base, diagnostic):
    cover, cfg = plan['cover'], config['cover']
    source = resolve(base, cover['path'])
    require(sha256(source) == cover['source_sha256'], 'Cover source changed')
    if cover['source_kind'] == 'image':
        raw = Image.open(source).convert('RGB')
        size = raw.size
    else:
        frame = directory/'cover-source-frame.png'
        run([ff, '-hide_banner', '-loglevel', 'error', '-y', '-ss', str(cover['source_in_seconds']),
             '-i', str(source), '-frames:v', '1', str(frame)])
        raw = Image.open(frame).convert('RGB')
        info = json.loads(run([probe, '-v', 'error', '-select_streams', 'v:0', '-show_streams', '-of', 'json',
                               str(source)]).stdout)['streams'][0]
        size = (int(info['width']), int(info['height']))
    native = directory/'cover-source-original.png'
    raw.save(native)
    x, y, cw, ch = cover['crop']
    require(x >= 0 and y >= 0 and x+cw <= size[0] and y+ch <= size[1], 'Cover crop out of bounds')
    bx, by, bw, bh = lock['cover']['background_video']
    sw, sh = geometry(cover['crop'], (bw, bh))
    q = cfg['quality']
    checked = check_geometry(size, cover['crop'], [sw, sh], [bw, bh],
                             preferred_max_upscale_ratio=q['preferred_max_upscale_ratio'],
                             hard_max_upscale_ratio=q['hard_max_upscale_ratio'],
                             scale_ratio_tolerance=q['scale_ratio_tolerance'])
    (directory/'cover-geometry.json').write_text(json.dumps(checked, ensure_ascii=False, indent=2), encoding='utf-8')
    blocked = bool(checked.get('failures')) or cover.get('clean_image_reviewed') is not True
    if blocked and not diagnostic:
        raise ValueError('BLOCKED_VISUAL: cover geometry or clean-frame review failed; previews retained, no video encoded')
    background = raw.crop((x, y, x+cw, y+ch)).resize((sw, sh), Image.Resampling.LANCZOS)
    background = background.crop(((sw-bw)//2, (sh-bh)//2, (sw-bw)//2+bw, (sh-bh)//2+bh))
    canvas = Image.new('RGB', (lock['canvas']['width'], lock['canvas']['height']),
                       ImageColor.getrgb(lock['colors'][lock['cover']['canvas_fill']]))
    canvas.paste(background, (bx, by))
    canvas.save(directory/'cover-background.png')
    canvas = canvas.convert('RGBA')
    canvas.alpha_composite(Image.open(ROOT/config['cover']['frame_overlay']).convert('RGBA'))
    boxes = {}
    headline_anchor, sub_anchor = lock['cover']['headline_visible_bbox'], lock['cover']['subline_visible_bbox']
    # Reference bboxes are XYXY sample measurements, not fixed widths for every new string.
    def fitted_font(value, max_px, min_px, stroke):
        """Largest size up to the lock maximum whose real glyph run fits the cover text width."""
        width = type_.font(max_px).getlength(value)
        size = min(max_px, int((cfg['max_text_width']-2*stroke-6)*max_px/max(width, 1)))
        require(size >= min_px, f'Cover text "{value}" does not fit above the minimum font size; rewrite, never squeeze')
        return type_.font(size)
    margin = (canvas.width-cfg['max_text_width'])//2
    for key, max_px, min_px, color, stroke in (
            ('headline', cfg['headline_font_px'], cfg['headline_min_font_px'], lock['colors']['cover_cyan'], 5),
            ('subline', cfg['subline_font_px'], cfg['subline_min_font_px'], 'white', 4)):
        anchor = headline_anchor if key == 'headline' else sub_anchor
        font = fitted_font(cover[key], max_px, min_px, stroke)
        size_px = font.size
        center_y = (anchor[1]+anchor[3])/2
        box = [margin, center_y-size_px*.6, canvas.width-2*margin, size_px*1.2]
        boxes['cover_'+key] = draw_text(canvas, cover[key], font, box, color, 'center', stroke)
        boxes['cover_'+key+'_font_px'] = size_px
    cover_path = directory/'cover-master.png'
    canvas.convert('RGB').save(cover_path)
    canvas.convert('RGB').crop((bx, by, bx+bw, by+bh)).save(directory/'cover-3x4.png')
    canvas.convert('RGB').resize((270, 480), Image.Resampling.LANCZOS).save(directory/'cover-270x480.png')
    canvas.crop((bx, by, bx+bw, by+bh)).convert('RGB').resize((270, 360), Image.Resampling.LANCZOS).save(directory/'cover-270x360.png')
    return cover_path, native, boxes, blocked


def caption_units(plan, lock, config, type_, text_mode, directory, boxes):
    """Caption images with their arrival/exit frames and saved slide sequences.

    brief_video: one unit per caption block, later blocks replace earlier ones.
    brief_text: one unit per line; lines accumulate down the panel.
    """
    w, h, fps = lock['canvas']['width'], lock['canvas']['height'], plan['fps']
    cap, note = lock['body']['caption'], lock['brief_text']['text']
    slide, taps, units = cap['slide_frames'], cap['slide_taps'], []
    reveal = math.ceil(config['video']['timing']['text_line_reveal_seconds']*fps)
    cursor_y = note['first_line_center_y']
    blocks = plan['caption_blocks']
    for index, block in enumerate(blocks):
        lines = caption_lines(block, lock, type_, text_mode)
        arrival = caption_arrival(block, index, fps, config, lock)
        if not text_mode:
            image = stack(lines, cap['line_pitch'])
            x, y = int(round(cap['center_x']-image.width/2)), int(round(cap['center_y']-image.height/2))
            begin = arrival-slide
            exit_frame = (caption_arrival(blocks[index+1], index+1, fps, config, lock)-slide
                          if index+1 < len(blocks) else None)
            groups = [(f'{block["id"]}', image, x, y, begin, arrival, exit_frame)]
        else:
            groups = []
            for order, image in enumerate(lines):
                groups.append((f'{block["id"]}-{order+1}', image, int(round(note['center_x']-image.width/2)),
                               int(round(cursor_y-image.height/2)), arrival-slide+order*reveal,
                               arrival+order*reveal, None))
                cursor_y += note['line_pitch']
            cursor_y += note['paragraph_gap']
        for unit_id, image, x, y, begin, land, exit_frame in groups:
            tag = f'{len(units)+1:02}'
            frame = Image.new('RGBA', (w, h), (0, 0, 0, 0))
            frame.alpha_composite(image, (x, y))
            frame.save(directory/f'caption-{tag}-hold.png')
            boxes[f'caption_{unit_id}'] = [x, y, image.width, image.height]
            for k, item in enumerate(slide_frames(image, x, y, 1, (w, h), slide, taps)):
                item.save(directory/f'caption-{tag}-in-{k:02}.png')
            if exit_frame is not None:
                for k, item in enumerate(slide_frames(image, x, y, -1, (w, h), slide, taps)):
                    item.save(directory/f'caption-{tag}-out-{k:02}.png')
            units.append({'id': unit_id, 'tag': tag, 'begin': begin, 'arrival': land, 'exit': exit_frame})
    return units


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeline', required=True, type=Path)
    parser.add_argument('--out-dir', required=True, type=Path)
    parser.add_argument('--font', required=True, type=Path)
    parser.add_argument('--font-index', type=int, default=0, help='face index inside a font collection')
    parser.add_argument('--ffmpeg', default='ffmpeg')
    parser.add_argument('--ffprobe', default='ffprobe')
    parser.add_argument('--render', action='store_true', help='Also encode video; default prepares previews only')
    parser.add_argument('--diagnostic', action='store_true', help='INTERNAL rejected-cover reproduction, never production')
    args = parser.parse_args()
    started = time.perf_counter()
    plan = load_timeline(args.timeline)
    require(args.font.is_file(), 'Required font missing; no silent fallback')
    config = read_json(ROOT/'config.json')
    lock_path = ROOT/config['layout']['active_lock_file']
    lock = read_json(lock_path)
    w, h, fps = lock['canvas']['width'], lock['canvas']['height'], plan['fps']
    type_ = Type(args.font, args.font_index)
    text_mode = plan['format'] == 'brief_text'
    directory = args.out_dir.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    ff, probe, base = args.ffmpeg, args.ffprobe, args.timeline.resolve().parent
    zones = body_geometry(lock, plan)
    cover_path, cover_native, cover_boxes, blocked = save_cover(
        plan, lock, config, type_, directory, ff, probe, base, args.diagnostic)
    header, boxes = header_layer(plan, lock, type_, zones, text_mode)
    header.save(directory/'header-overlay.png')
    boxes.update(cover_boxes)
    fx, fy, fw, fh = (zones['panel'] if text_mode else zones['footage'])
    units = caption_units(plan, lock, config, type_, text_mode, directory, boxes)
    for item in plan.get('overlays') or []:
        layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        ox, oy, ow, oh = item['box']
        with Image.open(resolve(base, item['path'])) as shot:
            layer.alpha_composite(shot.convert('RGBA').resize((ow, oh), Image.Resampling.LANCZOS), (ox, oy))
        item['_layer'] = directory/f'overlay-{item["start_frame"]:05d}.png'
        layer.save(item['_layer'])
        boxes[f'overlay_{item["start_frame"]}'] = list(item['box'])
    # Build all temporal intervals from the same half-open frame timeline.
    # Input 0 is the cover image; it is overlaid on a one-frame pad in front of the body.
    inputs = ['-loop', '1', '-framerate', str(fps), '-i', str(cover_path)]
    parts, labels, clips, probe_cache, index = [], [], plan.get('clips') or [], {}, 1
    supersample = config['video']['still_media']['motion_supersample']
    footage = lock['body']['footage']
    total_body = plan['total_frames']-plan['cover_frames']
    for n, clip in enumerate(clips, 1):
        src = resolve(base, clip['path'])
        if str(src) not in probe_cache:
            if clip['media_type'] == 'video':
                probe_cache[str(src)] = json.loads(run([probe, '-v', 'error', '-select_streams', 'v:0',
                                                        '-show_streams', '-of', 'json', str(src)]).stdout)['streams'][0]
            else:
                with Image.open(src) as still:
                    probe_cache[str(src)] = {'width': still.width, 'height': still.height, 'sample_aspect_ratio': '1:1'}
        info = probe_cache[str(src)]
        frames = clip['end_frame']-clip['start_frame']
        if clip['media_type'] == 'video':
            require(info.get('sample_aspect_ratio', '1:1') in ('1:1', 'N/A'), 'Normalize non-square pixels explicitly first')
            require(not any(x.get('rotation', 0) for x in info.get('side_data_list', [])), 'Normalize rotated source explicitly first')
            require(clip['source_in_seconds']+frames/fps <= float(info['duration'])+1/fps, 'Source cannot cover selected interval')
            inputs += ['-ss', str(clip['source_in_seconds']), '-i', str(src)]
        else:
            inputs += ['-loop', '1', '-framerate', str(fps), '-i', str(src)]
        cx, cy, cw, ch = clip['crop']
        require(cx >= 0 and cy >= 0 and cx+cw <= info['width'] and cy+ch <= info['height'], 'Clip crop out of bounds')
        sw, sh = geometry(clip['crop'], (fw, fh))
        require(max(sw/cw, sh/ch) <= lock['media_quality']['hard_max_upscale_ratio']+.005, 'Body crop over-enlarged')
        if clip['media_type'] == 'video':
            # tpad only covers sub-frame loss of the frame-rate conversion at the tail (never real short footage,
            # which the contract rejects); trim then yields exactly the planned frame count.
            source_chain = (f'[{index}:v]fps={fps},tpad=stop_mode=clone:stop={frames},trim=end_frame={frames},'
                            f'setpts=N/({fps}*TB),setsar=1')
        else:
            source_chain = f'[{index}:v]trim=end_frame=1'
        parts.append(source_chain+f',split=2[full{n}][src{n}]')
        # The band shows the chosen crop; the backdrop is the whole source frame, cover-fitted and blurred.
        chain = f'[src{n}]crop={cw}:{ch}:{cx}:{cy},scale={sw}:{sh}:flags=lanczos,crop={fw}:{fh}'
        if clip['media_type'] != 'video':
            chain += (f',{still_motion_filter(clip["motion"], frames, fps, fw, fh, supersample)}'
                      f',trim=end_frame={frames},setpts=N/({fps}*TB)')
        if clip.get('blur'):
            mx, my, mw, mh = clip['blur']
            require(mx >= 0 and my >= fh*.7 and mw > 0 and mh > 0 and mx+mw <= fw and my+mh <= fh,
                    'Only a bounded bottom-subtitle blur is supported; coordinates refer to fitted footage')
            chain += f',split=2[b{index}][m{index}]'
            parts.append(chain)
            parts.extend([f"[m{index}]crop={mw}:{mh}:{mx}:{my},boxblur=18:2,format=yuva444p,"
                          f"geq=lum='lum(X,Y)':cb='cb(X,Y)':cr='cr(X,Y)':a='255*clip(Y/12,0,1)'[blur{index}]",
                          f'[b{index}][blur{index}]overlay={mx}:{my}[clean{index}]'])
            chain = f'[clean{index}]'
        parts.append(chain+f'[bn{n}]')
        scale = footage['background_zoom']*max(w/info['width'], h/info['height'])
        still_full = (f'tpad=stop_mode=clone:stop={frames},trim=end_frame={frames},setpts=N/({fps}*TB),'
                      if clip['media_type'] != 'video' else '')
        parts.append(f'[full{n}]{still_full}scale={math.ceil(info["width"]*scale)}:{math.ceil(info["height"]*scale)}:flags=bilinear,'
                     f'crop={w}:{h},setsar=1,gblur=sigma={footage["background_blur_sigma"]}[bg{n}]')
        parts.append(f'[bg{n}][bn{n}]overlay=0:{fy}:format=auto,format=yuv420p[c{n}]')
        labels.append(f'[c{n}]')
        index += 1
    if text_mode:
        background = directory/'brief-background.png'
        drift = lock['brief_text']['background']['drift_px']
        brief_background(lock, drift).save(background)
        inputs += ['-loop', '1', '-framerate', str(fps), '-i', str(background)]
        parts.append(f"[{index}:v]crop={w}:{h}:0:'(ih-{h})*n/{max(total_body-1, 1)}',trim=end_frame={total_body},"
                     f'setpts=N/({fps}*TB),format=yuv420p[t0]')
        labels.append('[t0]')
        index += 1
    total = plan['total_frames']
    parts.append(''.join(labels)+f'concat=n={len(labels)}:v=1:a=0,trim=end_frame={total_body},'
                 f'tpad=start=1:start_mode=add:color=black,setpts=N/({fps}*TB)[body]')
    # Every overlay is a finite stream that exists exactly on its own frame interval; none uses a looped
    # secondary or `shortest`, so the main frame count can never change inside the chain.
    parts.append(f'[0:v]trim=end_frame=1,setpts=PTS-STARTPTS[cov]')
    parts.append('[body][cov]overlay=0:0:eof_action=pass[cover]')
    inputs += ['-loop', '1', '-framerate', str(fps), '-i', str(directory/'header-overlay.png')]
    parts.append(f'[{index}:v]trim=end_frame={total-1},setpts=PTS-STARTPTS+1/{fps}/TB[hdr]')
    parts.append('[cover][hdr]overlay=0:0:eof_action=pass[v0]')
    stage, count, index = 'v0', 0, index+1
    for item in plan.get('overlays') or []:
        inputs += ['-loop', '1', '-framerate', str(fps), '-i', str(item['_layer'])]
        span = item['end_frame']-item['start_frame']
        parts.append(f"[{index}:v]trim=end_frame={span},setpts=PTS-STARTPTS+{item['start_frame']}/{fps}/TB[ov{count}]")
        parts.append(f'[{stage}][ov{count}]overlay=0:0:eof_action=pass[v{count+1}]')
        stage, count, index = f'v{count+1}', count+1, index+1
    slide = lock['body']['caption']['slide_frames']
    for unit in units:
        tag = unit['tag']
        begin, arrival, limit = unit['begin'], unit['arrival'], unit['exit'] if unit['exit'] is not None else total
        inputs += ['-framerate', str(fps), '-i', str(directory/f'caption-{tag}-in-%02d.png')]
        parts.append(f"[{index}:v]format=rgba,setpts=PTS-STARTPTS+{begin}/{fps}/TB[ci{count}]")
        parts.append(f"[{stage}][ci{count}]overlay=0:0:eof_action=pass:enable='between(n,{begin},{arrival-1})'[v{count+1}]")
        stage, count, index = f'v{count+1}', count+1, index+1
        inputs += ['-loop', '1', '-framerate', str(fps), '-i', str(directory/f'caption-{tag}-hold.png')]
        parts.append(f"[{index}:v]trim=end_frame={limit-arrival},setpts=PTS-STARTPTS+{arrival}/{fps}/TB[ch{count}]")
        parts.append(f"[{stage}][ch{count}]overlay=0:0:eof_action=pass:enable='between(n,{arrival},{limit-1})'[v{count+1}]")
        stage, count, index = f'v{count+1}', count+1, index+1
        if unit['exit'] is not None:
            inputs += ['-framerate', str(fps), '-i', str(directory/f'caption-{tag}-out-%02d.png')]
            parts.append(f"[{index}:v]format=rgba,setpts=PTS-STARTPTS+{unit['exit']}/{fps}/TB[co{count}]")
            parts.append(f"[{stage}][co{count}]overlay=0:0:eof_action=pass:"
                         f"enable='between(n,{unit['exit']},{unit['exit']+slide-1})'[v{count+1}]")
            stage, count, index = f'v{count+1}', count+1, index+1
    # Overlay frame sync accumulates sub-tick rounding; rebuilding PTS here keeps every frame on its own tick
    # (with eight or more chained overlays the last frame otherwise collided and was dropped).
    # The trailing fps filter stamps a real duration on every frame. Without it the last frame has duration 0,
    # MP4 records the media length as the last frame's start time, and the edit list then discards that frame
    # whenever the total lands on a whole millisecond boundary (e.g. 10.9 s).
    parts.append(f'[{stage}]setpts=N/({fps}*TB),trim=end_frame={total},format=yuv420p,fps={fps}[out]')
    graph = ';'.join(parts)
    (directory/'render-filter.txt').write_text(graph, encoding='utf-8')
    output = directory/('INTERNAL-DRAFT.mp4' if args.diagnostic else 'draft.mp4')
    report = {'status': 'BLOCKED_VISUAL' if blocked else 'NEEDS_VISUAL_REVIEW', 'final_ready': False,
              'diagnostic': args.diagnostic, 'timeline_sha256': sha256(args.timeline), 'lock_sha256': sha256(lock_path),
              'font_sha256': sha256(args.font), 'renderer_sha256': sha256(Path(__file__)),
              'cover_sha256': sha256(cover_path), 'cover_source_sha256': sha256(cover_native),
              'cover_source_kind': plan['cover']['source_kind'], 'format': plan['format'],
              'body_media_types': [c['media_type'] for c in clips], 'body_geometry': zones,
              'still_motion_supersample': supersample if any(c['media_type'] != 'video' for c in clips) else None,
              'source_label': plan.get('source_label'),
              'derived_publish_title': ' '.join(x['text'] for x in plan['headline_lines']),
              'caption_units': units, 'text_placements': boxes,
              'timings': {'prepare_seconds': time.perf_counter()-started},
              'video': None, 'video_sha256': None,
              'note': 'Geometry and glyph boxes do not establish source cleanliness, semantic correctness or platform approval.'}
    if args.render:
        started_encode = time.perf_counter()
        command = [ff, '-hide_banner', '-loglevel', 'error', '-y', *inputs, '-filter_complex_threads', '2',
                  '-filter_complex', graph, '-map', '[out]', '-an', '-c:v', 'libx264', '-preset', 'fast', '-crf', '18',
                  '-threads', '4', '-pix_fmt', config['video']['pixel_format'], '-fps_mode', 'passthrough',
                  '-movflags', '+faststart', str(output)]
        (directory/'render-command.json').write_text(json.dumps(command, ensure_ascii=False, indent=1), encoding='utf-8')
        run_loud(command, timeout=1800)
        report['timings']['encode_seconds'] = time.perf_counter()-started_encode
        report['video'], report['video_sha256'] = str(output), sha256(output)
    (directory/'render-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('status', 'diagnostic', 'format', 'video', 'derived_publish_title')},
                     ensure_ascii=False))
    return 0
    print(json.dumps({k: report[k] for k in ('status', 'diagnostic', 'format', 'video', 'derived_publish_title')},
                     ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
