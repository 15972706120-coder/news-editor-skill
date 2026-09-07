#!/usr/bin/env python3
"""Fixed three-panel renderer. One timeline, measured glyph boxes, one video encode.

Cover uses a separately acquired still image. Body footage may include a small,
contract-limited amount of sourced still media with deterministic keyframe motion.
Pillow draws packaging, never news imagery.
--diagnostic may render a rejected cover for an INTERNAL regression, never FINAL_READY.
"""
from __future__ import annotations
import argparse
import json
import math
import subprocess
import time
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageColor
from production_contract import load_timeline, read_json, resolve, require, sha256
from check_cover_geometry import check_geometry

ROOT = Path(__file__).resolve().parent.parent


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=240)
    if p.returncode:
        raise RuntimeError(p.stderr[-1600:])
    return p


def geometry(crop, target):
    x, y, w, h = crop
    require(all(type(v) is int for v in crop) and min(x, y) >= 0 and min(w, h) > 0, 'Invalid crop')
    scale = max(target[0]/w, target[1]/h)
    return math.ceil(w*scale), math.ceil(h*scale)


def still_motion_filter(motion, frames, fps, width, height):
    """Deterministic linear Ken Burns motion in normalized anchor coordinates."""
    sz,ez=motion['start_zoom'],motion['end_zoom']
    sx,sy=motion['start_anchor']; ex,ey=motion['end_anchor']
    denominator=max(frames-1,1)
    z=f'{sz}+({ez-sz})*on/{denominator}'
    ax=f'{sx}+({ex-sx})*on/{denominator}'
    ay=f'{sy}+({ey-sy})*on/{denominator}'
    return (f"zoompan=z='{z}':x='(iw-iw/zoom)*({ax})':y='(ih-ih/zoom)*({ay})'"
            f':d={frames}:s={width}x{height}:fps={fps}')


def gradient(lock):
    w, h = lock['canvas']['width'], lock['canvas']['height']
    top = ImageColor.getrgb(lock['colors']['board_gradient_top'])
    bottom = ImageColor.getrgb(lock['colors']['board_gradient_bottom'])
    image = Image.new('RGBA', (w, h))
    draw = ImageDraw.Draw(image)
    for y in range(h):
        color = tuple(round(a+(b-a)*y/(h-1)) for a, b in zip(top, bottom))
        draw.line((0, y, w, y), fill=(*color, 255))
    return image


def draw_text(image, value, font_path, size, box, color, align='left', stroke=0, stroke_fill='black'):
    """Box is XYWH. Align actual visible glyphs, not font ascent/baseline guesses."""
    require(isinstance(value, str) and value and '\n' not in value, 'Text must be one nonempty line')
    font = ImageFont.truetype(str(font_path), size)
    draw = ImageDraw.Draw(image)
    left, top, right, bottom = draw.textbbox((0, 0), value, font=font, stroke_width=stroke)
    x, y, w, h = box
    tw, th = right-left, bottom-top
    require(tw <= w and th <= h, f'Text overflow at fixed {size}px: {value} ({tw}x{th} > {w}x{h}); rewrite, never shrink')
    ox = x + ((w-tw)/2 if align == 'center' else (w-tw if align == 'right' else 0))
    oy = y + (h-th)/2
    draw.text((round(ox-left), round(oy-top)), value, font=font, fill=color,
              stroke_width=stroke, stroke_fill=stroke_fill)
    return [round(ox), round(oy), tw, th]


