<#
.SYNOPSIS
    Guardarrail de coste en vivo: gasto diario de Cost Explorer + recursos que
    estan facturando AHORA mismo, en AWS y en GCP.
.DESCRIPTION
    Complementa a guard_cost.py (que mira el PLAN, antes de crear nada): este
    script mira la REALIDAD facturada, para cazar fugas que el plan no ve
    (una task que quedo RUNNING, una EIP suelta, un NAT/ALB que alguien creo a
    mano, una VM GCP encendida, un cluster GKE que no se apago).

      - AWS: `ce get-cost-and-usage` (granularidad DIARIA, los ultimos -Days
        dias) + barrido de recursos siempre-facturables: EC2 running, EIP, NAT
        Gateway, ALB/NLB, RDS y tasks ECS RUNNING (informativas: durante una
        sesion son esperadas; el resto son FUGAS -> exit 1).
      - GCP: si `gcloud` esta en el PATH, VMs RUNNING y clusters GKE. Si no,
        avisa y sigue (el guardarrail AWS sigue siendo valido).

    Exit 1 si hay fugas o si algun dia supera -DailyBudgetUsd; 0 si el
    laboratorio esta en reposo y dentro de presupuesto.

    PLAN Â§3.5. Sin sesion de computo; Cost Explorer tiene lag de ~24h, por eso
    el barrido de recursos es el que manda para el "ahora".
.PARAMETER Region
    Region AWS (default: us-east-1).
.PARAMETER Profile
    Perfil de credenciales AWS (default: otel-lab). Nunca credenciales en claro.
.PARAMETER Days
    Ultimos N dias de Cost Explorer (default: 7).
.PARAMETER DailyBudgetUsd
    Techo diario del budget (default: 1 USD/dia, PLAN Â§3.5).
.EXAMPLE
    powershell -NoProfile -File scripts/cost_guard.ps1
    make cost-guard
#>
[CmdletBinding()]
param(
    [string]$Region = "us-east-1",
    [string]$Profile = "otel-lab",
    [int]$Days = 7,
    [double]$DailyBudgetUsd = 1
)

$ErrorActionPreference = "Stop"

function Invoke-AwsText {
    param([string[]]$AwsArgs)
    # aws devuelve "null" cuando --query no encuentra nada: se normaliza a "".
    # EAP=Continue SOLO alrededor del nativo: si no, PS 5.1 convierte cualquier
    # salida de stderr en un NativeCommandError y aborta antes de leer el exit
    # code. El fallo real se decide por $LASTEXITCODE.
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = & aws --region $Region --profile $Profile @AwsArgs 2>&1 | Out-String
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
    if ($code -ne 0) {
        throw "aws $($AwsArgs -join ' ') fallo (exit $code): $($out.Trim())"
    }
    $t = $out.Trim()
    if ($t -eq "null") { return "" }
    return $t
}

function To-Items {
    param([string]$Json)
    # --output json de una lista vacia es "[]"; un --query de [x].y suelto puede
    # venir como null. Normaliza a array.
    if (-not $Json) { return @() }
    $parsed = $Json | ConvertFrom-Json -ErrorAction Stop
    if ($null -eq $parsed) { return @() }
    return @($parsed)
}

# Consultas de barrido que pueden fallar por permisos: un AccessDenied NO es una
# fuga, pero tampoco se puede afirmar "todo limpio". Se registra como NO
# VERIFICADO y se reporta al final (el veredicto de fugas solo cuenta $leaks).
$unverified = New-Object System.Collections.Generic.List[string]

function Get-ItemsOrWarn {
    param([string]$Label, [string[]]$AwsArgs)
    try {
        return To-Items (Invoke-AwsText $AwsArgs)
    } catch {
        $unverified.Add($Label)
        $msg = ($_.Exception.Message -split "`n")[0].Trim()
        Write-Host "[cost_guard] AVISO: no se pudo verificar ${Label}: $msg" -ForegroundColor DarkYellow
        return @()
    }
}

# --- 1. Cost Explorer: gasto diario ---------------------------------------
$start = (Get-Date).AddDays(-$Days).ToString("yyyy-MM-dd")
$end = (Get-Date).AddDays(1).ToString("yyyy-MM-dd")
Write-Host "[cost_guard] Cost Explorer AWS $start .. $end" -ForegroundColor Cyan

$ceJson = Invoke-AwsText @(
    "ce", "get-cost-and-usage",
    "--time-period", "Start=$start,End=$end",
    "--granularity", "DAILY",
    "--metrics", "UnblendedCost",
    "--output", "json"
)
$ce = $ceJson | ConvertFrom-Json -ErrorAction Stop

$grandTotal = 0.0
$overBudgetDay = $false
foreach ($day in @($ce.ResultsByTime)) {
    $amount = [double]$day.Total.UnblendedCost.Amount
    $grandTotal += $amount
    $flag = if ($amount -gt $DailyBudgetUsd) { " <-- SUPERA -DailyBudgetUsd" } else { "" }
    if ($amount -gt $DailyBudgetUsd) { $overBudgetDay = $true }
    Write-Host ("[cost_guard]   {0}  {1,8:N4} USD{2}" -f $day.TimePeriod.Start, $amount, $flag)
}
Write-Host ("[cost_guard] total {0} dias = {1:N4} USD" -f $Days, $grandTotal)

# --- 2. Recursos AWS que facturan ahora ------------------------------------
$leaks = New-Object System.Collections.Generic.List[string]

