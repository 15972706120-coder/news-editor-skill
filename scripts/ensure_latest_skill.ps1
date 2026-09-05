[CmdletBinding()]
param(
    [string]$RunId = ([guid]::NewGuid().ToString()),
    [int]$TimeoutSeconds = 0
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:Schema = 'news-editor-version-gate/v1'
$script:SkillRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$script:TrustedRepository = 'https://github.com/15972706120-coder/news-editor-skill.git'
$script:TrustedBranch = 'main'
$script:TrustedRemote = 'origin'
$script:GitPath = $null
$script:Repository = $script:TrustedRepository
$script:Branch = $script:TrustedBranch
$script:RemoteName = $script:TrustedRemote
$script:RemoteSha = $null
$script:LocalSha = $null
$script:ActiveSha = $null
$script:Version = $null
$script:NetworkAttempts = 2
$script:LockTimeoutSeconds = 30

function New-GateResult {
    param(
        [Parameter(Mandatory)][string]$Status,
        [Parameter(Mandatory)][int]$ExitCode,
        [Parameter(Mandatory)][string]$Message,
        [bool]$MustReload = $false
    )

    [pscustomobject]@{
        schema         = $script:Schema
        run_id         = $RunId
        status         = $Status
        exit_code      = $ExitCode
        checked_at_utc = [DateTime]::UtcNow.ToString('o')
        repository     = $script:Repository
        branch         = $script:Branch
        local_sha      = $script:LocalSha
        remote_sha     = $script:RemoteSha
        active_sha     = $script:ActiveSha
        version        = $script:Version
        must_reload    = $MustReload
        message        = $Message
    }
}

function Invoke-GitProcess {
    param(
        [Parameter(Mandatory)][string[]]$Arguments,
        [int]$Seconds = 15
    )

    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $script:GitPath
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $startInfo.StandardErrorEncoding = [System.Text.Encoding]::UTF8
    $startInfo.Environment['GIT_TERMINAL_PROMPT'] = '0'
    $startInfo.Environment['GCM_INTERACTIVE'] = 'Never'
    foreach ($argument in $Arguments) {
        [void]$startInfo.ArgumentList.Add($argument)
    }

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    [void]$process.Start()
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    if (-not $process.WaitForExit($Seconds * 1000)) {
        try { $process.Kill($true) } catch { }
        return [pscustomobject]@{
            ExitCode = 124
            Stdout = ''
            Stderr = "Process timed out after $Seconds seconds."
        }
    }

    [pscustomobject]@{
        ExitCode = $process.ExitCode
        Stdout = $stdoutTask.GetAwaiter().GetResult().Trim()
        Stderr = $stderrTask.GetAwaiter().GetResult().Trim()
    }
}

function Invoke-LocalGit {
    param(
        [Parameter(Mandatory)][string[]]$Arguments,
        [int]$Seconds = 15
    )

    $prefix = @('-c', "safe.directory=$($script:SkillRoot)", '-C', $script:SkillRoot)
    Invoke-GitProcess -Arguments ($prefix + $Arguments) -Seconds $Seconds
}

function Normalize-GitUrl {
    param([Parameter(Mandatory)][string]$Url)

    $normalized = $Url.Trim().TrimEnd('/')
    if ($normalized.EndsWith('.git', [System.StringComparison]::OrdinalIgnoreCase)) {
        $normalized = $normalized.Substring(0, $normalized.Length - 4)
    }
    $normalized.ToLowerInvariant()
}

function Read-And-ValidateConfig {
    $configPath = Join-Path $script:SkillRoot 'config.json'
    if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
        throw 'config.json is missing.'
    }
    $config = Get-Content -Raw -LiteralPath $configPath -Encoding UTF8 | ConvertFrom-Json
    if ($null -eq $config.skill_update) {
        throw 'config.json is missing skill_update.'
    }

    $script:Repository = [string]$config.skill_update.repository
    $script:Branch = [string]$config.skill_update.branch
    $script:RemoteName = [string]$config.skill_update.remote
    $script:NetworkAttempts = [int]$config.skill_update.network_attempts
    if ($TimeoutSeconds -gt 0) {
        $script:TimeoutSeconds = $TimeoutSeconds
    } else {
        $script:TimeoutSeconds = [int]$config.skill_update.network_timeout_seconds
    }

    if ((Normalize-GitUrl $script:Repository) -ne (Normalize-GitUrl $script:TrustedRepository)) {
        throw 'The configured repository is not the trusted News-Editor repository.'
    }
    if ($script:Branch -ne $script:TrustedBranch) {
        throw 'The configured branch is not the trusted News-Editor branch.'
    }
    if ($script:RemoteName -ne $script:TrustedRemote) {
        throw 'The configured remote is not the trusted News-Editor remote.'
    }
    if ([bool]$config.skill_update.allow_stale_on_failure) {
        throw 'Strict version policy requires allow_stale_on_failure=false.'
    }
    if ([string]$config.skill_update.policy -ne 'strict_before_every_run') {
        throw 'Strict version policy is not enabled.'
    }
    if ($script:NetworkAttempts -lt 1 -or $script:NetworkAttempts -gt 3) {
        throw 'network_attempts must be between 1 and 3.'
    }
    if ($script:TimeoutSeconds -lt 5 -or $script:TimeoutSeconds -gt 60) {
        throw 'network_timeout_seconds must be between 5 and 60.'
    }
}

