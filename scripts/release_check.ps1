param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$expectedVersion = '0.1.0'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$safeRepo = $repo.Replace('\', '/')
$results = [System.Collections.Generic.List[object]]::new()

function Add-Result {
    param([string]$Name, [bool]$Passed, [string]$Detail)
    $results.Add([pscustomobject]@{ Name = $Name; Passed = $Passed; Detail = $Detail })
    $label = if ($Passed) { 'PASS' } else { 'FAIL' }
    Write-Output "[$label] $Name - $Detail"
}

Push-Location $repo
try {
    # Include untracked files: a new release script should fail the clean-tree gate
    # until it is committed. Ignored build artifacts are intentionally excluded.
    $status = @(& git -c "safe.directory=$safeRepo" status --porcelain=v1 --untracked-files=all 2>&1)
    $gitAvailable = $LASTEXITCODE -eq 0
    Add-Result 'Git working tree' ($gitAvailable -and $status.Count -eq 0) `
        $(if (-not $gitAvailable) { 'Git status unavailable' } elseif ($status.Count) { "$($status.Count) changed/untracked path(s)" } else { 'clean' })

    $projectFile = Join-Path $repo 'pyproject.toml'
    $projectText = Get-Content -LiteralPath $projectFile -Raw
    $projectSection = [regex]::Match($projectText, '(?ms)^\[project\]\s*(.*?)(?=^\[|\z)')
    $versionMatch = [regex]::Match($projectSection.Groups[1].Value, '(?m)^version\s*=\s*"([^"]+)"')
    $version = if ($versionMatch.Success) { $versionMatch.Groups[1].Value } else { '<missing>' }
    Add-Result 'Project version' ($version -eq $expectedVersion) "found $version; expected $expectedVersion"

    $tag = @(& git -c "safe.directory=$safeRepo" rev-parse --verify --quiet "refs/tags/v$expectedVersion" 2>&1)
    Add-Result 'Git tag' ($LASTEXITCODE -eq 0) "v$expectedVersion"

    $python = 'python'
    $localPython = Join-Path $repo 'work/.venv/Scripts/python.exe'
    if (Test-Path -LiteralPath $localPython) { $python = $localPython }
    $originalPythonPath = $env:PYTHONPATH
    try {
        $env:PYTHONPATH = Join-Path $repo 'src'
        $testOutput = @(& $python -m pytest -q -p no:cacheprovider 2>&1)
        $testsPassed = $LASTEXITCODE -eq 0
    } finally {
        $env:PYTHONPATH = $originalPythonPath
    }
    $testSummary = ($testOutput | Where-Object { $_ -match '\d+ passed|\d+ failed|ERROR|No module named pytest' } | Select-Object -Last 1)
    if (-not $testSummary) { $testSummary = 'pytest produced no summary' }
    Add-Result 'pytest' $testsPassed ([string]$testSummary).Trim()

    $dist = Join-Path $repo 'dist'
    $wheelFiles = @()
    if (Test-Path -LiteralPath $dist) {
        $wheelFiles = @(Get-ChildItem -LiteralPath $dist -Filter "memory_evolution_engine-$expectedVersion-*.whl" -File)
    }
    Add-Result 'dist wheel' ($wheelFiles.Count -gt 0) `
        $(if ($wheelFiles.Count) { "$($wheelFiles.Count) matching wheel(s)" } else { 'missing matching wheel in dist/' })

    $metadataValid = $wheelFiles.Count -gt 0
    $metadataDetail = 'no wheel to inspect'
    if ($wheelFiles.Count -gt 0) {
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        foreach ($wheel in $wheelFiles) {
            $archive = [System.IO.Compression.ZipFile]::OpenRead($wheel.FullName)
            try {
                $entry = $archive.Entries | Where-Object { $_.FullName -match '\.dist-info/METADATA$' } | Select-Object -First 1
                if ($null -eq $entry) { $metadataValid = $false; $metadataDetail = "$($wheel.Name): METADATA missing"; continue }
                $reader = [System.IO.StreamReader]::new($entry.Open())
                try { $metadata = $reader.ReadToEnd() } finally { $reader.Dispose() }
                $nameOk = $metadata -match '(?m)^Name:\s*memory-evolution-engine\s*$'
                $versionOk = $metadata -match "(?m)^Version:\s*$([regex]::Escape($expectedVersion))\s*`$"
                if (-not ($nameOk -and $versionOk)) {
                    $metadataValid = $false
                    $metadataDetail = "$($wheel.Name): name/version mismatch"
                } else { $metadataDetail = "$($wheel.Name): name and version match" }
            } finally { $archive.Dispose() }
        }
    }
    Add-Result 'Wheel metadata' $metadataValid $metadataDetail

    # Examine tracked and non-ignored untracked files. Report paths/categories only;
    # never print a suspected secret value.
    $files = @(& git -c "safe.directory=$safeRepo" ls-files --cached --others --exclude-standard)
    $findings = [System.Collections.Generic.List[string]]::new()
    $secretPattern = '(?i)(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)'
    $assignmentPattern = '(?im)^\s*(?:[A-Z_]*(?:TOKEN|SECRET|API_KEY|PASSWORD)[A-Z_]*)\s*[:=]\s*["'']?[^\s"''#]{8,}'
    $localPathPattern = '(?i)(?:[A-Z]:[\\/]Users[\\/]|/Users/|/home/)'
    foreach ($relative in $files) {
        $normalized = $relative.Replace('\', '/')
        if ($normalized -match '(^|/)\.env(?:$|\.)' -or
            $normalized -match '(?i)(?:^|/)(?:id_rsa|id_ed25519|credentials\.json|service.account\.json)$' -or
            $normalized -match '(?i)\.(?:pem|key|p12|pfx)$') {
            $findings.Add("sensitive filename: $normalized")
        }
        $fullPath = Join-Path $repo $relative
        if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf)) { continue }
        if ((Get-Item -LiteralPath $fullPath).Length -gt 2MB) { continue }
        $content = Get-Content -LiteralPath $fullPath -Raw -ErrorAction SilentlyContinue
        if ($null -eq $content) { continue }
        if ($content -match $secretPattern -or $content -match $assignmentPattern) {
            $findings.Add("possible credential: $normalized")
        }
        if ($normalized -match '(?i)\.(?:toml|json|ya?ml|ini|cfg|env)$' -and $content -match $localPathPattern) {
            $findings.Add("local path configuration: $normalized")
        }
    }
    Add-Result 'Repository secrets and local paths' ($findings.Count -eq 0) `
        $(if ($findings.Count) { ($findings | Sort-Object -Unique) -join '; ' } else { 'no matches in tracked or non-ignored files' })
} finally {
    Pop-Location
}

if (@($results | Where-Object { -not $_.Passed }).Count -gt 0) { exit 1 }
exit 0
