param(
    [string]$MosquittoDir = "C:\Users\laure\Downloads\Midterm\Mosquitto",
    [string]$ProjectDir = "C:\Users\laure\Downloads\Midterm",
    [string]$PublisherScript = "30_day_simulation.py",
    [string]$GuiSubscriberScript = "habit_tracker_subscriber.py",
    [string]$Topic = "sensor/daily_bit",
    [string]$BrokerHost = "localhost",
    [int]$Port = 1883
)

$ErrorActionPreference = "Stop"

$brokerExe = Join-Path $MosquittoDir "mosquitto.exe"
$publisherPath = Join-Path $ProjectDir $PublisherScript
$guiSubscriberPath = Join-Path $ProjectDir $GuiSubscriberScript

if (-not (Test-Path $brokerExe)) { throw "Broker not found: $brokerExe" }
if (-not (Test-Path $publisherPath)) { throw "Publisher script not found: $publisherPath" }
if (-not (Test-Path $guiSubscriberPath)) { throw "GUI subscriber script not found: $guiSubscriberPath" }

$existing = Get-Process mosquitto -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Mosquitto is already running. Reusing existing broker."
    $startedBroker = $false
} else {
    $null = Start-Process -FilePath $brokerExe -ArgumentList "-p $Port -v"
    Start-Sleep -Seconds 1
    Write-Host "Started broker on $BrokerHost`:$Port."
    $startedBroker = $true
}

$guiCommand = "python '$guiSubscriberPath' --broker '$BrokerHost' --port $Port --topic '$Topic'"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $guiCommand | Out-Null
Write-Host "Opened GUI subscriber window for topic '$Topic'."
Start-Sleep -Seconds 1

Write-Host "Running publisher script: $publisherPath"
python $publisherPath

if ($startedBroker) {
    Get-Process mosquitto -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Write-Host "Stopped broker started by this script."
}

Write-Host "Demo finished."