function Read-Version {
    $versionPath = Join-Path $script:SkillRoot 'VERSION'
    if (-not (Test-Path -LiteralPath $versionPath -PathType Leaf)) {
        throw 'VERSION is missing.'
    }
    $versionText = (Get-Content -Raw -LiteralPath $versionPath -Encoding UTF8).Trim()
    $config = Get-Content -Raw -LiteralPath (Join-Path $script:SkillRoot 'config.json') -Encoding UTF8 | ConvertFrom-Json
    if ($versionText -ne [string]$config.version) {
        throw 'VERSION and config.json version do not match.'
    }
    $script:Version = $versionText
}

function Test-CoreFiles {
    $requiredFiles = @(
        'SKILL.md',
        'VERSION',
        'config.json',
        'scripts/ensure_latest_skill.ps1',
        'scripts/check_skill_consistency.py',
        'references/current-production-profile-v2.md',
        'assets/references/locked-layout/layout-lock-v2.json'
    )
    foreach ($relativePath in $requiredFiles) {
        if (-not (Test-Path -LiteralPath (Join-Path $script:SkillRoot $relativePath) -PathType Leaf)) {
            throw "Required file is missing: $relativePath"
        }
    }

    $skillText = Get-Content -Raw -LiteralPath (Join-Path $script:SkillRoot 'SKILL.md') -Encoding UTF8
    if ($skillText -notmatch '(?m)^name:\s*news-editor\s*$') {
        throw 'SKILL.md frontmatter name is not news-editor.'
    }
    if ($skillText.IndexOf('ensure_latest_skill.ps1', [System.StringComparison]::Ordinal) -lt 0) {
        throw 'SKILL.md does not declare the version gate.'
    }

    $lockData = Get-Content -Raw -LiteralPath (Join-Path $script:SkillRoot 'assets/references/locked-layout/layout-lock-v2.json') -Encoding UTF8 | ConvertFrom-Json
    if ([string]$lockData.status -ne 'active') {
        throw 'The active layout lock is invalid.'
    }
    Read-Version
}

function Get-RemoteSha {
    $lastError = ''
    for ($attempt = 1; $attempt -le $script:NetworkAttempts; $attempt++) {
        $query = Invoke-GitProcess -Arguments @(
            'ls-remote', '--exit-code', '--heads', $script:TrustedRepository,
            "refs/heads/$($script:TrustedBranch)"
        ) -Seconds $script:TimeoutSeconds
        if ($query.ExitCode -eq 0) {
            $lines = @($query.Stdout -split "`r?`n" | Where-Object { $_.Trim() })
            if ($lines.Count -ne 1) {
                throw 'GitHub returned an invalid number of branch records.'
            }
            $parts = $lines[0] -split '\s+'
            if ($parts.Count -lt 2 -or $parts[0] -notmatch '^[0-9a-fA-F]{40}$') {
                throw 'GitHub returned an invalid commit SHA.'
            }
            return $parts[0].ToLowerInvariant()
        }
        $lastError = $query.Stderr
    }
    throw "Unable to verify GitHub main after $($script:NetworkAttempts) attempts: $lastError"
}

