#Requires -Version 5.1
<#
  Optional Windows app shell for Cleverly.
  This does not require admin rights by itself; Docker Desktop access is still
  required for the underlying Cleverly.ps1 commands.
#>

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Script = Join-Path $Root "Cleverly.ps1"
$Url = "http://127.0.0.1:7000"

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

function New-Button {
    param(
        [string]$Text,
        [int]$X,
        [int]$Y,
        [scriptblock]$Click,
        [int]$Width = 112,
        [int]$Height = 34
    )
    $button = New-Object System.Windows.Forms.Button
    $button.Text = $Text
    $button.SetBounds($X, $Y, $Width, $Height)
    $button.Add_Click($Click)
    return $button
}

function New-GroupBox {
    param(
        [string]$Text,
        [int]$X,
        [int]$Y,
        [int]$Width,
        [int]$Height
    )
    $group = New-Object System.Windows.Forms.GroupBox
    $group.Text = $Text
    $group.SetBounds($X, $Y, $Width, $Height)
    $group.Anchor = "Top,Left,Right"
    return $group
}

function Invoke-ExternalCommand {
    param(
        [string]$FileName,
        [string[]]$Arguments = @(),
        [int]$TimeoutSeconds = 6
    )
    $process = $null
    try {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $FileName
        $psi.Arguments = ($Arguments -join " ")
        $psi.WorkingDirectory = $Root
        $psi.UseShellExecute = $false
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.CreateNoWindow = $true

        $process = New-Object System.Diagnostics.Process
        $process.StartInfo = $psi
        [void]$process.Start()
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            try { $process.Kill() } catch { }
            return [pscustomobject]@{
                ExitCode = 124
                Text = "timed out after $TimeoutSeconds seconds"
            }
        }
        $stdout = $process.StandardOutput.ReadToEnd().Trim()
        $stderr = $process.StandardError.ReadToEnd().Trim()
        $text = (($stdout, $stderr) | Where-Object { $_ }) -join [Environment]::NewLine
        return [pscustomobject]@{
            ExitCode = $process.ExitCode
            Text = $text
        }
    } catch {
        return [pscustomobject]@{
            ExitCode = 1
            Text = $_.Exception.Message
        }
    } finally {
        if ($process) { $process.Dispose() }
    }
}

function Invoke-CleverlyCommand {
    param(
        [string]$Action,
        [string[]]$ExtraArgs = @()
    )
    if (-not (Test-Path -LiteralPath $Script)) {
        throw "Cleverly.ps1 was not found next to this launcher."
    }
    $argList = @(
        "-NoLogo",
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", ('"{0}"' -f $Script),
        $Action
    ) + $ExtraArgs

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "powershell.exe"
    $psi.Arguments = ($argList -join " ")
    $psi.WorkingDirectory = $Root
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $psi
    [void]$process.Start()
    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    $process.WaitForExit()

    return [pscustomobject]@{
        Action = $Action
        ExitCode = $process.ExitCode
        StdOut = $stdout
        StdErr = $stderr
    }
}

function Invoke-PowerShellScript {
    param(
        [string]$Path,
        [string[]]$ExtraArgs = @()
    )
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Script was not found: $Path"
    }
    $argList = @(
        "-NoLogo",
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", ('"{0}"' -f $Path)
    ) + $ExtraArgs

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "powershell.exe"
    $psi.Arguments = ($argList -join " ")
    $psi.WorkingDirectory = $Root
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $psi
    [void]$process.Start()
    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    $process.WaitForExit()

    return [pscustomobject]@{
        Action = [System.IO.Path]::GetFileName($Path)
        ExitCode = $process.ExitCode
        StdOut = $stdout
        StdErr = $stderr
    }
}

function Get-DetectedGpuSummary {
    try {
        $controllers = @(Get-CimInstance -ClassName Win32_VideoController -ErrorAction Stop)
        if (-not $controllers -or $controllers.Count -eq 0) {
            return "unknown"
        }
        $summaries = foreach ($controller in $controllers) {
            $name = ([string]$controller.Name).Trim()
            $ramGb = 0.0
            if ($controller.AdapterRAM) {
                $ramGb = [math]::Round(([double]$controller.AdapterRAM / 1GB), 1)
            }
            if ($ramGb -gt 0) {
                "{0} ({1}GB reported)" -f $name, $ramGb
            } else {
                $name
            }
        }
        return ($summaries -join "; ")
    } catch {
        return "unknown"
    }
}

