#!/usr/bin/env python3
"""Synthetic footage and cover stills for the engine smoke test and the layout reference stills.

Everything is generated from gradients, drifting light bands and grain, so neither the smoke test
nor the reference stills depends on downloaded, account or third-party media.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

# Per shot: upper colour, lower colour, light colour. Four looks so a shot change is visible.
PALETTES = (
    ((52, 96, 132), (198, 214, 220), (255, 246, 226)),
    ((10, 26, 62), (36, 96, 164), (128, 194, 255)),
    ((78, 46, 24), (172, 126, 72), (255, 216, 152)),
    ((30, 32, 36), (130, 134, 142), (238, 240, 242)),
)
CLIP_SIZE = (720, 444)
COVER_SIZE = (1080, 1440)


def scene(size: tuple[int, int], shot: int, seconds: float) -> np.ndarray:
    """One synthetic frame as float32 RGB in [0, 1]: gradient, light bands, a travelling glow, grain."""
    w, h = size
    upper, lower, light = (np.array(colour, np.float32)/255 for colour in PALETTES[shot % len(PALETTES)])
    y = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None, None]
    x = np.linspace(0.0, 1.0, w, dtype=np.float32)[None, :]
    base = upper*(1-y)+lower*y
    bands = 0.13*np.sin(x*9.5+shot+seconds*0.5)+0.08*np.sin(x*23+2*shot-seconds*0.9)
    cx, cy = 0.5+0.32*np.sin(0.45*seconds+shot), 0.5+0.12*np.cos(0.3*seconds+shot)
    glow = np.exp(-((x-cx)/0.26)**2-((y[:, :, 0]-cy)/0.55)**2)
    grain = np.random.default_rng(shot*100003+round(seconds*30)).normal(0.0, 0.012, (h, w))
    return np.clip(base*(1+bands[..., None]+0.6*glow[..., None])+glow[..., None]*light*0.35+grain[..., None], 0.0, 1.0)


def _encode_frame(image: np.ndarray) -> bytes:
    return (image*255+0.5).astype(np.uint8).tobytes()


def synthetic_clip(path: Path, frames: int, shot: int, fps: int = 30, ffmpeg: str = 'ffmpeg',
                   size: tuple[int, int] = CLIP_SIZE) -> Path:
    """Write `frames` frames of synthetic footage as an H.264 clip that any stage can treat as source video."""
    w, h = size
    command = [ffmpeg, '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{w}x{h}',
               '-r', str(fps), '-i', '-', '-an', '-c:v', 'libx264', '-crf', '14', '-pix_fmt', 'yuv420p', str(path)]
    with open(os.devnull, 'wb') as sink:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=sink)
        try:
            for index in range(frames):
                process.stdin.write(_encode_frame(scene(size, shot, index/fps)))
            process.stdin.close()
        except BrokenPipeError:
            process.stdin.close()
        code = process.wait()
    if code:
        raise RuntimeError(f'ffmpeg exited with {code} while writing the synthetic clip {Path(path).name}')
    return Path(path)


def synthetic_cover(path: Path, shot: int, size: tuple[int, int] = COVER_SIZE) -> Path:
    """Write a sharp 3:4 synthetic still, large enough to pass the cover geometry gate without upscaling."""
    image = np.frombuffer(_encode_frame(scene(size, shot, 0.0)), dtype=np.uint8).reshape(size[1], size[0], 3)
    Image.fromarray(image).save(path)
    return Path(path)