function Invoke-VersionGate {
    try {
        $gitCommand = Get-Command git -ErrorAction Stop
        $script:GitPath = $gitCommand.Source
    } catch {
        return New-GateResult -Status 'BLOCKED_GIT_MISSING' -ExitCode 10 -Message 'Git is required before News-Editor can verify GitHub.'
    }

    $inside = Invoke-LocalGit -Arguments @('rev-parse', '--is-inside-work-tree')
    if ($inside.ExitCode -ne 0 -or $inside.Stdout -ne 'true') {
        return New-GateResult -Status 'BLOCKED_NOT_GIT' -ExitCode 11 -Message 'The active News-Editor installation is not a Git worktree.'
    }

    $dirty = Invoke-LocalGit -Arguments @('status', '--porcelain=v1', '--untracked-files=all')
    if ($dirty.ExitCode -ne 0) {
        return New-GateResult -Status 'BLOCKED_INSTALL_INVALID' -ExitCode 12 -Message 'Unable to inspect the News-Editor worktree.'
    }
    if ($dirty.Stdout) {
        $changedPaths = @($dirty.Stdout -split "`r?`n" | ForEach-Object {
            $entry = $_.Trim()
            if ($entry -match '^\S+\s+(.+)$') { $Matches[1] } else { $entry }
        })
        return New-GateResult -Status 'BLOCKED_LOCAL_CHANGES' -ExitCode 20 -Message ("Local changes must be committed to GitHub or moved to a development clone: " + ($changedPaths -join ', '))
    }

    try {
        Read-And-ValidateConfig
        Test-CoreFiles
    } catch {
        return New-GateResult -Status 'BLOCKED_INSTALL_INVALID' -ExitCode 12 -Message $_.Exception.Message
    }

    $origin = Invoke-LocalGit -Arguments @('remote', 'get-url', $script:RemoteName)
    if ($origin.ExitCode -ne 0 -or (Normalize-GitUrl $origin.Stdout) -ne (Normalize-GitUrl $script:TrustedRepository)) {
        return New-GateResult -Status 'BLOCKED_ORIGIN_MISMATCH' -ExitCode 21 -Message 'The origin remote is not the trusted News-Editor repository.'
    }

    $currentBranch = Invoke-LocalGit -Arguments @('symbolic-ref', '--short', '-q', 'HEAD')
    if ($currentBranch.ExitCode -ne 0 -or $currentBranch.Stdout -ne $script:TrustedBranch) {
        return New-GateResult -Status 'BLOCKED_BRANCH_MISMATCH' -ExitCode 22 -Message 'The active installation must be on the main branch.'
    }

    $head = Invoke-LocalGit -Arguments @('rev-parse', 'HEAD')
    if ($head.ExitCode -ne 0 -or $head.Stdout -notmatch '^[0-9a-fA-F]{40}$') {
        return New-GateResult -Status 'BLOCKED_INSTALL_INVALID' -ExitCode 12 -Message 'Unable to read the local commit SHA.'
    }
    $script:LocalSha = $head.Stdout.ToLowerInvariant()
    $script:ActiveSha = $script:LocalSha

    try {
        $script:RemoteSha = Get-RemoteSha
    } catch {
        return New-GateResult -Status 'BLOCKED_VERSION_NETWORK' -ExitCode 30 -Message $_.Exception.Message
    }

    if ($script:LocalSha -eq $script:RemoteSha) {
        return New-GateResult -Status 'LATEST_READY' -ExitCode 0 -Message 'The active News-Editor commit exactly matches GitHub main.'
    }

    $fetch = Invoke-LocalGit -Arguments @('fetch', '--no-tags', $script:RemoteName, "refs/heads/$($script:TrustedBranch)") -Seconds $script:TimeoutSeconds
    if ($fetch.ExitCode -ne 0) {
        return New-GateResult -Status 'BLOCKED_UPDATE_FETCH' -ExitCode 31 -Message 'GitHub was reachable, but the latest commit could not be fetched.'
    }
    $fetchHead = Invoke-LocalGit -Arguments @('rev-parse', 'FETCH_HEAD')
    if ($fetchHead.ExitCode -ne 0 -or $fetchHead.Stdout -notmatch '^[0-9a-fA-F]{40}$') {
        return New-GateResult -Status 'BLOCKED_UPDATE_FETCH' -ExitCode 31 -Message 'FETCH_HEAD is invalid.'
    }
    $fetchedSha = $fetchHead.Stdout.ToLowerInvariant()

    if ($fetchedSha -ne $script:RemoteSha) {
        try {
            $script:RemoteSha = Get-RemoteSha
        } catch {
            return New-GateResult -Status 'BLOCKED_VERSION_NETWORK' -ExitCode 30 -Message $_.Exception.Message
        }
        if ($fetchedSha -ne $script:RemoteSha) {
            return New-GateResult -Status 'BLOCKED_REMOTE_CHANGED' -ExitCode 32 -Message 'GitHub main changed during the version check; start a new run.'
        }
    }

    $ancestor = Invoke-LocalGit -Arguments @('merge-base', '--is-ancestor', $script:LocalSha, $script:RemoteSha)
    if ($ancestor.ExitCode -ne 0) {
        return New-GateResult -Status 'BLOCKED_VERSION_DIVERGED' -ExitCode 33 -Message 'Local main is ahead of or diverged from GitHub main; automatic overwrite is forbidden.'
    }

    $merge = Invoke-LocalGit -Arguments @('merge', '--ff-only', $script:RemoteSha) -Seconds $script:TimeoutSeconds
    if ($merge.ExitCode -ne 0) {
        return New-GateResult -Status 'BLOCKED_UPDATE_ACTIVATION' -ExitCode 34 -Message 'The verified commit could not be activated with a fast-forward update.'
    }

    $updatedHead = Invoke-LocalGit -Arguments @('rev-parse', 'HEAD')
    if ($updatedHead.ExitCode -ne 0 -or $updatedHead.Stdout.ToLowerInvariant() -ne $script:RemoteSha) {
        return New-GateResult -Status 'BLOCKED_UPDATE_ACTIVATION' -ExitCode 34 -Message 'The active commit does not match the verified GitHub commit after update.'
    }
    $script:ActiveSha = $updatedHead.Stdout.ToLowerInvariant()

    try {
        Read-And-ValidateConfig
        Test-CoreFiles
    } catch {
        return New-GateResult -Status 'BLOCKED_UPDATE_VALIDATION' -ExitCode 35 -Message $_.Exception.Message -MustReload $true
    }

    return New-GateResult -Status 'UPDATED_READY_RELOAD' -ExitCode 0 -Message 'News-Editor was fast-forwarded to the verified GitHub main commit. Reload the Skill before any task action.' -MustReload $true
}

