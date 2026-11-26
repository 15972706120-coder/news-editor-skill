#requires -Version 7.0

[CmdletBinding()]
param(
    [string]$ProjectPath,
    [ValidateSet('locked-ffmpeg', 'remotion')]
    [string]$Renderer = 'locked-ffmpeg',
    [string]$FfmpegPath,
    [string]$FfprobePath,
    # Interpreter to use instead of searching PATH; it still has to be Python 3.10 or newer.
    [string]$PythonPath,
    [switch]$Deep,
    [switch]$Json
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:NewsEditorChecks = [System.Collections.Generic.List[object]]::new()
$script:PythonInvocation = $null
$script:YtDlpInvocation = $null
$script:EdgeTtsInvocation = $null
$script:FfmpegExecutable = $null
$script:FfprobeExecutable = $null
$script:RemotionProjectReady = $false

function Add-Check {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Category,
        [Parameter(Mandatory)][bool]$Required,
        [Parameter(Mandatory)][ValidateSet('PASS', 'WARN', 'FAIL', 'SKIP')][string]$Status,
        [Parameter(Mandatory)][string]$Detail,
        [string]$Fix = ''
    )

    $script:NewsEditorChecks.Add([pscustomobject]@{
        name = $Name
        category = $Category
        required = $Required
        status = $Status
        detail = $Detail
        fix = $Fix
    })
}

function Resolve-CommandPath {
    param([Parameter(Mandatory)][string]$Name)

    $command = Get-Command $Name -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $command) {
        return $null
    }
    if ($command.Source) {
        return $command.Source
    }
    return $command.Path
}

function Invoke-External {
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$Arguments = @()
    )

    try {
        $output = & $FilePath @Arguments 2>&1 | Out-String
        return [pscustomobject]@{
            exitCode = $LASTEXITCODE
            output = $output.Trim()
            exception = $null
        }
    }
    catch {
        return [pscustomobject]@{
            exitCode = 999
            output = ''
            exception = $_.Exception.Message
        }
    }
}

function ConvertTo-VersionValue {
    param([string]$Text)

    if ([string]::IsNullOrWhiteSpace($Text)) {
        return $null
    }
    $match = [regex]::Match($Text, '(\d+\.\d+(?:\.\d+){0,2})')
    if (-not $match.Success) {
        return $null
    }
    try {
        return [version]$match.Groups[1].Value
    }
    catch {
        return $null
    }
}

function Find-FirstFile {
    param([string[]]$Candidates)

    foreach ($candidate in $Candidates) {
        if (-not [string]::IsNullOrWhiteSpace($candidate) -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return (Get-Item -LiteralPath $candidate).FullName
        }
    }
    return $null
}

# Operating-system key shared with config output.platform_roots and layout.bold_font.
$platformKey = 'linux'
$userHome = $HOME
if ($IsWindows) {
    $platformKey = 'win32'
    $userHome = $env:USERPROFILE
}
elseif ($IsMacOS) {
    $platformKey = 'darwin'
}

$osDescription = [System.Runtime.InteropServices.RuntimeInformation]::OSDescription
$osArchitecture = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture
if ($IsWindows) {
    Add-Check -Name 'platform' -Category 'system' -Required $true -Status 'PASS' `
        -Detail "$osDescription $osArchitecture"
}
elseif ($IsMacOS) {
    Add-Check -Name 'platform' -Category 'system' -Required $true -Status 'PASS' `
        -Detail "$osDescription $osArchitecture；资讯快讯链路在 macOS 上按 config 取本系统的字体和输出目录，Remotion 旧工程与平台发布只在 Windows 上验证过"
}
else {
    Add-Check -Name 'platform' -Category 'system' -Required $true -Status 'FAIL' `
        -Detail "当前系统不受支持：$osDescription $osArchitecture。" `
        -Fix '使用 Windows 10/11 x64 或 macOS，或先为目标系统改写字体、浏览器和路径规则。'
}

$powerShellVersion = $PSVersionTable.PSVersion
if ($powerShellVersion -ge [version]'7.0') {
    Add-Check -Name 'powershell' -Category 'system' -Required $true -Status 'PASS' `
        -Detail $powerShellVersion.ToString()
}
else {
    Add-Check -Name 'powershell' -Category 'system' -Required $true -Status 'FAIL' `
        -Detail $powerShellVersion.ToString() -Fix '安装 PowerShell 7，并使用 pwsh 运行本脚本。'
}

