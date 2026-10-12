<#
.SYNOPSIS
    Tunel SSM (ECS Exec) hacia el service Fargate sin ingress publico (R-10).
.DESCRIPTION
    Descubre la task RUNNING del service y abre una sesion de port-forwarding
    por cada puerto del vector -Ports hacia localhost. No abre ningun puerto
    en el Security Group: todo viaja por AWS SSM Session Manager (requiere el
    session-manager-plugin en el PATH). Parar = Ctrl+C; las sesiones mueren.

    Dos modos:
      EcsExec (por defecto, el original): usa el documento
        AWS-StartPortForwardingSessionForECS contra el target
        ecs:<cluster>_<task>_<runtimeId> y rellena -Ports (8000,8001,4317).
      Task: usa el documento AWS-StartPortForwardingSessionForRemoteSession
        contra el taskId plano de la task (la task entera comparte el netns
        awsvpc, asi que alcanza CUALQUIER puerto de CUALQUIER contenedor de
        la task, no solo los declarados en portMappings). Rellena -TaskPorts
        (por defecto 8000 = service-a y 8889 = exporter prometheus del
        collector, necesario para las lecturas Prometheus de la verificacion
        de correlacion).
.PARAMETER Cluster
    Nombre del cluster ECS (default: otel-lab).
.PARAMETER Service
    Nombre del service ECS (default: otel-lab).
.PARAMETER Region
    Region AWS (default: us-east-1).
.PARAMETER Profile
    Perfil de credenciales AWS (default: otel-lab).
.PARAMETER Ports
    Puertos locales == puertos de contenedor para el modo EcsExec
    (default: 8000,8001,4317).
.PARAMETER TaskPorts
    Puertos locales == puertos de contenedor para el modo Task
    (default: 8000,8889).
.PARAMETER Mode
    EcsExec | Task (default: EcsExec).
.EXAMPLE
    .\scripts\tunnel.ps1 -Ports 8000,8001,4317
.EXAMPLE
    .\scripts\tunnel.ps1 -Mode Task -TaskPorts 8000,8889
#>
[CmdletBinding()]
param(
    [string]$Cluster = "otel-lab",
    [string]$Service = "otel-lab",
    [string]$Region = "us-east-1",
    [string]$Profile = "otel-lab",
    [ValidateSet("EcsExec", "Task")]
    [string]$Mode = "EcsExec",
    [int[]]$Ports = @(8000, 8001, 4317),
    [int[]]$TaskPorts = @(8000, 8889)
)

$ErrorActionPreference = "Stop"
$aws = "aws"

if ($Mode -eq "Task") {
    $vector = $TaskPorts
    $document = "AWS-StartPortForwardingSessionForRemoteSession"
} else {
    $vector = $Ports
    $document = "AWS-StartPortForwardingSessionForECS"
}

Write-Host "[tunnel] cluster=$Cluster service=$Service region=$Region mode=$Mode" -ForegroundColor Cyan

# 1) task RUNNING del service
$taskArn = & $aws --region $Region --profile $Profile ecs list-tasks `
    --cluster $Cluster --service-name $Service --desired-status RUNNING `
    --query "taskArns[0]" --output text 2>$null
if (-not $taskArn -or $taskArn -eq "None") {
    Write-Error "[tunnel] no hay task RUNNING. Ejecuta primero: make aws-start"
    exit 1
}
$taskId = ($taskArn -split "/")[-1]

if ($Mode -eq "Task") {
    # El documento de remote session toma el taskId plano: la sesion cae en
    # el netns awsvpc de la task (todos los contenedores comparten localhost).
    $target = $taskId
} else {
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
}

Write-Host "[tunnel] target SSM: $target" -ForegroundColor Cyan
Write-Host "[tunnel] abriendo port-forwards (Ctrl+C para cerrar todo):" -ForegroundColor Cyan

function Get-SessionParameters {
    param([int]$Port, [string]$DocumentName)
    if ($DocumentName -eq "AWS-StartPortForwardingSessionForRemoteSession") {
        # Forma documentada para este documento: clave=valor simple.
        return "portNumber=$Port,localPortNumber=$Port"
    }
    # Forma JSON (listas de un elemento) que ya usaba el modo EcsExec.
    return ("{{`"portNumber`":[`"{0}`"],`"localPortNumber`":[`"{0}`"]}}" -f $Port)
}

$jobs = @()
foreach ($port in $vector) {
    Write-Host ("[tunnel]   localhost:{0} -> task:{0} ({1})" -f $port, $document)
    $args = @(
        "--region", $Region, "--profile", $Profile,
        "ssm", "start-session",
        "--target", $target,
        "--document-name", $document,
        "--parameters", (Get-SessionParameters -Port $port -DocumentName $document)
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
foreach ($port in $vector) {
    $p = Get-SessionParameters -Port $port -DocumentName $document
    Write-Host ("  aws --region {0} --profile {1} ssm start-session --target {2} " +
        "--document-name {3} --parameters '{4}'" -f `
        $Region, $Profile, $target, $document, $p)
}
