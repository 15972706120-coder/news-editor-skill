# News-Editor 新电脑环境与依赖安装手册

> 配音使用 MiniMax T2A API（见 [MiniMax TTS 集成](minimax-tts.md)）；Edge TTS 已弃用，`check_environment.ps1` 中的 edge-tts 检查仅为旧项目兼容，不是新制作环境的一部分。

> 适用平台：Windows 10/11 x64  
> 目标：在一台未配置的新电脑上完成 GitHub 版本核验、热点发现、抖音搜索与下载、新闻女声、Remotion 排版渲染、FFmpeg 合成和最终验收。
> 执行原则：每次运行先通过 Skill 版本启动门，再安装或预检基础环境并进入新闻制作；不要在制作中途临时猜测工具路径或随意更换技术路线。

## 1. 依赖不是同一种“插件”

News-Editor 依赖分为五层，迁移时必须分别检查：

| 层级 | 必需组件 | 负责的环节 | 安装位置 |
|---|---|---|---|
| Codex Skill | `news-editor`、`agent-browser`、完整 Remotion Skills | 工作规范、浏览器操作方法、Remotion 编排知识 | 用户 Skills 目录 |
| 系统运行时 | Git、Node.js、npm/npx、Python、PowerShell | 核对并安全快进 Skill，运行 CLI、Python 模块和预检脚本 | 操作系统 |
| CLI / Python 包 | `agent-browser`、`yt-dlp` | 抖音检索、视频下载 | 全局 npm 或 Python 环境 |
| 语音 API | MiniMax T2A（`scripts/minimax_tts.py`） | 新闻女声配音 | 环境变量凭据，按停点检查 |
| 原生媒体工具 | FFmpeg、FFprobe、Chrome/Chromium | 转码、混音、抽帧、媒体探测、网页登录和渲染 | 系统 PATH 或显式路径 |
| 项目依赖与资源 | Remotion、React、TypeScript、微软雅黑、BGM 和参考资产 | 版式、时间轴、渲染、中文排版和声音 | 每个项目及 Skill 包 |

`agents/openai.yaml` 当前只能声明 MCP 工具，不能可靠声明 npm、Python、FFmpeg 或字体依赖。因此，本文件和 `scripts/check_environment.ps1` 是 News-Editor 的环境事实来源。

## 2. 必需能力与可选能力

### 2.1 完整制作必须具备

- Windows 10/11 x64。
- Git，并能通过 HTTPS 访问 GitHub；每次运行必须实时核验远端 commit。
- PowerShell 7 或更高版本；使用 `pwsh` 运行预检和制作脚本。
- Node.js 24 LTS 或兼容的新版本，并包含 npm、npx。
- Python 3.10 或更高版本。
- `agent-browser` CLI，并完成第一次 `agent-browser install`。
- `agent-browser` Codex Skill。
- `yt-dlp`，CLI 或 `python -m yt_dlp` 至少一种方式可运行。
- MiniMax T2A 凭据（`MINIMAX_API_KEY`、`MINIMAX_API_BASE_URL`）：不需要预装，第一次实际生成配音前按停点检查。
- FFmpeg 和 FFprobe；FFmpeg 构建必须包含 H.264、AAC 以及新闻流程所需滤镜。
- Chrome、Chrome for Testing、Chromium 或受支持的 Edge 浏览器。
- Remotion 完整 Skills，以及每个视频项目自己的 Remotion npm 依赖。
- 微软雅黑常规和粗体字体，或用户明确批准并同步修改模板的替代中文字体。
- News-Editor 自带的三个 BGM、视觉参考和验收脚本。
- 可访问新闻网站、抖音、npm/PyPI、MiniMax API 的网络。
- 用户在专用浏览器配置中手动完成的抖音登录态。

### 2.2 条件性或可选能力

- 独立 GPU：不是硬依赖；CPU 可以渲染，但速度较慢。
- 用户自备配音：用户直接提供音频或指定其他语音服务时，可以替代 MiniMax 生成。
- 用户自备浏览器自动化：只有明确验证与现有检索规范等价时才能替代 `agent-browser`，默认不替代。

## 3. 已跑通版本基线

以下版本在 2026-08-26 的 Windows 项目中共同跑通。它们用于定位兼容问题，不代表以后必须永久锁死：

