#!/usr/bin/env python3
"""Generate a short News-Editor voice stem with MiniMax T2A v2."""

from __future__ import annotations

import argparse
import binascii
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
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


def fail(message: str, code: int = 1) -> "NoReturn":
    print(message, file=sys.stderr)
    raise SystemExit(code)


def configuration() -> tuple[str, str, str, str]:
    key = os.environ.get("MINIMAX_API_KEY", "").strip()
    base = os.environ.get("MINIMAX_API_BASE_URL", "").strip().rstrip("/")
    model = os.environ.get("MINIMAX_TTS_MODEL", DEFAULT_MODEL).strip()
    voice = os.environ.get("MINIMAX_VOICE_ID", DEFAULT_VOICE).strip()
    if not key:
        fail(
            "缺少 MINIMAX_API_KEY。请在本机环境变量中配置，不要把密钥粘贴到聊天、命令行、日志或仓库。",
            2,
        )
    if base not in ALLOWED_BASE_URLS:
        fail(
            "MINIMAX_API_BASE_URL 必须明确设置为中国大陆站或国际站的 MiniMax 官方 HTTPS 地址；禁止自动跨站尝试密钥。",
            2,
        )
    if not model or not voice:
        fail("MiniMax model 和 voice_id 不能为空。", 2)
    return key, base, model, voice


def post_json(url: str, key: str, payload: dict[str, object], timeout: float) -> tuple[dict[str, object], str | None]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        trace_id = response.headers.get("Trace-Id") or response.headers.get("X-Trace-Id")
        body = json.loads(response.read().decode("utf-8"))
    if not isinstance(body, dict):
        raise RuntimeError("MiniMax returned a non-object JSON response")
    return body, trace_id


def synthesize(text: str, output: Path, timeout: float, attempts: int) -> dict[str, object]:
    key, base, model, voice = configuration()
    if not text.strip():
        fail("配音文本不能为空。", 2)
    if len(text) >= 10000:
        fail("同步 T2A 文本必须少于 10000 字符；超过 3000 字符时应改用流式或拆段。", 2)

    payload: dict[str, object] = {
        "model": model,
        "text": text,
        "stream": False,
        "language_boost": "Chinese",
        "output_format": "hex",
        "voice_setting": {
            "voice_id": voice,
            "speed": 1.0,
            "vol": 1.0,
            "pitch": 0,
            "emotion": "calm",
        },
        "audio_setting": {"sample_rate": 44100, "format": "wav", "channel": 1},
    }

    last_error = ""
    for attempt in range(1, attempts + 1):
        try:
            body, trace_id = post_json(f"{base}/v1/t2a_v2", key, payload, timeout)
            base_resp = body.get("base_resp") or {}
            api_code = int(base_resp.get("status_code", -1)) if isinstance(base_resp, dict) else -1
            data = body.get("data")
            status = data.get("status") if isinstance(data, dict) else None
            audio_hex = data.get("audio") if isinstance(data, dict) else None
            if api_code == 0 and status == 2 and isinstance(audio_hex, str) and audio_hex:
                try:
                    audio = binascii.unhexlify(audio_hex)
                except (binascii.Error, ValueError) as error:
                    raise RuntimeError("MiniMax data.audio is not valid hexadecimal audio") from error
                output.parent.mkdir(parents=True, exist_ok=True)
                temporary = output.with_suffix(output.suffix + ".part")
                temporary.write_bytes(audio)
                try:
                    with wave.open(str(temporary), "rb") as wav_file:
                        wav_spec = {
                            "sample_rate": wav_file.getframerate(),
                            "channels": wav_file.getnchannels(),
                            "frames": wav_file.getnframes(),
                        }
                except (wave.Error, EOFError) as error:
                    temporary.unlink(missing_ok=True)
                    raise RuntimeError("MiniMax audio payload is not a decodable WAV") from error
                if wav_spec["frames"] <= 0:
                    temporary.unlink(missing_ok=True)
                    raise RuntimeError("MiniMax WAV contains no audio frames")
                temporary.replace(output)
                return {
                    "output": str(output.resolve()),
                    "bytes": output.stat().st_size,
                    "model": model,
                    "voice_id": voice,
                    "wav": wav_spec,
                    "trace_id": trace_id or body.get("trace_id"),
                }
            message = base_resp.get("status_msg", "unknown API error") if isinstance(base_resp, dict) else "unknown API error"
            last_error = f"MiniMax API status_code={api_code}: {message}; trace_id={trace_id or body.get('trace_id')}"
            if api_code not in RETRYABLE_CODES or attempt == attempts:
                fail(last_error)
        except urllib.error.HTTPError as error:
            last_error = f"MiniMax HTTP {error.code}; request failed without logging credentials"
            if error.code not in {429, 500, 502, 503, 504} or attempt == attempts:
                fail(last_error)
        except (urllib.error.URLError, TimeoutError) as error:
            last_error = f"MiniMax network error: {error}"
            if attempt == attempts:
                fail(last_error)
        time.sleep(min(8.0, (2 ** (attempt - 1)) + random.random()))
    fail(last_error)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--text")
    source.add_argument("--text-file", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--attempts", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.attempts <= 3:
        fail("--attempts must be between 1 and 3", 2)
    text = args.text if args.text is not None else args.text_file.read_text(encoding="utf-8")
    print(json.dumps(synthesize(text, args.output, args.timeout, args.attempts), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
