# Roadmap

This roadmap describes useful directions for Live Call-and-Response. The project is a research prototype, so reproducibility and musical behavior matter more than adding broad features quickly.

## Current Focus

- Keep realtime MIDI phrase detection understandable and tunable.
- Preserve offline evaluation workflows for comparing generated responses.
- Keep model and dependency attribution clear.
- Make setup easier for Windows users with virtual MIDI routing.

## Near-Term Improvements

- Add a small redistributable demo MIDI input set.
- Add endpoint detector tests with deterministic MIDI event streams.
- Add clearer latency and phrase-boundary tuning examples.
- Add screenshots or a short web studio demo GIF.
- Document recommended loopMIDI and DAW routing setups.

## Longer-Term Ideas

- Better backend abstraction for AMT and Aria experiments.
- More musical-control presets.
- Realtime quality metrics shown in the web studio.
- Exportable session summaries for listening tests.
- Optional typed configuration files for repeatable experiments.

## Non-Goals

- Shipping model weights in this repository.
- Bundling DAWs, VSTs, or sample libraries.
- Treating this prototype as a production performance system without local testing.
