#requires -version 5.1
<#
.SYNOPSIS
    Overhead benchmark orchestrator (PLAN section 4): runs the local compose
    stack under variant A (OTel off) and variant B (OTel on, head sampling
    0.25, managed trace export off per section 3.4 C2) in ABBA order with
    3 replicas per variant, captures the k6 summary, a Prometheus snapshot of
    the measurement window and the per-cloud log-ingest cost delta per run.

.DESCRIPTION
    Methodology (documented in docs/evidence/04-overhead.md):
      * The campaign runs against the LOCAL compose stack (same binary/digest
        for A and B). k6 runs in Docker (grafana/k6 image, never a host
        binary). Prometheus local is the measuring instrument; jaeger and
        grafana stay down during the campaign (PLAN section 4.A).
      * ABBA order: A1 B1 B2 A2 A3 B3, then two attribution runs with variant
        B (sampling 1.0 and 0.0) to split span generation vs export cost.
      * Per run: 60 s stack warmup, k6 (1 min ramp warmup + 5 min steady at
        100 VU, POST /orders, no think time), 120 s cooldown, fresh DB seed
        (compose down -v between runs).
      * Cost per cloud is read from the REAL log ingest of the gated
        awscloudwatchlogs/googlecloud exporters (logs only; traces are off per
        section 3.4 C2): AWS via DescribeLogStreams storedBytes, GCP via the
        logging.googleapis.com/ingestion_bytes metric. -CloudLogs opens the
        gates on variant B runs; without it the campaign is pure-local (plan
        B: cost estimated as bytes x price in the report).
      * Cloud-side calls are READ-ONLY APIs plus the log ingest itself; no
        compute is provisioned in either cloud.

    Outputs (all under bench/, gitignored):
      bench/windows.json        run windows, hostname, image id, exit codes
      bench/k6/<run>.json       k6 --summary-export files
      bench/metrics/<run>.json  Prometheus snapshots of the window
      bench/cost/<run>.json     per-run cloud ingest deltas
      bench/logs/orchestrator.log
      bench/logs/<run>.k6.log   k6 stdout/stderr

.EXAMPLE
    powershell -File scripts/benchmark.ps1 -Mode quick
    powershell -File scripts/benchmark.ps1 -Mode full -CloudLogs
    powershell -File scripts/benchmark.ps1 -Mode attrib -CloudLogs
    powershell -File scripts/benchmark.ps1 -Mode cloudprobe
#>
[CmdletBinding()]
param(
    [ValidateSet('full', 'quick', 'attrib', 'cloudprobe')]
    [string]$Mode = 'full',
    # Open the cloud log-ingest gates on variant B runs (plan A).
    [switch]$CloudLogs,
    [int]$SteadySeconds = 300,
    [int]$WarmupSeconds = 60,
    [int]$CooldownSeconds = 120,
    # Extra wait before reading the per-run cloud ingest (billing metrics lag).
    [int]$CostSettleSeconds = 180,
    [string]$K6Image = 'grafana/k6:2.0.0',
    [string]$TargetUrl = 'http://host.docker.internal:8000/orders',
    [string]$AwsProfile = 'otel-lab',
    [string]$AwsRegion = 'us-east-1',
    [string]$GcpProject = '',
    [string]$AdcFile = '',
    [string]$BenchDir = ''
)

Set-StrictMode -Version 3.0
$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
if (-not $BenchDir) { $BenchDir = Join-Path $Root 'bench' }
$Deploy = Join-Path $Root 'deploy'
$K6Src = Join-Path $Root 'k6'
foreach ($d in @($BenchDir, (Join-Path $BenchDir 'k6'), (Join-Path $BenchDir 'metrics'), (Join-Path $BenchDir 'cost'), (Join-Path $BenchDir 'logs'))) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d | Out-Null }
}
$LogFile = Join-Path $BenchDir 'logs\orchestrator.log'
$WindowsFile = Join-Path $BenchDir 'windows.json'

# Docker Desktop mounts: forward slashes work and avoid quoting surprises.
$K6SrcMount = ($K6Src -replace '\\', '/')
$BenchK6Mount = ((Join-Path $BenchDir 'k6') -replace '\\', '/')