| 组件 | 已验证版本 |
|---|---|
| Node.js | 24.15.0 |
| npm | 11.12.1 |
| Python | 3.14.5 |
| agent-browser | 0.35.0 |
| yt-dlp | 2026.08.19 |
| MiniMax T2A | speech-2.8-hd（2026-09-04 验证） |
| FFmpeg / FFprobe | 8.1.1 |
| Remotion / `@remotion/cli` | 4.0.516 |
| React / React DOM | 19.1.1 |
| TypeScript | 5.9.2 |

版本策略：

1. 复制已有 Remotion 工程时，以项目的 `package-lock.json` 和 `npm ci` 为准。
2. 新建工程时使用官方当前稳定版本，并保证所有 `remotion` 与 `@remotion/*` 包版本完全一致。
3. `yt-dlp` 受网站变化影响较大；先使用已验证版本，解析器失效时再按官方升级方式受控更新。
4. 不在每次新闻制作时自动升级系统依赖；升级属于环境维护动作，升级后必须重新运行深度预检。News-Editor Skill 本身例外：每次运行都实时核验 GitHub，仅在远端领先且可安全快进时更新。

## 4. 推荐安装顺序

新电脑按以下顺序配置：

```text
复制 News-Editor Skill
  ↓
安装 Node.js 24 LTS、Python、Chrome、FFmpeg
  ↓
安装 agent-browser CLI 与浏览器
  ↓
安装 yt-dlp
  ↓
安装 agent-browser / Remotion Skills
  ↓
确认微软雅黑与 Skill 内置资产
  ↓
为 Remotion 项目执行 npm ci 或创建新项目
  ↓
运行 check_environment.ps1
  ↓
运行深度音视频冒烟测试
  ↓
用户手动登录抖音
```

## 5. 安装 News-Editor Skill

必须安装或克隆整个目录，而不是只复制 `SKILL.md`：

```text
news-editor/
├─ SKILL.md
├─ agents/
├─ assets/
├─ references/
└─ scripts/
```

优先使用官方用户级 Skills 目录；也兼容当前 Codex 环境已启用的旧目录。不要在两个扫描位置保留两份同名 Skill：

```powershell
$skillTarget = Join-Path $env:USERPROFILE '.agents\skills\news-editor'
```

安装后检查：

```powershell
Test-Path (Join-Path $skillTarget 'SKILL.md')
Test-Path (Join-Path $skillTarget 'scripts\ensure_latest_skill.ps1')
Test-Path (Join-Path $skillTarget 'scripts\check_environment.ps1')
Test-Path (Join-Path $skillTarget 'assets\audio\bgm-01.mp3')
```

安装完成后立即运行一次版本启动门；以后每个新请求也先运行：

```powershell
$runId = [guid]::NewGuid().ToString()
pwsh -NoProfile -File (Join-Path $skillTarget 'scripts\ensure_latest_skill.ps1') -RunId $runId
```

只接受 `LATEST_READY`，或在 `UPDATED_READY_RELOAD` 后重新读取新版 Skill。任何其他状态都停止，不以本地缓存或旧副本继续。

不要迁移旧电脑的 Cookie 文件、浏览器主配置或临时签名视频地址。新电脑使用专用浏览器目录，由用户重新登录抖音。

## 6. 安装系统运行时

### 6.1 PowerShell 7

Windows 自带的 Windows PowerShell 5.1 不是本 Skill 的运行环境。安装 PowerShell 7，并使用 `pwsh` 启动脚本：

```powershell
winget install --exact --id Microsoft.PowerShell
pwsh --version
```

### 6.2 Node.js

