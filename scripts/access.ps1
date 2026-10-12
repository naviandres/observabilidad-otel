<#
.SYNOPSIS
    Acceso temporal al task ECS Fargate: regla ingress /32 en el Security
    Group del operador (verbos Grant / Revoke).
.DESCRIPTION
    DESVIACION DOCUMENTADA DEL PLAN (seccion "puertos via tunel SSM"):

      El PLAN asumia port-forwarding SSM Session Manager hacia la task
      (AWS-StartPortForwardingSession*). En la sesion viva del 2026-10-12
      `aws ssm start-session` RECHAZO todos los formatos de target ECS
      probados (taskId plano, ecs:cluster:task, ecs-cluster-service-task,
      runtimeId, ARN) con los documentos AWS-StartPortForwardingSessionFor-
      RemoteSession / ForECS / ForRemoteHost: "Invalid target ID format".
      La documentacion actual de ECS Exec solo soporta execute-command
      (shell), NO reenvio de puertos hacia tasks Fargate.

      Este helper es el sustituto real: mientras dura la sesion del operador
      anade una regla ingress TEMPORAL limitada al /32 de SU IP publica;
      al terminar se revoca. Steady-state: el Security Group sigue SIN
      ninguna regla de ingress (R-10/PLAN 3.2 intacto). La regla vive solo
      durante la sesion.

    Puertos por defecto: 8000 (service-a, endpoint de verificacion) y 8889
    (exporter Prometheus del collector, lecturas A8/A9). La IP publica de la
    task NO sale en `ecs describe-tasks`: se resuelve por el ENI de la task
    (`ec2 describe-network-interfaces` -> Association.PublicIp), verificado
    en vivo 2026-10-12.
.PARAMETER Action
    Grant: anade la regla /32 y persiste los SecurityGroupRuleId creados
    (archivo de sesion en $env:TEMP). Revoke: revoca por
    --security-group-rule-ids (archivo de sesion + discovery en vivo via
    describe-security-group-rules) y VERIFICA despues que el SG muestra
    IpPermissions [] (0 ingress).
.PARAMETER Region
    Region AWS (default: us-east-1).
.PARAMETER Profile
    Perfil de credenciales AWS (default: otel-lab).
.PARAMETER SgName
    Nombre del Security Group de la task (default: otel-lab-task, el que
    crea `make aws-net-create`).
.PARAMETER Ports
    Puertos TCP a abrir (default: 8000, 8889).
.PARAMETER OperatorIp
    IPv4 del operador; si falta se autodetecta con https://api4.ipify.org.
.PARAMETER Cluster
    Cluster ECS para imprimir el endpoint descubierto (default: otel-lab).
.PARAMETER Service
    Service ECS para imprimir el endpoint descubierto (default: otel-lab).
.EXAMPLE
    .\scripts\access.ps1 -Action Grant
    # ... correr verify_correlation.py contra la IP publica de la task ...
    .\scripts\access.ps1 -Action Revoke
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Grant", "Revoke")]
    [string]$Action,
    [string]$Region = "us-east-1",
    [string]$Profile = "otel-lab",
    [string]$SgName = "otel-lab-task",
    [int[]]$Ports = @(8000, 8889),
    [string]$OperatorIp,
    [string]$Cluster = "otel-lab",
    [string]$Service = "otel-lab"
)

$ErrorActionPreference = "Stop"
$Marker = "otel-lab-temp-session"

function Write-DeviationNotice {
    Write-Host ""
    Write-Host "[access] DESVIACION DOCUMENTADA DEL PLAN (puertos via tunel SSM):" -ForegroundColor Yellow
    Write-Host "[access]   SSM NO soporta port-forwarding a tasks Fargate ('Invalid" -ForegroundColor Yellow
    Write-Host "[access]   target ID format' en todos los documentos/targets ECS," -ForegroundColor Yellow
    Write-Host "[access]   verificado en vivo 2026-10-12; ECS Exec solo da shell)." -ForegroundColor Yellow
    Write-Host "[access]   steady-state = SG SIN ingress; esta regla /32 vive SOLO" -ForegroundColor Yellow
    Write-Host "[access]   durante la sesion y se revoca con -Action Revoke." -ForegroundColor Yellow
    Write-Host ""
}

function Invoke-Aws {
    param([string[]]$AwsArgs)
    & aws --region $Region --profile $Profile @AwsArgs
}