$mutex = $null
$lockAcquired = $false
$result = $null
try {
    $hashBytes = [System.Security.Cryptography.SHA256]::HashData([System.Text.Encoding]::UTF8.GetBytes($script:SkillRoot.ToLowerInvariant()))
    $lockId = [Convert]::ToHexString($hashBytes).Substring(0, 16)
    $mutex = [System.Threading.Mutex]::new($false, "NewsEditorSkillVersionGate-$lockId")
    try {
        $lockAcquired = $mutex.WaitOne($script:LockTimeoutSeconds * 1000)
    } catch [System.Threading.AbandonedMutexException] {
        $lockAcquired = $true
    }
    if (-not $lockAcquired) {
        $result = New-GateResult -Status 'BLOCKED_UPDATE_LOCK_TIMEOUT' -ExitCode 40 -Message 'Another News-Editor update did not release the lock in time.'
    } else {
        $result = Invoke-VersionGate
    }
} catch {
    $result = New-GateResult -Status 'BLOCKED_VERSION_GATE_ERROR' -ExitCode 99 -Message $_.Exception.Message
} finally {
    if ($lockAcquired -and $null -ne $mutex) {
        try { $mutex.ReleaseMutex() } catch { }
    }
    if ($null -ne $mutex) {
        $mutex.Dispose()
    }
}

$result | ConvertTo-Json -Depth 5 -Compress
exit ([int]$result.exit_code)
