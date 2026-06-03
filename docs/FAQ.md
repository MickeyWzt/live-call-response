# FAQ

## What is this project for?

Live Call-and-Response is a realtime symbolic-music prototype. It listens to MIDI input, detects phrase boundaries, and generates a response through a transformer-backed music model.

## Is this an audio plugin?

No. The project works with symbolic MIDI events, not raw audio. You can route MIDI to instruments or synths, but audio rendering is outside the core system.

## Do I need a physical MIDI keyboard?

A physical MIDI controller is the best target workflow, but virtual MIDI ports can be used for local testing. See the README setup section for the expected `Python_IN` and `Python_OUT` routing pattern.

## Why does timing matter so much?

The model responds to symbolic note events and phrase-end detection. Small changes in endpoint timing, held notes, or MIDI routing can change the generated response.

## Is the model included in the repository?

No large model checkpoint is bundled. Follow the README instructions for installing or wiring the upstream transformer package and any required local assets.

## What should I include in a bug report?

Include your operating system, Python version, MIDI device or virtual port setup, the command you ran, console logs, and a short description of what you played or expected to hear.
