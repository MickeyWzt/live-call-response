# Contributing to Live Call-and-Response

Thanks for taking a look at this project. This repository is a realtime MIDI and symbolic-music experiment, so useful contributions usually improve reproducibility, musical control, latency, or evaluation quality.

## Good First Contributions

- Document setup steps for specific MIDI devices, virtual MIDI ports, or DAW routing.
- Improve Windows setup notes for loopMIDI, Python, or model installation.
- Add small MIDI examples that are safe to redistribute.
- Add tests for phrase endpoint detection, note conversion, or metric calculations.
- Improve the browser interface without changing the realtime engine contract.
- Document evaluation results with clear commands and input files.

## Before Opening a Pull Request

1. Keep model weights, generated datasets, Hugging Face caches, DAW projects, VSTs, audio sample libraries, and thesis artifacts out of the repository.
2. Run the relevant script or test path locally and include the command in the PR description.
3. Keep changes focused. A small change that is easy to review is better than a broad rewrite.
4. If you change realtime behavior, describe how it affects latency, phrase boundaries, or musical output.
5. If you add dependencies, explain why the project needs them.

## Development Notes

Python 3.12 on Windows is the primary tested environment. The project can be adapted elsewhere, but please call out platform-specific assumptions in your PR.

For MIDI work, include the input/output port setup you used. If a bug only appears with a specific virtual MIDI routing setup, describe the routing clearly enough for someone else to reproduce it.

## Reporting Issues

When reporting a bug, include:

- Operating system and Python version.
- The command you ran.
- MIDI input/output port names.
- Backend and model ID.
- Whether the issue affects the CLI, the web studio, or offline evaluation.
- A short log excerpt when available.

## Attribution

If your contribution depends on a model, package, paper, or dataset, include the relevant attribution and license notes.