# Support

Live Call-and-Response is a realtime MIDI research prototype. Reproducible setup details matter a lot because MIDI routing, model caches, and latency settings can change the behavior.

## Before Opening an Issue

Check these first:

1. Python dependencies are installed in the active virtual environment.
2. The upstream Anticipatory Music Transformer package is installed if using the AMT backend.
3. MIDI input and output ports are visible with `--list-ports`.
4. Virtual MIDI ports such as `Python_IN` and `Python_OUT` are configured if using loopMIDI.
5. Model weights are available locally when using offline mode.

## Where to Ask

- Use a bug report issue for crashes, MIDI routing problems, endpoint detection problems, or web studio failures.
- Use a feature request issue for new backends, evaluation metrics, UI improvements, or latency-control ideas.
- Use the security policy for local server exposure, unsafe file handling, or private data concerns.

## Useful Details

When asking for help, include:

- Operating system and Python version.
- Command line used.
- MIDI input and output port names.
- Backend and model ID.
- Whether the issue affects realtime CLI, web studio, or offline evaluation.
- A short log excerpt.

## Scope

This repository does not include model weights, DAW projects, sample libraries, VSTs, generated experiment data, or thesis artifacts.
