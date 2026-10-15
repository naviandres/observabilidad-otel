<#
.SYNOPSIS
    Aplica (y verifica) las alarmas CloudWatch del panel AWS-native desde
    dashboards/cloudwatch-alarms.json.
.DESCRIPTION
    Idempotente: put-metric-alarm reemplaza la definicion existente con el
    mismo AlarmName. Las alarmas leen UNICAMENTE los contadores EMF que el
    collector publica en el namespace OtelLab (lista blanca de 4 metricas,
    decision de coste PLAN 3.4 C1); no introducen metricas nuevas ni usan
    GetMetricData (R-05). Si se pasa -SnsTopicArn (output `alerts_topic_arn`
    del IaC (fase 5)), se inyecta como AlarmActions en cada alarma; sin el,
    AlarmActions queda como venga en el JSON (vacio) y la notificacion es
    responsabilidad de quien invoque el script (p. ej. `make aws-alarms
    SNS_TOPIC_ARN=<arn>`).

    NO re-serializa con ConvertFrom-Json/ConvertTo-Json: en Windows
    PowerShell 5.1 ese round-trip corrompe el JSON (colapsa arrays de un
    elemento, reordena) y aws CLI lo rechaza con "Error parsing parameter
    'cli-input-json': Invalid JSON received" (bug verificado en la mini-
    sesion AWS). En su lugar parte el array con python (el .venv del
    entregable si existe, si no python/python3 del PATH) y escribe cada
    alarma a un temp file, aplicandolo con --cli-input-json file://<tmp>.

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
.PARAMETER SnsTopicArn
    ARN del topic SNS de alertas (output `alerts_topic_arn` de infra/aws). Si
    se pasa, se inyecta como AlarmActions en cada alarma, cerrando la deuda del
    guardrail (el topic lo crea el IaC). Vacio = AlarmActions tal
    como venga en el JSON (comportamiento previo).
.PARAMETER DryRun
    Prepara los JSON de alarma (con AlarmActions si se paso -SnsTopicArn) y los
    imprime SIN llamar a AWS. Util para verificar el cableado del topic sin
    sesion de nube.
.PARAMETER Help
    Imprime esta ayuda (comment-based help) y termina. Util en CI/verificacion.
.EXAMPLE
    pwsh -NoProfile -File scripts/cloudwatch_alarms.ps1
    powershell -NoProfile -File scripts/cloudwatch_alarms.ps1
    powershell -NoProfile -File scripts/cloudwatch_alarms.ps1 -SnsTopicArn arn:aws:sns:us-east-1:123:otel-lab-alerts
    powershell -NoProfile -File scripts/cloudwatch_alarms.ps1 -SnsTopicArn arn:aws:sns:us-east-1:123:otel-lab-alerts -DryRun
    make aws-alarms SNS_TOPIC_ARN=arn:aws:sns:us-east-1:123:otel-lab-alerts
#>
[CmdletBinding()]
param(
    [string]$Region = "us-east-1",
    [string]$Profile = "otel-lab",
    [string]$File = "dashboards/cloudwatch-alarms.json",
    [string]$SnsTopicArn = "",
    [switch]$DryRun,
    [switch]$Help
)

if ($Help) {
    Get-Help $PSCommandPath -Full | Out-String | Write-Host
    exit 0
}

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $File)) {
    Write-Error "no existe el archivo de alarmas: $File (ejecutar desde la raiz del repo)"
}

# Interpreter python: .venv del entregable primero, PATH despues (win/linux).
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = $null
foreach ($cand in @(
    (Join-Path $repoRoot ".venv\Scripts\python.exe"),
    (Join-Path $repoRoot ".venv/bin/python"),
    "python", "python3"
)) {
    if ($cand -match '[\\/]' -or $cand -match '\.') {
        if (Test-Path -LiteralPath $cand) { $python = $cand; break }
    } else {
        $cmd = Get-Command $cand -ErrorAction SilentlyContinue
        if ($cmd) { $python = $cmd.Source; break }
    }
}
if (-not $python) {
    Write-Error "python no encontrado (.venv del entregable ni PATH): imprescindible para partir el JSON sin re-serializar"
}