# GCP project: explicit param, else the gcloud default (read-only config get).
if (-not $GcpProject) {
    $GcpProject = ((& gcloud config get-value project 2>$null | Out-String).Trim())
}
# ADC file: explicit param, else the standard gcloud location (outside repo).
if (-not $AdcFile) {
    $AdcFile = Join-Path $env:APPDATA 'gcloud\application_default_credentials.json'
}
# Always exported so compose can interpolate the cloudlogs overlay even when
# only tearing down; the file itself is only MOUNTED on cloud-gated runs.
$env:BENCH_ADC_FILE = $AdcFile
$env:BENCH_GCP_PROJECT = $GcpProject
$env:BENCH_AWS_REGION = $AwsRegion

function Write-BenchLog([string]$Message) {
    $line = '{0} {1}' -f (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss'Z'"), $Message
    Add-Content -Path $LogFile -Value $line
    # Write-Host (not Write-Output) so the line never pollutes a function's
    # return value; it still reaches the console/redirected stdout.
    Write-Host $line
}

# Native tools (docker, aws, gcloud) write progress to stderr; under
# ErrorActionPreference=Stop PowerShell 5.1 turns those lines into
# terminating errors. Run native calls with EAP relaxed and judge the exit
# code explicitly instead.
function Invoke-Native {
    param([scriptblock]$Call)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & $Call
    } finally {
        $ErrorActionPreference = $prev
    }
    return [pscustomobject]@{ Output = $out; Code = $LASTEXITCODE }
}

function Invoke-Compose {
    param([string[]]$ComposeArgs, [string[]]$ExtraFiles = @())
    $files = @('-f', (Join-Path $Deploy 'docker-compose.yml'),
               '-f', (Join-Path $Deploy 'docker-compose.observability.yml'),
               '-f', (Join-Path $Deploy 'docker-compose.bench.yml'))
    foreach ($f in $ExtraFiles) { $files += @('-f', $f) }
    $all = @('compose') + $files + $ComposeArgs
    $r = Invoke-Native { & docker @all 2>&1 }
    if ($r.Code -ne 0) {
        $r.Output | ForEach-Object { Add-Content -Path $LogFile -Value ("  " + $_) }
        throw "docker compose failed ($($ComposeArgs[0])) exit=$($r.Code)"
    }
    return , $r.Output
}

function Get-StackComposeFiles([bool]$WithCloudLogs) {
    $extra = @()
    if ($WithCloudLogs) {
        $extra += (Join-Path $Deploy 'docker-compose.bench-cloudlogs.yml')
    }
    return $extra
}

function Stop-Stack {
    param([string[]]$ExtraFiles = @())
    Invoke-Compose @('down', '-v') $ExtraFiles | Out-Null
}

function Start-Stack {
    param([bool]$OtelEnabled, [string]$Sampling, [bool]$WithCloudLogs)
    # Variant envs consumed by docker-compose.bench.yml interpolation.
    if ($OtelEnabled) { $env:OTEL_BENCH_ENABLED = 'true' } else { $env:OTEL_BENCH_ENABLED = 'false' }
    $env:OTEL_BENCH_SAMPLING = $Sampling
    if ($WithCloudLogs -and $OtelEnabled) {
        $env:OTEL_BENCH_AWS_LOGS_EXPORTER = 'awscloudwatchlogs/bench'
        $env:OTEL_BENCH_GCP_LOGS_EXPORTER = 'googlecloud/bench'
        # AWS creds for the collector container: exported into the process
        # environment only, never written to a repo file.
        $cred = Invoke-Native { & aws configure export-credentials --profile $AwsProfile --format env 2>&1 }
        if ($cred.Code -ne 0) { throw "aws export-credentials failed: $($cred.Output)" }
        $credLines = $cred.Output
        $env:BENCH_AWS_ACCESS_KEY_ID = ''
        $env:BENCH_AWS_SECRET_ACCESS_KEY = ''
        $env:BENCH_AWS_SESSION_TOKEN = ''
        foreach ($l in $credLines) {
            if ($l -match '^export AWS_ACCESS_KEY_ID=(.+)$') { $env:BENCH_AWS_ACCESS_KEY_ID = $Matches[1].Trim('"') }
            if ($l -match '^export AWS_SECRET_ACCESS_KEY=(.+)$') { $env:BENCH_AWS_SECRET_ACCESS_KEY = $Matches[1].Trim('"') }
            if ($l -match '^export AWS_SESSION_TOKEN=(.+)$') { $env:BENCH_AWS_SESSION_TOKEN = $Matches[1].Trim('"') }
        }
    } else {
        $env:OTEL_BENCH_AWS_LOGS_EXPORTER = 'nop/aws'
        $env:OTEL_BENCH_GCP_LOGS_EXPORTER = 'nop/gcp'
        $env:BENCH_AWS_ACCESS_KEY_ID = ''
        $env:BENCH_AWS_SECRET_ACCESS_KEY = ''
        $env:BENCH_AWS_SESSION_TOKEN = ''
    }
    $extra = Get-StackComposeFiles $WithCloudLogs
    Invoke-Compose @('up', '-d', '--no-build', 'postgres', 'otel-collector',
                     'service-a', 'service-b', 'prometheus') $extra | Out-Null
}

