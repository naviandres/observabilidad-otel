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
    Grant: anade la regla /32, captura los SecurityGroupRuleId de la propia
    respuesta de authorize-security-group-ingress (patron sgr-<hex> sobre el
    texto crudo; los nombres reales del alambre Query son
    securityGroupRuleSet[].securityGroupRuleId, verificado contra modelos
    botocore) y persiste IP + puertos + rule-ids en un archivo de sesion en
    $env:TEMP. Revoke: revoca de forma DETERMINISTA y
    SIMETRICA al grant con revoke-security-group-ingress --ip-permissions
    (IpProtocol/FromPort/ToPort/IpRanges=[{CidrIp=<ip>/32}]); la IP se lee
    SIEMPRE del archivo de sesion de Grant (si falta, se recalcula y se avisa);
    los rule-ids del archivo son SOLO una optimizacion (revoke por
    --security-group-rule-ids si existen). Despues VERIFICA que el SG muestra
    IpPermissions [] (0 ingress, R-10) y sale con error si quedan reglas.
    NO hay discovery por la llamada describe-security-group-rules: no acepta
    --group-ids y los filtros server-side de direccion y description los
    rechaza la API (InvalidParameterValue, verificado en vivo por el revisor,
    ronda 3).
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
    # argumentos nativos en Windows PowerShell 5.1). OJO: botocore recorta
    # "file://" (7 chars) y usa el resto como ruta LITERAL; la forma URI
    # "file:///C:/..." produce "/C:/..." -> WinError 22 (verificado en vivo
    # 2026-10-12 con --dry-run). En Windows se pasa "file://C:/...".
    $path = Join-Path $env:TEMP ("otel-lab-sg-" + [guid]::NewGuid().ToString("N") + ".json")
    Set-Content -Path $path -Value $Json -Encoding ascii -NoNewline
    return "file://" + ($path -replace '\\', '/')
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

function Get-SessionFilePath {
    param([string]$SgId)
    Join-Path $env:TEMP ("otel-lab-sg-session-" + $SgId + ".txt")
}

