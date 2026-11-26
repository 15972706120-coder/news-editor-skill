#!/usr/bin/env python3
"""Caption continuity of an encoded brief: complete on the frame it lands on, present until it leaves,
and a new animation state on every frame of its slide-in.

The frame count and the layout proof sample frames after a caption has landed, so neither notices a caption
that is missing on its landing frame (an overlay stamped a few ticks late did exactly that), nor a six-frame
slide that shows one state twice and drops the next (an overlay stamped exactly, on a main stream whose
timestamps drift a few sub-frame ticks). This decodes every frame at quarter resolution for the hold, and the
slide-in frames at full resolution against the renderer's own slide images (`--render-dir`).
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

from production_contract import caption_arrival, config, lock

SCALE = 4
MASK_ALPHA, MASK_PIXELS = 200, 500


def raw_frames(ffmpeg: str, video, video_filter: str, pix_fmt: str) -> bytes:
    """Raw frames from ffmpeg. A decoder that fails raises with its own message instead of looking like zero frames.

    Frame passthrough is requested the way the renderer and the validator do; the older `-vsync 0`
    spelling is rejected by FFmpeg 9.
    """
    result = subprocess.run([ffmpeg, '-v', 'error', '-i', str(video), '-vf', video_filter, '-fps_mode', 'passthrough',
                             '-f', 'rawvideo', '-pix_fmt', pix_fmt, '-'], capture_output=True)
    if result.returncode:
        raise ValueError(f'ffmpeg exited with {result.returncode} while decoding {Path(video).name}: '
                         + result.stderr.decode('utf-8', 'replace').strip()[-400:])
    return result.stdout


def caption_units(blocks, text_mode, fps, cfg, lk):
    """(key, renderer tag, first slide frame, landing frame) in the renderer's own order."""
    slide, reveal = lk['body']['caption']['slide_frames'], math.ceil(cfg['video']['timing']['text_line_reveal_seconds']*fps)
    units, tag = [], 0
    for index, block in enumerate(blocks):
        arrival = caption_arrival(block, index, fps, cfg, lk)
        keys = [f'{block["id"]}-{order+1}' for order in range(len(block['lines']))] if text_mode else [block['id']]
        for order, key in enumerate(keys):
            tag += 1
            land = arrival+order*reveal
            units.append((key, f'{tag:02}', land-slide, land))
    return units


def state_score(frame, image):
    """Mean error between a frame and one slide state over the state's own near-opaque pixels, with the
    error that state's remaining transparency allows. None when the state has too few such pixels."""
    mask = image[..., 3] >= MASK_ALPHA
    if mask.sum() < MASK_PIXELS:
        return None
    error = float(np.abs(frame[mask]-image[..., :3][mask]).mean())
    tolerance = (1-float(image[..., 3][mask].mean())/255)*255*1.3+8
    return error, tolerance


def slide_problems(ffmpeg: str, video, render_dir: Path, units, width, height, slide) -> list[str]:
    states = {}
    for key, tag, _, _ in units:
        images = []
        for k in range(slide):
            path = render_dir/f'caption-{tag}-in-{k:02}.png'
            if not path.is_file():
                images = None
                break
            with Image.open(path) as image:
                images.append(np.asarray(image.convert('RGBA')))
        states[key] = images
    if any(images is None for images in states.values()):
        return ['slide-in images missing in the render directory']
    wanted = sorted({begin+k for _, _, begin, _ in units for k in range(slide)})
    expression = '+'.join(f'eq(n\\,{frame})' for frame in wanted)
    raw = raw_frames(ffmpeg, video, f"select='{expression}'", 'rgb24')
    decoded = np.frombuffer(raw, dtype=np.uint8)
    if len(decoded) < len(wanted)*width*height*3:
        return [f'only {len(decoded)//(width*height*3)} of {len(wanted)} slide-in frames decoded']
    by_frame = dict(zip(wanted, decoded[:len(wanted)*width*height*3].reshape(-1, height, width, 3)))
    problems = []
    for key, _, begin, _ in units:
        for k in range(slide):
            frame = by_frame[begin+k].astype(int)
            # Every slide state is judged on its own near-opaque pixels, so the backdrop does not matter, not
            # even where the slide starts on a footage cut. Neighbouring states differ by as little as 2 px,
            # so the frame counts as showing the state with the smallest error relative to its tolerance.
            scores = [state_score(frame, image) for image in states[key]]
            shown = [j for j, score in enumerate(scores) if score and score[0] <= score[1]]
            if shown:
                best = min(shown, key=lambda j: scores[j][0]/scores[j][1])
                if best != k:
                    where = 'an earlier' if best < k else 'a later'
                    problems.append(f'{key}: slide-in frame {begin+k} shows {where} animation state ({best}), expected state {k}')
            elif scores[k] is not None:
                problems.append(f'{key}: slide-in frame {begin+k} shows no readable caption (expected state {k})')
    return problems


