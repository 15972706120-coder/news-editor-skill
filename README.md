# News-Editor Skill

用于从非国家级媒体与原始发布渠道发现热点，以合格来源的完整标题精准检索抖音素材，并制作、修改和验收竖屏新闻短视频。

## 安装

将完整仓库克隆到 Codex 用户级 Skills 目录：

```powershell
$skillRoot = Join-Path $env:USERPROFILE '.agents\skills\news-editor'
git clone https://github.com/15972706120-coder/news-editor-skill.git $skillRoot
```

如果当前 Codex 环境已经从其他位置加载同名 `news-editor`，请只保留一个发现入口，避免出现两个同名 Skill。

## 更新到最新稳定版

```powershell
$skillRoot = Join-Path $env:USERPROFILE '.agents\skills\news-editor'
git -C $skillRoot pull --ff-only
```

Codex 通常会自动检测 Skill 变化；如果新版本没有出现，重启 Codex。`main` 分支只发布通过校验的稳定版，版本变化见 [CHANGELOG.md](CHANGELOG.md)。

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

完整制作还需要 PowerShell 7、Node.js、Python、agent-browser、yt-dlp、edge-tts、FFmpeg/FFprobe、Chrome、Remotion 和微软雅黑。详细说明见 [环境手册](references/environment-setup.md)。

## 文件边界

- `outputs/YYYY-MM-DD/N.中文新闻短名/` 只放最终 MP4 和 `封面.png`。
- 原片、工程、音频、预览、日志和 QA 放在同级 `.news-editor-work/`。
- 不得向 Git 提交 Cookie、浏览器 profile、临时签名地址、下载元数据或新闻生产输出。

## 校验

```powershell
python -X utf8 <skill-creator-path>\scripts\quick_validate.py $skillRoot
pwsh -NoProfile -File (Join-Path $skillRoot 'scripts\check_output_layout.ps1') -OutputRoot <outputs-path>
```
