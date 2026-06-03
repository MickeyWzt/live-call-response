# Examples

These examples show common Live Call-and-Response workflows for development, performance testing, and evaluation.

## Example 1: Launch the Web Studio

Use this when you want the browser interface for device selection and note visualization.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python code/interface_backend.py --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

Expected result: a local web interface opens where you can configure a MIDI session and inspect note activity.

## Example 2: Route Through Virtual MIDI Ports

Use this when testing without a full studio setup.

1. Create virtual MIDI ports named `Python_IN` and `Python_OUT`.
2. Route your controller or MIDI generator into `Python_IN`.
3. Route `Python_OUT` to a synth, DAW track, or monitoring tool.
4. Start the realtime CLI:

```powershell
python code/live_call_response.py `
  --backend amt `
  --model-id stanford-crfm/music-small-800k `
  --offline `
  --input-port "Python_IN" `
  --output-port "Python_OUT" `
  --monitor-input `
  --latency-mode fast `
  --musical-control
```

## Example 3: Verify MIDI Output

Before debugging generation, confirm the output port can play notes.

```powershell
python code/live_call_response.py --list-ports
python code/live_call_response.py --test-output --output-port "Python_OUT"
```

If the test output is silent, fix MIDI routing before changing model or generation settings.

## Example 4: Run an Offline A/B Trial

Use this for repeatable evaluation away from a live performance setup.

```powershell
python code/offline_ab_test.py --input-midi code/demo_call.mid --trials 3
```

Save the generated samples and notes separately from source control unless they are intentionally small fixtures.

## Example 5: Report a Realtime Bug

Include:

- Operating system and Python version.
- MIDI device, virtual-port, or DAW routing setup.
- Full command line.
- Whether `--list-ports` sees the expected ports.
- Whether `--test-output` produces sound.
- A short description of the musical phrase and expected response.
