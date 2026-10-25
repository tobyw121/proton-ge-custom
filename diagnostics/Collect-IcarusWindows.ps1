#Requires -Version 5.1
<# Windows-only diagnostic capture. Does not modify Icarus or GameGuard. #>
[CmdletBinding()]
param(
    [string]$VfunPath,
    [string]$OutputDirectory = "$env:USERPROFILE\Desktop\Icarus-Diagnose",
    [ValidateRange(1, 60)][int]$MaximumMinutes = 10,
    [ValidateRange(15, 600)][int]$StableSeconds = 60,
    [switch]$ManualStop
)
$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Dieses Skript benoetigt Windows.' }

# Elevate this script only; do not change the system execution policy.
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    function Literal([string]$s) { "'" + $s.Replace("'", "''") + "'" }
    $command = '& ' + (Literal $PSCommandPath) + ' -OutputDirectory ' + (Literal $OutputDirectory) +
        ' -MaximumMinutes ' + $MaximumMinutes + ' -StableSeconds ' + $StableSeconds
    if ($VfunPath) { $command += ' -VfunPath ' + (Literal $VfunPath) }
    if ($ManualStop) { $command += ' -ManualStop' }
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    $elevated = Start-Process "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" `
        -Verb RunAs -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $encoded) -Wait -PassThru
    exit $elevated.ExitCode
}
Add-Type -AssemblyName System.Windows.Forms
if (Get-Process -Name Procmon, Procmon64, Procmon64a -ErrorAction SilentlyContinue) {
    throw 'Process Monitor laeuft bereits. Bitte zuerst selbst beenden, dann erneut starten.'
}
if (Get-Process -Name VFUNLauncher, VLauncher, IcarusLauncher, Launcher, Launcher32, GameMon, GameMon64 -ErrorAction SilentlyContinue) {
    throw 'Bitte vorhandene VFUN-/Icarus-/GameGuard-Prozesse zuerst selbst schliessen.'
}
if (-not $VfunPath) {
    foreach ($candidate in @('C:\VFUN\VLauncher\VFUNLauncher.exe',
            "${env:ProgramFiles(x86)}\VFUN\VLauncher\VFUNLauncher.exe",
            "$env:ProgramFiles\VFUN\VLauncher\VFUNLauncher.exe")) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { $VfunPath = $candidate; break }
    }
}
if (-not $VfunPath) {
    $dialog = New-Object Windows.Forms.OpenFileDialog
    $dialog.Title = 'Installierten VFUNLauncher.exe auswaehlen (kein Installer)'
    $dialog.Filter = 'Windows-Programme (*.exe)|*.exe'
    if ($dialog.ShowDialog() -ne 'OK') { exit 0 }
    $VfunPath = $dialog.FileName
    $dialog.Dispose()
}
$VfunPath = (Resolve-Path -LiteralPath $VfunPath).Path
if ([IO.Path]::GetExtension($VfunPath) -ne '.exe') { throw 'Bitte eine EXE auswaehlen.' }
$run = Join-Path $OutputDirectory ((Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 6))
$null = New-Item -ItemType Directory -Path $run -Force
$tools = Join-Path $run 'tools'
$null = New-Item -ItemType Directory -Path $tools
Start-Transcript -Path (Join-Path $run 'skript.log') | Out-Null
$procmon = $null
$captureStarted = $false
$captureStopped = $false
$form = $null
$reason = 'Fehler vor Aufnahme'
$failure = $null
$start = Get-Date

function Run-Procmon([string]$Arguments, [int]$TimeoutSeconds = 60) {
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $procmon
    $info.Arguments = $Arguments
    $info.UseShellExecute = $false
    $p = New-Object Diagnostics.Process
    $p.StartInfo = $info
    $null = $p.Start()
    if (-not $p.WaitForExit($TimeoutSeconds * 1000)) {
        throw "Process-Monitor-Aufruf nicht rechtzeitig beendet: $Arguments"
    }
    if ($p.ExitCode -ne 0) { throw "Process Monitor meldet Exitcode $($p.ExitCode): $Arguments" }
}
function Snapshot([string]$Stage) {
    Get-CimInstance Win32_SystemDriver | Select-Object Name, DisplayName, State, StartMode, PathName |
        Export-Csv (Join-Path $run "treiber-$Stage.csv") -NoTypeInformation -Encoding UTF8
    Get-CimInstance Win32_Service | Select-Object Name, State, StartMode, PathName |
        Export-Csv (Join-Path $run "dienste-$Stage.csv") -NoTypeInformation -Encoding UTF8
    Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId, Name, ExecutablePath |
        Export-Csv (Join-Path $run "prozesse-$Stage.csv") -NoTypeInformation -Encoding UTF8
}
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    Write-Host 'Lade Process Monitor direkt von Microsoft ...'
    $zip = Join-Path $tools 'ProcessMonitor.zip'
    Invoke-WebRequest 'https://download.sysinternals.com/files/ProcessMonitor.zip' -OutFile $zip -UseBasicParsing
    Expand-Archive -LiteralPath $zip -DestinationPath $tools
    $architecture = $env:PROCESSOR_ARCHITEW6432
    if (-not $architecture) { $architecture = $env:PROCESSOR_ARCHITECTURE }
    $exe = switch ($architecture) { 'ARM64' { 'Procmon64a.exe' }; 'AMD64' { 'Procmon64.exe' }; default { 'Procmon.exe' } }
    $procmon = Join-Path $tools $exe
    $signature = Get-AuthenticodeSignature -LiteralPath $procmon
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation(?:,|$)') {
        throw 'Die Microsoft-Signatur von Process Monitor konnte nicht bestaetigt werden.'
    }
    Write-Host 'Process Monitor verwendet /AcceptEula: Die Sysinternals-Lizenz wird fuer diesen Benutzer akzeptiert.'
    Snapshot 'vorher'
    $start = Get-Date
    $pml = Join-Path $run 'aufnahme.pml'
    $null = Start-Process $procmon -ArgumentList "/AcceptEula /Quiet /Minimized /NoFilter /BackingFile `"$pml`"" -PassThru
    $captureStarted = $true
    Run-Procmon '/WaitForIdle'
    if (-not (Get-Process -Name Procmon, Procmon64, Procmon64a -ErrorAction SilentlyContinue)) {
        throw 'Process Monitor hat die Aufnahme nicht gestartet.'
    }

    $form = New-Object Windows.Forms.Form
    $form.Text = 'Icarus-Diagnose: Aufnahme laeuft'
    $form.Width = 680; $form.Height = 250
    $form.StartPosition = 'CenterScreen'
    $label = New-Object Windows.Forms.Label
    $label.SetBounds(15, 15, 635, 70)
    $label.Text = 'In VFUN anmelden und Icarus starten. Bei Fehler 114 auf Fehler klicken. Automatik: sichtbares Launcher-Fenster mit geladener CryGame.dll fuer die angegebene Dauer; das beweist noch keine sichtbare Spielwelt.'
    $form.Controls.Add($label)
    $check = New-Object Windows.Forms.CheckBox
    $check.SetBounds(15, 90, 630, 25)
    $check.Text = "Automatisch stoppen nach $StableSeconds Sekunden mit erkanntem Spielfenster"
    $check.Checked = -not $ManualStop
    $form.Controls.Add($check)
    $script:StopReason = $null
    $ok = New-Object Windows.Forms.Button
    $ok.SetBounds(15, 130, 285, 40); $ok.Text = 'Spielwelt sichtbar - Aufnahme beenden'
    $ok.Add_Click({ $script:StopReason = 'Spielwelt vom Benutzer bestaetigt' })
    $form.Controls.Add($ok)
    $errorButton = New-Object Windows.Forms.Button
    $errorButton.SetBounds(320, 130, 285, 40); $errorButton.Text = 'Fehler sichtbar - Aufnahme beenden'
    $errorButton.Add_Click({ $script:StopReason = 'Fehler vom Benutzer gemeldet' })
    $form.Controls.Add($errorButton)
    $form.Add_FormClosing({ $script:StopReason = 'Diagnosefenster geschlossen' })
    $form.Show()
    # Use the installed launcher's working directory, preserving relative paths.
    $launcher = Start-Process -FilePath $VfunPath -WorkingDirectory (Split-Path $VfunPath) -PassThru
    Write-Host "VFUN gestartet, PID $($launcher.Id). Aufnahmeordner: $run"
    $seen = @{}
    $nextCheck = Get-Date
    while (-not $script:StopReason) {
        [Windows.Forms.Application]::DoEvents()
        if (((Get-Date) - $start).TotalMinutes -ge $MaximumMinutes) {
            $script:StopReason = 'Zeitlimit erreicht'; break
        }
        if ((Get-Date) -ge $nextCheck) {
            $nextCheck = (Get-Date).AddSeconds(2)
            $current = @{}
            foreach ($p in @(Get-Process -Name Launcher, Launcher32 -ErrorAction SilentlyContinue)) {
                try {
                    $p.Refresh()
                    $loaded = @($p.Modules | Where-Object { $_.ModuleName -ieq 'CryGame.dll' }).Count -gt 0
                    if ($p.MainWindowHandle -ne [IntPtr]::Zero -and $loaded) {
                        $key = "$($p.Id)-$($p.StartTime.Ticks)"
                        $current[$key] = $true
                        if (-not $seen.ContainsKey($key)) { $seen[$key] = Get-Date }
                        if ($check.Checked -and ((Get-Date) - $seen[$key]).TotalSeconds -ge $StableSeconds) {
                            $script:StopReason = 'Automatik: CryGame.dll und Spielfenster stabil (keine Spielwelt-Bestaetigung)'
                        }
                    }
                } catch { } # Protected processes can deny module queries; manual buttons remain available.
            }
            foreach ($key in @($seen.Keys)) { if (-not $current.ContainsKey($key)) { $seen.Remove($key) } }
        }
        Start-Sleep -Milliseconds 200
    }
    $reason = $script:StopReason
} catch {
    $failure = $_.Exception.Message
    $reason = 'Skriptfehler: ' + $failure
    Write-Warning $reason
} finally {
    if ($captureStarted) {
        try { Run-Procmon '/Terminate' 90; $captureStopped = $true } catch { Write-Warning "Aufnahmeende: $_" }
    }
    if ($form) { $form.Dispose() }
    try { Snapshot 'nachher' } catch { Write-Warning "Nachher-Sicherung: $_" }
    try {
        $os = Get-CimInstance Win32_OperatingSystem
        [ordered]@{ Start = $start.ToString('o'); Ende = (Get-Date).ToString('o'); Grund = $reason;
            VFUN = $VfunPath; Administrator = $admin; Windows = $os.Caption; Build = $os.BuildNumber;
            AutomatikSekunden = $StableSeconds; Fehler = $failure } |
            ConvertTo-Json | Set-Content (Join-Path $run 'lauf.json') -Encoding UTF8
        $before = @(Import-Csv (Join-Path $run 'treiber-vorher.csv'))
        $after = @(Import-Csv (Join-Path $run 'treiber-nachher.csv'))
        if ($before.Count -and $after.Count) {
            Compare-Object $before $after -Property Name, State, StartMode, PathName |
                Export-Csv (Join-Path $run 'treiber-unterschiede.csv') -NoTypeInformation -Encoding UTF8
        }
        foreach ($log in @('System', 'Application')) {
            $events = @(Get-WinEvent -FilterHashtable @{ LogName = $log; StartTime = $start; EndTime = (Get-Date) } -ErrorAction SilentlyContinue)
            if ($events.Count) {
                $events | Select-Object TimeCreated, ProviderName, Id, LevelDisplayName, Message |
                    Export-Csv (Join-Path $run "ereignisse-$log.csv") -NoTypeInformation -Encoding UTF8
            }
        }
    } catch { Write-Warning "Metadaten: $_" }
    Stop-Transcript | Out-Null
}
# Export only after capture has stopped. No game processes are killed.
if ($captureStopped -and (Test-Path -LiteralPath (Join-Path $run 'aufnahme.pml'))) {
    try {
        $csv = Join-Path $run 'aufnahme.csv'
        Run-Procmon "/AcceptEula /Quiet /NoFilter /OpenLog `"$(Join-Path $run 'aufnahme.pml')`" /SaveAs `"$csv`"" 600
        $inputStream = [IO.File]::OpenRead($csv)
        try {
            $outputStream = [IO.File]::Create($csv + '.gz')
            try {
                $gzip = New-Object IO.Compression.GZipStream($outputStream, [IO.Compression.CompressionMode]::Compress)
                try { $inputStream.CopyTo($gzip) } finally { $gzip.Dispose() }
            } finally { $outputStream.Dispose() }
        } finally { $inputStream.Dispose() }
        Remove-Item -LiteralPath $csv
    } catch { Write-Warning "CSV-Export fehlgeschlagen; PML behalten: $_" }
}
Write-Host "Fertig: $reason`nErgebnisse: $run"
Write-Host 'Zum Teilen: aufnahme.csv.gz, lauf.json und die CSV-Dateien. PML fuer lokale Analyse behalten.'
Write-Host 'Procmon zeichnet auch andere Programme und moeglicherweise Anmeldetokens auf. Vor dem Teilen pruefen.'
$null = Read-Host 'Enter zum Schliessen'
if ($failure) { exit 1 }
