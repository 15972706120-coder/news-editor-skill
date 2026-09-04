#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""News-Editor Skill 自一致性检查。

防止规范漂移：被取代的术语/坐标残留、失效的内部链接、缺失资产、脚本语法错误。
pre-commit 通过 hooks/pre-commit 调用；FAIL 退出码 1，WARN 退出码 0。
"""

from __future__ import annotations

import re
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
    (re.compile(r"(outputs[/\\]|`outputs`|<项目根>/outputs)"), "FAIL", "输出区已迁移到 D:\\每日新闻，残留 outputs 引用"),
    (re.compile(r"edge[-_]tts|Edge TTS", re.IGNORECASE), "FAIL", "Edge TTS 已弃用；新制作环境不得包含（CHANGELOG 除外）"),
    (re.compile(r"layout-lock-v1(\.json)?"), "WARN", "V1 锁定坐标仅历史参考，现行唯一坐标源是 layout-lock-v2.json"),
    (re.compile(r"1080[×x]464|1080[×x]1054|1080[×x]402"), "WARN", "V1 分板数字（464/1054/402）残留"),
    (re.compile(r"x=0, y=464|x=0, y=1518|y=1602|y=1726"), "WARN", "V1 文字坐标残留"),
]
EXEMPT_FILES = {  # 相对 ROOT 的文件 → 豁免的 (模式序号) 集合
    "scripts/check_environment.ps1": {1},  # edge-tts 已降级为 legacy 非必需检查，保留兼容
}

LINK_RE = re.compile(r"\[[^\]]+\]\(([^)#\s]+)(?:#[^)\s]*)?\)")
ASSET_RE = re.compile(r"(assets/[\w\-./]+\.\w+)")


def check_terms(issues: list) -> None:
    # "不得使用/已弃用 Edge TTS" 等政策声明是合法表述，不构成残留
    policy_words = ("不得", "不再", "已弃用", "弃用", "禁止", "迁移", "历史", "兼容", "仅供")
    targets = [*(p.resolve() for p in CURRENT_DOCS), *sorted(SCRIPTS_DIR.glob("*.ps1"))]
    for path in targets:
        rel = path.relative_to(ROOT).as_posix()
        exempt = EXEMPT_FILES.get(rel, set())
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        for idx, (pattern, level, message) in enumerate(BANNED_PATTERNS):
            if idx in exempt:
                continue
            for m in pattern.finditer(text):
                line_no = text.count("\n", 0, m.start()) + 1
                line_text = lines[line_no - 1] if 0 < line_no <= len(lines) else ""
                if idx == 1 and any(w in line_text for w in policy_words):
                    continue
                issues.append((level, f"{rel}:{line_no}", f"{message}（命中: {m.group(0)[:40]}）"))


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
