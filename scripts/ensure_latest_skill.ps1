[CmdletBinding()]
param(
    [string]$RunId = ([guid]::NewGuid().ToString()),
    [int]$TimeoutSeconds = 0,
    [string]$ManifestOut = '',
    [string]$ParentRunManifest = '',
    [string]$ParentRunManifestSha256 = '',
    [string]$ChildId = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:Schema = 'news-editor-version-gate/v1'
$script:ManifestSchema = 'news-editor-orchestrated-run/v1'
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
$script:ManifestMaxAgeHours = 12
$script:GateMode = if ($ParentRunManifest) { 'orchestrated_child' } else { 'orchestrator' }
$script:ManifestPath = $null
$script:ManifestSha256 = $null
$script:ParentRunId = $null

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
        mode           = $script:GateMode
        child_id       = if ($ChildId) { $ChildId } else { $null }
        parent_run_id  = $script:ParentRunId
        manifest_path  = $script:ManifestPath
        manifest_sha256 = $script:ManifestSha256
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

    if ($null -eq $config.orchestration -or -not [bool]$config.orchestration.enabled) {
        throw 'config.json orchestration mode is missing or disabled.'
    }
    if ([string]$config.orchestration.remote_gate_owner -ne 'orchestrator') {
        throw 'orchestration.remote_gate_owner must be orchestrator.'
    }
    if ([string]$config.orchestration.child_gate_mode -ne 'pinned_local_sha') {
        throw 'orchestration.child_gate_mode must be pinned_local_sha.'
    }
    if ([string]$config.orchestration.run_manifest_schema -ne $script:ManifestSchema) {
        throw 'orchestration.run_manifest_schema does not match the gate implementation.'
    }
    $script:ManifestMaxAgeHours = [int]$config.orchestration.run_manifest_max_age_hours
    if ($script:ManifestMaxAgeHours -lt 1 -or $script:ManifestMaxAgeHours -gt 24) {
        throw 'orchestration.run_manifest_max_age_hours must be between 1 and 24.'
    }
}

function Get-FileSha256 {
    param([Parameter(Mandatory)][string]$Path)

    (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
}

function Get-CoreContractHashes {
    $relativePaths = @(
        'SKILL.md',
        'VERSION',
        'config.json',
        'references/subagent-orchestration.md',
        'scripts/ensure_latest_skill.ps1',
        'scripts/orchestration_contract.py'
    )
    $hashes = [ordered]@{}
    foreach ($relativePath in $relativePaths) {
        $path = Join-Path $script:SkillRoot $relativePath
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Required orchestration contract file is missing: $relativePath"
        }
        $hashes[$relativePath] = Get-FileSha256 -Path $path
    }
    $hashes
}

function Test-PathInsideSkillRoot {
    param([Parameter(Mandatory)][string]$Path)

    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $rootWithSeparator = $script:SkillRoot.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
    $fullPath.StartsWith($rootWithSeparator, [System.StringComparison]::OrdinalIgnoreCase) -or
        $fullPath.Equals($script:SkillRoot, [System.StringComparison]::OrdinalIgnoreCase)
}

