# Security Policy

## Scope

This project runs local MIDI, web, and model-inference tooling for music experiments. Security-sensitive reports may include issues around local web server exposure, unsafe file handling, dependency risks, or accidental inclusion of private assets.

## Reporting a Vulnerability

Please do not open a public issue for vulnerabilities that could expose local files, private experiment data, credentials, or unsafe server behavior.

Instead, report privately through GitHub's security reporting flow if available, or contact the maintainer through the GitHub profile.

Include:

- A short description of the issue.
- Steps to reproduce.
- Affected script, route, or command.
- Whether the issue requires local network access.
- Any safe proof-of-concept details.

## Asset and Data Safety

Do not submit model weights, Hugging Face caches, generated experiment datasets, thesis drafts, DAW projects, VSTs, sample libraries, or private recordings to this repository. If an issue requires an example file, use a minimal redistributable MIDI file or describe the structure instead.

## Supported Versions

This is an early research prototype. Security fixes are applied to the `main` branch.