使用 [Node.js 官方下载页](https://nodejs.org/en/download) 安装当前 LTS。当前 `agent-browser` 和完整 Remotion 流程建议使用 Node.js 24 LTS。

可选的 Windows Package Manager 命令：

```powershell
winget install --exact --id OpenJS.NodeJS.LTS
```

关闭并重新打开终端后验证：

```powershell
node --version
npm --version
npx --version
```

### 6.3 Python

从 [Python 官方 Windows 下载页](https://www.python.org/downloads/windows/) 安装 Python 3.10 或更高版本，建议 3.12+。安装器中启用 Python Launcher；是否加入 PATH 不影响后续流程，只要 `python` 或 `py -3` 可运行。

验证：

```powershell
python --version
# 或
py -3 --version
```

### 6.4 Chrome 或 Chromium 浏览器

安装 [Google Chrome](https://www.google.com/chrome/) 或受支持的 Chromium 浏览器。News-Editor 需要：

- 用户可见窗口完成抖音登录；
- `agent-browser` 通过 CDP 接管已授权会话；
- Remotion 在本地使用浏览器渲染。

可选命令：

```powershell
winget install --exact --id Google.Chrome
```

不要把用户日常 Chrome 主 profile 作为自动化 profile。使用专用目录并单独登录。

### 6.5 FFmpeg 和 FFprobe

FFmpeg 官方只直接发布源代码，并在[官方下载页](https://ffmpeg.org/download.html)列出 Windows 构建入口。可以使用受信任的 Windows 构建或包管理器：

```powershell
winget install --exact --id Gyan.FFmpeg
```

重新打开终端后验证：

```powershell
ffmpeg -version
ffprobe -version
ffmpeg -hide_banner -filters
ffmpeg -hide_banner -encoders
```

News-Editor 至少需要以下能力：

- 编码器：`libx264`、`aac`；
- 视频滤镜：`crop`、`scale`、`boxblur`、`fade`；
- 音频滤镜：`loudnorm`、`volume`、`amix`、`afade`、`alimiter`；
- FFprobe JSON 输出。

如果 FFmpeg 没有加入 PATH，可以在执行脚本时通过 `-FfmpegPath` 和 `-FfprobePath` 显式指定，但不能在 Skill 中硬编码旧电脑路径。

## 7. 安装浏览器检索能力

### 7.1 agent-browser CLI

官方项目：[vercel-labs/agent-browser](https://github.com/vercel-labs/agent-browser)

```powershell
npm install --global agent-browser
agent-browser install
agent-browser --version
```

`agent-browser install` 在第一次安装时准备 Chrome for Testing。Windows 上仍建议另外保留一个可见的 Chrome，供用户完成抖音登录并通过 CDP 接管。

### 7.2 agent-browser Skill

CLI 负责执行，Skill 负责教 Agent 使用正确的 snapshot、元素引用和 CDP 工作流，两者不能混为一项。

```powershell
npx -y skills@latest add vercel-labs/agent-browser -g -y
```

验证至少一个位置存在：

```powershell
Test-Path (Join-Path $env:USERPROFILE '.agents\skills\agent-browser\SKILL.md')
Test-Path (Join-Path $env:USERPROFILE '.codex\skills\agent-browser\SKILL.md')
```

第一次真实检索前，按 `references/douyin-news-footage-pipeline.md` 建立专用 Chrome profile，由用户手动登录抖音。不要自动导出或迁移 Cookie。

## 8. 安装视频下载工具

为了避免 Windows 用户级 Scripts 目录未加入 PATH，News-Editor 优先使用 Python 模块形式：

```powershell
python -m pip install --upgrade pip
python -m pip install --upgrade yt-dlp
```

如果使用 `py -3`：

```powershell
py -3 -m pip install --upgrade pip
py -3 -m pip install --upgrade yt-dlp
```

验证：

```powershell
python -m yt_dlp --version
```

官方说明：[yt-dlp 官方项目和安装方式](https://github.com/yt-dlp/yt-dlp)

配音不安装本地包：新闻女声由 MiniMax T2A 生成，凭据与调用方式见 [MiniMax TTS 集成](minimax-tts.md)，第一次实际生成配音前才检查环境变量。Edge TTS（`edge-tts`）已弃用，仅旧项目兼容时使用。

## 9. 安装 Remotion 能力

### 9.1 完整 Remotion Skills

本地仅有一个 Remotion 目录或发现条目，不代表已拥有完整制作规范。按照 [Remotion 官方文档](https://www.remotion.dev/docs/)安装完整 Skills：

```powershell
npx -y skills@latest add remotion-dev/skills -g -y
```

### 9.2 新建 Remotion 项目

官方推荐入口：

```powershell
npx create-video@latest --yes --blank my-news-video
Set-Location 'my-news-video'
npm install
npx remotion skills add
npm run dev
```

### 9.3 迁移已有项目

项目必须同时携带：

- `package.json`；
- `package-lock.json`；
- `src/`；
- 项目使用的 `public/` 素材；
- 配置文件和必要脚本。

在项目目录执行：

```powershell
npm ci
npx --no-install remotion versions
npx --no-install remotion compositions src/index.ts
```

不要复制旧电脑的 `node_modules`；在新电脑使用 `npm ci` 从锁文件恢复。

### 9.4 手工建立项目时的最低包集合

若没有脚手架，至少安装：

```powershell
npm install --save-exact remotion @remotion/cli react react-dom
npm install --save-dev --save-exact typescript @types/react @types/react-dom
```

所有 `remotion` 与 `@remotion/*` 包必须使用同一个版本。安装后提交或保存 lock 文件。商业使用前检查 [Remotion 当前许可条款](https://www.remotion.dev/docs/license)。

## 10. 字体、BGM 和参考资产

News-Editor 默认固定使用微软雅黑：

```powershell
Test-Path (Join-Path $env:WINDIR 'Fonts\msyh.ttc')
Test-Path (Join-Path $env:WINDIR 'Fonts\msyhbd.ttc')
```

如果缺少字体：

1. 在 Windows“可选功能”中安装简体中文补充字体；或使用有明确授权的字体。
2. 如果改用其他字体，必须由用户批准，并同步更新 `references/visual-audio-template.md`。
3. 禁止在渲染时静默替换成外观明显不同的字体。

Skill 内置资源必须存在且文件非空：

```text
assets/audio/bgm-01.mp3
assets/audio/bgm-02.mp3
assets/audio/bgm-03.mp3
assets/references/cover-style-reference.png
assets/references/cover-typography-reference.png
assets/references/in-video-typography-reference.png
assets/references/finished-video-reference.mp4
scripts/validate_news_video.py
```

缺失任一 BGM、视觉参考或验收脚本时，不应把该 Skill 视作完整迁移。

## 11. 强制版本门与环境预检

每次调用先对当前 Agent 实际加载的 Skill 根目录运行版本门；新电脑第一次使用、工具升级后或完整制作开始前，再运行环境预检。不得从多个目录静默选择“第一份”而掩盖版本漂移：

```powershell
$skillRoot = '<当前 Agent 实际加载的 news-editor 根目录>'
$runId = [guid]::NewGuid().ToString()
pwsh -NoProfile -File (Join-Path $skillRoot 'scripts\ensure_latest_skill.ps1') -RunId $runId
pwsh -NoProfile -File (Join-Path $skillRoot 'scripts\check_environment.ps1')
```

如果因平台兼容保留多个扫描目录，应分别运行版本门并确认它们返回相同 `remote_sha`；同一平台只配置一个发现入口。版本门发生更新时，必须重新读取该目录中的 `SKILL.md`、`config.json` 与所需 references 后再执行环境预检。

如果已经创建或复制 Remotion 工程：

```powershell
pwsh -NoProfile -File (Join-Path $skillRoot 'scripts\check_environment.ps1') `
  -ProjectPath '<RemotionProjectPath>'
```

执行会访问网络的深度音视频测试：

```powershell
pwsh -NoProfile -File (Join-Path $skillRoot 'scripts\check_environment.ps1') `
  -ProjectPath '<RemotionProjectPath>' `
  -Deep
```

输出 JSON，供 Agent 写入项目记录：

```powershell
pwsh -NoProfile -File (Join-Path $skillRoot 'scripts\check_environment.ps1') -Json |
  Set-Content -LiteralPath '.\environment-report.json' -Encoding utf8
```

规则：

- 存在任何必需项 `FAIL` 时，脚本退出码为 1，完整制作不得开始。
- `WARN` 表示需要人工步骤或当前模式未检查，例如尚未指定 Remotion 项目、尚未登录抖音。
- 只有预检通过后才进入选题、素材和渲染节点。
- 预检脚本只检测，不自动安装软件，不修改浏览器登录态。

## 12. 新电脑的最终冒烟测试

预检通过后，还要完成以下四个真实能力测试：

### 12.1 浏览器测试

- 启动专用 Chrome profile。
- 用户手动登录抖音。
- 通过 CDP 连接 `agent-browser`。
- 打开抖音首页、执行 snapshot 并保存一张截图。

### 12.2 下载器测试

- 对一条用户有权使用且允许下载的公开测试视频执行 `--simulate`。
- 确认能读到 ID、标题、时长和格式。
- 不在测试日志中输出 Cookie 或签名媒体地址。

### 12.3 TTS 测试

- 使用默认女声生成一句中文 MP3。
- 使用 FFprobe 确认音频时长大于 0。
- 实际播放，确认中文发音正常。

### 12.4 Remotion 渲染测试

- 列出 compositions。
- 渲染 1 秒、320×240 的测试 composition。
- 使用 FFprobe 确认 H.264 视频轨存在。
- 再用 FFmpeg 混入测试音频，确认 AAC 音轨存在。

只有四项均通过，才能认定新电脑具备完整 News-Editor 能力。抖音登录是人工授权状态，不能仅凭软件安装成功来替代。

## 13. 缺失能力与节点阻断关系

| 缺失项 | 允许继续的工作 | 必须停止的节点 |
|---|---|---|
| Git 或 GitHub 网络 | 无 | Skill 版本启动门；不得使用无法核验的旧版继续 |
| agent-browser CLI 或 Skill | 已给定链接的事实整理 | 抖音搜索、候选链接提取 |
| yt-dlp | 搜索与候选链接整理 | 本地素材下载 |
| FFmpeg / FFprobe | 选题、事实和文案 | 下载合并、媒体质检、裁切、混音、验收 |
| MiniMax 凭据 | 无配音草稿、用户自带配音 | 新闻女声配音生成（按停点检查） |
| Node/npm | 事实、素材下载、FFmpeg 编辑 | Remotion 工程安装和渲染 |
| Remotion Skill | 已存在工程的机械渲染 | 新工程的可靠设计与时间轴实现 |
| Remotion npm 包 | FFmpeg 独立处理 | Remotion 预览与渲染 |
| Chrome/Chromium | 非浏览器事实整理 | 登录态搜索、CDP、Remotion 浏览器渲染 |
| 微软雅黑 | 选题、素材和音频 | 使用默认视觉模板的最终渲染 |
| BGM 资产 | 无 BGM 的内部测试 | 默认完整混音交付 |
| 网络 | 本地已有素材的部分编辑 | 热点发现、抖音搜索、下载、MiniMax 配音 |

发现缺失项时，Agent 应报告“缺失组件、受影响节点、检测证据和安装命令”，而不是进入受影响节点后反复重试。

## 14. 安全与凭据迁移

- 不把 Cookie、账号密码、验证码、浏览器 Local Storage 或临时媒体签名打包进 Skill。
- 版本门只访问 config 指定并由脚本锁定的公开 GitHub 仓库，不接受带用户名、Token 或密码的远程 URL，不触发交互式凭据提示。
- 安装目录有本地修改、错误远程或历史分叉时不执行 reset、clean、stash、merge commit 或强制覆盖；转到独立开发克隆提交并发布后再运行。
- 不复制旧电脑的浏览器主 profile；新电脑由用户重新登录。
- 不在 `source-manifest.md`、Git 或聊天中记录 Cookie 值。
- 不让环境脚本自动关闭浏览器、删除 profile 或修改系统执行策略。
- 软件安装和全局升级属于外部环境变更，Agent 必须在用户授权范围内执行。
- 平台禁止下载或需要绕过验证时停止，不使用规避技术。

## 15. 迁移验收清单

- [ ] `news-editor` 整个 Skill 目录已复制。
- [ ] Node.js、npm、npx 版本检查通过。
- [ ] Python 版本检查通过。
- [ ] `agent-browser --version` 与 `agent-browser install` 已完成。
- [ ] agent-browser Skill 已安装。
- [ ] `python -m yt_dlp --version` 通过。
- [ ] MiniMax 凭据按停点检查通过（首次配音前）。
- [ ] FFmpeg、FFprobe、必要编码器和滤镜通过。
- [ ] Chrome/Chromium 可见浏览器存在。
- [ ] Remotion 完整 Skills 已安装。
- [ ] Remotion 项目包含 `package-lock.json` 并完成 `npm ci`。
- [ ] 微软雅黑常规和粗体存在。
- [ ] 三首 BGM、视觉参考和验收脚本存在。
- [ ] `check_environment.ps1` 无必需项失败。
- [ ] 深度 FFmpeg、TTS 和 Remotion 测试通过。
- [ ] 用户已在专用浏览器 profile 手动登录抖音。
- [ ] Cookie、Token 和旧电脑临时路径没有进入 Skill 或项目清单。
