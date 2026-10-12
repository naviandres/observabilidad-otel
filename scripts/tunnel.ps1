<#
.SYNOPSIS
    Tunel SSM (ECS Exec) hacia el service Fargate sin ingress publico (R-10).
.DESCRIPTION
    Descubre la task RUNNING del service y abre una sesion de port-forwarding
    por cada puerto del vector -Ports hacia localhost. No abre ningun puerto
    en el Security Group: todo viaja por AWS SSM Session Manager (requiere el
    session-manager-plugin en el PATH). Parar = Ctrl+C; las sesiones mueren.
.PARAMETER Cluster
    Nombre del cluster ECS (default: otel-lab).
.PARAMETER Service
    Nombre del service ECS (default: otel-lab).
.PARAMETER Region
    Region AWS (default: us-east-1).
.PARAMETER Profile
    Perfil de credenciales AWS (default: otel-lab).
.PARAMETER Ports
    Puertos locales == puertos de contenedor (default: 8000,8001,4317).
.EXAMPLE
    .\scripts\tunnel.ps1 -Ports 8000,8001,4317
#>
[CmdletBinding()]
param(
    [string]$Cluster = "otel-lab",
    [string]$Service = "otel-lab",
    [string]$Region = "us-east-1",
    [string]$Profile = "otel-lab",
    [int[]]$Ports = @(8000, 8001, 4317)
)

$ErrorActionPreference = "Stop"
$aws = "aws"

Write-Host "[tunnel] cluster=$Cluster service=$Service region=$Region" -ForegroundColor Cyan

# 1) task RUNNING del service
$taskArn = & $aws --region $Region --profile $Profile ecs list-tasks `
    --cluster $Cluster --service-name $Service --desired-status RUNNING `
    --query "taskArns[0]" --output text 2>$null
if (-not $taskArn -or $taskArn -eq "None") {
    Write-Error "[tunnel] no hay task RUNNING. Ejecuta primero: make aws-start"
    exit 1
}
$taskId = ($taskArn -split "/")[-1]

# 2) runtimeId del container service-a: identifica la sesion SSM de ECS
$runtimeId = & $aws --region $Region --profile $Profile ecs describe-tasks `
    --cluster $Cluster --tasks $taskArn `
    --query "tasks[0].containers[?name=='service-a'].runtimeId | [0]" `
    --output text 2>$null
if (-not $runtimeId -or $runtimeId -eq "None") {
    Write-Error "[tunnel] no se pudo resolver el runtimeId (service-a no RUNNING todavia)"
    exit 1
}

$target = "ecs:{0}_{1}_{2}" -f $Cluster, $taskId, $runtimeId
Write-Host "[tunnel] target SSM: $target" -ForegroundColor Cyan
Write-Host "[tunnel] abriendo port-forwards (Ctrl+C para cerrar todo):" -ForegroundColor Cyan

$jobs = @()
foreach ($port in $Ports) {
    Write-Host ("[tunnel]   localhost:{0} -> {1}:{0}" -f $port, $(if ($port -eq 4317) { "otel-collector" } else { "service" }))
    $args = @(
        "--region", $Region, "--profile", $Profile,
        "ssm", "start-session",
        "--target", $target,
        "--document-name", "AWS-StartPortForwardingSessionForECS",
        "--parameters", ("{{`"portNumber`":[`"{0}`"],`"localPortNumber`":[`"{0}`"]}}" -f $port)
    )
    $jobs += Start-Job -ScriptBlock {
        param($a, $p)
        & $a @p
    } -ArgumentList $aws, $args
}

try {
    Wait-Job -Job $jobs | Out-Null
    Receive-Job -Job $jobs -Keep
} finally {
    foreach ($j in $jobs) { Remove-Job -Job $j -Force -ErrorAction SilentlyContinue }
    Write-Host "[tunnel] sesiones cerradas" -ForegroundColor Yellow
}

# Fallback sin jobs (terminal primera parte): imprime los comandos crudos.
Write-Host "`n[tunnel] si las sesiones fallaron, ejecuta en terminales separadas:"
foreach ($port in $Ports) {
    $p = ("{{`"portNumber`":[{0}],`"localPortNumber`":[{0}]}}" -f $port)
    Write-Host ("  aws --region {0} --profile {1} ssm start-session --target {2} " +
        "--document-name AWS-StartPortForwardingSessionForECS --parameters '{3}'" -f `
        $Region, $Profile, $target, $p)
}