# Partir el array: un temp file por alarma, JSON virgen escrito por python
# (json.dump), nunca por ConvertTo-Json de PS 5.1.
$tmpDir = Join-Path ([System.IO.Path]::GetTempPath()) ("otel-lab-alarms-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tmpDir | Out-Null

$splitScript = @'
import json, os, sys
src, outdir = sys.argv[1], sys.argv[2]
topic = os.environ.get("OTEL_SNS_TOPIC", "").strip()
with open(src, encoding="utf-8") as fh:
    alarms = json.load(fh)
if not isinstance(alarms, list):
    sys.exit("el JSON de alarmas debe ser un array")
for i, alarm in enumerate(alarms):
    if topic:
        alarm["AlarmActions"] = [topic]
    path = os.path.join(outdir, "alarm-%d.json" % i).replace("\\", "/")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(alarm, fh)
    print("%s\t%s" % (alarm["AlarmName"], path))
'@

try {
    # El codigo python va en un ARCHIVO temporal: pasar multilineas con
    # comillas dobles via -c a un exe nativo desde PS 5.1 corrompe el quoting.
    $splitPy = Join-Path $tmpDir "split_alarms.py"
    # UTF8 SIN BOM: Set-Content -Encoding UTF8 de PS 5.1 escribe BOM y python
    # lo rechaza (SyntaxError).
    [System.IO.File]::WriteAllText($splitPy, $splitScript, (New-Object System.Text.UTF8Encoding($false)))
    # El topic SNS (si se paso) viaja por entorno al splitter python, que
    # inyecta AlarmActions en cada alarma.
    $env:OTEL_SNS_TOPIC = $SnsTopicArn
    $splitOut = & $python $splitPy $File $tmpDir
    if ($LASTEXITCODE -ne 0 -or -not $splitOut) {
        Write-Error "fallo al partir el JSON de alarmas con python (exit $LASTEXITCODE)"
    }
    $entries = @($splitOut | ForEach-Object { $n, $p = $_ -split "`t"; [pscustomobject]@{ Name = $n; Path = $p } })

    if ($DryRun) {
        # Verificacion del cableado SIN AWS: imprime el AlarmActions preparado de
        # cada alarma (leer el JSON con ConvertFrom-Json es seguro; el bug de
        # re-serializacion era de ConvertTo-Json).
        foreach ($entry in $entries) {
            $obj = Get-Content -LiteralPath $entry.Path -Raw | ConvertFrom-Json
            $actions = @($obj.AlarmActions)
            Write-Host "[alarms] (dry-run) $($entry.Name) AlarmActions=[$($actions -join ', ')]"
        }
        Write-Host "[alarms] dry-run: $($entries.Count) alarmas preparadas; no se llama a AWS." -ForegroundColor Cyan
        return
    }

    Write-Host "[alarms] aplicando $($entries.Count) alarmas (idempotente, put-metric-alarm)"

    foreach ($entry in $entries) {
        & aws --region $Region --profile $Profile cloudwatch put-metric-alarm `
            --cli-input-json "file://$($entry.Path)"
        if ($LASTEXITCODE -ne 0) {
            Write-Error "put-metric-alarm fallo para $($entry.Name) (exit $LASTEXITCODE)"
        }
        Write-Host "[alarms] OK $($entry.Name)"
    }

    # Verificacion: las alarmas deben existir con el umbral definido.
    # Ojo: el miembro de salida de describe-alarms es MetricAlarms (no
    # AlarmStates); ademas --query sobre una lista vacia imprime la cadena
    # "null", que en PS es truthy: hay que compararla explicitamente.
    foreach ($entry in $entries) {
        $state = & aws --region $Region --profile $Profile cloudwatch describe-alarms `
            --alarm-names $entry.Name `
            --query "MetricAlarms[0].{name:AlarmName,threshold:Threshold,state:StateValue}" `
            --output json
        if ($LASTEXITCODE -ne 0 -or -not $state -or $state.Trim() -eq "null") {
            Write-Error "verificacion fallo: $($entry.Name) no aparece en describe-alarms"
        }
        Write-Host "[alarms] verificada $state"
    }

    if ($SnsTopicArn) {
        Write-Host "[alarms] listo: $($entries.Count) alarmas en $Region -> AlarmActions=$SnsTopicArn" -ForegroundColor Green
    } else {
        Write-Host "[alarms] listo: $($entries.Count) alarmas en $Region (sin AlarmActions; pasa -SnsTopicArn para cablearlas al topic)" -ForegroundColor Yellow
    }
} finally {
    Remove-Item -LiteralPath $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
}