function Write-OrchestrationManifest {
    param([Parameter(Mandatory)][string]$OutputPath)

    if (Test-PathInsideSkillRoot -Path $OutputPath) {
        throw 'The orchestration run manifest must be written outside the Skill repository.'
    }
    $fullPath = [System.IO.Path]::GetFullPath($OutputPath)
    if (Test-Path -LiteralPath $fullPath) {
        throw 'The orchestration run manifest path already exists; use a new run_id and path.'
    }
    $parent = Split-Path -Parent $fullPath
    if (-not $parent) {
        throw 'The orchestration run manifest path must include a parent directory.'
    }
    [void](New-Item -ItemType Directory -Path $parent -Force)

    $checkedAt = [DateTime]::UtcNow
    $manifest = [ordered]@{
        schema          = $script:ManifestSchema
        run_id          = $RunId
        gate_status     = 'LATEST_READY'
        checked_at_utc  = $checkedAt.ToString('o')
        expires_at_utc  = $checkedAt.AddHours($script:ManifestMaxAgeHours).ToString('o')
        repository      = $script:Repository
        branch          = $script:Branch
        active_sha      = $script:ActiveSha
        remote_sha      = $script:RemoteSha
        version         = $script:Version
        origin_skill_root = $script:SkillRoot
        contract_files  = Get-CoreContractHashes
    }
    $json = $manifest | ConvertTo-Json -Depth 8
    $tempPath = "$fullPath.$([guid]::NewGuid().ToString('N')).tmp"
    try {
        [System.IO.File]::WriteAllText($tempPath, $json + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
        Move-Item -LiteralPath $tempPath -Destination $fullPath
    } finally {
        if (Test-Path -LiteralPath $tempPath) {
            Remove-Item -LiteralPath $tempPath -Force
        }
    }
    $script:ManifestPath = $fullPath
    $script:ManifestSha256 = Get-FileSha256 -Path $fullPath
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
        'scripts/orchestration_contract.py',
        'scripts/check_skill_consistency.py',
        'references/subagent-orchestration.md',
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

function Invoke-OrchestratedChildGate {
    try {
        $gitCommand = Get-Command git -ErrorAction Stop
        $script:GitPath = $gitCommand.Source
    } catch {
        return New-GateResult -Status 'BLOCKED_GIT_MISSING' -ExitCode 10 -Message 'Git is required before an orchestrated child can attest the pinned News-Editor commit.'
    }

    if ($ManifestOut) {
        return New-GateResult -Status 'BLOCKED_CHILD_CONTEXT' -ExitCode 41 -Message 'ManifestOut cannot be combined with ParentRunManifest.'
    }
    if (-not $ChildId -or $ChildId -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$') {
        return New-GateResult -Status 'BLOCKED_CHILD_CONTEXT' -ExitCode 41 -Message 'ChildId is required and must be a short stable identifier.'
    }
    if ($ParentRunManifestSha256 -notmatch '^[0-9a-fA-F]{64}$') {
        return New-GateResult -Status 'BLOCKED_CHILD_CONTEXT' -ExitCode 41 -Message 'ParentRunManifestSha256 must be a 64-character SHA-256 value.'
    }

    $inside = Invoke-LocalGit -Arguments @('rev-parse', '--is-inside-work-tree')
    if ($inside.ExitCode -ne 0 -or $inside.Stdout -ne 'true') {
        return New-GateResult -Status 'BLOCKED_NOT_GIT' -ExitCode 11 -Message 'The active News-Editor installation is not a Git worktree.'
    }
    $dirty = Invoke-LocalGit -Arguments @('status', '--porcelain=v1', '--untracked-files=all')
    if ($dirty.ExitCode -ne 0 -or $dirty.Stdout) {
        return New-GateResult -Status 'BLOCKED_LOCAL_CHANGES' -ExitCode 20 -Message 'An orchestrated child requires a clean News-Editor worktree.'
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
        $manifestPath = (Resolve-Path -LiteralPath $ParentRunManifest -ErrorAction Stop).Path
        if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
            throw 'Parent run manifest is not a file.'
        }
        $actualManifestSha = Get-FileSha256 -Path $manifestPath
        if ($actualManifestSha -ne $ParentRunManifestSha256.ToLowerInvariant()) {
            throw 'Parent run manifest SHA-256 does not match the value supplied by the orchestrator.'
        }
        $manifest = Get-Content -Raw -LiteralPath $manifestPath -Encoding UTF8 | ConvertFrom-Json
        if ([string]$manifest.schema -ne $script:ManifestSchema) {
            throw 'Parent run manifest schema is invalid.'
        }
        if ([string]$manifest.run_id -ne $RunId) {
            throw 'Child RunId does not match the parent run manifest.'
        }
        if ([string]$manifest.gate_status -ne 'LATEST_READY') {
            throw 'Parent run manifest was not issued by a successful remote version gate.'
        }
        if ((Normalize-GitUrl ([string]$manifest.repository)) -ne (Normalize-GitUrl $script:TrustedRepository) -or
            [string]$manifest.branch -ne $script:TrustedBranch) {
            throw 'Parent run manifest repository or branch is not trusted.'
        }
        $manifestSha = ([string]$manifest.active_sha).ToLowerInvariant()
        if ($manifestSha -notmatch '^[0-9a-f]{40}$' -or $manifestSha -ne ([string]$manifest.remote_sha).ToLowerInvariant()) {
            throw 'Parent run manifest does not pin one verified GitHub commit.'
        }
        if ($manifestSha -ne $script:LocalSha) {
            throw 'The child installation does not match the commit pinned by the orchestrator.'
        }
        $checkedAt = [DateTime]::Parse([string]$manifest.checked_at_utc).ToUniversalTime()
        $expiresAt = [DateTime]::Parse([string]$manifest.expires_at_utc).ToUniversalTime()
        $now = [DateTime]::UtcNow
        if ($checkedAt -gt $now.AddMinutes(5) -or $expiresAt -le $now -or
            ($now - $checkedAt).TotalHours -gt $script:ManifestMaxAgeHours -or
            $expiresAt -gt $checkedAt.AddHours($script:ManifestMaxAgeHours).AddMinutes(1)) {
            throw 'Parent run manifest is expired or has an invalid validity window.'
        }
        $expectedHashes = Get-CoreContractHashes
        foreach ($relativePath in $expectedHashes.Keys) {
            $recorded = [string]$manifest.contract_files.$relativePath
            if (-not $recorded -or $recorded.ToLowerInvariant() -ne $expectedHashes[$relativePath]) {
                throw "Parent run manifest contract hash mismatch: $relativePath"
            }
        }
        $script:ManifestPath = $manifestPath
        $script:ManifestSha256 = $actualManifestSha
        $script:ParentRunId = [string]$manifest.run_id
        $script:RemoteSha = $manifestSha
    } catch {
        return New-GateResult -Status 'BLOCKED_CHILD_CONTEXT' -ExitCode 41 -Message $_.Exception.Message
    }

    New-GateResult -Status 'CHILD_CONTEXT_READY' -ExitCode 0 -Message 'The child locally attested the clean installation, pinned parent commit, manifest hash, validity window, and core contract hashes; no network request was made.'
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
        if ($script:GateMode -eq 'orchestrated_child') {
            $result = Invoke-OrchestratedChildGate
        } else {
            if ($ParentRunManifestSha256 -or $ChildId) {
                $result = New-GateResult -Status 'BLOCKED_CHILD_CONTEXT' -ExitCode 41 -Message 'ParentRunManifestSha256 and ChildId require ParentRunManifest.'
            } else {
                $result = Invoke-VersionGate
                if ($result.status -eq 'LATEST_READY' -and $ManifestOut) {
                    try {
                        Write-OrchestrationManifest -OutputPath $ManifestOut
                        $result = New-GateResult -Status 'LATEST_READY' -ExitCode 0 -Message 'The active News-Editor commit exactly matches GitHub main; an orchestration run manifest was created.'
                    } catch {
                        $result = New-GateResult -Status 'BLOCKED_MANIFEST_WRITE' -ExitCode 42 -Message $_.Exception.Message
                    }
                }
            }
        }
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