def caption_problems(ffmpeg: str, video, render_report: dict, timeline: dict, render_dir=None) -> list[str]:
    cfg, lk = config(), lock()
    fps, total = timeline['fps'], timeline['total_frames']
    width, height = lk['canvas']['width']//SCALE, lk['canvas']['height']//SCALE
    try:
        raw = raw_frames(ffmpeg, video, f'scale={width}:{height}', 'gray')
    except ValueError as error:
        return [str(error)]
    frames = np.frombuffer(raw, dtype=np.uint8).reshape(-1, height, width)
    if len(frames) < total:
        return [f'only {len(frames)} of {total} frames decoded']
    text_mode, blocks, places = timeline['format'] == 'brief_text', timeline['caption_blocks'], render_report['text_placements']
    slide = lk['body']['caption']['slide_frames']
    reveal = math.ceil(cfg['video']['timing']['text_line_reveal_seconds']*fps)
    problems = []
    for index, block in enumerate(blocks):
        arrival = caption_arrival(block, index, fps, cfg, lk)
        until = caption_arrival(blocks[index+1], index+1, fps, cfg, lk)-slide if index+1 < len(blocks) else total
        keys = ([f'caption_{block["id"]}-{order+1}' for order in range(len(block['lines']))] if text_mode
                else [f'caption_{block["id"]}'])
        for order, key in enumerate(keys):
            x, y, w, h = places[key]
            zone = (slice(y//SCALE, -(-(y+h))//SCALE), slice(x//SCALE, -(-(x+w))//SCALE))
            land = arrival+order*reveal
            base = frames[max(land-slide-1, 1)][zone].astype(int)
            # How strongly the caption stands out of its box; the background behind it may change at a cut.
            strength = [float(np.abs(frames[frame][zone].astype(int)-base).mean()) for frame in range(land, until)]
            # The landing frame must look like the one right after it; the slow background drift rules out
            # comparing it with the strongest frame of the whole hold.
            settled = strength[1] if len(strength) > 1 else strength[0]
            if settled < 1:
                problems.append(f'{key}: caption not detectable')
                continue
            if strength[0] < 0.9*settled:
                problems.append(f'{key}: blank on its landing frame {land}')
            typical = float(np.median(strength))
            gone = [land+offset for offset, value in enumerate(strength) if value < 0.5*typical]
            if gone:
                problems.append(f'{key}: missing on frames {gone[:6]}')
    if render_dir is not None:
        try:
            problems += slide_problems(ffmpeg, video, Path(render_dir), caption_units(blocks, text_mode, fps, cfg, lk),
                                       lk['canvas']['width'], lk['canvas']['height'], slide)
        except ValueError as error:
            problems.append(str(error))
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description='Report captions that are blank on their landing frame, vanish before they leave or skip a slide-in state.')
    parser.add_argument('video')
    parser.add_argument('--render-report', required=True)
    parser.add_argument('--timeline', required=True)
    parser.add_argument('--ffmpeg')
    parser.add_argument('--render-dir', help='directory of the slide-in images; defaults to the render report directory')
    args = parser.parse_args()
    read = lambda path: json.loads(Path(path).read_text(encoding='utf-8-sig'))
    render_dir = Path(args.render_dir) if args.render_dir else Path(args.render_report).resolve().parent
    problems = caption_problems(args.ffmpeg or 'ffmpeg', args.video, read(args.render_report), read(args.timeline), render_dir)
    print(json.dumps({'status': 'CAPTIONS_CONTINUOUS' if not problems else 'CAPTIONS_INTERRUPTED', 'problems': problems}, ensure_ascii=False))
    return 1 if problems else 0


if __name__ == '__main__':
    raise SystemExit(main())
