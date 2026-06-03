# Development

This guide describes lightweight local checks and setup notes for Live Call-and-Response.

## Repository Shape

- `code/live_call_response.py` is the realtime call-and-response engine.
- `code/midi_vad_endpoint.py` contains the MIDI endpoint detector.
- `code/interface_backend.py` runs the local web studio backend.
- `code/static/` contains the browser UI.
- `code/offline_ab_test.py` and metric scripts support offline evaluation.

## Local Python Checks

Run this from the repository root:

```powershell
python -m compileall -q code
```

For targeted endpoint detector work:

```powershell
python code/midi_vad_endpoint.py --simulate
```

## Runtime Setup

Python 3.12 on Windows is the primary tested environment.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

The AMT backend also needs the upstream Anticipatory Music Transformer package:

```powershell
git clone https://github.com/jthickstun/anticipation external/anticipation
pip install external/anticipation
```

## MIDI Notes

When changing realtime behavior, document:

- Input and output MIDI port names.
- Virtual MIDI routing, such as loopMIDI ports.
- Backend and model ID.
- Latency mode and musical-control flags.
- Whether the behavior affects endpoint detection or response generation.

## Asset Boundaries

Do not commit model weights, Hugging Face caches, DAW projects, VSTs, sample libraries, generated experiment data, or thesis artifacts.
