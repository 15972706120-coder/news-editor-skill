#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""News-Editor Skill 自一致性检查。

防止规范漂移：被取代的术语/坐标残留、失效的内部链接、缺失资产、脚本语法错误。
pre-commit 通过 hooks/pre-commit 调用；FAIL 退出码 1，WARN 退出码 0。
"""

from __future__ import annotations

import re
import math
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
DOCS = [ROOT / "SKILL.md", *sorted((ROOT / "references").glob("*.md"))]
SCRIPTS_DIR = ROOT / "scripts"

# CHANGELOG 记录历史，允许提及旧术语
CURRENT_DOCS = [p for p in DOCS if p.name != "CHANGELOG.md"]

# (模式, 级别, 说明)；对个别已正确降级的历史兼容代码豁免
BANNED_PATTERNS = [
    (re.compile(r"(outputs[/\\]|`outputs`|<项目根>/outputs)"), "FAIL", "输出区已迁移（根目录见 config.json），残留 outputs 引用"),
    (re.compile(r"edge[-_]tts|Edge TTS", re.IGNORECASE), "FAIL", "Edge TTS 已弃用；新制作环境不得包含（CHANGELOG 除外）"),
    (re.compile(r"layout-lock-v1(\.json)?"), "WARN", "V1 锁定坐标仅历史参考，现行唯一坐标源是 layout-lock-v2.json"),
    (re.compile(r"1080[×x]464|1080[×x]1054|1080[×x]402"), "WARN", "V1 分板数字（464/1054/402）残留"),
    (re.compile(r"D:.每日新闻"), "FAIL", "输出根目录是 FACT，只允许存在于 config.json；文档请引用 config 的 output.root"),
    (re.compile(r"x=\d+,\s*y=\d+"), "WARN", "坐标复述：FACT 只允许存在于 config.json 与锁定 JSON；人读镜像仅限 locked-layout-validation.md"),
    (re.compile(r"Chinese \(Mandarin\)_"), "WARN", "音色 ID 复述：默认值只存在于 config.json；API 说明文档 minimax-tts.md 除外"),
]
EXEMPT_FILES = {  # 相对 ROOT 的文件 → 豁免的 (模式序号) 集合
    "scripts/check_environment.ps1": {1},  # edge-tts 已降级为 legacy 非必需检查，保留兼容
    "scripts/check_skill_consistency.py": {0, 2, 4, 5, 6},  # 检查器自身的模式定义字符串
    "scripts/minimax_tts.py": {6},  # config 缺失时的内置兜底音色（防御性编程）
}
# FACT 复述的合法位置（模式序号 → 允许出现的文件集合，均为相对 ROOT 路径）
FACT_EXEMPT_DOCS = {
    4: {"config.json", "CHANGELOG.md"},                       # 输出根路径
    5: {"config.json", "CHANGELOG.md", "references/locked-layout-validation.md"},  # 坐标（人读镜像）
    6: {"config.json", "CHANGELOG.md", "references/minimax-tts.md"},               # 音色 ID（API 文档）
}

LINK_RE = re.compile(r"\[[^\]]+\]\(([^)#\s]+)(?:#[^)\s]*)?\)")
ASSET_RE = re.compile(r"(assets/[\w\-./]+\.\w+)")


def check_terms(issues: list) -> None:
    # "不得使用/已弃用 Edge TTS" 等政策声明是合法表述，不构成残留
    policy_words = ("不得", "不再", "已弃用", "弃用", "禁止", "迁移", "历史", "兼容", "仅供")
    targets = [*(p.resolve() for p in CURRENT_DOCS), ROOT / "README.md", *sorted(SCRIPTS_DIR.glob("*.ps1")),
               *sorted(SCRIPTS_DIR.glob("*.py")), ROOT / "config.json"]
    targets = [p for p in targets if p.exists()]
    for path in targets:
        rel = path.relative_to(ROOT).as_posix()
        exempt = EXEMPT_FILES.get(rel, set())
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        for idx, (pattern, level, message) in enumerate(BANNED_PATTERNS):
            if idx in exempt or rel in FACT_EXEMPT_DOCS.get(idx, set()):
                continue
            for m in pattern.finditer(text):
                line_no = text.count("\n", 0, m.start()) + 1
                line_text = lines[line_no - 1] if 0 < line_no <= len(lines) else ""
                if idx == 1 and any(w in line_text for w in policy_words):
                    continue
                issues.append((level, f"{rel}:{line_no}", f"{message}（命中: {m.group(0)[:40]}）"))


def check_config(issues: list) -> None:
    import json
    path = ROOT / "config.json"
    if not path.exists():
        issues.append(("FAIL", "config.json", "缺失唯一 FACT 事实源 config.json"))
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        issues.append(("FAIL", "config.json", f"JSON 解析失败: {error}"))
        return
    for section in ("skill_update", "output", "voice", "mix", "video", "layout", "cover"):
        if section not in data:
            issues.append(("FAIL", "config.json", f"缺少必需段: {section}"))
    version_path = ROOT / "VERSION"
    if not version_path.exists():
        issues.append(("FAIL", "VERSION", "缺失版本文件"))
    else:
        version_text = version_path.read_text(encoding="utf-8").strip()
        if version_text != str(data.get("version", "")):
            issues.append(("FAIL", "VERSION", "VERSION 与 config.json version 不一致"))
        changelog = ROOT / "CHANGELOG.md"
        if changelog.exists():
            match = re.search(r"^##\s+([^\s]+)", changelog.read_text(encoding="utf-8"), re.MULTILINE)
            if not match or match.group(1) != version_text:
                issues.append(("FAIL", "CHANGELOG.md", "首个版本标题必须与 VERSION 一致"))

    update = data.get("skill_update", {})
    expected_update = {
        "repository": "https://github.com/15972706120-coder/news-editor-skill.git",
        "remote": "origin",
        "branch": "main",
        "policy": "strict_before_every_run",
        "allow_stale_on_failure": False,
    }
    for key, expected in expected_update.items():
        if update.get(key) != expected:
            issues.append(("FAIL", "config.json", f"skill_update.{key} 必须为 {expected!r}"))
    for key in ("network_timeout_seconds", "network_attempts"):
        if not isinstance(update.get(key), int) or update[key] <= 0:
            issues.append(("FAIL", "config.json", f"skill_update.{key} 必须是正整数"))

    gate_script = ROOT / "scripts/ensure_latest_skill.ps1"
    if not gate_script.exists():
        issues.append(("FAIL", "scripts/ensure_latest_skill.ps1", "缺失 GitHub 最新版本启动门脚本"))
    skill_path = ROOT / "SKILL.md"
    if skill_path.exists():
        skill_text = skill_path.read_text(encoding="utf-8")
        gate_pos = skill_text.find("ensure_latest_skill.ps1")
        config_pos = skill_text.find("## 当前默认配置")
        if gate_pos < 0 or config_pos < 0 or gate_pos > config_pos:
            issues.append(("FAIL", "SKILL.md", "版本启动门必须位于当前配置和所有任务动作之前"))
    if not str(data.get("output", {}).get("root", "")):
        issues.append(("FAIL", "config.json", "output.root 为空"))
    lock = data.get("layout", {}).get("active_lock_file", "")
    if lock and not (ROOT / lock).exists():
        issues.append(("FAIL", "config.json", f"layout.active_lock_file 指向的文件不存在: {lock}"))
    bgm = data.get("mix", {}).get("bgm_source", "")
    if bgm and not (ROOT / bgm).exists():
        issues.append(("FAIL", "config.json", f"mix.bgm_source 指向的文件不存在: {bgm}"))

    quality = data.get("cover", {}).get("quality", {})
    quality_keys = ("preferred_max_upscale_ratio", "hard_max_upscale_ratio", "scale_ratio_tolerance")
    valid_quality = all(
        type(quality.get(key)) in (int, float) and math.isfinite(quality[key]) and quality[key] > 0
        for key in quality_keys
    )
    if not valid_quality:
        issues.append(("FAIL", "config.json", "cover.quality 的三个几何阈值必须是有限正数"))
    elif (quality["preferred_max_upscale_ratio"] > quality["hard_max_upscale_ratio"]
          or quality["scale_ratio_tolerance"] >= 1):
        issues.append(("FAIL", "config.json", "cover.quality 首选值不得超过硬上限，比例容差必须小于 1"))
    for filename in ("check_cover_geometry.py", "test_cover_geometry.py"):
        if not (SCRIPTS_DIR / filename).is_file():
            issues.append(("FAIL", f"scripts/{filename}", "缺失封面几何门或其回归测试"))


def check_cover_policy(issues: list) -> None:
    # 定向拦截曾导致 Agent 误用的活动条款，不拦截正文底部字幕例外。
    stale_phrases = (
        "局部区域允许精确模糊",
        "只对实际残留的原始文字框做精确局部模糊",
        "精确局部模糊最后",
        "只有确实残留的原始文字框允许精确局部模糊",
        "只有仍残留的原文字区域才允许精确局部模糊",
        "封面和正文都先尝试换帧、裁切和焦点位移；只允许模糊",
    )
    for path in CURRENT_DOCS:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if any(phrase in line for phrase in stale_phrases):
                issues.append(("FAIL", f"{path.relative_to(ROOT).as_posix()}:{number}",
                               "封面零模糊规则冲突；正文字幕例外不得用于封面"))


def check_links(issues: list) -> None:
    for path in CURRENT_DOCS:
        rel = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in LINK_RE.finditer(text):
            target = m.group(1)
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            resolved = (path.parent / target).resolve()
            if not resolved.exists():
                issues.append(("FAIL", f"{rel}", f"内部链接目标不存在: {target}"))


def check_assets(issues: list) -> None:
    for path in CURRENT_DOCS + [ROOT / "README.md"]:
        if not path.exists():
            continue
        rel = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in ASSET_RE.finditer(text):
            if not (ROOT / m.group(1)).exists():
                issues.append(("FAIL", f"{rel}", f"引用的资产不存在: {m.group(1)}"))


def check_python_syntax(issues: list) -> None:
    for path in sorted(SCRIPTS_DIR.glob("*.py")):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(path)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            issues.append(("FAIL", path.relative_to(ROOT).as_posix(),
                           f"Python 语法错误: {result.stderr.strip().splitlines()[-1] if result.stderr else 'unknown'}"))


def check_active_layout(issues: list) -> None:
    import json
    v2 = ROOT / "assets/references/locked-layout/layout-lock-v2.json"
    data = json.loads(v2.read_text(encoding="utf-8"))
    if data.get("status") != "active":
        issues.append(("FAIL", v2.relative_to(ROOT).as_posix(), "layout-lock-v2.json status 不是 active"))


def main() -> int:
    issues: list[tuple[str, str, str]] = []
    check_config(issues)
    check_cover_policy(issues)
    check_terms(issues)
    check_links(issues)
    check_assets(issues)
    check_python_syntax(issues)
    check_active_layout(issues)

    fails = [i for i in issues if i[0] == "FAIL"]
    warns = [i for i in issues if i[0] == "WARN"]
    for level, where, message in sorted(issues):
        print(f"[{level}] {where} — {message}")
    print(f"\n检查完成: {len(fails)} FAIL / {len(warns)} WARN")
    if fails:
        print("存在 FAIL，提交被阻止；修复后再提交。")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