$ec2 = Get-ItemsOrWarn -Label "EC2 running" -AwsArgs @(
    "ec2", "describe-instances",
    "--filters", "Name=instance-state-name,Values=running",
    "--query", "Reservations[].Instances[].InstanceId", "--output", "json"
)
if ($ec2.Count -gt 0) { $leaks.Add("EC2 running: $($ec2 -join ', ')") }

$eips = Get-ItemsOrWarn -Label "Elastic IPs" -AwsArgs @(
    "ec2", "describe-addresses", "--query", "Addresses[].PublicIp", "--output", "json"
)
if ($eips.Count -gt 0) { $leaks.Add("EIP asignadas: $($eips -join ', ')") }

$nats = Get-ItemsOrWarn -Label "NAT gateways" -AwsArgs @(
    "ec2", "describe-nat-gateways", "--filter", "Name=state,Values=available",
    "--query", "NatGateways[].NatGatewayId", "--output", "json"
)
if ($nats.Count -gt 0) { $leaks.Add("NAT Gateway: $($nats -join ', ')") }

$lbs = Get-ItemsOrWarn -Label "ALB/NLB" -AwsArgs @(
    "elbv2", "describe-load-balancers", "--query", "LoadBalancers[].LoadBalancerArn",
    "--output", "json"
)
if ($lbs.Count -gt 0) { $leaks.Add("ALB/NLB: $($lbs.Count) (detalle, sin listar todos)") }

$rds = Get-ItemsOrWarn -Label "RDS" -AwsArgs @(
    "rds", "describe-db-instances", "--query", "DBInstances[].DBInstanceIdentifier",
    "--output", "json"
)
if ($rds.Count -gt 0) { $leaks.Add("RDS: $($rds -join ', ')") }

# Tasks ECS RUNNING: informativas (esperadas durante la sesion).
$ecsRunning = 0
foreach ($clusterArn in (Get-ItemsOrWarn -Label "ECS clusters" -AwsArgs @(
    "ecs", "list-clusters", "--query", "clusterArns", "--output", "json"
))) {
    $tasks = Get-ItemsOrWarn -Label "ECS tasks ($clusterArn)" -AwsArgs @(
        "ecs", "list-tasks", "--cluster", $clusterArn, "--desired-status", "RUNNING",
        "--query", "taskArns", "--output", "json"
    )
    if ($tasks.Count -gt 0) {
        $ecsRunning += $tasks.Count
        Write-Host "[cost_guard]   ECS ${clusterArn}: $($tasks.Count) task(s) RUNNING (esperado en sesion)" -ForegroundColor Yellow
    }
}

# --- 3. Recursos GCP (si gcloud esta disponible) --------------------------
# gcloud.ps1 es un wrapper PowerShell: sus avisos van al flujo de ERROR de PS
# (no al stderr nativo), asi que EAP=Stop abortaria. Se ejecuta con EAP
# local en Continue y se decide por $LASTEXITCODE, igual que en el lado AWS.
function Get-GcpItems {
    param([string]$Label, [string[]]$GcloudArgs)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = & gcloud @GcloudArgs 2>$null | Out-String
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
    if ($code -ne 0) {
        $unverified.Add($Label)
        Write-Host "[cost_guard] AVISO: no se pudo verificar ${Label} (gcloud exit $code)" -ForegroundColor DarkYellow
        return @()
    }
    return To-Items $out
}

$gcpLeaks = 0
if (Get-Command gcloud -ErrorAction SilentlyContinue) {
    Write-Host "[cost_guard] GCP: gcloud detectado" -ForegroundColor Cyan
    $vms = Get-GcpItems -Label "GCP compute RUNNING" -GcloudArgs @(
        "compute", "instances", "list", "--filter=status=RUNNING", "--format=json(name)"
    )
    if ($vms.Count -gt 0) { $gcpLeaks += $vms.Count; $leaks.Add("GCP compute RUNNING: $($vms.Count)") }
    $gke = Get-GcpItems -Label "GKE clusters" -GcloudArgs @(
        "container", "clusters", "list", "--format=json(name)"
    )
    if ($gke.Count -gt 0) {
        Write-Host "[cost_guard]   GKE: $($gke.Count) cluster(s) (apagar tras el benchmark)" -ForegroundColor Yellow
    }
} else {
    Write-Host "[cost_guard] AVISO: gcloud no esta en el PATH; se omite el lado GCP (AWS sigue valido)." -ForegroundColor Yellow
}

# --- 4. Veredicto ----------------------------------------------------------
Write-Host "[cost_guard] recursos ECS RUNNING (informativos): $ecsRunning"
if ($unverified.Count -gt 0) {
    Write-Host "[cost_guard] AVISO: $($unverified.Count) barrido(s) NO verificados (permisos/CLI): $($unverified -join ', ')" -ForegroundColor DarkYellow
    Write-Host "[cost_guard]        revisar la politica least-privilege del usuario de despliegue (infra/aws/iam.tf, statement CostGuardrails)." -ForegroundColor DarkYellow
}
if ($leaks.Count -gt 0) {
    Write-Host "[cost_guard] FUGAS de coste detectadas:" -ForegroundColor Red
    foreach ($l in $leaks) { Write-Host "[cost_guard]   - $l" -ForegroundColor Red }
    exit 1
}
if ($overBudgetDay) {
    Write-Host "[cost_guard] AVISO: algun dia supero $DailyBudgetUsd USD (revisar)." -ForegroundColor Red
    exit 1
}
Write-Host "[cost_guard] OK: sin fugas y dentro de $DailyBudgetUsd USD/dia." -ForegroundColor Green
exit 0
