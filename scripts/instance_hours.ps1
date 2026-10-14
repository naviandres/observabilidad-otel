<#
.SYNOPSIS
    Horas de task Fargate -> docs/evidence/instance-hours.csv, y gate de las
    150 h acumuladas (PLAN §3.5).
.DESCRIPTION
    El presupuesto de horas del laboratorio vive en el CSV de evidencia
    (docs/evidence/instance-hours.csv). Este script:

      - sync   : lee las tasks del cluster ECS (RUNNING + STOPPED), calcula las
                 horas de cada una con startedAt/stoppedAt (una RUNNING se
                 cuenta hasta ahora) y ANADE una fila `summary` por task si no
                 existe ya (idempotente: dedup por id de task + accion).
      - record : anade a mano una fila `summary` con -StartedAt/-StoppedAt
                 (para intervalos reconstruidos de una sesion).
      - check  : NO toca la nube; solo suma la columna `hours` del CSV.

    En los tres modos, TERMINA con el gate: si el acumulado supera -MaxHours
    (default 150) sale con exit 1. `make budget-hours` usa `-Mode check`.

    Filas sin horas (start/stop, aun sin cierre) se ignoran al sumar. La tarifa
    por defecto es la de Fargate us-east-1 para 1 vCPU + 2 GB: 0.04928 USD/h.
.PARAMETER Mode
    sync | record | check (default: sync).
.PARAMETER Cluster
    Cluster ECS (default: otel-lab).
.PARAMETER Csv
    CSV de evidencia (default: docs/evidence/instance-hours.csv, relativo a la
    raiz del repo).
.PARAMETER MaxHours
    Techo de horas acumuladas (default: 150).
.PARAMETER RatePerHour
    Tarifa USD/h (default: 0.04928).
.EXAMPLE
    powershell -NoProfile -File scripts/instance_hours.ps1 -Mode sync
    make budget-hours        # -Mode check
#>
[CmdletBinding()]
param(
    [ValidateSet("sync", "record", "check")]
    [string]$Mode = "sync",
    [string]$Cluster = "otel-lab",
    [string]$Region = "us-east-1",
    [string]$Profile = "otel-lab",
    [string]$Csv = "docs/evidence/instance-hours.csv",
    [double]$MaxHours = 150,
    [double]$RatePerHour = 0.04928,
    [int]$Vcpu = 1,
    [double]$MemoryGb = 2,
    [datetime]$StartedAt,
    [datetime]$StoppedAt
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not [System.IO.Path]::IsPathRooted($Csv)) {
    $Csv = Join-Path $repoRoot $Csv
}
if (-not (Test-Path -LiteralPath $Csv)) {
    Write-Error "[instance_hours] no existe el CSV: $Csv"
}

$header = @(
    "date", "task", "action", "timestamp_utc", "hours",
    "vcpu", "memory_gb", "rate_usd_per_hour", "subtotal_usd", "notes"
)

function Get-Rows {
    # Import-Csv maneja las comillas; las filas sin `hours` se conservan.
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $rows = @(Import-Csv -LiteralPath $Csv)
    $ErrorActionPreference = $prev
    return $rows
}

function Get-TotalHours {
    $total = 0.0
    foreach ($r in (Get-Rows)) {
        $h = $r.hours
        if ($h -and $h.Trim() -ne "") {
            $total += [double]$h
        }
    }
    return $total
}

function Format-Cell {
    param([string]$Value)
    if ($null -eq $Value) { $Value = "" }
    if ($Value -match '[",]') {
        return '"' + ($Value -replace '"', '""') + '"'
    }
    return $Value
}

function Add-CsvRow {
    param([hashtable]$Row)
    $cells = foreach ($k in $header) { Format-Cell ([string]$Row[$k]) }
    $line = ($cells -join ",")
    Add-Content -LiteralPath $Csv -Value $line -Encoding ascii
}

function Test-SummaryExists {
    param([string]$TaskId, [string]$StampKey)
    foreach ($r in (Get-Rows)) {
        if ($r.task -eq $TaskId -and $r.action -eq "summary" -and $r.timestamp_utc -eq $StampKey) {
            return $true
        }
    }
    return $false
}

