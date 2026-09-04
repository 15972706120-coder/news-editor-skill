#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""minimax_tts 参数扩展版：在 skill 版基础上暴露 speed/pitch/vol/emotion/timbre_weights。

凭据仍只从环境变量 MINIMAX_API_KEY / MINIMAX_API_BASE_URL 读取；
音色默认取 MINIMAX_VOICE_ID（缺省 Chinese (Mandarin)_News_Anchor）。
仅在官方允许基址内调用，成功校验与 skill 版一致（status_code==0, data.status==2, hex 可解码）。
"""
from __future__ import annotations

import argparse
import binascii
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

ALLOWED_BASE_URLS = {
    "https://api.minimax.cn",
    "https://api-bj.minimaxi.com",
    "https://api.minimax.io",
    "https://api-uw.minimax.io",
}
RETRYABLE_CODES = {1000, 1001, 1002, 1024, 1033, 2045}
DEFAULT_MODEL = "speech-2.8-hd"
DEFAULT_VOICE = "Chinese (Mandarin)_News_Anchor"


def fail(message: str, code: int = 1):
    print(message, file=sys.stderr)
    raise SystemExit(code)


def synthesize(text: str, output: Path, voice: str, speed: float, vol: float,
               pitch: int, emotion: str | None, timbre: list[dict],
               attempts: int, timeout: float) -> dict:
    key = os.environ.get("MINIMAX_API_KEY", "").strip()
    base = os.environ.get("MINIMAX_API_BASE_URL", "").strip().rstrip("/")
    model = os.environ.get("MINIMAX_TTS_MODEL", DEFAULT_MODEL).strip()
    if not key:
        fail("缺少 MINIMAX_API_KEY 环境变量。", 2)
    if base not in ALLOWED_BASE_URLS:
        fail("MINIMAX_API_BASE_URL 必须是 MiniMax 官方 HTTPS 地址。", 2)

    voice_setting: dict = {"voice_id": voice, "speed": speed, "vol": vol, "pitch": pitch}
    if emotion:
        voice_setting["emotion"] = emotion
    payload: dict = {
        "model": model,
        "text": text,
        "stream": False,
        "language_boost": "Chinese",
        "output_format": "hex",
        "voice_setting": voice_setting,
        "audio_setting": {"sample_rate": 44100, "format": "wav", "channel": 1},
    }
    if timbre:
        payload["timbre_weights"] = timbre
        payload["voice_setting"]["voice_id"] = ""

    url = f"{base}/v1/t2a_v2"
    last_error = ""
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(
                url,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                trace_id = response.headers.get("Trace-Id")
                body = json.loads(response.read().decode("utf-8"))
            base_resp = body.get("base_resp") or {}
            api_code = int(base_resp.get("status_code", -1))
            data = body.get("data") or {}
            audio_hex = data.get("audio")
            if api_code == 0 and data.get("status") == 2 and isinstance(audio_hex, str) and audio_hex:
                audio = binascii.unhexlify(audio_hex)
                output.parent.mkdir(parents=True, exist_ok=True)
                tmp = output.with_suffix(output.suffix + ".part")
                tmp.write_bytes(audio)
                try:
                    with wave.open(str(tmp), "rb") as w:
                        spec = {"sample_rate": w.getframerate(), "channels": w.getnchannels(), "frames": w.getnframes()}
                except (wave.Error, EOFError):
                    tmp.unlink(missing_ok=True)
                    raise RuntimeError("返回的音频不是可解码 WAV")
                if spec["frames"] <= 0:
                    tmp.unlink(missing_ok=True)
                    raise RuntimeError("WAV 无音频帧")
                tmp.replace(output)
                return {"output": str(output.resolve()), "bytes": output.stat().st_size,
                        "model": model, "voice_id": voice or "(timbre_mix)",
                        "speed": speed, "pitch": pitch, "emotion": emotion,
                        "timbre_weights": timbre, "wav": spec, "trace_id": trace_id}
            message = base_resp.get("status_msg", "unknown")
            last_error = f"MiniMax API status_code={api_code}: {message}; trace_id={trace_id}"
            if api_code not in RETRYABLE_CODES or attempt == attempts:
                fail(last_error)
        except urllib.error.HTTPError as error:
            last_error = f"MiniMax HTTP {error.code}"
            if error.code not in {429, 500, 502, 503, 504} or attempt == attempts:
                fail(last_error)
        except (urllib.error.URLError, TimeoutError) as error:
            last_error = f"网络错误: {error}"
            if attempt == attempts:
                fail(last_error)
        time.sleep(min(8.0, 2 ** (attempt - 1) + random.random()))
    fail(last_error)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--text")
    source.add_argument("--text-file", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--voice", default=os.environ.get("MINIMAX_VOICE_ID", DEFAULT_VOICE))
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--vol", type=float, default=1.0)
    parser.add_argument("--pitch", type=int, default=0)
    parser.add_argument("--emotion", default=None,
                        help="happy/sad/angry/fearful/disgusted/surprised/calm 等；缺省不设置")
    parser.add_argument("--timbre", default=None,
                        help="混合音色 JSON，如 '[{\"voice_id\":\"A\",\"weight\":70},{\"voice_id\":\"B\",\"weight\":30}]'")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--attempts", type=int, default=3)
    args = parser.parse_args()
    if not -12 <= args.pitch <= 12:
        fail("--pitch 范围 -12..12", 2)
    if not 0.5 <= args.speed <= 2.0:
        fail("--speed 范围 0.5..2.0", 2)
    timbre = json.loads(args.timbre) if args.timbre else []
    text = args.text if args.text is not None else args.text_file.read_text(encoding="utf-8")
    print(json.dumps(synthesize(text, args.output, args.voice, args.speed, args.vol,
                                args.pitch, args.emotion, timbre, args.attempts, args.timeout),
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
