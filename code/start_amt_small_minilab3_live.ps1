$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

python -u "code/live_call_response.py" `
  --backend amt `
  --model-id "stanford-crfm/music-small-800k" `
  --offline `
  --input-port "Minilab3" `
  --output-port "Python_OUT" `
  --monitor-input `
  --startup-test-note `
  --response-seconds 8 `
  --max-events 16 `
  --top-p 0.98 `
  --temperature 0.9 `
  --latency-mode fast `
  --max-underrun-seconds 1.5 `
  --musical-control `
  --no-speculative-preload `
  --fallback-on-empty `
  --no-duration-match `
  --live-stop-on-target-notes `
  --same-pitch-limit 2 `
  --dominant-pitch-max-share 0.35 `
  --min-cutoff 0.45 `
  --chord-cluster-window 0.08 `
  --endpoint-confirm-delay 0.15