function Add-SummaryIfMissing {
    param(
        [string]$TaskId,
        [datetime]$Start,
        [datetime]$Stop,
        [string]$Note
    )
    $hours = [math]::Round((($Stop - $Start).TotalHours), 2)
    if ($hours -lt 0) { throw "[instance_hours] stop anterior a start para $TaskId" }
    $stampKey = ("{0:yyyy-MM-ddTHH:mm:ssZ}/{1:yyyy-MM-ddTHH:mm:ssZ}" -f $Start.ToUniversalTime(), $Stop.ToUniversalTime())
    if (Test-SummaryExists -TaskId $TaskId -StampKey $stampKey) {
        Write-Host "[instance_hours] ya existe summary de $TaskId ($stampKey); sin cambios." -ForegroundColor DarkGray
        return 0
    }
    $subtotal = [math]::Round($hours * $RatePerHour, 3)
    Add-CsvRow @{
        date              = $Start.ToUniversalTime().ToString("yyyy-MM-dd")
        task              = $TaskId
        action            = "summary"
        timestamp_utc     = $stampKey
        hours             = $hours
        vcpu              = $Vcpu
        memory_gb         = $MemoryGb
        rate_usd_per_hour = $RatePerHour
        subtotal_usd      = $subtotal
        notes             = ("{0}h x ${1}/h = ~${2} USD; {3}" -f $hours, $RatePerHour, $subtotal, $Note)
    }
    Write-Host "[instance_hours] +$TaskId $hours h (~$subtotal USD)" -ForegroundColor Green
    return $hours
}

function Sync-FromEcs {
    if (-not (Get-Command aws -ErrorAction SilentlyContinue)) {
        Write-Error "[instance_hours] aws CLI no encontrado (necesario en -Mode sync)"
    }
    $arns = @(& aws --region $Region --profile $Profile ecs list-tasks `
        --cluster $Cluster --query "taskArns" --output json 2>$null | Out-String | ConvertFrom-Json)
    if (-not $arns) {
        Write-Host "[instance_hours] el cluster $Cluster no tiene tasks registradas; nada que anadir." -ForegroundColor Yellow
        return
    }
    $chunks = @()
    for ($i = 0; $i -lt $arns.Count; $i += 100) {
        $chunks += , $arns[$i..([math]::Min($i + 99, $arns.Count - 1))]
    }
    foreach ($chunk in $chunks) {
        $desc = & aws --region $Region --profile $Profile ecs describe-tasks `
            --cluster $Cluster --tasks @chunk --output json 2>$null | Out-String | ConvertFrom-Json
        foreach ($t in @($desc.tasks)) {
            if (-not $t.startedAt) { continue }
            $taskId = ($t.taskArn -split "/")[-1]
            $start = [datetime]::Parse($t.startedAt).ToUniversalTime()
            $stop = if ($t.stoppedAt) { [datetime]::Parse($t.stoppedAt).ToUniversalTime() } else { (Get-Date).ToUniversalTime() }
            $note = "ECS $($t.lastStatus) rev $(( $t.taskDefinition -split ':')[-1])"
            [void](Add-SummaryIfMissing -TaskId $taskId -Start $start -Stop $stop -Note $note)
        }
    }
}

# --- Ejecuta el modo -------------------------------------------------------
switch ($Mode) {
    "sync" { Sync-FromEcs }
    "record" {
        if (-not $StartedAt -or -not $StoppedAt) {
            Write-Error "[instance_hours] -Mode record exige -StartedAt y -StoppedAt (ISO 8601)"
        }
        [void](Add-SummaryIfMissing -TaskId "manual" -Start $StartedAt.ToUniversalTime() `
            -Stop $StoppedAt.ToUniversalTime() -Note "registro manual")
    }
    "check" { Write-Host "[instance_hours] -Mode check: solo gate sobre el CSV." -ForegroundColor Cyan }
}

# --- Gate de las 150 h -----------------------------------------------------
$total = Get-TotalHours
Write-Host ("[instance_hours] acumulado = {0:N2} h (techo {1:N0} h)" -f $total, $MaxHours) -ForegroundColor Cyan
if ($total -gt $MaxHours) {
    Write-Host "[instance_hours] ERROR: se supero el techo de horas del PLAN §3.5." -ForegroundColor Red
    exit 1
}
Write-Host "[instance_hours] OK: dentro del techo de horas." -ForegroundColor Green
exit 0
