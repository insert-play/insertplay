# wsl-disk.ps1 - mount/unmount a Linux-formatted USB disk through WSL2 and give it its own drive letter.
#
# Usage (normal PowerShell, no need to open it as admin; one UAC prompt appears per mount/unmount):
#   .\tools\wsl-disk.ps1 mount
#   .\tools\wsl-disk.ps1 unmount
#   .\tools\wsl-disk.ps1 status
# Settings come from tools\.env (see tools\.env.example); any parameter below overrides it, e.g.
#   .\tools\wsl-disk.ps1 mount -Type ext4 -ReadWrite
#
# How the letter works: net use only accepts the WSL distro ROOT (error 64 on subfolders), which would
# expose the whole distro under the letter. Instead a directory symlink (mklink /D, no admin needed with
# Developer Mode) points at \\wsl.localhost\<distro>\mnt\wsl\<name>, and `subst` turns that link into a
# drive letter. The letter therefore contains ONLY the disk. subst letters are per session: they vanish
# on reboot/logoff, just run mount again.
#
# Only the elevated "wsl --mount/--unmount" calls need admin. Keep this file ASCII-only (Windows
# PowerShell 5.1 misreads UTF-8 without BOM).

param(
    [Parameter(Mandatory, Position = 0)][ValidateSet('mount', 'unmount', 'status')][string]$Action,
    [string]$Serial,                       # disk serial (Get-Disk | fl SerialNumber); stable, unlike the disk number
    [int]$Partition,                       # partition number to mount (Get-Partition -DiskNumber N)
    [string]$Type,                         # filesystem type the WSL kernel understands: ext4, btrfs, ...
    [string]$Name,                         # mount name: appears at /mnt/wsl/<Name>
    [string]$Letter,                       # drive letter, or 'auto' = first free letter from X downwards
    [string]$Distro,                       # WSL distro that serves the files
    [switch]$ReadWrite,                    # default is read-only
    [switch]$Elevated                      # internal: set on the elevated child
)

$ErrorActionPreference = 'Stop'

# --- settings: parameter > tools\.env > built-in default ----------------------------------------
$envFile = Join-Path $PSScriptRoot '.env'
$cfg = @{}
if (Test-Path -LiteralPath $envFile) {
    foreach ($line in Get-Content -LiteralPath $envFile) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
            $cfg[$Matches[1]] = $Matches[2].Trim('"', "'")
        }
    }
}
function Pick($param, $key, $default) {
    if ($param) { return $param }
    if ($cfg.ContainsKey($key) -and $cfg[$key] -ne '') { return $cfg[$key] }
    return $default
}
$Serial    = Pick $Serial    'WSL_DISK_SERIAL'    $null
$Partition = [int](Pick $(if ($Partition) { $Partition } else { $null }) 'WSL_DISK_PARTITION' 0)
$Type      = Pick $Type      'WSL_DISK_TYPE'      'ext4'
$Name      = Pick $Name      'WSL_DISK_NAME'      'disk'
$Letter    = Pick $Letter    'WSL_DISK_LETTER'    'auto'
$Distro    = Pick $Distro    'WSL_DISK_DISTRO'    'Ubuntu'
if (-not $ReadWrite -and $cfg['WSL_DISK_READWRITE'] -eq 'true') { $ReadWrite = [switch]::new($true) }

if (-not $Serial -or $Partition -lt 1) {
    throw "WSL_DISK_SERIAL and WSL_DISK_PARTITION are required: copy tools\.env.example to tools\.env and fill them in (or pass -Serial / -Partition)."
}

$pidFile = Join-Path $env:TEMP "wsl-disk-$Name.pid"
$target  = "\\wsl.localhost\$Distro\mnt\wsl\$Name"                   # the disk, inside the distro
$link    = Join-Path $env:LOCALAPPDATA "wsl-disk\$Name"              # local symlink -> $target

function Get-TargetDisk {
    $d = Get-Disk | Where-Object { $_.SerialNumber -and $_.SerialNumber.Trim() -eq $Serial }
    if (-not $d) { throw "No disk with serial $Serial is connected." }
    # never let this tool near the machine's own disks
    if ($d.BusType -ne 'USB' -or $d.IsBoot -or $d.IsSystem) {
        throw "Refusing: disk $($d.Number) ($($d.FriendlyName)) is not an external USB data disk."
    }
    return $d
}

# --- elevated child: only the wsl --mount / --unmount calls --------------------------------
if ($Elevated) {
    $n = (Get-TargetDisk).Number
    if ($Action -eq 'mount') {
        $opts = if ($ReadWrite) { 'rw' } else { 'ro' }
        # a leftover "--bare" attachment must be released first
        wsl --unmount "\\.\PHYSICALDRIVE$n" 2>$null | Out-Null
        wsl --mount "\\.\PHYSICALDRIVE$n" --partition $Partition --type $Type --options $opts --name $Name
        if ($LASTEXITCODE -ne 0) { throw "wsl --mount failed ($LASTEXITCODE)" }
    }
    elseif ($Action -eq 'unmount') {
        wsl --unmount "\\.\PHYSICALDRIVE$n"
    }
    exit 0
}