$nodePath = Resolve-CommandPath 'node'
if ($nodePath) {
    $nodeProbe = Invoke-External -FilePath $nodePath -Arguments @('--version')
    $nodeVersion = ConvertTo-VersionValue $nodeProbe.output
    if ($nodeProbe.exitCode -eq 0 -and $nodeVersion -and $nodeVersion.Major -ge 24) {
        Add-Check -Name 'node' -Category 'runtime' -Required $true -Status 'PASS' `
            -Detail "$($nodeProbe.output) at $nodePath"
    }
    else {
        Add-Check -Name 'node' -Category 'runtime' -Required $true -Status 'FAIL' `
            -Detail "检测到 $($nodeProbe.output)，完整流程要求 Node.js 24 LTS 或更高兼容版本。" `
            -Fix '从 https://nodejs.org/en/download 安装当前 LTS，重新打开终端。'
    }
}
else {
    Add-Check -Name 'node' -Category 'runtime' -Required $true -Status 'FAIL' `
        -Detail '未找到 node。' -Fix '安装 Node.js 24 LTS。'
}

foreach ($nodeTool in @('npm', 'npx')) {
    $toolPath = Resolve-CommandPath $nodeTool
    if ($toolPath) {
        $probe = Invoke-External -FilePath $toolPath -Arguments @('--version')
        if ($probe.exitCode -eq 0) {
            Add-Check -Name $nodeTool -Category 'runtime' -Required $true -Status 'PASS' `
                -Detail "$($probe.output) at $toolPath"
        }
        else {
            Add-Check -Name $nodeTool -Category 'runtime' -Required $true -Status 'FAIL' `
                -Detail "命令存在但无法运行：$($probe.exception)$($probe.output)" `
                -Fix '修复 Node.js/npm 安装后重新打开终端。'
        }
    }
    else {
        Add-Check -Name $nodeTool -Category 'runtime' -Required $true -Status 'FAIL' `
            -Detail "未找到 $nodeTool。" -Fix '重新安装 Node.js LTS，并确认 npm/npx 可用。'
    }
}

# An explicit -PythonPath wins. Otherwise Windows keeps its python / py -3 order, while Unix looks
# for python3 and versioned names; the first interpreter that is 3.10 or newer is the one used.
$pythonCandidates = [System.Collections.Generic.List[object]]::new()
if ($PythonPath) {
    $pythonCandidates.Add([pscustomobject]@{ file = $PythonPath; prefix = @(); label = $PythonPath })
}
elseif ($IsWindows) {
    $pythonPath = Resolve-CommandPath 'python'
    if ($pythonPath) {
        $pythonCandidates.Add([pscustomobject]@{ file = $pythonPath; prefix = @(); label = 'python' })
    }
    $launcherPath = Resolve-CommandPath 'py'
    if ($launcherPath) {
        $pythonCandidates.Add([pscustomobject]@{ file = $launcherPath; prefix = @('-3'); label = 'py -3' })
    }
}
else {
    foreach ($pythonName in @('python3', 'python3.14', 'python3.13', 'python3.12', 'python3.11', 'python3.10')) {
        $foundPath = Resolve-CommandPath $pythonName
        if ($foundPath) {
            $pythonCandidates.Add([pscustomobject]@{ file = $foundPath; prefix = @(); label = $pythonName })
        }
    }
}

$pythonSeen = ''
foreach ($pythonCandidate in $pythonCandidates) {
    $pythonProbe = Invoke-External -FilePath $pythonCandidate.file -Arguments ($pythonCandidate.prefix + @('--version'))
    $pythonVersion = ConvertTo-VersionValue $pythonProbe.output
    if ($pythonProbe.exitCode -eq 0 -and $pythonVersion -and $pythonVersion -ge [version]'3.10') {
        $script:PythonInvocation = [pscustomobject]@{ file = $pythonCandidate.file; prefix = $pythonCandidate.prefix }
        Add-Check -Name 'python' -Category 'runtime' -Required $true -Status 'PASS' `
            -Detail "$($pythonProbe.output) at $($pythonCandidate.file)"
        break
    }
    $pythonSeen = "$pythonSeen $($pythonCandidate.label): $($pythonProbe.output)$($pythonProbe.exception)".Trim()
}
if (-not $script:PythonInvocation) {
    $pythonDetail = if ($pythonSeen) { "检测到$pythonSeen，要求 Python 3.10 或更高版本。" } else { '未找到 python、python3 或 py。' }
    $pythonFix = if ($IsWindows) { '从 https://www.python.org/downloads/windows/ 安装 Python 3.10+。' } else { '安装 Python 3.10+，或用 -PythonPath 指定解释器。' }
    Add-Check -Name 'python' -Category 'runtime' -Required $true -Status 'FAIL' -Detail $pythonDetail -Fix $pythonFix
}

$agentBrowserPath = Resolve-CommandPath 'agent-browser'
if ($agentBrowserPath) {
    $agentProbe = Invoke-External -FilePath $agentBrowserPath -Arguments @('--version')
    if ($agentProbe.exitCode -eq 0) {
        Add-Check -Name 'agent-browser-cli' -Category 'browser' -Required $true -Status 'PASS' `
            -Detail "$($agentProbe.output) at $agentBrowserPath"
    }
    else {
        Add-Check -Name 'agent-browser-cli' -Category 'browser' -Required $true -Status 'FAIL' `
            -Detail "命令存在但无法运行：$($agentProbe.exception)$($agentProbe.output)" `
            -Fix '运行 npm install -g agent-browser，然后运行 agent-browser install。'
    }
}
else {
    Add-Check -Name 'agent-browser-cli' -Category 'browser' -Required $true -Status 'FAIL' `
        -Detail '未找到 agent-browser。' `
        -Fix '运行 npm install -g agent-browser；随后运行 agent-browser install。'
}

$skillCandidates = @(
    (Join-Path $userHome '.agents' 'skills' 'agent-browser' 'SKILL.md'),
    (Join-Path $userHome '.codex' 'skills' 'agent-browser' 'SKILL.md')
)
$agentBrowserSkill = Find-FirstFile $skillCandidates
if ($agentBrowserSkill) {
    Add-Check -Name 'agent-browser-skill' -Category 'agent-skill' -Required $true -Status 'PASS' `
        -Detail $agentBrowserSkill
}
else {
    Add-Check -Name 'agent-browser-skill' -Category 'agent-skill' -Required $true -Status 'FAIL' `
        -Detail '未找到 agent-browser Skill。' `
        -Fix '运行 npx -y skills@latest add vercel-labs/agent-browser -g -y。'
}

$remotionSkillCandidates = @(
    (Join-Path $userHome '.agents' 'skills' 'remotion' 'SKILL.md'),
    (Join-Path $userHome '.codex' 'skills' 'remotion' 'SKILL.md')
)
$remotionSkill = Find-FirstFile $remotionSkillCandidates
if ($remotionSkill) {
    $remotionSkillText = Get-Content -LiteralPath $remotionSkill -Raw -Encoding utf8
    if ($remotionSkillText -match 'catalogue entry|discovery stub') {
        Add-Check -Name 'remotion-skill' -Category 'agent-skill' -Required $false -Status 'WARN' `
            -Detail "检测到 Remotion 发现存根：$remotionSkill" `
            -Fix '建议运行 npx -y skills@latest add remotion-dev/skills -g -y 安装完整 Remotion Skills。'
    }
    else {
        Add-Check -Name 'remotion-skill' -Category 'agent-skill' -Required $false -Status 'PASS' `
            -Detail $remotionSkill
    }
}
else {
    Add-Check -Name 'remotion-skill' -Category 'agent-skill' -Required $false -Status 'WARN' `
        -Detail '未找到 Remotion Skill。已有工程仍可机械渲染，但新工程实现可靠性会降低。' `
        -Fix '运行 npx -y skills@latest add remotion-dev/skills -g -y。'
}

if ($script:PythonInvocation) {
    $ytProbe = Invoke-External -FilePath $script:PythonInvocation.file `
        -Arguments ($script:PythonInvocation.prefix + @('-m', 'yt_dlp', '--version'))
    if ($ytProbe.exitCode -eq 0) {
        $script:YtDlpInvocation = [pscustomobject]@{
            file = $script:PythonInvocation.file
            prefix = $script:PythonInvocation.prefix + @('-m', 'yt_dlp')
        }
        Add-Check -Name 'yt-dlp' -Category 'download' -Required $true -Status 'PASS' `
            -Detail "$($ytProbe.output) via Python module"
    }
    else {
        $ytDlpPath = Resolve-CommandPath 'yt-dlp'
        if ($ytDlpPath) {
            $directYtProbe = Invoke-External -FilePath $ytDlpPath -Arguments @('--version')
            if ($directYtProbe.exitCode -eq 0) {
                $script:YtDlpInvocation = [pscustomobject]@{ file = $ytDlpPath; prefix = @() }
                Add-Check -Name 'yt-dlp' -Category 'download' -Required $true -Status 'PASS' `
                    -Detail "$($directYtProbe.output) at $ytDlpPath"
            }
            else {
                Add-Check -Name 'yt-dlp' -Category 'download' -Required $true -Status 'FAIL' `
                    -Detail 'yt-dlp 命令和 Python 模块均无法运行。' `
                    -Fix '运行 python -m pip install --upgrade yt-dlp。'
            }
        }
        else {
            Add-Check -Name 'yt-dlp' -Category 'download' -Required $true -Status 'FAIL' `
                -Detail '未找到 yt-dlp Python 模块或命令。' `
                -Fix '运行 python -m pip install --upgrade yt-dlp。'
        }
    }

    $ttsProbe = Invoke-External -FilePath $script:PythonInvocation.file `
        -Arguments ($script:PythonInvocation.prefix + @('-m', 'edge_tts', '--version'))
    if ($ttsProbe.exitCode -eq 0) {
        $script:EdgeTtsInvocation = [pscustomobject]@{
            file = $script:PythonInvocation.file
            prefix = $script:PythonInvocation.prefix + @('-m', 'edge_tts')
        }
        Add-Check -Name 'edge-tts-legacy' -Category 'speech' -Required $false -Status 'PASS' `
            -Detail "$($ttsProbe.output) via Python module；仅供旧项目兼容，新制作使用 MiniMax。"
    }
    else {
        $edgeTtsPath = Resolve-CommandPath 'edge-tts'
        if ($edgeTtsPath) {
            $directTtsProbe = Invoke-External -FilePath $edgeTtsPath -Arguments @('--version')
            if ($directTtsProbe.exitCode -eq 0) {
                $script:EdgeTtsInvocation = [pscustomobject]@{ file = $edgeTtsPath; prefix = @() }
                Add-Check -Name 'edge-tts-legacy' -Category 'speech' -Required $false -Status 'PASS' `
                    -Detail "$($directTtsProbe.output) at $edgeTtsPath；仅供旧项目兼容。"
            }
            else {
                Add-Check -Name 'edge-tts-legacy' -Category 'speech' -Required $false -Status 'SKIP' `
                    -Detail '未启用旧版 Edge TTS；新制作使用 MiniMax。'
            }
        }
        else {
            Add-Check -Name 'edge-tts-legacy' -Category 'speech' -Required $false -Status 'SKIP' `
                -Detail '未安装旧版 Edge TTS；这不会阻塞 MiniMax 制作。'
        }
    }
}
else {
    Add-Check -Name 'yt-dlp' -Category 'download' -Required $true -Status 'FAIL' `
        -Detail 'Python 不可用，未继续检查 yt-dlp。' -Fix '先安装 Python，再安装 yt-dlp。'
    Add-Check -Name 'edge-tts-legacy' -Category 'speech' -Required $false -Status 'SKIP' `
        -Detail 'Python 不可用，未检查旧版 Edge TTS。'
}

$miniMaxClient = Join-Path $PSScriptRoot 'minimax_tts.py'
if (Test-Path -LiteralPath $miniMaxClient -PathType Leaf) {
    Add-Check -Name 'minimax-client' -Category 'speech' -Required $true -Status 'PASS' `
        -Detail 'MiniMax TTS client is present. API credentials are checked only at the first real voice generation.'
}
else {
    Add-Check -Name 'minimax-client' -Category 'speech' -Required $true -Status 'FAIL' `
        -Detail 'Missing scripts/minimax_tts.py.' -Fix 'Restore the complete News-Editor Skill package.'
}

if ([string]::IsNullOrWhiteSpace($env:MINIMAX_API_KEY)) {
    Add-Check -Name 'minimax-credentials' -Category 'speech' -Required $false -Status 'SKIP' `
        -Detail 'Deferred until the first real MiniMax voice generation; never paste the key into chat or logs.'
}
else {
    Add-Check -Name 'minimax-credentials' -Category 'speech' -Required $false -Status 'PASS' `
        -Detail 'MINIMAX_API_KEY is configured in the process environment; value not displayed.'
}

if ($FfmpegPath) {
    if (Test-Path -LiteralPath $FfmpegPath -PathType Leaf) {
        $script:FfmpegExecutable = (Get-Item -LiteralPath $FfmpegPath).FullName
    }
}
else {
    $script:FfmpegExecutable = Resolve-CommandPath 'ffmpeg'
}

if ($FfprobePath) {
    if (Test-Path -LiteralPath $FfprobePath -PathType Leaf) {
        $script:FfprobeExecutable = (Get-Item -LiteralPath $FfprobePath).FullName
    }
}
else {
    $script:FfprobeExecutable = Resolve-CommandPath 'ffprobe'
}

if ($script:FfmpegExecutable) {
    $ffmpegProbe = Invoke-External -FilePath $script:FfmpegExecutable -Arguments @('-version')
    if ($ffmpegProbe.exitCode -eq 0) {
        $firstLine = ($ffmpegProbe.output -split "`n" | Select-Object -First 1).Trim()
        Add-Check -Name 'ffmpeg' -Category 'media' -Required $true -Status 'PASS' `
            -Detail "$firstLine at $($script:FfmpegExecutable)"

        $filterProbe = Invoke-External -FilePath $script:FfmpegExecutable -Arguments @('-hide_banner', '-filters')
        $requiredFilters = @('crop', 'scale', 'boxblur', 'fade', 'loudnorm', 'volume', 'amix', 'afade', 'alimiter')
        $missingFilters = @($requiredFilters | Where-Object { $filterProbe.output -notmatch "(?m)\b$([regex]::Escape($_))\b" })
        if ($filterProbe.exitCode -eq 0 -and $missingFilters.Count -eq 0) {
            Add-Check -Name 'ffmpeg-filters' -Category 'media' -Required $true -Status 'PASS' `
                -Detail ($requiredFilters -join ', ')
        }
        else {
            Add-Check -Name 'ffmpeg-filters' -Category 'media' -Required $true -Status 'FAIL' `
                -Detail "缺失或无法确认：$($missingFilters -join ', ')" `
                -Fix '安装包含完整常用滤镜的现代 FFmpeg 构建。'
        }

        $encoderProbe = Invoke-External -FilePath $script:FfmpegExecutable -Arguments @('-hide_banner', '-encoders')
        $missingEncoders = [System.Collections.Generic.List[string]]::new()
        if ($encoderProbe.output -notmatch '(?m)\blibx264\b') { $missingEncoders.Add('libx264') }
        if ($encoderProbe.output -notmatch '(?m)\baac\b') { $missingEncoders.Add('aac') }
        if ($encoderProbe.exitCode -eq 0 -and $missingEncoders.Count -eq 0) {
            Add-Check -Name 'ffmpeg-encoders' -Category 'media' -Required $true -Status 'PASS' `
                -Detail 'libx264, aac'
        }
        else {
            Add-Check -Name 'ffmpeg-encoders' -Category 'media' -Required $true -Status 'FAIL' `
                -Detail "缺失或无法确认：$($missingEncoders -join ', ')" `
                -Fix '安装包含 libx264 和 AAC 编码器的 FFmpeg 构建。'
        }
    }
    else {
        Add-Check -Name 'ffmpeg' -Category 'media' -Required $true -Status 'FAIL' `
            -Detail 'ffmpeg 文件存在但无法运行。' -Fix '重新安装 FFmpeg 或指定正确的 -FfmpegPath。'
    }
}
else {
    Add-Check -Name 'ffmpeg' -Category 'media' -Required $true -Status 'FAIL' `
        -Detail '未找到 ffmpeg。' `
        -Fix '安装 FFmpeg 并加入 PATH，或传入 -FfmpegPath。'
}

if ($script:FfprobeExecutable) {
    $ffprobeProbe = Invoke-External -FilePath $script:FfprobeExecutable -Arguments @('-version')
    if ($ffprobeProbe.exitCode -eq 0) {
        $firstLine = ($ffprobeProbe.output -split "`n" | Select-Object -First 1).Trim()
        Add-Check -Name 'ffprobe' -Category 'media' -Required $true -Status 'PASS' `
            -Detail "$firstLine at $($script:FfprobeExecutable)"
    }
    else {
        Add-Check -Name 'ffprobe' -Category 'media' -Required $true -Status 'FAIL' `
            -Detail 'ffprobe 文件存在但无法运行。' -Fix '重新安装 FFmpeg 或指定正确的 -FfprobePath。'
    }
}
else {
    Add-Check -Name 'ffprobe' -Category 'media' -Required $true -Status 'FAIL' `
        -Detail '未找到 ffprobe。' `
        -Fix '安装 FFmpeg 完整构建并加入 PATH，或传入 -FfprobePath。'
}

$browserCandidates = @()
if ($IsWindows) {
    $programFiles = [Environment]::GetFolderPath('ProgramFiles')
    $programFilesX86 = [Environment]::GetFolderPath('ProgramFilesX86')
    $localAppData = [Environment]::GetFolderPath('LocalApplicationData')
    $browserCandidates = @(
        (Join-Path $programFiles 'Google\Chrome\Application\chrome.exe'),
        (Join-Path $programFilesX86 'Google\Chrome\Application\chrome.exe'),
        (Join-Path $localAppData 'Google\Chrome\Application\chrome.exe'),
        (Join-Path $programFiles 'Microsoft\Edge\Application\msedge.exe'),
        (Join-Path $programFilesX86 'Microsoft\Edge\Application\msedge.exe')
    )
}
elseif ($IsMacOS) {
    $browserCandidates = @(
        (Join-Path $userHome 'Applications/Google Chrome.app/Contents/MacOS/Google Chrome'),
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
        '/Applications/Chromium.app/Contents/MacOS/Chromium',
        '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge'
    )
}
$browserPath = Find-FirstFile $browserCandidates
if ($browserPath) {
    Add-Check -Name 'chromium-browser' -Category 'browser' -Required $true -Status 'PASS' `
        -Detail $browserPath
}
elseif ($IsMacOS) {
    Add-Check -Name 'chromium-browser' -Category 'browser' -Required $true -Status 'FAIL' `
        -Detail '未在 /Applications 或用户 Applications 中找到 Chrome、Chromium 或 Edge。' `
        -Fix '安装 Chrome，随后运行 agent-browser install。'
}
else {
    Add-Check -Name 'chromium-browser' -Category 'browser' -Required $true -Status 'FAIL' `
        -Detail '未找到 Chrome 或 Edge 的常见安装路径。' `
        -Fix '安装 Chrome/Chromium；随后运行 agent-browser install。'
}

# The one bold font this operating system may render with is config layout.bold_font, keyed like
# output.platform_roots. A missing entry or file is a stop: the renderer never falls back to another face.
$boldFont = $null
$fontConfigPath = Join-Path $PSScriptRoot '..' 'config.json'
if (Test-Path -LiteralPath $fontConfigPath -PathType Leaf) {
    $fontConfig = Get-Content -LiteralPath $fontConfigPath -Raw | ConvertFrom-Json
    $layoutSection = $fontConfig.PSObject.Properties['layout']
    if ($layoutSection) {
        $boldFontSection = $layoutSection.Value.PSObject.Properties['bold_font']
        if ($boldFontSection) {
            $boldFontPlatform = $boldFontSection.Value.PSObject.Properties[$platformKey]
            if ($boldFontPlatform) {
                $boldFont = $boldFontPlatform.Value
            }
        }
    }
}
if ($boldFont -and (Test-Path -LiteralPath ([string]$boldFont.path) -PathType Leaf)) {
    Add-Check -Name 'approved-bold-font' -Category 'visual' -Required $true -Status 'PASS' `
        -Detail "$($boldFont.name) $($boldFont.path)（字体集合序号 $($boldFont.index)）"
}
elseif ($boldFont) {
    Add-Check -Name 'approved-bold-font' -Category 'visual' -Required $true -Status 'FAIL' `
        -Detail "config.json layout.bold_font.$platformKey 指定的字体文件不存在：$($boldFont.path)" `
        -Fix '恢复该系统字体，或经用户批准后更新 config.json 的 layout.bold_font。'
}
else {
    Add-Check -Name 'approved-bold-font' -Category 'visual' -Required $true -Status 'FAIL' `
        -Detail "config.json 的 layout.bold_font 没有定义 $platformKey 的粗体字体。" `
        -Fix '经用户批准后，在 config.json 的 layout.bold_font 中为该系统添加已批准的粗体字体。'
}

$skillRoot = Split-Path -Parent $PSScriptRoot
# Relative paths use forward slashes and are split before Join-Path, so backslashes are never read literally on Unix.
$requiredSkillFiles = @(
    'SKILL.md',
    'references/environment-setup.md',
    'references/douyin-news-footage-pipeline.md',
    'references/editorial-sop.md',
    'references/visual-audio-template.md',
    'references/quality-standards.md',
    'scripts/validate_news_video.py',
    'scripts/render_news_brief.py',
    'assets/audio/bgm-01.mp3',
    'assets/audio/bgm-02.mp3',
    'assets/audio/bgm-03.mp3',
    'assets/references/cover-style-reference.png',
    'assets/references/cover-typography-reference.png',
    'assets/references/v3-reference/reference-contact-sheet.png'
)
$missingSkillFiles = [System.Collections.Generic.List[string]]::new()
foreach ($relativePath in $requiredSkillFiles) {
    $fullPath = Join-Path $skillRoot ($relativePath -split '/')
    if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf) -or (Get-Item -LiteralPath $fullPath).Length -eq 0) {
        $missingSkillFiles.Add($relativePath)
    }
}
if ($missingSkillFiles.Count -eq 0) {
    Add-Check -Name 'news-editor-assets' -Category 'skill' -Required $true -Status 'PASS' `
        -Detail "$($requiredSkillFiles.Count) 个必需文件完整，Skill 根目录：$skillRoot"
}
else {
    Add-Check -Name 'news-editor-assets' -Category 'skill' -Required $true -Status 'FAIL' `
        -Detail "缺失或为空：$($missingSkillFiles -join ', ')" `
        -Fix '重新复制完整 news-editor Skill 目录，不要只复制 SKILL.md。'
}

if (-not $IsWindows -and ($Renderer -eq 'remotion' -or $ProjectPath)) {
    Add-Check -Name 'remotion-platform' -Category 'render' -Required $false -Status 'WARN' `
        -Detail 'Remotion 旧工程链路只在 Windows 上验证过；资讯快讯请使用默认的 locked-ffmpeg。'
}
if ($Renderer -eq 'remotion' -and [string]::IsNullOrWhiteSpace($ProjectPath)) {
    Add-Check -Name 'remotion-project' -Category 'render' -Required $true -Status 'FAIL' `
        -Detail '选择了 Remotion，但没有可验证的 ProjectPath。' -Fix '传入实际工程，或明确使用内置 locked-ffmpeg 适配器。'
}
elseif ([string]::IsNullOrWhiteSpace($ProjectPath)) {
    Add-Check -Name 'remotion-project' -Category 'render' -Required $false -Status 'SKIP' `
        -Detail '使用内置 locked-ffmpeg 排版，不依赖 Remotion 工程；这不代表已验证 Remotion。'
}
elseif (-not (Test-Path -LiteralPath $ProjectPath -PathType Container)) {
    Add-Check -Name 'remotion-project' -Category 'render' -Required $true -Status 'FAIL' `
        -Detail "项目目录不存在：$ProjectPath" -Fix '传入包含 package.json 的 Remotion 项目目录。'
}
else {
    $resolvedProjectPath = (Get-Item -LiteralPath $ProjectPath).FullName
    $packageJsonPath = Join-Path $resolvedProjectPath 'package.json'
    $packageLockPath = Join-Path $resolvedProjectPath 'package-lock.json'
    if (-not (Test-Path -LiteralPath $packageJsonPath -PathType Leaf)) {
        if ($Renderer -eq 'remotion') {
            Add-Check -Name 'remotion-project' -Category 'render' -Required $true -Status 'FAIL' `
                -Detail "缺少 package.json：$resolvedProjectPath" `
                -Fix '使用 create-video 创建工程，或传入正确的工程路径。'
        }
        else {
            # 资讯快讯用内置 locked-ffmpeg 排版，不依赖 Remotion 工程；ProjectPath 在这里只说明项目根目录
            Add-Check -Name 'remotion-project' -Category 'render' -Required $false -Status 'WARN' `
                -Detail "ProjectPath 不是 Remotion 工程（没有 package.json）：$resolvedProjectPath。本次用内置 locked-ffmpeg 排版，不依赖它；这不代表已验证 Remotion。" `
                -Fix '只有 Remotion 旧工程才需要传入含 package.json 的项目目录。'
        }
    }
    else {
        try {
            $packageJson = Get-Content -LiteralPath $packageJsonPath -Raw -Encoding utf8 | ConvertFrom-Json
            $dependencyNames = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
            $packageVersions = @{}
            foreach ($groupName in @('dependencies', 'devDependencies')) {
                $groupProperty = $packageJson.PSObject.Properties[$groupName]
                if ($groupProperty -and $groupProperty.Value) {
                    foreach ($property in $groupProperty.Value.PSObject.Properties) {
                        [void]$dependencyNames.Add($property.Name)
                        $packageVersions[$property.Name] = [string]$property.Value
                    }
                }
            }
            $requiredPackages = @('remotion', '@remotion/cli', 'react', 'react-dom', 'typescript')
            $missingPackages = @($requiredPackages | Where-Object { -not $dependencyNames.Contains($_) })
            if ($missingPackages.Count -eq 0) {
                Add-Check -Name 'remotion-packages' -Category 'render' -Required $true -Status 'PASS' `
                    -Detail ($requiredPackages -join ', ')
            }
            else {
                Add-Check -Name 'remotion-packages' -Category 'render' -Required $true -Status 'FAIL' `
                    -Detail "package.json 缺少：$($missingPackages -join ', ')" `
                    -Fix '安装缺失包，并保证 remotion 与 @remotion/* 版本一致。'
            }

            $remotionVersion = $packageVersions['remotion']
            $remotionCliVersion = $packageVersions['@remotion/cli']
            if ($remotionVersion -and $remotionCliVersion -and $remotionVersion -ceq $remotionCliVersion) {
                Add-Check -Name 'remotion-version-alignment' -Category 'render' -Required $true -Status 'PASS' `
                    -Detail "remotion=$remotionVersion; @remotion/cli=$remotionCliVersion"
            }
            else {
                Add-Check -Name 'remotion-version-alignment' -Category 'render' -Required $true -Status 'FAIL' `
                    -Detail "remotion=$remotionVersion; @remotion/cli=$remotionCliVersion" `
                    -Fix '把所有 remotion 和 @remotion/* 包锁定为完全相同的版本。'
            }
        }
        catch {
            Add-Check -Name 'remotion-packages' -Category 'render' -Required $true -Status 'FAIL' `
                -Detail "无法解析 package.json：$($_.Exception.Message)" `
                -Fix '修复 package.json 后重试。'
        }

        if (Test-Path -LiteralPath $packageLockPath -PathType Leaf) {
            Add-Check -Name 'remotion-lockfile' -Category 'render' -Required $true -Status 'PASS' `
                -Detail $packageLockPath
        }
        else {
            Add-Check -Name 'remotion-lockfile' -Category 'render' -Required $true -Status 'FAIL' `
                -Detail '缺少 package-lock.json。' `
                -Fix '在工程目录执行 npm install 并保留 package-lock.json。'
        }

        $remotionShellName = if ($IsWindows) { 'remotion.cmd' } else { 'remotion' }
        $remotionCmd = Join-Path $resolvedProjectPath 'node_modules' '.bin' $remotionShellName
        if (Test-Path -LiteralPath $remotionCmd -PathType Leaf) {
            $script:RemotionProjectReady = $true
            Add-Check -Name 'remotion-installed' -Category 'render' -Required $true -Status 'PASS' `
                -Detail $remotionCmd
        }
        else {
            Add-Check -Name 'remotion-installed' -Category 'render' -Required $true -Status 'FAIL' `
                -Detail '项目 node_modules 中没有 Remotion CLI。' `
                -Fix '在项目目录执行 npm ci。不要从旧电脑复制 node_modules。'
        }
    }
}

Add-Check -Name 'douyin-login' -Category 'authorization' -Required $false -Status 'WARN' `
    -Detail '软件预检不能证明抖音登录有效；首次运行必须由用户在专用浏览器 profile 手动登录。' `
    -Fix '按 douyin-news-footage-pipeline.md 建立专用 profile，并通过 CDP 连接。'

if ($Deep) {
    $tempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    $tempRoot = Join-Path $tempBase ("news-editor-preflight-" + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tempRoot | Out-Null

    try {
        if ($script:FfmpegExecutable -and $script:FfprobeExecutable) {
            $mediaTest = Join-Path $tempRoot 'ffmpeg-smoke.mp4'
            $mediaProbe = Invoke-External -FilePath $script:FfmpegExecutable -Arguments @(
                '-hide_banner', '-loglevel', 'error',
                '-f', 'lavfi', '-i', 'color=c=black:s=320x240:d=1:r=30',
                '-f', 'lavfi', '-i', 'sine=frequency=1000:duration=1',
                '-shortest', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                '-c:a', 'aac', '-y', $mediaTest
            )
            if ($mediaProbe.exitCode -eq 0 -and (Test-Path -LiteralPath $mediaTest -PathType Leaf)) {
                $decodeProbe = Invoke-External -FilePath $script:FfmpegExecutable `
                    -Arguments @('-v', 'error', '-i', $mediaTest, '-f', 'null', $(if ($IsWindows) { 'NUL' } else { '/dev/null' }))
                if ($decodeProbe.exitCode -eq 0) {
                    Add-Check -Name 'ffmpeg-deep-smoke' -Category 'deep-test' -Required $true -Status 'PASS' `
                        -Detail 'H.264 + AAC 生成和完整解码成功。'
                }
                else {
                    Add-Check -Name 'ffmpeg-deep-smoke' -Category 'deep-test' -Required $true -Status 'FAIL' `
                        -Detail "生成成功但解码失败：$($decodeProbe.output)" `
                        -Fix '更换完整 FFmpeg 构建。'
                }
            }
            else {
                Add-Check -Name 'ffmpeg-deep-smoke' -Category 'deep-test' -Required $true -Status 'FAIL' `
                    -Detail "H.264/AAC 测试生成失败：$($mediaProbe.exception)$($mediaProbe.output)" `
                    -Fix '检查 libx264、aac、lavfi 和输出目录权限。'
            }
        }

        # Edge TTS is legacy-only. Do not make a network call during the current MiniMax preflight.
        if ($false -and $script:EdgeTtsInvocation) {
            $ttsTest = Join-Path $tempRoot 'edge-tts-smoke.mp3'
            $ttsResult = Invoke-External -FilePath $script:EdgeTtsInvocation.file -Arguments (
                $script:EdgeTtsInvocation.prefix + @(
                    '--voice', 'zh-CN-XiaoxiaoNeural',
                    '--text', '新闻编辑环境检测通过',
                    '--write-media', $ttsTest
                )
            )
            if ($ttsResult.exitCode -eq 0 -and (Test-Path -LiteralPath $ttsTest -PathType Leaf) -and (Get-Item -LiteralPath $ttsTest).Length -gt 1000) {
                Add-Check -Name 'edge-tts-deep-smoke-legacy' -Category 'deep-test' -Required $false -Status 'PASS' `
                    -Detail '旧版 Edge TTS 冒烟成功；新制作仍使用 MiniMax。'
            }
            else {
                Add-Check -Name 'edge-tts-deep-smoke-legacy' -Category 'deep-test' -Required $false -Status 'WARN' `
                    -Detail "语音生成失败：$($ttsResult.exception)$($ttsResult.output)" `
                    -Fix '检查网络、edge-tts 版本和 Edge 在线语音服务可达性。'
            }
        }

        if ($ProjectPath -and $script:RemotionProjectReady) {
            $npxPath = Resolve-CommandPath 'npx'
            $resolvedProjectPath = (Get-Item -LiteralPath $ProjectPath).FullName
            Push-Location $resolvedProjectPath
            try {
                $remotionProbe = Invoke-External -FilePath $npxPath `
                    -Arguments @('--no-install', 'remotion', 'versions')
            }
            finally {
                Pop-Location
            }
            if ($remotionProbe.exitCode -eq 0) {
                Add-Check -Name 'remotion-deep-smoke' -Category 'deep-test' -Required $true -Status 'PASS' `
                    -Detail $remotionProbe.output
            }
            else {
                Add-Check -Name 'remotion-deep-smoke' -Category 'deep-test' -Required $true -Status 'FAIL' `
                    -Detail "Remotion CLI 无法启动：$($remotionProbe.exception)$($remotionProbe.output)" `
                    -Fix '在项目目录执行 npm ci，检查 Node 版本和 Remotion 包版本一致性。'
            }
        }
    }
    finally {
        $resolvedTempRoot = [IO.Path]::GetFullPath($tempRoot)
        $resolvedTempBase = [IO.Path]::GetFullPath($tempBase).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
        $tempLeaf = Split-Path -Leaf $resolvedTempRoot
        $safeToDelete = $resolvedTempRoot.StartsWith($resolvedTempBase, [StringComparison]::OrdinalIgnoreCase) -and
            $tempLeaf.StartsWith('news-editor-preflight-', [StringComparison]::OrdinalIgnoreCase)
        if ($safeToDelete -and (Test-Path -LiteralPath $resolvedTempRoot -PathType Container)) {
            Remove-Item -LiteralPath $resolvedTempRoot -Recurse -Force
        }
    }
}

$requiredFailures = @($script:NewsEditorChecks | Where-Object { $_.required -and $_.status -eq 'FAIL' })
$warnings = @($script:NewsEditorChecks | Where-Object { $_.status -eq 'WARN' })
$summary = [pscustomobject]@{
    ready = ($requiredFailures.Count -eq 0)
    deep = [bool]$Deep
    checkedAt = [DateTimeOffset]::Now.ToString('o')
    requiredFailures = $requiredFailures.Count
    warnings = $warnings.Count
    checks = $script:NewsEditorChecks
}

if ($Json) {
    $summary | ConvertTo-Json -Depth 6
}
else {
    $script:NewsEditorChecks |
        Select-Object status, required, category, name, detail, fix |
        Format-Table -Wrap -AutoSize |
        Out-Host
    Write-Host ""
    Write-Host "News-Editor ready: $($summary.ready); required failures: $($summary.requiredFailures); warnings: $($summary.warnings)"
}

if ($requiredFailures.Count -gt 0) {
    exit 1
}
exit 0