function Get-PrimaryModelSummary {
    $modelPath = Join-Path $Root "data\cleverly-primary-model.json"
    if (-not (Test-Path -LiteralPath $modelPath)) {
        return [pscustomobject]@{
            Ready = $false
            Text = "not set"
        }
    }
    try {
        $payload = Get-Content -LiteralPath $modelPath -Raw | ConvertFrom-Json
        $model = ([string]$payload.primary_model).Trim()
        $source = ([string]$payload.source).Trim()
        $profile = ([string]$payload.profile_label).Trim()
        if (-not $model) {
            return [pscustomobject]@{
                Ready = $false
                Text = "manifest exists, but no primary_model was found"
            }
        }
        $parts = @($model)
        if ($profile) { $parts += $profile }
        if ($source) { $parts += ("source: " + $source) }
        return [pscustomobject]@{
            Ready = $true
            Text = ($parts -join " | ")
        }
    } catch {
        return [pscustomobject]@{
            Ready = $false
            Text = "unreadable manifest: $($_.Exception.Message)"
        }
    }
}

function Get-AppHealthSummary {
    try {
        $response = Invoke-WebRequest -Uri "$Url/api/health" -UseBasicParsing -TimeoutSec 3
        return [pscustomobject]@{
            Ready = ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500)
            Text = ("HTTP " + $response.StatusCode)
        }
    } catch {
        return [pscustomobject]@{
            Ready = $false
            Text = "not running"
        }
    }
}

function Get-LauncherReadiness {
    $dockerCli = $null -ne (Get-Command docker -ErrorAction SilentlyContinue)
    $dockerEngine = [pscustomobject]@{ Ready = $false; Text = "Docker CLI not found" }
    $compose = [pscustomobject]@{ Ready = $false; Text = "Docker CLI not found" }
    $appImage = [pscustomobject]@{ Ready = $false; Text = "not checked" }
    $ollamaImage = [pscustomobject]@{ Ready = $false; Text = "not checked" }

    if ($dockerCli) {
        $version = Invoke-ExternalCommand "docker" @("version", "--format", "{{.Server.Version}}")
        $dockerEngine = [pscustomobject]@{
            Ready = ($version.ExitCode -eq 0)
            Text = if ($version.ExitCode -eq 0 -and $version.Text) { $version.Text } else { "not responding" }
        }
        $composeResult = Invoke-ExternalCommand "docker" @("compose", "version")
        $compose = [pscustomobject]@{
            Ready = ($composeResult.ExitCode -eq 0)
            Text = if ($composeResult.ExitCode -eq 0 -and $composeResult.Text) { $composeResult.Text } else { "not available" }
        }
        $appInspect = Invoke-ExternalCommand "docker" @("image", "inspect", "cleverly:local", "--format", "{{.Id}}")
        $appImage = [pscustomobject]@{
            Ready = ($appInspect.ExitCode -eq 0)
            Text = if ($appInspect.ExitCode -eq 0) { "cleverly:local present" } else { "cleverly:local missing" }
        }
        $ollamaInspect = Invoke-ExternalCommand "docker" @("image", "inspect", "cleverly-ollama:local", "--format", "{{.Id}}")
        $ollamaImage = [pscustomobject]@{
            Ready = ($ollamaInspect.ExitCode -eq 0)
            Text = if ($ollamaInspect.ExitCode -eq 0) { "cleverly-ollama:local present" } else { "cleverly-ollama:local missing" }
        }
    }

    $model = Get-PrimaryModelSummary
    $health = Get-AppHealthSummary
    $canStartOffline = $dockerCli -and $dockerEngine.Ready -and $compose.Ready -and $appImage.Ready -and $ollamaImage.Ready -and $model.Ready
    $recommended = if (-not $dockerCli) {
        "Install Docker Desktop, then run Check Setup again."
    } elseif (-not $dockerEngine.Ready) {
        "Start Docker Desktop, then run Check Setup again."
    } elseif (-not $appImage.Ready -or -not $ollamaImage.Ready -or -not $model.Ready) {
        "Run Connected Prep on a connected, non-sensitive machine or load an offline bundle."
    } elseif (-not $health.Ready) {
        "Click Start Offline, then Verify Offline."
    } else {
        "Cleverly is running. Click Verify Offline before sensitive work."
    }

    return [pscustomobject]@{
        DockerCli = $dockerCli
        DockerEngine = $dockerEngine
        Compose = $compose
        GpuEstimate = Get-DetectedGpuSummary
        AppImage = $appImage
        OllamaImage = $ollamaImage
        PrimaryModel = $model
        AppHealth = $health
        CanStartOffline = $canStartOffline
        RecommendedNextStep = $recommended
    }
}