function Invoke-AwsBestEffort {
    # Para llamadas cuyo fallo debe inspeccionarse (revoke tolera
    # InvalidPermission.NotFound). En PS 5.1, stderr de un comando nativo con
    # ErrorActionPreference=Stop puede abortar al redirigir 2>&1: se relaja
    # temporalmente y se devuelve codigo + texto capturado.
    param([string[]]$AwsArgs)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $out = & aws --region $Region --profile $Profile @AwsArgs 2>&1 | Out-String
    $code = $LASTEXITCODE
    $ErrorActionPreference = $prev
    return [pscustomobject]@{ Code = $code; Text = $out.Trim() }
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
    $resp = Invoke-Aws @("ec2", "authorize-security-group-ingress", "--group-id", $sgId, "--ip-permissions", $fileArg)
    if ($LASTEXITCODE -ne 0) {
        Write-Error "[access] authorize-security-group-ingress fallo (exit $LASTEXITCODE): $($resp -join ' ')"
        exit 1
    }
    Remove-Item ($fileArg -replace '^file://', '') -ErrorAction SilentlyContinue
    Write-Host "[access] regla TEMPORAL aplicada (description=$Marker)." -ForegroundColor Green
    # Persistir IP + puertos + los SecurityGroupRuleId de la PROPIA respuesta de
    # authorize. NO se descubren con la llamada describe-security-group-rules:
    # no acepta --group-ids y rechaza los filtros server-side de direccion y
    # description (InvalidParameterValue verificado en vivo, review ronda 3 ->
    # bug D1/N1).
    # Extraccion por patron 'sgr-<hex>' sobre el texto crudo: robusta ante los
    # nombres reales del alambre Query (securityGroupRuleSet[].securityGroupRuleId,
    # verificado contra los modelos botocore de aws-cli 2.32.31).
    $grantIds = @()
    try {
        $ruleRe = [regex]'sgr-[0-9a-f]+'
        $grantIds = @($ruleRe.Matches(($resp -join "")) |
            ForEach-Object { $_.Value } | Select-Object -Unique)
    } catch {
        Write-Host "[access] AVISO: no se pudo leer la respuesta de authorize; Revoke usara ip-permissions simetrico igualmente." -ForegroundColor Yellow
    }
    $sessionLines = @("ip=$ip") + @($Ports | ForEach-Object { "port=$_" }) + @($grantIds | ForEach-Object { "rule=$_" })
    Set-Content -Path (Get-SessionFilePath -SgId $sgId) -Value $sessionLines -Encoding ascii
    if ($grantIds.Count -gt 0) {
        Write-Host "[access] sesion persistida (ip=$ip, rule-ids: $($grantIds -join ' '))." -ForegroundColor Cyan
    } else {
        Write-Host "[access] sesion persistida (ip=$ip); sin rule-ids en la respuesta: Revoke ira por ip-permissions simetrico." -ForegroundColor Yellow
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
# Revocacion DETERMINISTA, sin discovery: la forma clasica de
# revoke-security-group-ingress --ip-permissions es SIMETRICA al grant
# (el matching es por protocolo/puerto/CidrIp; la description no participa).
# La IP se lee SIEMPRE del archivo de sesion de Grant; si no hay archivo se
# recalcula y se avisa de que podria diferir de la del grant. Los
# SecurityGroupRuleId persistidos por Grant se usan SOLO como optimizacion.
Write-Host "[access] revocando reglas /32 temporales del SG $sgId..." -ForegroundColor Cyan
$sessionFile = Get-SessionFilePath -SgId $sgId
$ipFromFile = ""
$revokePorts = @($Ports)
$ruleIds = @()
if (Test-Path $sessionFile) {
    $portsFromFile = @()
    foreach ($line in @(Get-Content $sessionFile)) {
        if ($line -match '^ip=(.+)$') { $ipFromFile = $Matches[1].Trim() }
        elseif ($line -match '^port=(\d+)$') { $portsFromFile += [int]$Matches[1] }
        elseif ($line -match '^rule=(sgr-[0-9a-f]+)$') { $ruleIds += $Matches[1] }
    }
    if ($portsFromFile.Count -gt 0) { $revokePorts = $portsFromFile }
}
if ($ipFromFile) {
    if ($OperatorIp -and ($OperatorIp.Trim() -ne $ipFromFile)) {
        Write-Host "[access] AVISO: -OperatorIp ($OperatorIp) difiere de la sesion de Grant ($ipFromFile); se usa la del archivo." -ForegroundColor Yellow
    }
    $revokeIp = $ipFromFile
} else {
    Write-Host "[access] AVISO: no hay archivo de sesion de Grant; recalculando IPv4 (podria diferir de la del grant si cambio tu IP)." -ForegroundColor Yellow
    $revokeIp = Get-OperatorIpv4 -Override $OperatorIp
}
# Optimizacion (mejor esfuerzo): revocar por rule-id si Grant los persistio.
if ($ruleIds.Count -gt 0) {
    $ruleIdArgs = @("ec2", "revoke-security-group-ingress", "--security-group-rule-ids") + @($ruleIds)
    $r = Invoke-AwsBestEffort $ruleIdArgs
    if ($r.Code -ne 0) {
        Write-Host "[access] rule-ids no revocados (continua por ip-permissions): $($r.Text)" -ForegroundColor Yellow
    } else {
        Write-Host "[access] revocado por rule-id: $($ruleIds -join ' ')" -ForegroundColor Green
    }
}
# Camino garantizado: ip-permissions simetrico al grant, puerto a puerto.
foreach ($port in $revokePorts) {
    $perm = @(
        @{
            IpProtocol = "tcp"
            FromPort   = $port
            ToPort     = $port
            IpRanges   = @(@{ CidrIp = "$revokeIp/32" })
        }
    )
    $fileArg = Write-JsonArgFile -Json (ConvertTo-JsonArray $perm)
    $r = Invoke-AwsBestEffort @(
        "ec2", "revoke-security-group-ingress", "--group-id", $sgId,
        "--ip-permissions", $fileArg
    )
    Remove-Item ($fileArg -replace '^file://', '') -ErrorAction SilentlyContinue
    if ($r.Code -ne 0) {
        if ($r.Text -match 'InvalidPermission\.NotFound') {
            Write-Host "[access] no existia regla para $revokeIp/32 en el puerto $port (nada que revocar)." -ForegroundColor Green
        } else {
            Write-Error "[access] revoke-security-group-ingress fallo en el puerto $port (exit $($r.Code)): $($r.Text)"
            exit 1
        }
    } else {
        Write-Host "[access] revocado ip-permissions: tcp/$port desde $revokeIp/32." -ForegroundColor Green
    }
}
Remove-Item $sessionFile -ErrorAction SilentlyContinue
# Verificacion post-revoke (R-10): describe-security-groups debe mostrar
# IpPermissions [] (steady-state sin ingress); exit 1 si queda cualquier regla.
$left = Invoke-Aws @(
    "ec2", "describe-security-groups", "--group-ids", $sgId,
    "--query", "SecurityGroups[0].IpPermissions", "--output", "json"
)
$leftText = ($left -join "").Trim()
if (-not $leftText -or $leftText -eq "[]") {
    Write-Host "[access] revocado y VERIFICADO: IpPermissions = [] (0 ingress, R-10 intacto)." -ForegroundColor Green
    exit 0
}
if ($leftText -match [regex]::Escape("$revokeIp/32")) {
    Write-Host "[access] ERROR: la /32 del operador PERSISTE en ${sgId}: $leftText" -ForegroundColor Red
} else {
    Write-Host "[access] ERROR: quedan ingress no esperadas en $sgId (steady-state esperado []): $leftText" -ForegroundColor Red
}
exit 1
