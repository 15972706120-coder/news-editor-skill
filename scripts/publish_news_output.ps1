[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$OutputRoot,

    [Parameter(Mandatory = $true)]
    [string]$WorkRoot,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d{4}-\d{2}-\d{2}$')]
    [string]$Date,

    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 999)]
    [int]$Sequence,

    [Parameter(Mandatory = $true)]
    [Alias('ChineseTitle')]
    [string]$CoverTitle,

    [Parameter(Mandatory = $true)]
    [string]$FinalVideo,

    [Parameter(Mandatory = $true)]
    [string]$Cover,

    [switch]$Replace
)

$ErrorActionPreference = 'Stop'

function Get-NormalizedPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    return [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
}

function Assert-PathInside {
    param(
        [Parameter(Mandatory = $true)][string]$Child,
        [Parameter(Mandatory = $true)][string]$Parent,
        [Parameter(Mandatory = $true)][string]$Label
    )

    $childPath = Get-NormalizedPath $Child
    $parentPath = Get-NormalizedPath $Parent
    if (-not $childPath.StartsWith($parentPath + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "$Label escapes its required root: $childPath"
    }
}

if (-not $OutputRoot) {
    # 缺省输出根目录从仓库根 config.json 读取
    $configPath = Join-Path $PSScriptRoot '..\config.json'
    if (Test-Path -LiteralPath $configPath) {
        $cfg = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
        $OutputRoot = $cfg.output.root
    }
    if (-not $OutputRoot) { throw '未提供 -OutputRoot，且 config.json 缺少 output.root。' }
}
$outputPath = Get-NormalizedPath $OutputRoot
$workPath = Get-NormalizedPath $WorkRoot
$videoPath = Get-NormalizedPath $FinalVideo
$coverPath = Get-NormalizedPath $Cover
$title = $CoverTitle.Trim()

if ($outputPath -eq $workPath) {
    throw 'OutputRoot and WorkRoot must be different directories.'
}
if ($workPath.StartsWith($outputPath + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'WorkRoot must not be inside OutputRoot.'
}
if (-not (Test-Path -LiteralPath $videoPath -PathType Leaf)) {
    throw "Final video not found: $videoPath"
}
if (-not (Test-Path -LiteralPath $coverPath -PathType Leaf)) {
    throw "Cover not found: $coverPath"
}
if ([System.IO.Path]::GetExtension($videoPath).ToLowerInvariant() -ne '.mp4') {
    throw 'FinalVideo must be an MP4 file.'
}
if ([System.IO.Path]::GetExtension($coverPath).ToLowerInvariant() -ne '.png') {
    throw 'Cover must be a PNG file.'
}
if ([string]::IsNullOrWhiteSpace($title) -or $title -notmatch '[\p{IsCJKUnifiedIdeographs}]') {
    throw 'CoverTitle must contain at least one Chinese character.'
}
$invalidChars = [System.IO.Path]::GetInvalidFileNameChars()
if ($title.IndexOfAny($invalidChars) -ge 0) {
    throw 'CoverTitle contains invalid Windows filename characters. Rewrite the cover title before rendering; do not silently rename only the file.'
}

$dateDir = Join-Path $outputPath $Date
$topicFolderName = "$Sequence.$title"
$topicDir = Join-Path $dateDir $topicFolderName
$destinationVideo = Join-Path $topicDir "$title.mp4"
$destinationCover = Join-Path $topicDir '封面.png'

Assert-PathInside -Child $dateDir -Parent $outputPath -Label 'Date directory'
Assert-PathInside -Child $topicDir -Parent $outputPath -Label 'Topic directory'
Assert-PathInside -Child $destinationVideo -Parent $topicDir -Label 'Video destination'
Assert-PathInside -Child $destinationCover -Parent $topicDir -Label 'Cover destination'

if (-not $PSCmdlet.ShouldProcess($topicDir, 'Publish one clean news deliverable')) {
    return
}

New-Item -ItemType Directory -Force -Path $outputPath | Out-Null
New-Item -ItemType Directory -Force -Path $dateDir | Out-Null

if (Test-Path -LiteralPath $topicDir) {
    $existing = @(Get-ChildItem -LiteralPath $topicDir -Force)
    if ($existing.Count -gt 0 -and -not $Replace) {
        throw "Topic output already exists. Use -Replace to archive and replace it: $topicDir"
    }
    if ($existing.Count -gt 0) {
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        $archiveDir = Join-Path $workPath (Join-Path $Date (Join-Path $topicFolderName (Join-Path '历史版本' $stamp)))
        Assert-PathInside -Child $archiveDir -Parent $workPath -Label 'Archive directory'
        New-Item -ItemType Directory -Force -Path $archiveDir | Out-Null
        foreach ($item in $existing) {
            Assert-PathInside -Child $item.FullName -Parent $topicDir -Label 'Existing output item'
            Move-Item -LiteralPath $item.FullName -Destination $archiveDir
        }
    }
} else {
    New-Item -ItemType Directory -Path $topicDir | Out-Null
}

$tempVideo = Join-Path $topicDir ('.publishing-' + [guid]::NewGuid().ToString('N') + '.mp4')
$tempCover = Join-Path $topicDir ('.publishing-' + [guid]::NewGuid().ToString('N') + '.png')
Assert-PathInside -Child $tempVideo -Parent $topicDir -Label 'Temporary video'
Assert-PathInside -Child $tempCover -Parent $topicDir -Label 'Temporary cover'

try {
    Copy-Item -LiteralPath $videoPath -Destination $tempVideo
    Copy-Item -LiteralPath $coverPath -Destination $tempCover
    Move-Item -LiteralPath $tempVideo -Destination $destinationVideo
    Move-Item -LiteralPath $tempCover -Destination $destinationCover
} catch {
    if (Test-Path -LiteralPath $tempVideo -PathType Leaf) {
        Remove-Item -LiteralPath $tempVideo
    }
    if (Test-Path -LiteralPath $tempCover -PathType Leaf) {
        Remove-Item -LiteralPath $tempCover
    }
    throw
}

[pscustomobject]@{
    Date = $Date
    Sequence = $Sequence
    Topic = $title
    OutputDirectory = $topicDir
    Video = $destinationVideo
    Cover = $destinationCover
}
