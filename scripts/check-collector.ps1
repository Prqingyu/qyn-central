# check-collector.ps1 - pre-deploy checks for the Windows collector scripts.
#
# Both checks exist because both caught a REAL bug (2026-10-13):
#
#   1. ASCII ONLY. Windows PowerShell 5.1 reads .ps1 files as ANSI (GBK on these
#      machines), so a UTF-8 file containing Chinese gets mis-decoded. The bad
#      byte pairing can swallow newlines - the file looks perfectly fine in an
#      editor, but on the target machine it dies with a parse error. This happened
#      twice today, the second time taking both collectors down into a
#      download-fail-retry loop.
#
#   2. Parse with the WINDOWS POWERSHELL parser, not just pwsh: syntax that is
#      fine in PowerShell 7 can be rejected by 5.1.
#
# NOTE: this file itself must stay ASCII-only for the same reason.
#
# Usage (run before every deploy):
#   powershell -ExecutionPolicy Bypass -File CENTRAL/scripts/check-collector.ps1

$dir = Join-Path $PSScriptRoot "..\GUI\status-wall\collector"
$files = Get-ChildItem -LiteralPath $dir -Filter *.ps1 -File
$problems = 0

foreach ($f in $files) {
    # 1) byte-level ASCII check (encoding independent)
    $bytes = [System.IO.File]::ReadAllBytes($f.FullName)
    $nonAscii = 0
    foreach ($b in $bytes) { if ($b -gt 127) { $nonAscii++ } }
    if ($nonAscii -gt 0) {
        Write-Host ("NON-ASCII BYTES  " + $f.Name + "  (" + $nonAscii + " bytes >127 - PowerShell 5.1 will mis-read this)")
        $problems++
    }

    # 2) parse with the Windows PowerShell parser
    $errs = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile($f.FullName, [ref]$null, [ref]$errs)
    if ($errs -and $errs.Count -gt 0) {
        foreach ($e in $errs) {
            Write-Host ("PARSE ERROR      " + $f.Name + " line " + $e.Extent.StartLineNumber + ": " + $e.Message)
        }
        $problems++
    } else {
        Write-Host ("OK               " + $f.Name)
    }
}

if ($problems -gt 0) {
    Write-Host ("FAILED: " + $problems + " problem(s) - do NOT deploy")
    exit 1
}
Write-Host "all collector scripts OK"
exit 0