def preview_layers(plan, lock, font, directory):
    body, colors = lock['body_page'], lock['colors']
    w = lock['canvas']['width']
    records, paths = [], []
    for i, page in enumerate(plan['pages']):
        image = gradient(lock)
        d = ImageDraw.Draw(image)
        x,y,fw,fh = body['footage']
        d.rectangle((x,y,x+fw-1,y+fh-1), fill=(0,0,0,0))
        for name in ('subtitle_bar','section_label'):
            x,y,bw,bh = body[name]
            d.rounded_rectangle((x,y,x+bw-1,y+bh-1), radius=20 if name=='subtitle_bar' else 7, fill=colors['title_yellow'])
        for name in ('divider','bottom_accent'):
            x,y,bw,bh = body[name]
            d.rectangle((x,y,x+bw-1,y+bh-1), fill=colors['title_yellow'])
        boxes = {}
        anchor = body['headline_visible_bbox']
        boxes['headline'] = draw_text(image, plan['headline'], font, body['headline_font_px'],
                                    [26,anchor[1],w-52,anchor[3]-anchor[1]], colors['title_yellow'], 'center')
        x,y,bw,bh = body['subtitle_bar']
        boxes['subtitle'] = draw_text(image, plan['subtitle'], font, body['subtitle_font_px'],
                                    [x+20,y,bw-40,bh], 'black', 'center')
        draw_text(image, '新闻速览', font, 30, body['section_label'], 'black', 'center')
        draw_text(image, f'{i+1:02} / {len(plan["pages"]):02}', font, 26, body['page_number_safe_bbox'], '#AAAAAA', 'right')
        x,y,bw,bh = body['white_copy_safe_bbox']
        boxes['white_lines'] = [draw_text(image, text, font, body['copy_font_px'],
                                        [x,y+j*bh/2,bw,bh/2], colors['white']) for j,text in enumerate(page['white_lines'])]
        boxes['red'] = draw_text(image, page['red_emphasis'], font, body['red_font_px'],
                                body['red_emphasis_safe_bbox'], colors['red_emphasis'], stroke=2, stroke_fill='white')
        if plan.get('source_label'):
            draw_text(image, plan['source_label'], font, 18, body['source_safe_bbox'], '#AAAAAA', 'right')
        target = directory/f'page-{i+1:02}-overlay.png'
        image.save(target)
        paths.append(target)
        records.append({'id':page['id'], 'visible_boxes_xywh':boxes, 'overlay_sha256':sha256(target)})
    return paths, records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeline', required=True, type=Path)
    parser.add_argument('--out-dir', required=True, type=Path)
    parser.add_argument('--font', required=True, type=Path)
    parser.add_argument('--ffmpeg', default='ffmpeg')
    parser.add_argument('--ffprobe', default='ffprobe')
    parser.add_argument('--render', action='store_true', help='Also encode video; default prepares previews only')
    parser.add_argument('--diagnostic', action='store_true', help='INTERNAL rejected-cover reproduction, never production')
    args = parser.parse_args()
    started = time.perf_counter()
    plan = load_timeline(args.timeline)
    require(plan.get('duration_profile') in ('standard','compact'),
            'Production render requires an explicit standard/compact duration profile')
    require(args.font.is_file(), 'Required font missing; no silent fallback')
    config = read_json(ROOT/'config.json')
    lock_path = ROOT/config['layout']['active_lock_file']
    lock = read_json(lock_path)
    require(plan['fps'] == lock['canvas']['fps'], 'FPS differs from active layout')
    directory = args.out_dir.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    ff, probe, base = args.ffmpeg, args.ffprobe, args.timeline.resolve().parent
    w,h,fps = lock['canvas']['width'],lock['canvas']['height'],plan['fps']
    layers, records = preview_layers(plan, lock, args.font, directory)
    cover = plan['cover']
    source = resolve(base, cover['path'])
    require(sha256(source) == cover['source_sha256'], 'Cover source changed')
    require(cover.get('source_kind') == 'image' and cover.get('derived_from_video') is False,
            'Cover must be a separately acquired image, never a video frame')
    raw = Image.open(source).convert('RGB')
    native = directory/'cover-source-original.png'
    raw.save(native)
    x,y,cw,ch = cover['crop']
    bx,by,bw,bh = lock['cover']['background_video']
    sw,sh = geometry(cover['crop'], (bw,bh))
    q = config['cover']['quality']
    checked = check_geometry(raw.size, cover['crop'], [sw,sh], [bw,bh],
                             preferred_max_upscale_ratio=q['preferred_max_upscale_ratio'],
                             hard_max_upscale_ratio=q['hard_max_upscale_ratio'],
                             scale_ratio_tolerance=q['scale_ratio_tolerance'])
    (directory/'cover-geometry.json').write_text(json.dumps(checked,ensure_ascii=False,indent=2),encoding='utf-8')
    blocked = bool(checked.get('failures')) or cover.get('clean_image_reviewed') is not True
    if blocked and not args.diagnostic:
        raise ValueError('BLOCKED_VISUAL: cover geometry or clean-frame review failed; previews retained, no video encoded')
    background = raw.crop((x,y,x+cw,y+ch)).resize((sw,sh),Image.Resampling.LANCZOS)
    background = background.crop(((sw-bw)//2,(sh-bh)//2,(sw-bw)//2+bw,(sh-bh)//2+bh))
    canvas = gradient(lock)
    canvas.paste(background,(bx,by))
    canvas.save(directory/'cover-background.png')
    canvas.alpha_composite(Image.open(ROOT/config['cover']['frame_overlay']).convert('RGBA'))
    headline_anchor, sub_anchor = lock['cover']['headline_visible_bbox'], lock['cover']['subline_visible_bbox']
    # Reference bboxes are XYXY sample measurements, not fixed widths for every new string.
    # Preserve their visual center while measuring each new glyph run at the locked font size.
    for key, text, size, margin, color, stroke in (
        ('headline',cover['headline'],config['cover']['headline_font_px'],80,lock['colors']['cover_cyan'],5),
        ('subline',cover['subline'],config['cover']['subline_font_px'],54,'white',4)):
        anchor = headline_anchor if key=='headline' else sub_anchor
        center_y=(anchor[1]+anchor[3])/2
        box=[margin,center_y-size*.6,w-2*margin,size*1.2]
        records.append({'cover_'+key:draw_text(canvas,text,args.font,size,box,color,'center',stroke)})
    cover_path = directory/'cover-master.png'
    canvas.convert('RGB').save(cover_path)
    canvas.convert('RGB').resize((270,480),Image.Resampling.LANCZOS).save(directory/'cover-270x480.png')
    canvas.crop((bx,by,bx+bw,by+bh)).convert('RGB').resize((270,360),Image.Resampling.LANCZOS).save(directory/'cover-270x360.png')
    # Build all temporal intervals from the same half-open frame timeline.
    inputs=['-loop','1','-framerate',str(fps),'-i',str(cover_path)]
    parts=[f'[0:v]trim=end_frame=1,setpts=N/({fps}*TB),setsar=1,format=yuv420p[c0]']
    labels=['[c0]']
    clips=plan['clips']
    require(clips, 'No source clips')
    cache_probe={}
    for clip in clips:
        src=resolve(base,clip['path'])
        if str(src) not in cache_probe:
            if clip['media_type'] == 'video':
                result=json.loads(run([probe,'-v','error','-select_streams','v:0','-show_streams','-of','json',str(src)]).stdout)['streams'][0]
                cache_probe[str(src)]=result
            else:
                with Image.open(src) as still:
                    cache_probe[str(src)]={'width':still.width,'height':still.height,'sample_aspect_ratio':'1:1'}
        info=cache_probe[str(src)]
        if clip['media_type'] == 'video':
            require(info.get('sample_aspect_ratio','1:1') in ('1:1','N/A'), 'Normalize non-square pixels explicitly first')
            require(not any(x.get('rotation',0) for x in info.get('side_data_list',[])), 'Normalize rotated source explicitly first')
            duration=(clip['end_frame']-clip['start_frame'])/fps
            require(clip['source_in_seconds']+duration <= float(info['duration'])+1/fps, 'Source cannot cover selected interval')
            inputs += ['-ss',str(clip['source_in_seconds']),'-i',str(src)]
        else:
            inputs += ['-loop','1','-framerate',str(fps),'-i',str(src)]
    for layer in layers:
        inputs += ['-loop','1','-framerate',str(fps),'-i',str(layer)]
    fx,fy,fw,fh=lock['body_page']['footage']
    for i,clip in enumerate(clips,1):
        cx,cy,cw,ch=clip['crop']
        info=cache_probe[str(resolve(base,clip['path']))]
        require(cx>=0 and cy>=0 and cx+cw<=info['width'] and cy+ch<=info['height'],'Clip crop out of bounds')
        sw,sh=geometry(clip['crop'],(fw,fh))
        require(max(sw/cw,sh/ch)<=lock['media_quality']['hard_max_upscale_ratio']+.005,'Body crop over-enlarged')
        frames=clip['end_frame']-clip['start_frame']
        page_index=next(j for j,p in enumerate(plan['pages']) if p['id']==clip['page_id'])
        base_filter=f'[{i}:v]crop={cw}:{ch}:{cx}:{cy},scale={sw}:{sh}:flags=lanczos,crop={fw}:{fh}'
        if clip['media_type'] == 'video':
            parts.append(base_filter+f',setsar=1,fps={fps},trim=end_frame={frames},setpts=N/({fps}*TB)[r{i}]')
        else:
            parts.append(base_filter+f',{still_motion_filter(clip["motion"],frames,fps,fw,fh)},trim=end_frame={frames},setpts=N/({fps}*TB)[r{i}]')
        label=f'[r{i}]'
        if clip.get('blur'):
            mx,my,mw,mh=clip['blur']
            require(mx>=0 and my>=fh*.7 and mw>0 and mh>0 and mx+mw<=fw and my+mh<=fh,
                    'Only a bounded bottom-subtitle blur is supported; coordinates refer to fitted footage')
            parts.extend([f'{label}split=2[b{i}][m{i}]',
                          f"[m{i}]crop={mw}:{mh}:{mx}:{my},boxblur=18:2,format=yuva444p,geq=lum='lum(X,Y)':cb='cb(X,Y)':cr='cr(X,Y)':a='255*clip(Y/12,0,1)'[blur{i}]",
                          f'[b{i}][blur{i}]overlay={mx}:{my}[clean{i}]'])
            label=f'[clean{i}]'
        parts.append(label+f'pad={w}:{h}:{fx}:{fy}:color=black[v{i}]')
        labels.append(f'[c{i}]')
    # Explicit split avoids consuming the same FFmpeg pad more than once.
    for j,p in enumerate(plan['pages']):
        indexes=[i for i,c in enumerate(clips,1) if c['page_id']==p['id']]
        parts.append(f'[{len(clips)+1+j}:v]split={len(indexes)}'+''.join(f'[l{i}]' for i in indexes))
    for i,clip in enumerate(clips,1):
        parts.append(f'[v{i}][l{i}]overlay=0:0:shortest=1,format=yuv420p[c{i}]')
    parts.append(''.join(labels)+f'concat=n={len(labels)}:v=1:a=0,setpts=N/({fps}*TB)[out]')
    graph=';'.join(parts)
    (directory/'render-filter.txt').write_text(graph,encoding='utf-8')
    timings={'prepare_seconds':time.perf_counter()-started}
    output=directory/('INTERNAL-DRAFT.mp4' if args.diagnostic else 'draft.mp4')
    if args.render:
        started_encode=time.perf_counter()
        run([ff,'-hide_banner','-loglevel','error','-y',*inputs,'-filter_complex_threads','2',
             '-filter_complex',graph,'-map','[out]','-an','-c:v','libx264','-preset','fast','-crf','18',
             '-threads','4','-pix_fmt',config['video']['pixel_format'],'-fps_mode','passthrough','-movflags','+faststart',str(output)])
        timings['encode_seconds']=time.perf_counter()-started_encode
    report={'status':'BLOCKED_VISUAL' if blocked else 'NEEDS_VISUAL_REVIEW','final_ready':False,
            'diagnostic':args.diagnostic,'timeline_sha256':sha256(args.timeline),'lock_sha256':sha256(lock_path),
            'font_sha256':sha256(args.font),'renderer_sha256':sha256(Path(__file__)),
            'cover_sha256':sha256(cover_path),'cover_source_sha256':sha256(source),
            'body_media_types':[c['media_type'] for c in clips],
            'text_placements':records,'timings':timings,
            'video':str(output) if args.render else None,
            'video_sha256':sha256(output) if args.render else None,
            'note':'Geometry and glyph boxes do not establish source cleanliness, semantic correctness or platform approval.'}
    (directory/'render-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