function Get-SecurityGroupId {
    $sg = Invoke-Aws @(
        "ec2", "describe-security-groups",
        "--filters", "Name=tag:Name,Values=$SgName",
        "--query", "SecurityGroups[0].GroupId", "--output", "text"
    )
    if (-not $sg -or $sg -eq "None") {
        Write-Error "[access] no existe el SG taggeado Name=$SgName. Crea la red: make aws-net-create"
        exit 1
    }
    return $sg
}

function Get-OperatorIpv4 {
    param([string]$Override)
    if ($Override) { $ip = $Override.Trim() }
    else {
        Write-Host "[access] autodetectando IPv4 del operador (api4.ipify.org)..." -ForegroundColor Cyan
        $ip = (Invoke-RestMethod -Uri "https://api4.ipify.org" -UseBasicParsing).Trim()
    }
    if ($ip -notmatch '^\d{1,3}(\.\d{1,3}){3}$') {
        Write-Error "[access] IPv4 invalida: '$ip' (pasa -OperatorIp manualmente)"
        exit 1
    }
    return $ip
}

function Write-JsonArgFile {
    param([string]$Json)
    # El CLI acepta estructuras via file:// (evita el quote-mangling de los
    # argumentos nativos en Windows PowerShell 5.1).
    $path = Join-Path $env:TEMP ("otel-lab-sg-" + [guid]::NewGuid().ToString("N") + ".json")
    Set-Content -Path $path -Value $Json -Encoding ascii -NoNewline
    return "file:///" + ($path -replace '\\', '/')
}

function ConvertTo-JsonArray {
    param($Object)
    # PS 5.1: ConvertTo-Json de un array de 1 elemento devuelve el objeto suelto.
    $json = ConvertTo-Json -Depth 8 -Compress -InputObject $Object
    if ($json.TrimStart().StartsWith("[")) { return $json }
    return "[$json]"
}

function Get-TaskPublicIp {
    # La IP publica del ENI de la task (describe-tasks NO la lista; sale en
    # describe-network-interfaces -> Association.PublicIp, verificado en vivo).
    $taskArn = Invoke-Aws @(
        "ecs", "list-tasks", "--cluster", $Cluster, "--service-name", $Service,
        "--desired-status", "RUNNING", "--query", "taskArns[0]", "--output", "text"
    )
    if (-not $taskArn -or $taskArn -eq "None") { return $null }
    $eni = Invoke-Aws @(
        "ecs", "describe-tasks", "--cluster", $Cluster, "--tasks", $taskArn,
        "--query", "tasks[0].attachments[0].details[?Name=='networkInterfaceId'].Value | [0]",
        "--output", "text"
    )
    if (-not $eni -or $eni -eq "None") { return $null }
    $ip = Invoke-Aws @(
        "ec2", "describe-network-interfaces", "--network-interface-ids", $eni,
        "--query", "NetworkInterfaces[0].Association.PublicIp", "--output", "text"
    )
    if (-not $ip -or $ip -eq "None") { return $null }
    return $ip
}

function Get-RuleSessionFile {
    param([string]$SgId)
    Join-Path $env:TEMP ("otel-lab-sg-rules-" + $SgId + ".txt")
}

function Get-TempRuleIds {
    # describe-security-group-rules es la forma ESTABLE: cada regla expone
    # SecurityGroupRuleId, Description y CidrIpv4 a nivel de la regla. La
    # descripcion NO vive a nivel de IpPermissions en describe-security-groups
    # (vive dentro de IpRanges[]), por eso el viejo filtro
    # IpPermissions[?Description==...] nunca coincidia (bug D1, review ronda 2).
    param([string]$SgId, [string]$Cidr = "")
    $query = if ($Cidr) {
        "SecurityGroupRules[?CidrIpv4=='$Cidr'].SecurityGroupRuleId"
    } else {
        "SecurityGroupRules[?ends_with(CidrIpv4, '/32')].SecurityGroupRuleId"
    }
    for ($i = 0; $i -lt 5; $i++) {
        $out = Invoke-Aws @(
            "ec2", "describe-security-group-rules", "--group-ids", $SgId,
            "--filters", "Name=egress,Values=false", "Name=description,Values=$Marker",
            "--query", $query, "--output", "text"
        )
        $ids = @($out -split '\s+' | Where-Object { $_ -match '^sgr-[0-9a-f]+$' })
        if ($ids.Count -gt 0) { return $ids }
        Start-Sleep -Seconds 2
    }
    return @()
}

$sgId = Get-SecurityGroupId

