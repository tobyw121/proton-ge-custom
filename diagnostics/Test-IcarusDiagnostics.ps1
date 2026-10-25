# Parser and isolated helpers only: no UAC, Procmon download, or game launch.
$ErrorActionPreference = 'Stop'
$tokens = $null
$problems = $null
$source = Join-Path $PSScriptRoot 'Collect-IcarusWindows.ps1'
$ast = [Management.Automation.Language.Parser]::ParseFile($source, [ref]$tokens, [ref]$problems)
if ($problems.Count) { throw ($problems | Out-String) }
foreach ($name in @('Literal', 'Run-Procmon')) {
    $function = $ast.FindAll({ param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name
    }, $true)[0]
    Invoke-Expression $function.Extent.Text
}
$sample = "C:\Users\O" + [char]39 + 'Brien\test script.ps1'
if ((Invoke-Expression (Literal $sample)) -cne $sample) { throw 'Path quoting failed.' }
if ($env:OS -eq 'Windows_NT') {
    $procmon = "$env:SystemRoot\System32\cmd.exe"
    Run-Procmon '/c exit 0' 10
    $rejected = $false
    try { Run-Procmon '/c exit 7' 10 } catch { $rejected = $_.Exception.Message -match 'Exitcode 7' }
    if (-not $rejected) { throw 'Non-zero child exit status was not handled correctly.' }
}
'Parser, elevation quoting and applicable process-helper checks passed.'
