# Live Call-and-Response

Realtime symbolic-music call-and-response prototype for MIDI improvisation.

The system listens to a human MIDI performer, detects phrase endpoints, encodes
the Call as symbolic events, and generates an AI Response with an autoregressive
Transformer backend. It also includes a local browser interface with MIDI device
selection, a virtual keyboard, and rhythm-game-style note visualization.

## Project Links

- [Support and troubleshooting](SUPPORT.md)
- [Roadmap](ROADMAP.md)
- [Contributing guide](CONTRIBUTING.md)
- [Security policy](SECURITY.md)
- [Third-party attribution](THIRD_PARTY.md)

## Features

- Dynamic MIDI endpoint detection with note-on/note-off capture
- AMT backend based on `stanford-crfm/music-small-800k` / `music-medium-800k`
- Optional Aria backend for offline experiments
- Musical response controller to reduce same-pitch repetition
- Realtime metrics CSV logging for thesis experiments
- Local web interface for starting sessions and visualizing note gates
- Offline A/B and objective evaluation scripts

## What Is Not Included

This repository does not include model weights, Hugging Face caches, piano
sample libraries, VST plugins, DAWs, generated experiment data, or thesis PDFs.

Download models and third-party audio tools separately according to their own
licenses.

## Quick Start

Python 3.12 is recommended on Windows.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Install the upstream Anticipatory Music Transformer package:

```powershell
git clone https://github.com/jthickstun/anticipation external/anticipation
pip install external/anticipation
```

Create loopMIDI ports:

- `Python_IN`
- `Python_OUT`

Run the web studio:

```powershell
python code/interface_backend.py --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

## Realtime CLI

```powershell
python code/live_call_response.py `
  --backend amt `
  --model-id stanford-crfm/music-small-800k `
  --offline `
  --input-port "Python_IN" `
  --output-port "Python_OUT" `
  --monitor-input `
  --latency-mode fast `
  --musical-control `
  --no-duration-match `
  --live-stop-on-target-notes
```

List MIDI ports:

```powershell
python code/live_call_response.py --list-ports
```

Test output:

```powershell
python code/live_call_response.py --test-output --output-port "Python_OUT"
```

## Offline Evaluation

Create anonymous A/B listening samples:

```powershell
python code/offline_ab_test.py --input-midi code/demo_call.mid --trials 3
```

Run objective metrics:

```powershell
python code/evaluate_melody_metrics.py --help
```

## Repository Layout

```text
code/
  live_call_response.py          realtime call-and-response engine
  midi_vad_endpoint.py           MIDI endpoint detector
  interface_backend.py           local FastAPI/WebSocket studio backend
  static/                        browser UI
  offline_ab_test.py             blind listening sample generator
  evaluate_melody_metrics.py     objective melody metrics
  run_objective_search.py        parameter search runner
```

## Citation and Attribution

If you use the AMT backend, cite the Anticipatory Music Transformer paper:

> Thickstun, J., Hall, D., Donahue, C., & Liang, P. Anticipatory Music
> Transformer. 2023.

See `THIRD_PARTY.md` for dependency and model attribution notes.

## License

This repository is released under the MIT License. Third-party components,
models, datasets, plugins, and audio assets remain under their respective
licenses.