$form = New-Object System.Windows.Forms.Form
$form.Text = "Cleverly"
$form.StartPosition = "CenterScreen"
$form.Size = New-Object System.Drawing.Size(830, 650)
$form.MinimumSize = New-Object System.Drawing.Size(760, 560)

$title = New-Object System.Windows.Forms.Label
$title.Text = "Cleverly Offline App"
$title.Font = New-Object System.Drawing.Font("Segoe UI", 14, [System.Drawing.FontStyle]::Bold)
$title.SetBounds(16, 12, 400, 28)
$form.Controls.Add($title)

$subtitle = New-Object System.Windows.Forms.Label
$subtitle.Text = "Guided local setup, offline start, and verification for the sealed Docker app."
$subtitle.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$subtitle.SetBounds(18, 42, 590, 22)
$form.Controls.Add($subtitle)

$state = New-Object System.Windows.Forms.Label
$state.Text = "Status: Ready"
$state.Font = New-Object System.Drawing.Font("Segoe UI", 9, [System.Drawing.FontStyle]::Bold)
$state.Anchor = "Top,Right"
$state.TextAlign = "MiddleRight"
$state.SetBounds(512, 52, 284, 20)
$form.Controls.Add($state)

$output = New-Object System.Windows.Forms.TextBox
$output.Multiline = $true
$output.ScrollBars = "Vertical"
$output.ReadOnly = $true
$output.Font = New-Object System.Drawing.Font("Consolas", 9)
$output.Anchor = "Top,Bottom,Left,Right"
$output.SetBounds(16, 354, 780, 238)
$form.Controls.Add($output)

$buttons = New-Object System.Collections.ArrayList

function Write-OutputBox {
    param([string]$Text)
    $output.AppendText($Text + [Environment]::NewLine)
}

function Set-ButtonsEnabled {
    param([bool]$Enabled)
    foreach ($button in $buttons) {
        $button.Enabled = $Enabled
    }
}

function Open-LocalPath {
    param(
        [string]$Path,
        [string]$MissingMessage
    )
    if (Test-Path -LiteralPath $Path) {
        Start-Process $Path
    } else {
        Write-OutputBox $MissingMessage
    }
}

function Write-CommandResult {
    param([pscustomobject]$Result)
    if ($Result.StdOut) { Write-OutputBox $Result.StdOut.TrimEnd() }
    if ($Result.StdErr) { Write-OutputBox $Result.StdErr.TrimEnd() }
    Write-OutputBox ("exit_code: " + $Result.ExitCode)
}

$script:ActiveTask = $null
$script:TaskTimer = New-Object System.Windows.Forms.Timer
$script:TaskTimer.Interval = 500
$script:TaskTimer.Add_Tick({
    if (-not $script:ActiveTask) { return }
    $process = $script:ActiveTask.Process
    if (-not $process.HasExited) { return }

    $script:TaskTimer.Stop()
    try { $process.WaitForExit() } catch { }

    $stdout = @($script:ActiveTask.StdOut | ForEach-Object { [string]$_ })
    $stderr = @($script:ActiveTask.StdErr | ForEach-Object { [string]$_ })
    if ($stdout.Count -gt 0) {
        Write-OutputBox (($stdout -join [Environment]::NewLine).TrimEnd())
    }
    if ($stderr.Count -gt 0) {
        Write-OutputBox (($stderr -join [Environment]::NewLine).TrimEnd())
    }
    Write-OutputBox ("exit_code: " + $process.ExitCode)

    foreach ($path in $script:ActiveTask.CleanupFiles) {
        if ($path -and (Test-Path -LiteralPath $path)) {
            Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
        }
    }

    $process.Dispose()
    $script:ActiveTask = $null
    $state.Text = "Status: Ready"
    Set-ButtonsEnabled $true
})

