$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

$AriaModel = Join-Path $Root "model_weights/aria-medium-gen"

python -u "code/live_call_response.py" `
  --backend aria `
  --aria-model-id $AriaModel `
  --offline `
  --aria-live-mode batch `
  --input-port "Python_IN" `
  --output-port "Python_OUT" `
  --monitor-input `
  --startup-test-note `
  --response-seconds 8 `
  --aria-response-bars 2 `
  --aria-max-new-tokens 768 `
  --latency-mode fast `
  --temperature 0.95 `
  --top-p 0.98
