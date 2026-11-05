# Poll ChatGPT tabs for a generating state and dump HTML snapshots.
# Read-only: observe + get-html. No messages are sent by this script.
param(
  [int]$Seconds = 600,
  [int[]]$Tabs = @(1017028332, 1017028469),
  [int]$MaxCaptures = 8
)
$env:BSK_AUTO_START = '0'
$outdir = "D:\CortexFlow\contextflow-research\_artifacts\streaming"
New-Item -ItemType Directory -Force -Path $outdir | Out-Null
Get-ChildItem $outdir -File -ErrorAction SilentlyContinue | Remove-Item -Force
$deadline = (Get-Date).AddSeconds($Seconds)
$n = 0
$capturing = $false
while ((Get-Date) -lt $deadline -and $n -lt $MaxCaptures) {
  foreach ($t in $Tabs) {
    try {
      $raw = bsk observe --session rnbk --tab-id $t --json 2>$null
      if (-not $raw) { continue }
      $j = $raw | ConvertFrom-Json
    } catch { continue }
    $txt = [string]$j.text
    $generating = ($txt -match '停止生成|停止回答|Stop generating|正在生成|正在思考')
    if (-not $generating) {
      # Fallback: composer visible but no send button -> likely generating
      $hasComposer = $txt -match 'textbox "询问 ChatGPT"'
      $hasSend = $txt -match 'button "发送"'
      if ($hasComposer -and -not $hasSend) { $generating = $true }
    }
    if ($generating) {
      $n++
      $sync = bsk cooperate sync --session rnbk --json 2>$null | ConvertFrom-Json
      $file = Join-Path $outdir ("streaming-{0:D2}-tab{1}.html" -f $n, $t)
      bsk get-html --session rnbk --tab-id $t --context $sync.context_id --observation $sync.observation_id --out $file --quiet 2>$null | Out-Null
      Write-Output ("CAPTURE {0} tab={1} at {2}" -f $n, $t, (Get-Date -Format HH:mm:ss))
      Start-Sleep -Milliseconds 900
    }
  }
  Start-Sleep -Milliseconds 600
}
Write-Output "done captures=$n"