function Start-ProcessTask {
    param(
        [string]$Name,
        [string]$FileName,
        [string[]]$Arguments = @(),
        [string[]]$CleanupFiles = @()
    )
    if ($script:ActiveTask) {
        Write-OutputBox "Another Cleverly task is already running. Wait for it to finish before starting a new one."
        return
    }

    Set-ButtonsEnabled $false
    $state.Text = "Status: Running $Name"
    Write-OutputBox ""
    Write-OutputBox ("==> " + $Name)
    Write-OutputBox "Running in the background. The window can still be moved or minimized."
    [System.Windows.Forms.Application]::DoEvents()

    $stdout = [System.Collections.ArrayList]::Synchronized((New-Object System.Collections.ArrayList))
    $stderr = [System.Collections.ArrayList]::Synchronized((New-Object System.Collections.ArrayList))
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo.FileName = $FileName
    $process.StartInfo.Arguments = ($Arguments -join " ")
    $process.StartInfo.WorkingDirectory = $Root
    $process.StartInfo.UseShellExecute = $false
    $process.StartInfo.RedirectStandardOutput = $true
    $process.StartInfo.RedirectStandardError = $true
    $process.StartInfo.CreateNoWindow = $true

    $process.add_OutputDataReceived({
        param($sender, $eventArgs)
        if ($eventArgs.Data) { [void]$stdout.Add($eventArgs.Data) }
    }.GetNewClosure())
    $process.add_ErrorDataReceived({
        param($sender, $eventArgs)
        if ($eventArgs.Data) { [void]$stderr.Add($eventArgs.Data) }
    }.GetNewClosure())

    try {
        [void]$process.Start()
        $process.BeginOutputReadLine()
        $process.BeginErrorReadLine()
        $script:ActiveTask = [pscustomobject]@{
            Name = $Name
            Process = $process
            StdOut = $stdout
            StdErr = $stderr
            CleanupFiles = $CleanupFiles
        }
        $script:TaskTimer.Start()
    } catch {
        $process.Dispose()
        foreach ($path in $CleanupFiles) {
            if ($path -and (Test-Path -LiteralPath $path)) {
                Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
            }
        }
        Write-OutputBox ("ERROR: " + $_.Exception.Message)
        $state.Text = "Status: Ready"
        Set-ButtonsEnabled $true
    }
}

function Confirm-ConnectedPrep {
    $message = "Connected prep downloads Docker images and the selected local model. Run it only on a connected, non-sensitive prep machine. Continue?"
    $choice = [System.Windows.Forms.MessageBox]::Show(
        $message,
        "Connected Prep",
        [System.Windows.Forms.MessageBoxButtons]::OKCancel,
        [System.Windows.Forms.MessageBoxIcon]::Warning
    )
    return ($choice -eq [System.Windows.Forms.DialogResult]::OK)
}

function Run-ReadinessCheck {
    Set-ButtonsEnabled $false
    $state.Text = "Status: Checking setup"
    Write-OutputBox ""
    Write-OutputBox "==> Check Setup"
    [System.Windows.Forms.Application]::DoEvents()
    try {
        $readiness = Get-LauncherReadiness
        Write-OutputBox ("Docker CLI: " + $(if ($readiness.DockerCli) { "OK" } else { "missing" }))
        Write-OutputBox ("Docker engine: " + $(if ($readiness.DockerEngine.Ready) { "OK - " + $readiness.DockerEngine.Text } else { "missing - " + $readiness.DockerEngine.Text }))
        Write-OutputBox ("Docker Compose: " + $(if ($readiness.Compose.Ready) { "OK - " + $readiness.Compose.Text } else { "missing - " + $readiness.Compose.Text }))
        Write-OutputBox ("GPU estimate: " + $readiness.GpuEstimate)
        Write-OutputBox ("App image: " + $readiness.AppImage.Text)
        Write-OutputBox ("Model image: " + $readiness.OllamaImage.Text)
        Write-OutputBox ("Primary model: " + $readiness.PrimaryModel.Text)
        Write-OutputBox ("App health: " + $readiness.AppHealth.Text)
        Write-OutputBox ("Ready for offline start: " + $(if ($readiness.CanStartOffline) { "yes" } else { "no" }))
        Write-OutputBox ("Recommended next step: " + $readiness.RecommendedNextStep)
    } catch {
        Write-OutputBox ("ERROR: " + $_.Exception.Message)
    } finally {
        $state.Text = "Status: Ready"
        Set-ButtonsEnabled $true
    }
}

