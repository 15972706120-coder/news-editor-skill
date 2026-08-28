[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$OutputRoot
)

$ErrorActionPreference = 'Stop'
$outputPath = [System.IO.Path]::GetFullPath($OutputRoot).TrimEnd('\')
$issues = [System.Collections.Generic.List[object]]::new()

function Add-Issue {
    param(
        [Parameter(Mandatory = $true)][string]$Code,
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Message
    )
    $issues.Add([pscustomobject]@{ Code = $Code; Path = $Path; Message = $Message })
}

if (-not (Test-Path -LiteralPath $outputPath -PathType Container)) {
    throw "OutputRoot not found: $outputPath"
}

$rootItems = @(Get-ChildItem -LiteralPath $outputPath -Force)
foreach ($item in $rootItems) {
    if (-not $item.PSIsContainer) {
        Add-Issue -Code 'ROOT_FILE' -Path $item.FullName -Message 'outputs 根目录只允许日期文件夹。'
        continue
    }
    if ($item.Name -notmatch '^\d{4}-\d{2}-\d{2}$') {
        Add-Issue -Code 'DATE_NAME' -Path $item.FullName -Message '日期目录必须命名为 YYYY-MM-DD。'
    }
}

$dateDirs = @($rootItems | Where-Object { $_.PSIsContainer -and $_.Name -match '^\d{4}-\d{2}-\d{2}$' } | Sort-Object Name)
foreach ($dateDir in $dateDirs) {
    $dateItems = @(Get-ChildItem -LiteralPath $dateDir.FullName -Force)
    foreach ($item in $dateItems | Where-Object { -not $_.PSIsContainer }) {
        Add-Issue -Code 'DATE_LOOSE_FILE' -Path $item.FullName -Message '日期目录下不允许散落文件。'
    }

    $topicDirs = @($dateItems | Where-Object PSIsContainer)
    $sequences = [System.Collections.Generic.List[int]]::new()
    foreach ($topicDir in $topicDirs) {
        $match = [regex]::Match($topicDir.Name, '^(\d+)\.(.+)$')
        if (-not $match.Success) {
            Add-Issue -Code 'TOPIC_NAME' -Path $topicDir.FullName -Message '新闻目录必须命名为 1.中文标题。'
            continue
        }

        $sequence = [int]$match.Groups[1].Value
        $topicTitle = $match.Groups[2].Value
        $sequences.Add($sequence)
        if ($topicTitle -notmatch '[\p{IsCJKUnifiedIdeographs}]') {
            Add-Issue -Code 'TOPIC_NOT_CHINESE' -Path $topicDir.FullName -Message '新闻目录标题必须包含中文。'
        }

        $topicItems = @(Get-ChildItem -LiteralPath $topicDir.FullName -Force)
        foreach ($nestedDir in $topicItems | Where-Object PSIsContainer) {
            Add-Issue -Code 'TOPIC_NESTED_DIR' -Path $nestedDir.FullName -Message '新闻交付目录内不允许出现子目录。'
        }

        $files = @($topicItems | Where-Object { -not $_.PSIsContainer })
        $videos = @($files | Where-Object Extension -eq '.mp4')
        $covers = @($files | Where-Object Name -eq '封面.png')
        if ($videos.Count -ne 1) {
            Add-Issue -Code 'VIDEO_COUNT' -Path $topicDir.FullName -Message '每条新闻必须且只能有一个最终 MP4。'
        }
        if ($covers.Count -ne 1) {
            Add-Issue -Code 'COVER_COUNT' -Path $topicDir.FullName -Message '每条新闻必须且只能有一个封面.png。'
        }

        $optionalNames = @('发布文案.txt', '来源说明.md', '配音.wav')
        foreach ($file in $files) {
            $isVideo = $file.Extension -eq '.mp4'
            $isCover = $file.Name -eq '封面.png'
            $isOptional = $file.Name -in $optionalNames
            if (-not ($isVideo -or $isCover -or $isOptional)) {
                Add-Issue -Code 'UNEXPECTED_FILE' -Path $file.FullName -Message '输出目录包含非交付文件。'
            }
            if ($file.Name -match '(?i)(draft|final|v\d{1,3}|test|qa|preview|contact.?sheet|screenshot|render|source|log)') {
                Add-Issue -Code 'INTERNAL_NAME' -Path $file.FullName -Message '输出文件名包含草稿、版本或内部工作标记。'
            }
        }
    }

    $ordered = @($sequences | Sort-Object)
    for ($index = 0; $index -lt $ordered.Count; $index++) {
        $expected = $index + 1
        if ($ordered[$index] -ne $expected) {
            Add-Issue -Code 'SEQUENCE_GAP' -Path $dateDir.FullName -Message "生产序号应从 1 连续排列；期望 $expected，实际 $($ordered[$index])。"
            break
        }
    }
}

$result = [pscustomobject]@{
    Passed = $issues.Count -eq 0
    OutputRoot = $outputPath
    DateDirectoryCount = $dateDirs.Count
    TopicDirectoryCount = ($dateDirs | ForEach-Object { @(Get-ChildItem -LiteralPath $_.FullName -Directory).Count } | Measure-Object -Sum).Sum
    IssueCount = $issues.Count
    Issues = @($issues)
}

$result | ConvertTo-Json -Depth 5
if ($issues.Count -gt 0) {
    exit 2
}