if ($Action -eq "Grant") {
    Write-DeviationNotice
    $ip = Get-OperatorIpv4 -Override $OperatorIp
    Write-Host "[access] SG=$sgId <- ingress TCP /32 de $ip para puertos [$($Ports -join ',')]" -ForegroundColor Cyan
    $permissions = @(
        foreach ($port in $Ports) {
            @{
                IpProtocol = "tcp"
                FromPort   = $port
                ToPort     = $port
                IpRanges   = @(@{ CidrIp = "$ip/32"; Description = $Marker })
            }
        }
    )
    $fileArg = Write-JsonArgFile -Json (ConvertTo-JsonArray $permissions)
    Invoke-Aws @("ec2", "authorize-security-group-ingress", "--group-id", $sgId, "--ip-permissions", $fileArg) | Out-Null
    Remove-Item ($fileArg -replace '^file:///', '') -ErrorAction SilentlyContinue
    Write-Host "[access] regla TEMPORAL aplicada (description=$Marker)." -ForegroundColor Green
    # Persistir los SecurityGroupRuleId creados: Revoke los usa como contrato
    # estable (la description no es filtrable a nivel de IpPermissions).
    $grantIds = Get-TempRuleIds -SgId $sgId -Cidr "$ip/32"
    if ($grantIds.Count -gt 0) {
        Set-Content -Path (Get-RuleSessionFile -SgId $sgId) -Value $grantIds -Encoding ascii
        Write-Host "[access] rule-ids persistidos para Revoke: $($grantIds -join ' ')" -ForegroundColor Cyan
    } else {
        Write-Host "[access] AVISO: no se descubrieron rule-ids; Revoke usara discovery en vivo." -ForegroundColor Yellow
    }
    try {
        $taskIp = Get-TaskPublicIp
        if ($taskIp) {
            Write-Host "[access] endpoint de la sesion:  http://$taskIp:8000  (service-a)" -ForegroundColor Cyan
            Write-Host "[access] scrape Prometheus A8/A9: http://$taskIp:8889/metrics" -ForegroundColor Cyan
        } else {
            Write-Host "[access] no hay task RUNNING todavia; reintentar la lectura del ENI tras aws-start." -ForegroundColor Yellow
        }
    } catch {
        Write-Host "[access] discovery del ENI fallo: $_" -ForegroundColor Yellow
    }
    Write-Host "[access] AL TERMINAR LA SESION: .\scripts\access.ps1 -Action Revoke" -ForegroundColor Yellow
    exit 0
}

# --- Revoke ---------------------------------------------------------------
Write-Host "[access] revocando reglas /32 temporales (description=$Marker) del SG $sgId..." -ForegroundColor Cyan
# Revocar por SecurityGroupRuleId (forma estable del CLI): los ids persistidos
# por GRANT en el archivo de sesion + discovery en vivo por
# describe-security-group-rules. NO se filtra IpPermissions[?Description==...]:
# la description vive dentro de IpRanges[] y ese filtro nunca coincidia
# (bug D1, review ronda 2: la regla /32 sobrevivia a la sesion -> R-10 roto).
$sessionFile = Get-RuleSessionFile -SgId $sgId
$ruleIds = @()
if (Test-Path $sessionFile) {
    $ruleIds += @(Get-Content $sessionFile | ForEach-Object { $_.Trim() } |
        Where-Object { $_ -match '^sgr-[0-9a-f]+$' })
}
$ruleIds += @(Get-TempRuleIds -SgId $sgId)
$ruleIds = @($ruleIds | Select-Object -Unique)
if ($ruleIds.Count -gt 0) {
    $revokeArgs = @("ec2", "revoke-security-group-ingress", "--security-group-rule-ids") + $ruleIds
    Invoke-Aws $revokeArgs | Out-Null
    Write-Host "[access] revocado por rule-id: $($ruleIds -join ' ')" -ForegroundColor Green
} else {
    Write-Host "[access] no queda ninguna regla temporal propia que revocar." -ForegroundColor Green
}
Remove-Item $sessionFile -ErrorAction SilentlyContinue
# Verificacion post-revoke: describe-security-groups debe mostrar IpPermissions []
$left = Invoke-Aws @(
    "ec2", "describe-security-groups", "--group-ids", $sgId,
    "--query", 'length(SecurityGroups[0].IpPermissions)', "--output", "text"
)
if ($left -ne "0") {
    Write-Host "[access] ERROR: quedan $left regla(s) de ingress en $sgId (esperado 0)." -ForegroundColor Red
    exit 1
}
Write-Host "[access] revocado y VERIFICADO: IpPermissions = [] (0 ingress, R-10 intacto)." -ForegroundColor Green
exit 0
