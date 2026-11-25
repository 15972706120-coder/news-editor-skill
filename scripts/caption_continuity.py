#!/usr/bin/env python3
"""Caption continuity of an encoded brief: complete on the frame it lands on, present until it leaves.

The frame count and the layout proof sample frames after a caption has landed, so neither notices a caption
that is missing on its landing frame (an overlay stamped a few ticks late did exactly that). This decodes every
frame at quarter resolution and compares each caption's box with the frame before its slide-in began.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess

import numpy as np
from pathlib import Path

from production_contract import caption_arrival, config, lock

SCALE = 4


def caption_problems(ffmpeg: str, video, render_report: dict, timeline: dict) -> list[str]:
    cfg, lk = config(), lock()
    fps, total = timeline['fps'], timeline['total_frames']
    width, height = lk['canvas']['width']//SCALE, lk['canvas']['height']//SCALE
    raw = subprocess.run([ffmpeg, '-v', 'error', '-i', str(video), '-vf', f'scale={width}:{height}',
                          '-f', 'rawvideo', '-pix_fmt', 'gray', '-'], capture_output=True).stdout
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
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description='Report captions that are blank on their landing frame or vanish before they leave.')
    parser.add_argument('video')
    parser.add_argument('--render-report', required=True)
    parser.add_argument('--timeline', required=True)
    parser.add_argument('--ffmpeg')
    args = parser.parse_args()
    read = lambda path: json.loads(Path(path).read_text(encoding='utf-8-sig'))
    problems = caption_problems(args.ffmpeg or 'ffmpeg', args.video, read(args.render_report), read(args.timeline))
    print(json.dumps({'status': 'CAPTIONS_CONTINUOUS' if not problems else 'CAPTIONS_INTERRUPTED', 'problems': problems}, ensure_ascii=False))
    return 1 if problems else 0


if __name__ == '__main__':
    raise SystemExit(main())
