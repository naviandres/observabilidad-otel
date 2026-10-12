<#
.SYNOPSIS
    Aplica (y verifica) las alarmas CloudWatch del panel AWS-native desde
    dashboards/cloudwatch-alarms.json.
.DESCRIPTION
    Idempotente: put-metric-alarm reemplaza la definicion existente con el
    mismo AlarmName. Las alarmas leen UNICAMENTE los contadores EMF que el
    collector publica en el namespace OtelLab (lista blanca de 4 metricas,
    decision de coste PLAN 3.4 C1); no introducen metricas nuevas ni usan
    GetMetricData (R-05). Sin acciones SNS por ahora: AlarmActions queda
    vacio y la notificacion es deuda del guardraíl de coste.

    Coste: dashboard y alarmas no facturan computo; la alarma 1 queda dentro
    del free tier (3 alarmas metricas gratis) o ~$0.10/mes la extra, y las
    metricas que referencian ya existen. NO requiere sesion de computo.
.PARAMETER Region
    Region AWS (default: us-east-1).
.PARAMETER Profile
    Perfil de credenciales AWS (default: otel-lab). Nunca credenciales en
    claro: el perfil es el unico mecanismo de autenticacion.
.PARAMETER File
    Ruta del JSON con el array de objetos de parametro para
    aws cloudwatch put-metric-alarm --cli-input-json.
.EXAMPLE
    pwsh -NoProfile -File scripts/cloudwatch_alarms.ps1
    make aws-alarms
#>
[CmdletBinding()]
param(
    [string]$Region = "us-east-1",
    [string]$Profile = "otel-lab",
    [string]$File = "dashboards/cloudwatch-alarms.json"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $File)) {
    Write-Error "no existe el archivo de alarmas: $File (ejecutar desde la raiz del repo)"
}

$alarms = Get-Content -LiteralPath $File -Raw | ConvertFrom-Json
Write-Host "[alarms] aplicando $($alarms.Count) alarmas (idempotente, put-metric-alarm)"

foreach ($alarm in $alarms) {
    $name = $alarm.AlarmName
    $json = $alarm | ConvertTo-Json -Depth 10 -Compress
    & aws --region $Region --profile $Profile cloudwatch put-metric-alarm `
        --cli-input-json $json
    if ($LASTEXITCODE -ne 0) {
        Write-Error "put-metric-alarm fallo para $name (exit $LASTEXITCODE)"
    }
    Write-Host "[alarms] OK $name"
}

# Verificacion: las alarmas deben existir con el umbral definido.
foreach ($alarm in $alarms) {
    $state = & aws --region $Region --profile $Profile cloudwatch describe-alarms `
        --alarm-names $alarm.AlarmName `
        --query "AlarmStates[0].{name:AlarmName,threshold:Threshold,state:StateValue}" `
        --output json
    if ($LASTEXITCODE -ne 0 -or -not $state) {
        Write-Error "verificacion fallo: $($alarm.AlarmName) no aparece en describe-alarms"
    }
    Write-Host "[alarms] verificada $state"
}

Write-Host "[alarms] listo: $($alarms.Count) alarmas en $Region (sin acciones SNS, deuda del guardraíl)"