function Get-ServiceHealth {
    try {
        $r = Invoke-WebRequest -Uri 'http://localhost:8000/metrics' -UseBasicParsing -TimeoutSec 5
        if ($r.StatusCode -eq 200) { return $true }
    } catch { }
    return $false
}

function Wait-StackReady {
    param([int]$TimeoutSeconds = 90)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Get-ServiceHealth) { return }
        Start-Sleep -Seconds 5
    }
    throw 'service-a did not become reachable on :8000'
}

function Get-ContainerId([string]$Service) {
    $out = Invoke-Compose @('ps', '-q', $Service)
    return (($out | Out-String).Trim())
}

# Seed inventory holds 10/20/15 units (init-scripts/01-init.sh); a 5-min
# 100-VU closed loop would exhaust it in seconds and turn the run into a 409
# storm. A background refill keeps the success path dominant; it is identical
# for variants A and B so it cancels in the differential (PLAN section 4.A).
function Start-RefillJob {
    $cid = Get-ContainerId 'postgres'
    $job = Start-Job -ArgumentList $cid -ScriptBlock {
        param($Cid)
        while ($true) {
            & docker exec $Cid psql -U postgres -d inventory -c 'UPDATE inventory SET quantity = 1000000;' 2>&1 | Out-Null
            Start-Sleep -Seconds 5
        }
    }
    return $job
}

function Stop-RefillJob($Job) {
    if ($Job) {
        Stop-Job $Job -ErrorAction SilentlyContinue
        Remove-Job $Job -Force -ErrorAction SilentlyContinue
    }
}