function Invoke-Elevated {
    $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"", $Action,
        '-Serial', $Serial, '-Partition', $Partition, '-Type', $Type, '-Name', $Name,
        '-Distro', $Distro, '-Elevated')
    if ($ReadWrite) { $argList += '-ReadWrite' }
    $p = Start-Process powershell -Verb RunAs -ArgumentList $argList -Wait -PassThru -WindowStyle Hidden
    if ($p.ExitCode -ne 0) { throw "Elevated step failed (exit $($p.ExitCode))." }
}

# Letter that `subst` already maps to OUR link, or $null. Output looks like: "X:\: => C:\...\<name>"
function Get-OurLetter {
    foreach ($line in (subst)) {
        if ($line -match '^([A-Za-z]):\\: => (.+)$' -and $Matches[2].TrimEnd('\') -eq $link) { return $Matches[1].ToUpper() }
    }
    return $null
}

function Resolve-FreeLetter {
    $used = (Get-PSDrive -PSProvider FileSystem).Name
    if ($Letter -ne 'auto') {
        if ($used -contains $Letter) { throw "Letter ${Letter}: is already in use by another drive." }
        return $Letter.ToUpper()
    }
    foreach ($c in 'X','Y','Z','V','U','T','S','R','Q') { if ($used -notcontains $c) { return $c } }
    throw 'No free drive letter found.'
}

function Remove-OurLink {
    # only ever removes a symlink (reparse point); a real folder with this name is left alone
    if (Test-Path -LiteralPath $link) {
        $item = Get-Item -LiteralPath $link -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { cmd /c rmdir "`"$link`"" | Out-Null }
        else { throw "$link exists and is a real folder, not our link. Not touching it." }
    }
}

switch ($Action) {
    'mount' {
        [void](Get-TargetDisk)   # fail early, before the UAC prompt
        Invoke-Elevated
        # keep the distro alive, otherwise WSL idles it out and the mount goes away
        if (-not (Test-Path $pidFile) -or -not (Get-Process -Id (Get-Content $pidFile) -ErrorAction SilentlyContinue)) {
            $k = Start-Process wsl -ArgumentList @('-d', $Distro, '-u', 'root', '--', 'sleep', 'infinity') -WindowStyle Hidden -PassThru
            $k.Id | Set-Content $pidFile
        }
        Start-Sleep -Seconds 2
        if (-not (Test-Path -LiteralPath $target)) { throw "$target not reachable: the mount is not up." }

        # symlink -> disk (recreated each time so it never points at a stale target)
        New-Item -ItemType Directory -Force -Path (Split-Path $link) | Out-Null
        Remove-OurLink
        cmd /c mklink /D "`"$link`"" "`"$target`"" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "mklink failed (is Developer Mode on?)." }

        # letter -> symlink (our own letter is reused, another drive's letter is never touched)
        $L = Get-OurLetter
        if (-not $L) {
            $L = Resolve-FreeLetter
            subst "${L}:" $link
            if ($LASTEXITCODE -ne 0) { throw "subst ${L}: failed (exit $LASTEXITCODE)." }
        }
        if (-not (Test-Path -LiteralPath "${L}:\")) { throw "${L}: was created but is not readable." }
        "Mounted as ${L}:  ($(if ($ReadWrite) {'read-write'} else {'read-only'}))"
        "Real disk usage (from inside Linux):"
        wsl -d $Distro -u root -- df -h "/mnt/wsl/$Name" | Select-Object -Last 1
    }
    'unmount' {
        $L = Get-OurLetter
        if ($L) { subst "${L}:" /d }                 # only our own letter, never another
        Remove-OurLink
        if (Test-Path $pidFile) {
            Stop-Process -Id (Get-Content $pidFile) -ErrorAction SilentlyContinue
            Remove-Item $pidFile -ErrorAction SilentlyContinue
        }
        Invoke-Elevated
        "Unmounted. Safe to unplug."
    }
    'status' {
        $d = Get-Disk | Where-Object { $_.SerialNumber -and $_.SerialNumber.Trim() -eq $Serial }
        if ($d) { "Disk $($d.Number) $($d.FriendlyName) offline=$($d.IsOffline)" } else { "Disk with serial $Serial not connected." }
        $L = Get-OurLetter
        "Letter: $(if ($L) { $L + ':' } else { 'none' })"
        "Keep-alive running: $((Test-Path $pidFile) -and [bool](Get-Process -Id (Get-Content $pidFile) -ErrorAction SilentlyContinue))"
        if (Test-Path -LiteralPath $target) {
            "Real disk usage:"; wsl -d $Distro -u root -- df -h "/mnt/wsl/$Name" | Select-Object -Last 1
        }
    }
}