function Run-Action {
    param(
        [string]$Action,
        [string[]]$ExtraArgs = @()
    )
    $argList = @(
        "-NoLogo",
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", ('"{0}"' -f $Script),
        $Action
    ) + $ExtraArgs
    Start-ProcessTask -Name $Action -FileName "powershell.exe" -Arguments $argList
}

function Run-Script {
    param(
        [string]$Name,
        [string]$Path,
        [string[]]$ExtraArgs = @()
    )
    if (-not (Test-Path -LiteralPath $Path)) {
        Write-OutputBox "ERROR: Script was not found: $Path"
        return
    }
    $argList = @(
        "-NoLogo",
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", ('"{0}"' -f $Path)
    ) + $ExtraArgs
    Start-ProcessTask -Name $Name -FileName "powershell.exe" -Arguments $argList
}

function Run-OfflineVerification {
    $smokePath = Join-Path $Root "ci\fresh-machine-offline-smoke.ps1"
    if (-not (Test-Path -LiteralPath $smokePath)) {
        Write-OutputBox "ERROR: ci\fresh-machine-offline-smoke.ps1 was not found."
        return
    }
    $verifyScript = Join-Path ([System.IO.Path]::GetTempPath()) ("cleverly-verify-offline-" + [System.Guid]::NewGuid().ToString("N") + ".ps1")
    $content = @"
`$ErrorActionPreference = "Continue"
Write-Host "Running doctor..."
& "$Script" doctor
`$doctorExit = if (`$null -ne `$LASTEXITCODE) { `$LASTEXITCODE } else { 0 }
Write-Host ""
Write-Host "Running fresh-machine-offline-smoke.ps1..."
& "$smokePath" -SkipRestart
`$smokeExit = if (`$null -ne `$LASTEXITCODE) { `$LASTEXITCODE } else { 0 }
if (`$doctorExit -ne 0) { exit `$doctorExit }
exit `$smokeExit
"@
    Set-Content -LiteralPath $verifyScript -Encoding UTF8 -Value $content
    $argList = @(
        "-NoLogo",
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", ('"{0}"' -f $verifyScript)
    )
    Start-ProcessTask -Name "Verify Offline" -FileName "powershell.exe" -Arguments $argList -CleanupFiles @($verifyScript)
}

$firstRunGroup = New-GroupBox "First Run" 16 74 780 104
$firstRunNote = New-Object System.Windows.Forms.Label
$firstRunNote.Text = "Recommended path: Check Setup -> Connected Prep or prepared bundle -> Start Offline -> Verify Offline."
$firstRunNote.SetBounds(16, 22, 740, 18)
$firstRunGroup.Controls.Add($firstRunNote)

$checkSetup = New-Button "Check Setup" 16 52 { Run-ReadinessCheck } 118
$connectedPrep = New-Button "Connected Prep" 142 52 { if (Confirm-ConnectedPrep) { Run-Action "setup" @("-AllowConnectedPrep", "-NoOpen") } } 118
$buildBundle = New-Button "Build Bundle" 268 52 { if (Confirm-ConnectedPrep) { Run-Action "bundle" @("-AllowConnectedPrep") } } 118
$startOffline = New-Button "Start Offline" 394 52 { Run-Action "start" @("-NoOpen") } 118
$verifyOffline = New-Button "Verify Offline" 520 52 { Run-OfflineVerification } 118
$openBundle = New-Button "Open Bundle" 646 52 { Open-LocalPath (Join-Path $Root "dist\cleverly-offline-bundle") "Offline bundle folder not found. Run bundle on a connected prep machine first." } 118

@($checkSetup, $connectedPrep, $buildBundle, $startOffline, $verifyOffline, $openBundle) | ForEach-Object {
    [void]$buttons.Add($_)
    $firstRunGroup.Controls.Add($_)
}
$form.Controls.Add($firstRunGroup)

$opsGroup = New-GroupBox "Operations And Evidence" 16 188 780 150
$stop = New-Button "Stop" 16 24 { Run-Action "stop" } 118
$restart = New-Button "Restart" 142 24 { Run-Action "restart" @("-NoOpen") } 118
$status = New-Button "Status" 268 24 { Run-Action "status" } 118
$doctor = New-Button "Doctor" 394 24 { Run-Action "doctor" } 118
$logs = New-Button "Logs" 520 24 { Run-Action "logs" } 118
$setup = New-Button "Setup" 646 24 { Run-Action "setup" @("-NoOpen") } 118
$logFolder = New-Button "Open Logs" 16 66 { Open-LocalPath (Join-Path $Root "logs") "Logs folder not found yet. Start Cleverly first." } 118
$checklist = New-Button "Checklist" 142 66 { Open-LocalPath (Join-Path $Root "docs\release-checklist.md") "release-checklist.md was not found." } 118
$smoke = New-Button "Offline Smoke" 268 66 { Run-Script "Offline Smoke" (Join-Path $Root "ci\fresh-machine-offline-smoke.ps1") @("-SkipRestart") } 118
$readme = New-Button "README" 394 66 { Open-LocalPath (Join-Path $Root "README.md") "README.md was not found." } 118
$makeRelease = New-Button "Make Release" 520 66 { Run-Script "Make Release" (Join-Path $Root "scripts\make-release.ps1") @("-SkipBundle", "-SkipInstaller", "-AllowDirty") } 118
$freshProof = New-Button "Fresh Proof" 646 66 { Run-Script "Fresh Proof" (Join-Path $Root "ci\fresh-machine-proof.ps1") @("-SkipRestart") } 118
$securityScan = New-Button "Security Scan" 16 108 { Run-Script "Security Scan" (Join-Path $Root "scripts\run-static-security.ps1") @("-WarnOnly") } 118
$releaseFolder = New-Button "Release Folder" 142 108 { Open-LocalPath (Join-Path $Root "dist\release-candidates") "No release candidate folder found yet." } 118
$sbom = New-Button "SBOM" 268 108 { Run-Script "SBOM" (Join-Path $Root "scripts\generate-sbom.ps1") @("-SkipDocker") } 118
$proofFolder = New-Button "Proofs" 394 108 { Open-LocalPath (Join-Path $Root "dist") "dist folder not found yet." } 118
$standaloneApp = New-Button "Standalone App" 520 108 { Start-Process (Join-Path $Root "Cleverly-Standalone.cmd") } 118
$standaloneDoc = New-Button "Standalone Doc" 646 108 { Open-LocalPath (Join-Path $Root "docs\standalone-mode.md") "standalone-mode.md was not found." } 118

@($stop, $restart, $status, $doctor, $logs, $setup, $logFolder, $checklist, $smoke, $readme, $makeRelease, $freshProof, $securityScan, $releaseFolder, $sbom, $proofFolder, $standaloneApp, $standaloneDoc) | ForEach-Object {
    [void]$buttons.Add($_)
    $opsGroup.Controls.Add($_)
}
$form.Controls.Add($opsGroup)

$open = New-Object System.Windows.Forms.Button
$open.Text = "Open UI"
$open.Anchor = "Top,Right"
$open.SetBounds(684, 20, 112, 34)
$open.Add_Click({ Start-Process $Url })
$form.Controls.Add($open)

$form.Add_Shown({
    Write-OutputBox "Cleverly launcher ready."
    Write-OutputBox "Default URL: $Url"
    Write-OutputBox "Recommended path: Check Setup -> Connected Prep or prepared bundle -> Start Offline -> Verify Offline."
})

[void]$form.ShowDialog()
