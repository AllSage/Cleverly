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
        [scriptblock]$Click
    )
    $button = New-Object System.Windows.Forms.Button
    $button.Text = $Text
    $button.SetBounds($X, $Y, 112, 34)
    $button.Add_Click($Click)
    return $button
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

$form = New-Object System.Windows.Forms.Form
$form.Text = "Cleverly"
$form.StartPosition = "CenterScreen"
$form.Size = New-Object System.Drawing.Size(760, 520)
$form.MinimumSize = New-Object System.Drawing.Size(680, 420)

$title = New-Object System.Windows.Forms.Label
$title.Text = "Cleverly Offline App"
$title.Font = New-Object System.Drawing.Font("Segoe UI", 14, [System.Drawing.FontStyle]::Bold)
$title.SetBounds(16, 12, 400, 28)
$form.Controls.Add($title)

$subtitle = New-Object System.Windows.Forms.Label
$subtitle.Text = "Start, stop, inspect, and open the local offline Docker app."
$subtitle.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$subtitle.SetBounds(18, 42, 520, 22)
$form.Controls.Add($subtitle)

$state = New-Object System.Windows.Forms.Label
$state.Text = "Status: Ready"
$state.Font = New-Object System.Drawing.Font("Segoe UI", 9, [System.Drawing.FontStyle]::Bold)
$state.Anchor = "Top,Right"
$state.TextAlign = "MiddleRight"
$state.SetBounds(470, 52, 256, 20)
$form.Controls.Add($state)

$output = New-Object System.Windows.Forms.TextBox
$output.Multiline = $true
$output.ScrollBars = "Vertical"
$output.ReadOnly = $true
$output.Font = New-Object System.Drawing.Font("Consolas", 9)
$output.Anchor = "Top,Bottom,Left,Right"
$output.SetBounds(16, 154, 710, 306)
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

function Run-Action {
    param(
        [string]$Action,
        [string[]]$ExtraArgs = @()
    )
    Set-ButtonsEnabled $false
    $state.Text = "Status: Running $Action"
    Write-OutputBox ""
    Write-OutputBox ("==> " + $Action)
    [System.Windows.Forms.Application]::DoEvents()
    try {
        $result = Invoke-CleverlyCommand -Action $Action -ExtraArgs $ExtraArgs
        if ($result.StdOut) { Write-OutputBox $result.StdOut.TrimEnd() }
        if ($result.StdErr) { Write-OutputBox $result.StdErr.TrimEnd() }
        Write-OutputBox ("exit_code: " + $result.ExitCode)
    } catch {
        Write-OutputBox ("ERROR: " + $_.Exception.Message)
    } finally {
        $state.Text = "Status: Ready"
        Set-ButtonsEnabled $true
    }
}

function Run-Script {
    param(
        [string]$Name,
        [string]$Path,
        [string[]]$ExtraArgs = @()
    )
    Set-ButtonsEnabled $false
    $state.Text = "Status: Running $Name"
    Write-OutputBox ""
    Write-OutputBox ("==> " + $Name)
    [System.Windows.Forms.Application]::DoEvents()
    try {
        $result = Invoke-PowerShellScript -Path $Path -ExtraArgs $ExtraArgs
        if ($result.StdOut) { Write-OutputBox $result.StdOut.TrimEnd() }
        if ($result.StdErr) { Write-OutputBox $result.StdErr.TrimEnd() }
        Write-OutputBox ("exit_code: " + $result.ExitCode)
    } catch {
        Write-OutputBox ("ERROR: " + $_.Exception.Message)
    } finally {
        $state.Text = "Status: Ready"
        Set-ButtonsEnabled $true
    }
}


$start = New-Button "Start" 16 74 { Run-Action "start" @("-NoOpen") }
$stop = New-Button "Stop" 136 74 { Run-Action "stop" }
$restart = New-Button "Restart" 256 74 { Run-Action "restart" @("-NoOpen") }
$status = New-Button "Status" 376 74 { Run-Action "status" }
$doctor = New-Button "Doctor" 496 74 { Run-Action "doctor" }
$logs = New-Button "Logs" 616 74 { Run-Action "logs" }
$setup = New-Button "Setup" 16 116 { Run-Action "setup" @("-NoOpen") }
$bundle = New-Button "Open Bundle" 136 116 { Open-LocalPath (Join-Path $Root "dist\cleverly-offline-bundle") "Offline bundle folder not found. Run bundle on a connected prep machine first." }
$logFolder = New-Button "Open Logs" 256 116 { Open-LocalPath (Join-Path $Root "logs") "Logs folder not found yet. Start Cleverly first." }
$checklist = New-Button "Checklist" 376 116 { Open-LocalPath (Join-Path $Root "docs\release-checklist.md") "release-checklist.md was not found." }
$smoke = New-Button "Offline Smoke" 496 116 { Run-Script "Offline Smoke" (Join-Path $Root "ci\fresh-machine-offline-smoke.ps1") @("-SkipRestart") }
$readme = New-Button "README" 616 116 { Open-LocalPath (Join-Path $Root "README.md") "README.md was not found." }

@($start, $stop, $restart, $status, $doctor, $logs, $setup, $bundle, $logFolder, $checklist, $smoke, $readme) | ForEach-Object {
    [void]$buttons.Add($_)
    $form.Controls.Add($_)
}

$open = New-Object System.Windows.Forms.Button
$open.Text = "Open UI"
$open.Anchor = "Top,Right"
$open.SetBounds(616, 20, 112, 34)
$open.Add_Click({ Start-Process $Url })
$form.Controls.Add($open)

$form.Add_Shown({
    Write-OutputBox "Cleverly launcher ready."
    Write-OutputBox "Default URL: $Url"
})

[void]$form.ShowDialog()
