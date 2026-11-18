# AutoApply Local installer for Windows PowerShell 5.1+ and PowerShell 7.
#   irm https://raw.githubusercontent.com/tlamDson/autoapply-local/main/install.ps1 | iex
#   or, inside a checkout: ./install.ps1 [--no-setup]
# It never installs system software: a missing dependency prints its install command.
# Under `irm | iex` this runs in the caller's session, so it sets $LASTEXITCODE instead of exiting.

$AutoApplyRepoUrl = 'https://github.com/tlamDson/autoapply-local.git'

function Test-AutoApplyCheckout([string]$Path) {
    if (-not $Path) { return $false }
    return (Test-Path -LiteralPath (Join-Path $Path 'autoapply_local/__init__.py')) -and
        (Test-Path -LiteralPath (Join-Path $Path 'pyproject.toml')) -and
        (Test-Path -LiteralPath (Join-Path $Path 'scripts/install.py'))
}

function Find-AutoApplyPython {
    foreach ($candidate in @('py -3', 'python', 'python3')) {
        $parts = $candidate -split ' '
        $rest = @($parts | Select-Object -Skip 1)
        if (-not (Get-Command $parts[0] -ErrorAction SilentlyContinue)) { continue }
        try {
            & $parts[0] @rest -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>$null | Out-Null
        } catch { continue }
        if ($LASTEXITCODE -eq 0) { return $candidate }
    }
    return $null
}

function Resolve-AutoApplyRepo {
    # Returns the checkout path, or an exit code when it cannot continue.
    $here = $PSScriptRoot
    $cwd = (Get-Location).Path
    $clone = Join-Path $cwd 'autoapply-local'
    if (Test-AutoApplyCheckout $here) { return $here }
    if (Test-AutoApplyCheckout $cwd) { return $cwd }
    if (Test-AutoApplyCheckout $clone) { return $clone }  # rerun of the one-line install
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Write-Host 'Missing dependency (nothing was installed): git'
        Write-Host '  winget install -e --id Git.Git'
        Write-Host 'Then open a new terminal and rerun the installer.'
        return 3
    }
    if (Test-Path -LiteralPath $clone) {
        Write-Host "$clone exists but is not an AutoApply Local checkout; move it and rerun."
        return 2
    }
    & git clone $AutoApplyRepoUrl $clone | Out-Host
    if ($LASTEXITCODE -ne 0) { return 4 }
    return $clone
}

# Top level, not inside a function, so the Python stage keeps the console for interactive setup.
$code = 0
$repo = Resolve-AutoApplyRepo
if ($repo -is [int]) {
    $code = $repo
} else {
    $python = Find-AutoApplyPython
    if (-not $python) {
        Write-Host 'Missing dependency (nothing was installed): Python 3.11+'
        Write-Host '  winget install -e --id Python.Python.3.12'
        Write-Host 'Then open a new terminal and rerun the installer.'
        $code = 3
    } else {
        $parts = $python -split ' '
        $rest = @($parts | Select-Object -Skip 1)
        & $parts[0] @rest (Join-Path $repo 'scripts/install.py') @args
        $code = $LASTEXITCODE
    }
}
if ($PSCommandPath) { exit $code }
$global:LASTEXITCODE = $code