function Invoke-K6Run {
    param([string]$Name, [int]$Steady)
    $outFile = Join-Path $BenchDir ("k6\" + $Name + '.json')
    $logFile = Join-Path $BenchDir ("logs\" + $Name + '.k6.log')
    $cliArgs = @(
        'run', '--rm',
        '-v', "${K6SrcMount}:/scripts:ro",
        '-v', "${BenchK6Mount}:/out",
        '-e', "BENCH_TARGET_URL=$TargetUrl",
        '-e', "BENCH_STEADY_SECONDS=$Steady",
        $K6Image, 'run', "--summary-export=/out/$Name.json", '/scripts/orders.js'
    )
    Write-BenchLog "k6 run $Name (steady=$Steady s)"
    $r = Invoke-Native { & docker @cliArgs > $logFile 2>&1 }
    $code = $r.Code
    # 0 = clean, 99 = threshold breach (summary still exported) -> keep going.
    if ($code -ne 0 -and $code -ne 99) {
        throw "k6 run $Name failed with exit code $code (see $logFile)"
    }
    if (-not (Test-Path $outFile)) { throw "k6 summary not found: $outFile" }
    return $code
}

$PromQueries = [ordered]@{
    cpu_nonidle_ratio      = 'sum(rate(otel_system_cpu_time_seconds_total{state!="idle"}[5m])) / sum(rate(otel_system_cpu_time_seconds_total[5m]))'
    collector_cpu_cores    = 'sum(rate(otel_otelcol_process_cpu_seconds_total[5m]))'
    mem_used_bytes         = 'sum(otel_system_memory_usage_bytes{state="used"})'
    mem_limit_bytes        = 'sum(otel_system_memory_limit_bytes)'
    collector_heap_bytes   = 'sum(otel_otelcol_process_runtime_heap_alloc_bytes)'
    exporter_queue_size    = 'sum(otel_otelcol_exporter_queue_size)'
    send_failed_spans      = 'sum(otel_otelcol_exporter_send_failed_spans_total)'
    refused_spans          = 'sum(otel_otelcol_receiver_refused_spans_total)'
    failed_spans           = 'sum(otel_otelcol_receiver_failed_spans_total)'
    accepted_spans         = 'sum(otel_otelcol_receiver_accepted_spans_total)'
    sent_spans             = 'sum(otel_otelcol_exporter_sent_spans_total)'
    accepted_log_records   = 'sum(otel_otelcol_receiver_accepted_log_records_total)'
    sent_log_records       = 'sum(otel_otelcol_exporter_sent_log_records_total)'
    accepted_metric_points = 'sum(otel_otelcol_receiver_accepted_metric_points_total)'
    sent_metric_points     = 'sum(otel_otelcol_exporter_sent_metric_points_total)'
    aws_sent_log_records   = 'sum(otel_otelcol_exporter_sent_log_records_total{exporter="awscloudwatchlogs/bench"})'
    gcp_sent_log_records   = 'sum(otel_otelcol_exporter_sent_log_records_total{exporter="googlecloud/bench"})'
    collector_uptime_s     = 'sum(otel_otelcol_process_uptime_total)'
    series_total           = 'count({__name__=~".+"})'
}

function Get-PromSnapshot {
    param([string]$Name, [datetime]$StartUtc, [datetime]$EndUtc)
    $queries = [ordered]@{}
    foreach ($key in $PromQueries.Keys) {
        $url = 'http://localhost:9090/api/v1/query?query=' + [uri]::EscapeDataString($PromQueries[$key])
        $val = $null
        try {
            $resp = Invoke-RestMethod -Uri $url -TimeoutSec 10
            if ($resp.data.result.Count -gt 0) { $val = [double]$resp.data.result[0].value[1] }
        } catch {
            Write-BenchLog "prom query $key failed: $_"
        }
        $queries[$key] = $val
    }
    $result = [ordered]@{
        run       = $Name
        start_utc = $StartUtc.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss'Z'")
        end_utc   = $EndUtc.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss'Z'")
        hostname  = $env:COMPUTERNAME
        queries   = $queries
    }
    $file = Join-Path $BenchDir ("metrics\" + $Name + '.json')
    ConvertTo-Json -InputObject $result -Depth 6 | Set-Content -Path $file -Encoding UTF8
    return $result
}

function Get-AwsBenchLogBytes {
    # storedBytes across the bench log groups. NOTE: CloudWatch updates
    # storedBytes/lastEventTimestamp with a long lag, so this read is
    # informational; the per-run ingest is measured from the exporter's
    # sent_log_records (labeled Prometheus series) times a sampled average
    # bytes/record (Get-AwsBytesPerRecord).
    $total = [int64]0
    $groups = @('/otel/labs/bench/service-a', '/otel/labs/bench/service-b', '/otel/labs/bench')
    foreach ($g in $groups) {
        $token = $null
        do {
            $cli = @('logs', 'describe-log-streams', '--log-group-name', $g,
                     '--order-by', 'LastEventTime', '--region', $AwsRegion,
                     '--profile', $AwsProfile, '--output', 'json', '--no-paginate')
            if ($token) { $cli += @('--starting-token', $token) }
            $r = Invoke-Native { & aws @cli 2>&1 | Out-String }
            $resp = $r.Output | Out-String
            if ($r.Code -ne 0) {
                # Missing group before the first gated run counts as zero bytes.
                if ($resp -match 'ResourceNotFoundException') { break }
                Write-BenchLog "aws describe-log-streams failed for $g"
                return $null
            }
            try { $json = $resp | ConvertFrom-Json } catch { return $null }
            if (-not $json.PSObject.Properties['logStreams']) { break }
            foreach ($s in $json.logStreams) {
                if ($s.PSObject.Properties['storedBytes']) { $total += [int64]$s.storedBytes }
            }
            $token = $null
            if ($json.PSObject.Properties['nextToken'] -and $json.nextToken) { $token = $json.nextToken }
        } while ($token)
    }
    return $total
}

function Get-AwsBytesPerRecord {
    # Sample the real per-record cost: pull the first page of recent events
    # from the bench group and average the message size (+26 B/record of
    # CloudWatch structural overhead per the ingestion billing rules).
    $end = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    $start = $end - 3600000
    $cli = @('logs', 'filter-log-events', '--log-group-name', '/otel/labs/bench',
             '--start-time', "$start", '--end-time', "$end", '--limit', '1000',
             '--region', $AwsRegion, '--profile', $AwsProfile, '--output', 'json')
    $r = Invoke-Native { & aws @cli 2>&1 | Out-String }
    $resp = $r.Output | Out-String
    if ($r.Code -ne 0) {
        Write-BenchLog 'aws filter-log-events failed; bytes/record sample skipped'
        return $null
    }
    try { $json = $resp | ConvertFrom-Json } catch { return $null }
    if (-not $json.PSObject.Properties['events']) { return $null }
    $events = @($json.events)
    if ($events.Count -lt 10) { return $null }
    $bytes = 0
    foreach ($e in $events) { $bytes += [Text.Encoding]::UTF8.GetByteCount([string]$e.message) }
    return ($bytes / $events.Count) + 26
}

function Get-GcpBytesPerRecord {
    # Sample the real GCP ingest cost: read the most recent bench log entries
    # and average their serialized size. gcloud read returns entries already
    # ingested, so this anchors records -> bytes for the per-run window when
    # the billing metric is behind (it lags ~5-10 min).
    $filter = ('logName="projects/{0}/logs/otel-labs-bench"' -f $GcpProject)
    $cli = @('logging', 'read', $filter,
             '--limit', '500', '--format', 'json', '--project', $GcpProject)
    $r = Invoke-Native { & gcloud @cli 2>&1 | Out-String }
    $resp = $r.Output | Out-String
    if ($r.Code -ne 0) {
        Write-BenchLog 'gcloud logging read failed; gcp bytes/record sample skipped'
        return $null
    }
    try { $entries = $resp | ConvertFrom-Json } catch { return $null }
    $entries = @($entries)
    if ($entries.Count -lt 10) { return $null }
    $bytes = 0
    foreach ($e in $entries) { $bytes += [Text.Encoding]::UTF8.GetByteCount(($e | ConvertTo-Json -Depth 10 -Compress)) }
    return ($bytes / $entries.Count)
}

function Get-GcpIngestBytes {
    param([datetime]$StartUtc, [datetime]$EndUtc)
    # logging.googleapis.com/billing/bytes_ingested (the v1 timeSeries.list
    # REST surface was retired; v3 takes interval.startTime/endTime). Points
    # are 1-minute deltas -> sum them across the window.
    $token = ((& gcloud auth print-access-token 2>$null | Out-String).Trim())
    if (-not $token -or -not $GcpProject) { return $null }
    $start = $StartUtc.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    $end = $EndUtc.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    $filter = 'metric.type="logging.googleapis.com/billing/bytes_ingested"'
    $url = ('https://monitoring.googleapis.com/v3/projects/{0}/timeSeries?filter={1}&interval.startTime={2}&interval.endTime={3}' -f `
            $GcpProject, [uri]::EscapeDataString($filter), $start, $end)
    try {
        $resp = Invoke-RestMethod -Uri $url -Headers @{ Authorization = "Bearer $token" } -TimeoutSec 20
    } catch {
        Write-BenchLog "gcp billing/bytes_ingested query failed: $_"
        return $null
    }
    $total = [int64]0
    if (-not $resp.PSObject.Properties['timeSeries']) { return $total }
    foreach ($series in $resp.timeSeries) {
        if (-not $series.PSObject.Properties['points']) { continue }
        foreach ($pt in $series.points) {
            $total += [int64]$pt.value.int64Value
        }
    }
    return $total
}

function Add-WindowsRecord($Record) {
    $list = @()
    if (Test-Path $WindowsFile) {
        $existing = Get-Content $WindowsFile -Raw | ConvertFrom-Json
        $list = @($existing)
    }
    $list += $Record
    # -InputObject keeps the array shape even for 1 element (PS 5.1).
    ConvertTo-Json -InputObject $list -Depth 6 | Set-Content -Path $WindowsFile -Encoding UTF8
}

function Invoke-BenchRun {
    param(
        [string]$Name,
        [bool]$OtelEnabled,
        [string]$Sampling,
        [bool]$WithCloudLogs,
        [int]$Steady
    )
    $extra = Get-StackComposeFiles $WithCloudLogs
    Write-BenchLog "=== run $Name (otel=$OtelEnabled sampling=$Sampling cloudlogs=$WithCloudLogs steady=${Steady}s) ==="
    Stop-Stack $extra                       # fresh seed every run
    Start-Sleep -Seconds 5
    Start-Stack -OtelEnabled $OtelEnabled -Sampling $Sampling -WithCloudLogs $WithCloudLogs
    Wait-StackReady
    Start-Sleep -Seconds $WarmupSeconds     # stack warmup (PLAN section 4.A)

    $imageId = (docker inspect --format '{{.Image}}' (Get-ContainerId 'service-a'))
    $storedBefore = $null
    if ($WithCloudLogs) { $storedBefore = Get-AwsBenchLogBytes }

    $job = Start-RefillJob
    $startUtc = (Get-Date).ToUniversalTime()
    $k6Code = Invoke-K6Run -Name $Name -Steady $Steady
    $endUtc = (Get-Date).ToUniversalTime()
    Stop-RefillJob $job

    Start-Sleep -Seconds 10                 # let the last batch (5 s timeout) flush
    $snapshot = Get-PromSnapshot -Name $Name -StartUtc $startUtc -EndUtc $endUtc

    Stop-Stack $extra
    Start-Sleep -Seconds $CooldownSeconds   # cooldown; lets ingest meters settle
    Start-Sleep -Seconds $CostSettleSeconds # billing metric lag (Cloud Watch/Logging meters)
    if ($WithCloudLogs) {
        $storedAfter = Get-AwsBenchLogBytes
        $gcpRunBytes = Get-GcpIngestBytes `
            -StartUtc $startUtc.AddMinutes(-1) -EndUtc $endUtc.AddMinutes(4)
        $bpr = Get-AwsBytesPerRecord
        if ($null -eq $script:GcpBpr) { $script:GcpBpr = Get-GcpBytesPerRecord }
        $gbpr = $script:GcpBpr
        # The collector restarts every run, so the exporter counters are
        # per-run cumulative: read them straight from the snapshot.
        $awsRecords = $snapshot.queries.aws_sent_log_records
        $gcpRecords = $snapshot.queries.gcp_sent_log_records
        $awsEst = $null
        if ($null -ne $awsRecords -and $null -ne $bpr) {
            $awsEst = [int64]([double]$awsRecords * [double]$bpr)
        }
        $gcpEst = $null
        if ($null -ne $gcpRecords -and $null -ne $gbpr) {
            $gcpEst = [int64]([double]$gcpRecords * [double]$gbpr)
        }
        $awsDelta = $null
        if ($null -ne $storedAfter -and $null -ne $storedBefore) {
            $awsDelta = [int64]$storedAfter - [int64]$storedBefore
        }
        $delta = [ordered]@{
            run = $Name
            window = @{ start_utc = $startUtc.ToString("yyyy-MM-ddTHH:mm:ss'Z'"); end_utc = $endUtc.ToString("yyyy-MM-ddTHH:mm:ss'Z'") }
            aws_stored_bytes_before = $storedBefore
            aws_stored_bytes_after = $storedAfter
            aws_stored_bytes_delta = $awsDelta
            aws_sent_log_records = $awsRecords
            aws_bytes_per_record_sample = $bpr
            aws_ingest_est_bytes = $awsEst
            gcp_sent_log_records = $gcpRecords
            gcp_bytes_per_record_sample = $gbpr
            gcp_ingest_est_bytes = $gcpEst
            gcp_ingestion_bytes_window = $gcpRunBytes
        }
        $file = Join-Path $BenchDir ("cost\" + $Name + '.json')
        ConvertTo-Json -InputObject $delta -Depth 6 | Set-Content -Path $file -Encoding UTF8
    }

    $record = [ordered]@{
        run            = $Name
        variant        = $(if ($OtelEnabled) { 'B' } else { 'A' })
        sampling       = $Sampling
        cloud_logs     = [bool]$WithCloudLogs
        steady_seconds = $Steady
        hostname       = $env:COMPUTERNAME
        image_id       = $imageId
        k6_exit_code   = $k6Code
        start_utc      = $startUtc.ToString("yyyy-MM-ddTHH:mm:ss'Z'")
        end_utc        = $endUtc.ToString("yyyy-MM-ddTHH:mm:ss'Z'")
    }
    Add-WindowsRecord $record
    Write-BenchLog "run $Name done (k6 exit $k6Code)"
}

# --- campaign definitions ----------------------------------------------------

$script:GcpBpr = $null
$runs = @()
switch ($Mode) {
    'full' {
        # ABBA order, 3 replicas per variant (PLAN section 4.A).
        $runs = @(
            @{ Name = 'A1'; Otel = $false; Sampling = '1.0';  Cloud = $false },
            @{ Name = 'B1'; Otel = $true;  Sampling = '0.25'; Cloud = $CloudLogs.IsPresent },
            @{ Name = 'B2'; Otel = $true;  Sampling = '0.25'; Cloud = $CloudLogs.IsPresent },
            @{ Name = 'A2'; Otel = $false; Sampling = '1.0';  Cloud = $false },
            @{ Name = 'A3'; Otel = $false; Sampling = '1.0';  Cloud = $false },
            @{ Name = 'B3'; Otel = $true;  Sampling = '0.25'; Cloud = $CloudLogs.IsPresent }
        )
    }
    'quick' {
        $runs = @(
            @{ Name = 'Aq'; Otel = $false; Sampling = '1.0';  Cloud = $false },
            @{ Name = 'Bq'; Otel = $true;  Sampling = '0.25'; Cloud = $CloudLogs.IsPresent }
        )
        if ($SteadySeconds -eq 300) { $SteadySeconds = 30 }
    }
    'attrib' {
        $runs = @(
            @{ Name = 'attrib-s100'; Otel = $true; Sampling = '1.0'; Cloud = $CloudLogs.IsPresent },
            @{ Name = 'attrib-s000'; Otel = $true; Sampling = '0.0'; Cloud = $CloudLogs.IsPresent }
        )
    }
    'cloudprobe' {
        # 60 s variant-B runs with the cloud gates ON: anchors the real ingest
        # per cloud without running the full campaign (plan-B helper).
        $runs = @(
            @{ Name = 'probe-aws'; Otel = $true; Sampling = '0.25'; Cloud = $true },
            @{ Name = 'probe-gcp'; Otel = $true; Sampling = '0.25'; Cloud = $true }
        )
        if ($SteadySeconds -eq 300) { $SteadySeconds = 60 }
    }
}

Write-BenchLog "campaign start mode=$Mode runs=$($runs.Count) steady=${SteadySeconds}s cloudlogs=$($CloudLogs.IsPresent)"

# Pull the pinned k6 image once, before touching the stack.
$inspect = Invoke-Native { & docker image inspect $K6Image 2>&1 | Out-Null }
if ($inspect.Code -ne 0) {
    Write-BenchLog "pulling $K6Image"
    $pull = Invoke-Native { & docker pull $K6Image | Out-Null }
    if ($pull.Code -ne 0) { throw "docker pull $K6Image failed (network blocked? see plan B in docs/evidence/04-overhead.md)" }
}

# Build the two service images once so A and B share the same digest.
Invoke-Compose @('build', 'service-a', 'service-b') | Out-Null

try {
    foreach ($r in $runs) {
        Invoke-BenchRun -Name $r.Name -OtelEnabled ([bool]$r.Otel) -Sampling $r.Sampling `
                        -WithCloudLogs ([bool]$r.Cloud) -Steady $SteadySeconds
    }
} finally {
    # Never leave orphans: full teardown of the bench stack either way.
    $extra = Get-StackComposeFiles $true
    Stop-Stack $extra
    Write-BenchLog 'campaign end; stack torn down'
}

Write-BenchLog 'Next: python scripts/bench_report.py to aggregate.'





