# News-Editor Skill

用于从非国家级媒体与原始发布渠道发现热点，以合格来源的完整标题精准检索抖音素材，并制作、修改和验收竖屏新闻短视频。

## 安装

将完整仓库克隆到 Codex 用户级 Skills 目录：

```powershell
$skillRoot = Join-Path $env:USERPROFILE '.agents\skills\news-editor'
git clone https://github.com/15972706120-coder/news-editor-skill.git $skillRoot
```

如果多个 Agent 平台必须使用不同的扫描目录，每个目录都必须是该仓库的独立干净克隆，并由启动门各自核对到同一个 GitHub commit；同一平台仍只保留一个发现入口，避免加载歧义。

## 每次运行前强制核对 GitHub

News-Editor 不是按日期缓存版本。每个新请求、新 Agent、任务重启或阻塞后重启，第一步都会实时比较当前安装 commit 与 GitHub `main`：

```powershell
$skillRoot = Join-Path $env:USERPROFILE '.agents\skills\news-editor'
$runId = [guid]::NewGuid().ToString()
pwsh -NoProfile -File (Join-Path $skillRoot 'scripts\ensure_latest_skill.ps1') -RunId $runId
```

`LATEST_READY` 表示当前 commit 与远端完全一致；`UPDATED_READY_RELOAD` 表示已完成安全快进，Agent 必须重新读取新版 Skill 后再工作。断网、超时、本地改动、错误远程、分支不符、历史分叉或更新后校验失败会严格阻断，不允许用旧版继续。脚本不会执行 reset、clean、stash 或强制覆盖。`main` 只发布通过校验的稳定版，版本变化见 [CHANGELOG.md](CHANGELOG.md)。

如需人工故障恢复，只能在确认工作树干净后执行 `git pull --ff-only`；它不是标准运行入口，也不能代替每次实时核验远端 SHA。

## 使用

显式调用：

```text
使用 $news-editor 制作今天的新闻内容
```

Skill 允许隐式调用，但完整制作仍包含选题确认、来源核验、授权范围和最终质量门。

## 首次环境检查

本 Skill 面向 Windows 10/11。首次使用或工具升级后运行：

```powershell
pwsh -NoProfile -File (Join-Path $skillRoot 'scripts\check_environment.ps1')
```

完整制作还需要 PowerShell 7、Node.js、Python、agent-browser、yt-dlp、FFmpeg/FFprobe、Chrome、Remotion、微软雅黑，以及在首次实际生成配音前由用户在本机配置的 MiniMax API 环境变量。不要把 API Key 写进仓库或聊天。详细说明见 [当前制作配置](references/current-production-profile-v2.md)、[MiniMax TTS 集成](references/minimax-tts.md)和[环境手册](references/environment-setup.md)。

## 文件边界

- 输出区 `<输出根>/YYYY-MM-DD/N.封面主标题/`（输出根见 [config.json](config.json) 的 `output.root`）只放与封面主标题同名的最终 MP4 和 `封面.png`。
- 原片、工程、音频、预览、日志和 QA 放在项目根的 `.news-editor-work/`。
- 不得向 Git 提交 Cookie、浏览器 profile、临时签名地址、下载元数据或新闻生产输出。

## 校验与开发门禁

```powershell
python -X utf8 <skill-creator-path>\scripts\quick_validate.py $skillRoot
pwsh -NoProfile -File (Join-Path $skillRoot 'scripts\check_output_layout.ps1')   # 输出根目录缺省从 config.json 读取
```

修改本仓库后启用可分发的提交门禁（一次性配置）：

```powershell
git -C $skillRoot config core.hooksPath hooks
```

`hooks/pre-commit` 会运行 `scripts/check_skill_consistency.py`，拦截被取代的旧术语与旧路径、V1 坐标残留、FACT 复述、失效内部链接与 Python 语法错误。
