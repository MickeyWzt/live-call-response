"""
Offline A/B blind listening generator for Call-and-Response experiments.

The script takes one or more human Call MIDI files, generates anonymous
Call+Response and Response-only MIDI samples with several candidate strategies,
then writes a score sheet and answer key for blind listening.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import random
import shutil
import sys
import tempfile
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


ROOT = Path(__file__).resolve().parents[1]
PROJECT_DEPS = ROOT / ".python_deps"
ANTICIPATION_REPO = ROOT / "code" / "anticipation"

if PROJECT_DEPS.exists():
    sys.path.insert(0, str(PROJECT_DEPS))
sys.path.insert(0, str(ANTICIPATION_REPO))
sys.path.insert(0, str(ROOT / "code"))

from live_call_response import (  # noqa: E402
    CapturedNote,
    GeneratedEvent,
    HF_HUB_CACHE,
    MusicalResponseController,
    StreamingAMTGenerator,
    analyze_call_phrase,
    build_response_plan,
    clean_prompt_phrase,
    clamp_float,
    clamp_int,
    event_to_tokens,
    load_transformer_model,
    model_cache_complete,
    model_device_name,
    notes_to_amt_events,
)


SMALL_MODEL_ID = "stanford-crfm/music-small-800k"
MEDIUM_MODEL_ID = "stanford-crfm/music-medium-800k"
ARIA_MODEL_ID = str(ROOT / "model_weights" / "aria-medium-gen")
STYLE_FREE = "free"
STYLE_PENTATONIC_TWO_BAR = "pentatonic_2bar_4_4"


@dataclass(frozen=True)
class Candidate:
    name: str
    kind: str
    model_id: Optional[str] = None
    controlled: bool = False


@dataclass
class GeneratedSample:
    call_id: str
    trial: int
    candidate: Candidate
    combined_events: List[int]
    response_events: List[int]
    response_note_count: int
    call_note_count: int
    call_duration_seconds: float
    response_seconds: float
    control_stats: Dict[str, Any]


def require_runtime() -> None:
    missing = []
    failed = []
    for module_name in ("mido", "torch", "transformers", "anticipation"):
        try:
            __import__(module_name)
        except ImportError:
            missing.append(module_name)
        except OSError as exc:
            failed.append((module_name, exc))
    if missing:
        raise SystemExit(
            "Missing runtime modules: "
            + ", ".join(missing)
            + ". Use Python 3.12 with the project .python_deps installed."
        )
    if failed:
        details = "; ".join(f"{name}: {exc}" for name, exc in failed)
        raise SystemExit(
            "Runtime modules are present but failed to load native libraries. "
            f"Python executable: {sys.executable}. Details: {details}. "
            "Use C:\\Python312\\python.exe or reinstall torch for the current interpreter."
        )


def default_output_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return ROOT / "ab_tests" / f"run_{stamp}"


def collect_input_midis(args: argparse.Namespace) -> List[Path]:
    if bool(args.input_midi) == bool(args.input_dir):
        raise SystemExit("Pass exactly one of --input-midi or --input-dir.")

    if args.input_midi:
        path = Path(args.input_midi)
        if not path.exists():
            raise SystemExit(f"Input MIDI does not exist: {path}")
        return [path]

    root = Path(args.input_dir)
    if not root.exists():
        raise SystemExit(f"Input directory does not exist: {root}")
    files = sorted(
        [item for item in root.iterdir() if item.suffix.lower() in {".mid", ".midi"}]
    )
    if not files:
        raise SystemExit(f"No .mid/.midi files found in: {root}")
    return files


def read_call_notes(path: Path, default_duration: float) -> List[CapturedNote]:
    import mido

    midi = mido.MidiFile(path)
    tempo = 500000
    current_time = 0.0
    active: Dict[tuple[int, int], CapturedNote] = {}
    completed: List[CapturedNote] = []

    for msg in mido.merge_tracks(midi.tracks):
        current_time += mido.tick2second(msg.time, midi.ticks_per_beat, tempo)
        if msg.type == "set_tempo":
            tempo = msg.tempo
            continue
        if msg.type == "note_on" and msg.velocity > 0:
            active[(msg.channel, msg.note)] = CapturedNote(
                onset=current_time,
                pitch=msg.note,
                velocity=msg.velocity,
                channel=msg.channel,
            )
            continue
        is_note_off = msg.type == "note_off" or (
            msg.type == "note_on" and msg.velocity == 0
        )
        if is_note_off:
            note = active.pop((msg.channel, msg.note), None)
            if note is None:
                continue
            note.duration = max(current_time - note.onset, default_duration)
            completed.append(note)

    for note in active.values():
        note.duration = default_duration
        completed.append(note)

    completed.sort(key=lambda item: item.onset)
    if not completed:
        raise SystemExit(f"No note events found in input MIDI: {path}")

    first_onset = completed[0].onset
    return [
        CapturedNote(
            onset=note.onset - first_onset,
            pitch=note.pitch,
            velocity=note.velocity,
            channel=note.channel,
            duration=note.duration or default_duration,
        )
        for note in completed
    ]


def response_seconds_for(profile, args: argparse.Namespace) -> float:
    if args.style == STYLE_PENTATONIC_TWO_BAR and args.response_seconds == "auto":
        return style_duration_seconds(args)
    if args.response_seconds == "auto":
        return clamp_float(
            profile.duration_seconds,
            args.min_response_seconds,
            args.max_response_seconds,
        )
    value = float(args.response_seconds)
    return clamp_float(value, args.min_response_seconds, args.max_response_seconds)


def style_duration_seconds(args: argparse.Namespace) -> float:
    return (60.0 / args.bpm) * args.beats_per_bar * args.bars


def events_after_start(events: Iterable[int], start_seconds: float) -> List[int]:
    from anticipation.config import TIME_RESOLUTION
    from anticipation.vocab import ATIME_OFFSET, CONTROL_OFFSET, TIME_OFFSET

    start_tick = int(round(TIME_RESOLUTION * start_seconds))
    selected: List[int] = []
    triples = list(events)
    for time_token, duration_token, note_token in zip(
        triples[0::3], triples[1::3], triples[2::3]
    ):
        if note_token < CONTROL_OFFSET:
            event_tick = time_token - TIME_OFFSET
        else:
            event_tick = time_token - ATIME_OFFSET
        if event_tick > start_tick:
            selected.extend([time_token, duration_token, note_token])
    return selected


def event_tick(time_token: int, note_token: int) -> int:
    from anticipation.vocab import ATIME_OFFSET, CONTROL_OFFSET, TIME_OFFSET

    if note_token < CONTROL_OFFSET:
        return time_token - TIME_OFFSET
    return time_token - ATIME_OFFSET


def events_between_seconds(events: Iterable[int], start_seconds: float, end_seconds: float) -> List[int]:
    from anticipation.config import TIME_RESOLUTION

    start_tick = int(round(start_seconds * TIME_RESOLUTION))
    end_tick = int(round(end_seconds * TIME_RESOLUTION))
    selected: List[int] = []
    triples = list(events)
    for time_token, duration_token, note_token in zip(
        triples[0::3], triples[1::3], triples[2::3]
    ):
        tick = event_tick(time_token, note_token)
        if start_tick < tick <= end_tick:
            selected.extend([time_token, duration_token, note_token])
    return selected


def nearest_pentatonic_pitch(pitch: int, args: argparse.Namespace, lower: int, upper: int) -> int:
    candidates = pentatonic_candidates(args, lower, upper)
    if not candidates:
        return clamp_int(pitch, lower, upper)
    return min(candidates, key=lambda candidate: (abs(candidate - pitch), candidate))


def pentatonic_scale_degrees(args: argparse.Namespace) -> List[int]:
    if args.pentatonic_mode == "minor":
        return [0, 3, 5, 7, 10]
    return [0, 2, 4, 7, 9]


def stable_pentatonic_degrees(args: argparse.Namespace) -> List[int]:
    if args.pentatonic_mode == "minor":
        return [0, 3, 7]
    return [0, 4, 7]


def pentatonic_candidates(args: argparse.Namespace, lower: int, upper: int) -> List[int]:
    root_pc = args.pentatonic_root % 12
    scale_degrees = pentatonic_scale_degrees(args)
    candidates: List[int] = []
    for octave_base in range(-12, 140, 12):
        for degree in scale_degrees:
            candidate = octave_base + root_pc + degree
            if lower <= candidate <= upper:
                candidates.append(candidate)
    return candidates


def nearest_stable_pitch(pitch: int, args: argparse.Namespace, lower: int, upper: int) -> int:
    root_pc = args.pentatonic_root % 12
    degrees = stable_pentatonic_degrees(args)
    candidates: List[int] = []
    for octave_base in range(-12, 140, 12):
        for degree in degrees:
            candidate = octave_base + root_pc + degree
            if lower <= candidate <= upper:
                candidates.append(candidate)
    if not candidates:
        return nearest_pentatonic_pitch(pitch, args, lower, upper)
    return min(candidates, key=lambda candidate: (abs(candidate - pitch), candidate))


def cadence_pitch(reference_pitch: int, args: argparse.Namespace, lower: int, upper: int) -> int:
    degree = 7 if args.cadence_degree == "fifth" else 0
    root_pc = args.pentatonic_root % 12
    candidates: List[int] = []
    for octave_base in range(-12, 140, 12):
        candidate = octave_base + root_pc + degree
        if lower <= candidate <= upper:
            candidates.append(candidate)
    if not candidates:
        return nearest_stable_pitch(reference_pitch, args, lower, upper)
    return min(candidates, key=lambda candidate: (abs(candidate - reference_pitch), candidate))


def rhythm_grid_ticks(args: argparse.Namespace) -> int:
    if args.rhythm_grid == "none":
        return 1
    beat_ticks = (60.0 / args.bpm) * 100
    if args.rhythm_grid == "1/8":
        return max(1, int(round(beat_ticks / 2.0)))
    return max(1, int(round(beat_ticks / 4.0)))


def tokens_to_generated_event(token_triplet: List[int]) -> GeneratedEvent:
    from anticipation.vocab import ATIME_OFFSET, CONTROL_OFFSET, DUR_OFFSET, NOTE_OFFSET, TIME_OFFSET

    time_token, duration_token, note_token = token_triplet
    if note_token < CONTROL_OFFSET:
        tick = time_token - TIME_OFFSET
    else:
        tick = time_token - ATIME_OFFSET
    duration_ticks = max(1, duration_token - DUR_OFFSET)
    note = note_token - NOTE_OFFSET
    instrument = note // 128
    pitch = note - instrument * 128
    return GeneratedEvent(tick=tick, duration_ticks=duration_ticks, pitch=pitch, instrument=instrument)


def is_structural_strong_beat(relative_tick: int, args: argparse.Namespace) -> bool:
    if not args.strong_beat_stable:
        return False
    beat_ticks = max(1, int(round((60.0 / args.bpm) * 100)))
    bar_ticks = beat_ticks * args.beats_per_bar
    grid = rhythm_grid_ticks(args)
    tolerance = max(1, grid // 2)
    position = relative_tick % bar_ticks
    strong_positions = [0]
    if args.beats_per_bar >= 4:
        strong_positions.append(beat_ticks * 2)
    return any(abs(position - target) <= tolerance for target in strong_positions)


def limit_melodic_leap(
    pitch: int,
    previous_pitch: Optional[int],
    args: argparse.Namespace,
    lower: int,
    upper: int,
) -> int:
    pitch = nearest_pentatonic_pitch(pitch, args, lower, upper)
    if previous_pitch is None or args.max_melodic_leap <= 0:
        return pitch
    if abs(pitch - previous_pitch) <= args.max_melodic_leap:
        return pitch
    candidates = [
        candidate
        for candidate in pentatonic_candidates(args, lower, upper)
        if abs(candidate - previous_pitch) <= args.max_melodic_leap
    ]
    if not candidates:
        return pitch
    return min(candidates, key=lambda candidate: (abs(candidate - pitch), abs(candidate - previous_pitch)))


def apply_theory_rules(
    events: List[GeneratedEvent],
    start_tick: int,
    end_tick: int,
    args: argparse.Namespace,
) -> List[GeneratedEvent]:
    if not args.theory_control:
        return events

    beat_ticks = max(1, int(round((60.0 / args.bpm) * 100)))
    min_duration_ticks = max(1, int(round(args.min_note_duration * 100)))
    default_duration_ticks = max(min_duration_ticks, int(round(args.default_note_duration * 100)))
    lower, upper = args.pitch_min, args.pitch_max

    shaped: List[GeneratedEvent] = []
    previous_pitch: Optional[int] = None
    for event in sorted(events, key=lambda item: item.tick):
        pitch = limit_melodic_leap(event.pitch, previous_pitch, args, lower, upper)
        if is_structural_strong_beat(event.tick - start_tick, args):
            pitch = nearest_stable_pitch(pitch, args, lower, upper)
        shaped_event = GeneratedEvent(
            tick=event.tick,
            duration_ticks=event.duration_ticks,
            pitch=pitch,
            instrument=event.instrument,
        )
        shaped.append(shaped_event)
        previous_pitch = pitch

    cadence_tick = max(start_tick + 1, end_tick - beat_ticks)
    cadence_reference = previous_pitch if previous_pitch is not None else args.pentatonic_root
    final_pitch = cadence_pitch(cadence_reference, args, lower, upper)
    final_duration = min(default_duration_ticks, max(1, end_tick - cadence_tick))

    if not shaped:
        return [GeneratedEvent(cadence_tick, final_duration, final_pitch, 0)]

    last = shaped[-1]
    if last.tick >= cadence_tick - rhythm_grid_ticks(args):
        shaped[-1] = GeneratedEvent(
            tick=last.tick,
            duration_ticks=min(max(last.duration_ticks, final_duration), max(1, end_tick - last.tick)),
            pitch=final_pitch,
            instrument=last.instrument,
        )
    else:
        shaped.append(GeneratedEvent(cadence_tick, final_duration, final_pitch, last.instrument))

    return shaped


def apply_response_style(
    response_events: Iterable[int],
    call_events: List[int],
    args: argparse.Namespace,
) -> List[int]:
    if args.style == STYLE_FREE:
        return list(response_events)

    from anticipation import ops
    from anticipation.config import TIME_RESOLUTION

    start_tick = int(round(ops.max_time(call_events) * TIME_RESOLUTION))
    window_ticks = int(round(style_duration_seconds(args) * TIME_RESOLUTION))
    end_tick = start_tick + max(1, window_ticks)
    grid_ticks = rhythm_grid_ticks(args)
    min_duration_ticks = max(1, int(round(args.min_note_duration * TIME_RESOLUTION)))
    max_duration_ticks = max(min_duration_ticks, int(round(args.max_note_duration * TIME_RESOLUTION)))

    styled_events: List[GeneratedEvent] = []
    triples = list(response_events)
    for raw_event in zip(triples[0::3], triples[1::3], triples[2::3]):
        event = tokens_to_generated_event(list(raw_event))
        if event.tick < start_tick:
            continue
        relative_tick = max(1, event.tick - start_tick)
        if grid_ticks > 1:
            relative_tick = max(1, int(round(relative_tick / grid_ticks)) * grid_ticks)
        tick = start_tick + relative_tick
        if tick >= end_tick:
            continue
        duration = clamp_int(event.duration_ticks, min_duration_ticks, max_duration_ticks)
        duration = min(duration, max(1, end_tick - tick))
        pitch = nearest_pentatonic_pitch(event.pitch, args, args.pitch_min, args.pitch_max)
        styled_events.append(GeneratedEvent(tick, duration, pitch, event.instrument))

    styled_events = apply_theory_rules(styled_events, start_tick, end_tick, args)
    styled: List[int] = []
    for event in styled_events:
        styled.extend(event_to_tokens(event))
    return styled


def shift_events_to_zero(events: Iterable[int]) -> List[int]:
    from anticipation.vocab import ATIME_OFFSET, CONTROL_OFFSET, TIME_OFFSET

    triples = list(events)
    ticks: List[int] = []
    for time_token, _, note_token in zip(triples[0::3], triples[1::3], triples[2::3]):
        if note_token < CONTROL_OFFSET:
            ticks.append(time_token - TIME_OFFSET)
        else:
            ticks.append(time_token - ATIME_OFFSET)
    if not ticks:
        return []
    offset = min(ticks)
    shifted: List[int] = []
    for time_token, duration_token, note_token in zip(
        triples[0::3], triples[1::3], triples[2::3]
    ):
        if note_token < CONTROL_OFFSET:
            shifted_time = TIME_OFFSET + max(0, time_token - TIME_OFFSET - offset)
        else:
            shifted_time = ATIME_OFFSET + max(0, time_token - ATIME_OFFSET - offset)
        shifted.extend([shifted_time, duration_token, note_token])
    return shifted


def shift_events_by_ticks(events: Iterable[int], tick_delta: int) -> List[int]:
    from anticipation.vocab import ATIME_OFFSET, CONTROL_OFFSET, TIME_OFFSET

    if tick_delta == 0:
        return list(events)
    shifted: List[int] = []
    triples = list(events)
    for time_token, duration_token, note_token in zip(
        triples[0::3], triples[1::3], triples[2::3]
    ):
        if note_token < CONTROL_OFFSET:
            shifted_time = TIME_OFFSET + max(0, time_token - TIME_OFFSET + tick_delta)
        else:
            shifted_time = ATIME_OFFSET + max(0, time_token - ATIME_OFFSET + tick_delta)
        shifted.extend([shifted_time, duration_token, note_token])
    return shifted


def save_events_as_midi(events: List[int], path: Path, args: argparse.Namespace) -> None:
    from anticipation import ops
    from anticipation.convert import events_to_midi
    import mido

    path.parent.mkdir(parents=True, exist_ok=True)
    if events:
        midi = events_to_midi(ops.sort(events))
    else:
        midi = mido.MidiFile(ticks_per_beat=480)
        midi.tracks.append(mido.MidiTrack())
    if midi.tracks:
        midi.tracks[0].insert(
            0,
            mido.MetaMessage(
                "time_signature",
                numerator=args.beats_per_bar,
                denominator=4,
                time=0,
            ),
        )
        midi.tracks[0].insert(0, mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(args.bpm), time=0))
    midi.save(path)


def load_aria_bundle(model_id: str, args: argparse.Namespace) -> Dict[str, object]:
    if args.endpoint:
        os.environ["HF_ENDPOINT"] = args.endpoint
    if args.offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
    else:
        os.environ.pop("HF_HUB_OFFLINE", None)
        os.environ.pop("TRANSFORMERS_OFFLINE", None)

    try:
        import torch
        import transformers.tokenization_utils as tokenization_utils
        from transformers.tokenization_utils_base import BatchEncoding
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except ImportError as exc:
        raise SystemExit(
            "Missing Aria runtime dependencies. Install torch, transformers, and aria-utils "
            "in the project Python environment before running --include-aria."
        ) from exc
    if not hasattr(tokenization_utils, "BatchEncoding"):
        tokenization_utils.BatchEncoding = BatchEncoding

    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32
    logging.getLogger("ariautils.tokenizer").setLevel(logging.ERROR)
    print(f"[model] loading Aria candidate: {model_id} on {device}")
    model_path = Path(model_id)
    looks_like_path = model_path.is_absolute() or any(part in model_id for part in ("\\", "/"))
    if model_path.exists():
        model_source = str(model_path)
    elif looks_like_path:
        raise SystemExit(
            f"Aria model path does not exist: {model_path}. "
            "Download aria-medium-gen model.safetensors and run prepare_aria_gen_local.py first."
        )
    else:
        model_source = model_id
    common_kwargs = {
        "trust_remote_code": True,
        "cache_dir": str(HF_HUB_CACHE),
        "local_files_only": args.offline,
    }
    if args.hf_token:
        common_kwargs["token"] = args.hf_token
    try:
        tokenizer = AutoTokenizer.from_pretrained(
            model_source,
            **common_kwargs,
        )
        model = AutoModelForCausalLM.from_pretrained(
            model_source,
            **common_kwargs,
            dtype=dtype,
        )
    except Exception as exc:
        mode = "local cache" if args.offline else "Hugging Face"
        raise SystemExit(
            f"Failed to load Aria model '{model_id}' from {mode}. "
            "For the first run, use --no-offline so the model and remote code can be cached, "
            "or prepare a local folder with prepare_aria_gen_local.py and pass that folder as --aria-model-id."
        ) from exc
    model.to(device)
    model.eval()
    return {"model": model, "tokenizer": tokenizer, "device": device}


def generate_aria_continuation(
    bundle: Dict[str, object],
    input_path: Path,
    call_events: List[int],
    response_seconds: float,
    args: argparse.Namespace,
) -> tuple[List[int], List[int], Dict[str, object]]:
    from anticipation import ops
    from anticipation.convert import midi_to_events
    import torch

    model = bundle["model"]
    tokenizer = bundle["tokenizer"]
    device = str(bundle["device"])
    prompt = tokenizer.encode_from_file(str(input_path), return_tensors="pt")
    input_ids = prompt.input_ids[..., : args.aria_prompt_tokens].to(device)
    max_length = input_ids.shape[-1] + args.aria_max_new_tokens

    with torch.inference_mode():
        generated = model.generate(
            input_ids,
            max_length=max_length,
            do_sample=True,
            temperature=args.aria_temperature,
            top_p=args.aria_top_p,
            use_cache=True,
        )

    midi_dict = tokenizer.decode(generated[0].detach().cpu().tolist())
    with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as handle:
        temp_path = Path(handle.name)
    try:
        midi_dict.to_midi().save(temp_path)
        generated_events = midi_to_events(str(temp_path))
    finally:
        temp_path.unlink(missing_ok=True)

    start_seconds = ops.max_time(call_events)
    response_events = events_between_seconds(
        generated_events,
        start_seconds=start_seconds,
        end_seconds=start_seconds + response_seconds,
    )
    if args.aria_apply_style:
        response_events = apply_response_style(response_events, call_events, args)
    combined = ops.sort(call_events + response_events)
    return combined, response_events, {
        "rejected_repeat_count": 0,
        "rejected_dominant_count": 0,
        "fallback_count": 0 if response_events else 1,
        "generation_error": "",
    }


def generate_raw_amt(
    model,
    call_events: List[int],
    response_seconds: float,
    args: argparse.Namespace,
) -> tuple[List[int], List[int]]:
    from anticipation import ops
    from anticipation.sample import generate

    start_time = ops.max_time(call_events)
    combined = generate(
        model,
        start_time=start_time,
        end_time=start_time + response_seconds,
        inputs=call_events,
        top_p=args.top_p,
        debug=args.debug,
    )
    combined = ops.sort(combined)
    response = events_after_start(combined, start_time)
    response = apply_response_style(response, call_events, args)
    combined = ops.sort(call_events + response)
    return combined, response


def generate_controlled_amt(
    model,
    notes: List[CapturedNote],
    response_seconds: float,
    args: argparse.Namespace,
) -> tuple[List[int], List[int], Dict[str, int]]:
    from anticipation import ops
    from anticipation.config import TIME_RESOLUTION

    profile = analyze_call_phrase(notes)
    prompt_notes = clean_prompt_phrase(notes, profile)
    plan = build_response_plan(
        profile=profile,
        response_seconds=response_seconds,
        max_events=args.max_events,
        pitch_min=args.pitch_min,
        pitch_max=args.pitch_max,
        response_length_ratio=args.response_length_ratio,
        response_note_ratio=args.response_note_ratio,
        same_pitch_limit=args.same_pitch_limit,
        dominant_pitch_max_share=args.dominant_pitch_max_share,
        resample_attempts=args.resample_attempts,
    )
    controller = MusicalResponseController(profile, plan)
    original_call_events = notes_to_amt_events(notes)
    prompt_call_events = notes_to_amt_events(prompt_notes)
    generator = StreamingAMTGenerator(
        model,
        top_p=args.top_p,
        temperature=args.temperature,
    )
    response_generated: List[GeneratedEvent] = []
    for event in generator.generate_events(
        call_events=prompt_call_events,
        response_seconds=plan.target_seconds,
        stop_event=threading.Event(),
        controller=controller,
    ):
        response_generated.append(event)
        if len(response_generated) >= plan.target_notes:
            break

    response_events: List[int] = []
    for event in response_generated:
        response_events.extend(event_to_tokens(event))
    prompt_start_tick = int(round(ops.max_time(prompt_call_events) * TIME_RESOLUTION))
    original_start_tick = int(round(ops.max_time(original_call_events) * TIME_RESOLUTION))
    response_events = shift_events_by_ticks(response_events, original_start_tick - prompt_start_tick)
    response_events = apply_response_style(response_events, original_call_events, args)
    combined = ops.sort(original_call_events + response_events)
    return combined, response_events, asdict(controller.stats)


def generate_motif_baseline(
    notes: List[CapturedNote],
    response_seconds: float,
    args: argparse.Namespace,
    rng: random.Random,
) -> tuple[List[int], List[int], Dict[str, int]]:
    from anticipation import ops
    from anticipation.config import TIME_RESOLUTION

    profile = analyze_call_phrase(notes)
    prompt_notes = clean_prompt_phrase(notes, profile)
    plan = build_response_plan(
        profile=profile,
        response_seconds=response_seconds,
        max_events=args.max_events,
        pitch_min=args.pitch_min,
        pitch_max=args.pitch_max,
        response_length_ratio=args.response_length_ratio,
        response_note_ratio=args.response_note_ratio,
        same_pitch_limit=args.same_pitch_limit,
        dominant_pitch_max_share=args.dominant_pitch_max_share,
        resample_attempts=args.resample_attempts,
    )

    call_events = notes_to_amt_events(notes)
    source = prompt_notes or notes
    if not source:
        return call_events, [], {"rejected_repeat_count": 0, "rejected_dominant_count": 0, "fallback_count": 0}

    target_count = min(plan.target_notes, max(1, len(source)))
    selected = list(reversed(source[-target_count:]))
    src_duration = max(
        (note.onset + max(note.duration or args.default_note_duration, 0.001))
        for note in selected
    )
    src_start = min(note.onset for note in selected)
    src_span = max(src_duration - src_start, 0.001)
    scale = plan.target_seconds / src_span
    start_tick = int(round(ops.max_time(call_events) * TIME_RESOLUTION))
    center = int(round(profile.mean_pitch))
    transpose = rng.choice([-5, -3, 2, 3, 5, 7])

    response: List[GeneratedEvent] = []
    pitch_counts: Dict[int, int] = {}
    for idx, note in enumerate(selected):
        source_offset = note.onset - src_start
        tick = start_tick + max(1, int(round(source_offset * scale * TIME_RESOLUTION)))
        duration = clamp_int(
            int(round(max(note.duration or args.default_note_duration, 0.04) * scale * TIME_RESOLUTION)),
            max(1, int(round(args.min_note_duration * TIME_RESOLUTION))),
            max(1, int(round(args.max_note_duration * TIME_RESOLUTION))),
        )
        inverted = center - (note.pitch - center)
        pitch = clamp_int(inverted + transpose, plan.pitch_min, plan.pitch_max)

        same_run = 0
        for previous in reversed(response):
            if previous.pitch != pitch:
                break
            same_run += 1
        if same_run >= plan.same_pitch_limit:
            pitch = clamp_int(pitch + rng.choice([-2, 2, -3, 3, 5]), plan.pitch_min, plan.pitch_max)
        projected = len(response) + 1
        if projected >= 4 and (pitch_counts.get(pitch, 0) + 1) / projected > plan.dominant_pitch_max_share:
            pitch = clamp_int(center + rng.choice([-7, -5, -2, 2, 5, 7]), plan.pitch_min, plan.pitch_max)

        response.append(GeneratedEvent(tick=tick, duration_ticks=duration, pitch=pitch, instrument=0))
        pitch_counts[pitch] = pitch_counts.get(pitch, 0) + 1

    response_events: List[int] = []
    for event in response:
        response_events.extend(event_to_tokens(event))
    response_events = apply_response_style(response_events, call_events, args)
    return ops.sort(call_events + response_events), response_events, {
        "rejected_repeat_count": 0,
        "rejected_dominant_count": 0,
        "fallback_count": 0,
    }


def build_candidates(args: argparse.Namespace) -> List[Candidate]:
    candidates = [
        Candidate("amt_small_raw", "amt", SMALL_MODEL_ID, controlled=False),
        Candidate("amt_small_controlled", "amt", SMALL_MODEL_ID, controlled=True),
        Candidate("motif_transform_baseline", "motif", None, controlled=True),
    ]
    if args.include_medium:
        if args.offline and not model_cache_complete(MEDIUM_MODEL_ID):
            print(
                f"[warn] skipping medium candidates because {MEDIUM_MODEL_ID} is not cached locally"
            )
        else:
            candidates.extend(
                [
                    Candidate("amt_medium_raw", "amt", MEDIUM_MODEL_ID, controlled=False),
                    Candidate("amt_medium_controlled", "amt", MEDIUM_MODEL_ID, controlled=True),
                ]
            )
    if args.include_aria:
        candidates.append(Candidate("aria_medium_gen", "aria", args.aria_model_id, controlled=False))
    if args.candidates:
        requested = {item.strip() for item in args.candidates.split(",") if item.strip()}
        known = {candidate.name for candidate in candidates}
        unknown = sorted(requested - known)
        if unknown:
            raise SystemExit(f"Unknown candidate(s): {unknown}. Available: {sorted(known)}")
        candidates = [candidate for candidate in candidates if candidate.name in requested]
    return candidates


def write_score_sheet(path: Path, sample_ids: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "sample_id",
        "rater",
        "musicality",
        "relevance",
        "continuity",
        "diversity",
        "repetition_problem",
        "overall",
        "notes",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for sample_id in sample_ids:
            writer.writerow({"sample_id": sample_id})


def write_answer_key(path: Path, rows: List[Dict[str, object]]) -> None:
    fields = [
        "sample_id",
        "call_id",
        "trial",
        "candidate",
        "model_id",
        "call_notes",
        "response_notes",
        "call_duration_seconds",
        "response_seconds",
        "combined_midi",
        "response_only_midi",
        "rejected_repeat_count",
        "rejected_dominant_count",
        "fallback_count",
        "generation_error",
        "style",
        "bpm",
        "bars",
        "beats_per_bar",
        "pentatonic_root",
        "pentatonic_mode",
        "theory_control",
        "strong_beat_stable",
        "cadence_degree",
        "max_melodic_leap",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def generate_samples(args: argparse.Namespace) -> None:
    require_runtime()

    from anticipation.convert import midi_to_events

    output_dir = Path(args.output_dir) if args.output_dir else default_output_dir()
    samples_dir = output_dir / "samples"
    responses_dir = output_dir / "responses"
    calls_dir = output_dir / "calls"
    output_dir.mkdir(parents=True, exist_ok=True)
    samples_dir.mkdir(exist_ok=True)
    responses_dir.mkdir(exist_ok=True)
    calls_dir.mkdir(exist_ok=True)

    rng = random.Random(args.seed)
    input_midis = collect_input_midis(args)
    if args.max_calls is not None:
        input_midis = input_midis[: args.max_calls]
    candidates = build_candidates(args)

    if not candidates:
        raise SystemExit("No candidates selected.")

    needed_amt_models = sorted(
        {
            candidate.model_id
            for candidate in candidates
            if candidate.kind == "amt" and candidate.model_id is not None
        }
    )
    models = {}
    for model_id in needed_amt_models:
        if args.offline and not model_cache_complete(model_id):
            raise SystemExit(
                f"No complete local snapshot for {model_id}. Run once without --offline to download it."
            )
        print(f"[model] loading candidate model: {model_id}")
        models[model_id] = load_transformer_model(model_id, args.offline, args.endpoint)
        print(f"[model] device={model_device_name(models[model_id])}")
    aria_bundles = {}
    for model_id in sorted(
        {
            candidate.model_id
            for candidate in candidates
            if candidate.kind == "aria" and candidate.model_id is not None
        }
    ):
        aria_bundles[model_id] = load_aria_bundle(model_id, args)

    generated: List[GeneratedSample] = []
    for input_index, input_path in enumerate(input_midis, start=1):
        call_id = input_path.stem
        copied_call = calls_dir / f"C{input_index:03d}_{input_path.name}"
        shutil.copy2(input_path, copied_call)

        notes = read_call_notes(input_path, args.default_note_duration)
        profile = analyze_call_phrase(notes)
        response_seconds = response_seconds_for(profile, args)
        call_events_for_raw = midi_to_events(str(input_path))

        print(
            f"[call] {input_path.name}: notes={profile.note_count} "
            f"duration={profile.duration_seconds:.3f}s response={response_seconds:.3f}s"
        )
        for trial in range(1, args.trials + 1):
            for candidate in candidates:
                print(f"[generate] call={call_id} trial={trial} candidate={candidate.name}")
                if candidate.kind == "motif":
                    combined, response, stats = generate_motif_baseline(
                        notes, response_seconds, args, rng
                    )
                elif candidate.kind == "aria":
                    try:
                        bundle = aria_bundles[candidate.model_id]
                        combined, response, stats = generate_aria_continuation(
                            bundle,
                            input_path,
                            call_events_for_raw,
                            response_seconds,
                            args,
                        )
                    except Exception as exc:
                        print(f"[warn] Aria generation failed for {call_id} trial={trial}: {exc}")
                        combined, response, stats = call_events_for_raw, [], {
                            "rejected_repeat_count": 0,
                            "rejected_dominant_count": 0,
                            "fallback_count": 1,
                            "generation_error": str(exc),
                        }
                elif candidate.controlled:
                    model = models[candidate.model_id]
                    combined, response, stats = generate_controlled_amt(
                        model, notes, response_seconds, args
                    )
                else:
                    model = models[candidate.model_id]
                    combined, response = generate_raw_amt(
                        model,
                        call_events_for_raw,
                        response_seconds,
                        args,
                    )
                    stats = {
                        "rejected_repeat_count": 0,
                        "rejected_dominant_count": 0,
                        "fallback_count": 0,
                        "generation_error": "",
                    }

                generated.append(
                    GeneratedSample(
                        call_id=call_id,
                        trial=trial,
                        candidate=candidate,
                        combined_events=combined,
                        response_events=response,
                        response_note_count=len(response) // 3,
                        call_note_count=profile.note_count,
                        call_duration_seconds=profile.duration_seconds,
                        response_seconds=response_seconds,
                        control_stats=stats,
                    )
                )

    rng.shuffle(generated)
    answer_rows: List[Dict[str, object]] = []
    sample_ids: List[str] = []
    for index, sample in enumerate(generated, start=1):
        sample_id = f"S{index:03d}"
        sample_ids.append(sample_id)
        combined_path = samples_dir / f"{sample_id}.mid"
        response_path = responses_dir / f"{sample_id}_response_only.mid"
        save_events_as_midi(sample.combined_events, combined_path, args)
        save_events_as_midi(shift_events_to_zero(sample.response_events), response_path, args)
        answer_rows.append(
            {
                "sample_id": sample_id,
                "call_id": sample.call_id,
                "trial": sample.trial,
                "candidate": sample.candidate.name,
                "model_id": sample.candidate.model_id or "",
                "call_notes": sample.call_note_count,
                "response_notes": sample.response_note_count,
                "call_duration_seconds": f"{sample.call_duration_seconds:.6f}",
                "response_seconds": f"{sample.response_seconds:.6f}",
                "combined_midi": str(combined_path),
                "response_only_midi": str(response_path),
                "rejected_repeat_count": sample.control_stats.get("rejected_repeat_count", 0),
                "rejected_dominant_count": sample.control_stats.get("rejected_dominant_count", 0),
                "fallback_count": sample.control_stats.get("fallback_count", 0),
                "generation_error": sample.control_stats.get("generation_error", ""),
                "style": args.style,
                "bpm": args.bpm,
                "bars": args.bars,
                "beats_per_bar": args.beats_per_bar,
                "pentatonic_root": args.pentatonic_root,
                "pentatonic_mode": args.pentatonic_mode,
                "theory_control": args.theory_control,
                "strong_beat_stable": args.strong_beat_stable,
                "cadence_degree": args.cadence_degree,
                "max_melodic_leap": args.max_melodic_leap,
            }
        )

    write_answer_key(output_dir / "answer_key.csv", answer_rows)
    write_score_sheet(output_dir / "score_sheet.csv", sample_ids)
    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "seed": args.seed,
        "input_midis": [str(path) for path in input_midis],
        "candidates": [asdict(candidate) for candidate in candidates],
        "sample_count": len(generated),
        "trials": args.trials,
        "args": vars(args),
    }
    with (output_dir / "manifest.json").open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)

    print(f"[done] output_dir={output_dir}")
    print(f"[done] blind samples={samples_dir}")
    print(f"[done] response only={responses_dir}")
    print(f"[done] score sheet={output_dir / 'score_sheet.csv'}")
    print(f"[done] answer key={output_dir / 'answer_key.csv'}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Offline blind A/B generator for MIDI Call-and-Response")
    parser.add_argument("--input-midi", default=None, help="single human Call MIDI path")
    parser.add_argument("--input-dir", default=None, help="directory of human Call MIDI files")
    parser.add_argument("--output-dir", default=None, help="output run directory")
    parser.add_argument("--trials", type=int, default=3, help="samples per candidate per Call")
    parser.add_argument("--seed", type=int, default=20260529, help="random seed for blinding and motif variants")
    parser.add_argument("--offline", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--include-medium", action="store_true", help="also include AMT medium candidates")
    parser.add_argument("--include-aria", action="store_true", help="also include Aria piano continuation")
    parser.add_argument("--aria-model-id", default=ARIA_MODEL_ID)
    parser.add_argument("--aria-prompt-tokens", type=int, default=512)
    parser.add_argument("--aria-max-new-tokens", type=int, default=768)
    parser.add_argument("--aria-temperature", type=float, default=0.97)
    parser.add_argument("--aria-top-p", type=float, default=0.95)
    parser.add_argument(
        "--aria-apply-style",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="apply the local pentatonic style rules to Aria continuations before scoring",
    )
    parser.add_argument(
        "--candidates",
        default=None,
        help="comma-separated candidate names, e.g. amt_small_controlled,motif_transform_baseline,aria_medium_gen",
    )
    parser.add_argument("--max-calls", type=int, default=None, help="limit number of input Calls for smoke tests")
    parser.add_argument("--endpoint", default=None, help="optional HuggingFace endpoint")
    parser.add_argument("--hf-token", default=None, help="optional Hugging Face access token for gated/private Aria checkpoints")
    parser.add_argument("--response-seconds", default="auto", help="'auto' or an explicit response length in seconds")
    parser.add_argument("--min-response-seconds", type=float, default=2.0)
    parser.add_argument("--max-response-seconds", type=float, default=8.0)
    parser.add_argument(
        "--style",
        choices=[STYLE_PENTATONIC_TWO_BAR, STYLE_FREE],
        default=STYLE_PENTATONIC_TWO_BAR,
        help="default constrains responses to 4/4 two-bar pentatonic phrases",
    )
    parser.add_argument("--bpm", type=float, default=100.0, help="tempo for two-bar style metadata and duration")
    parser.add_argument("--bars", type=int, default=2, help="number of response bars in styled mode")
    parser.add_argument("--beats-per-bar", type=int, default=4, help="time-signature numerator in styled mode")
    parser.add_argument("--pentatonic-root", type=int, default=60, help="MIDI root pitch; 60 means C")
    parser.add_argument(
        "--pentatonic-mode",
        choices=["major", "minor"],
        default="major",
        help="major uses scale degrees 1-2-3-5-6; minor uses 1-b3-4-5-b7",
    )
    parser.add_argument(
        "--rhythm-grid",
        choices=["none", "1/8", "1/16"],
        default="1/16",
        help="quantize response onsets in styled mode",
    )
    parser.add_argument(
        "--theory-control",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="apply explicit music-theory rules after generation",
    )
    parser.add_argument(
        "--strong-beat-stable",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="prefer stable scale tones on beats 1 and 3",
    )
    parser.add_argument(
        "--cadence-degree",
        choices=["root", "fifth"],
        default="root",
        help="force the final phrase tone toward the tonic or fifth",
    )
    parser.add_argument(
        "--max-melodic-leap",
        type=int,
        default=7,
        help="maximum preferred melodic leap in semitones; 0 disables leap limiting",
    )
    parser.add_argument("--max-events", type=int, default=64)
    parser.add_argument("--top-p", type=float, default=0.98)
    parser.add_argument("--temperature", type=float, default=0.9)
    parser.add_argument("--pitch-min", type=int, default=36)
    parser.add_argument("--pitch-max", type=int, default=96)
    parser.add_argument("--min-note-duration", type=float, default=0.04)
    parser.add_argument("--max-note-duration", type=float, default=2.5)
    parser.add_argument("--default-note-duration", type=float, default=0.25)
    parser.add_argument("--same-pitch-limit", type=int, default=2)
    parser.add_argument("--dominant-pitch-max-share", type=float, default=0.35)
    parser.add_argument("--resample-attempts", type=int, default=8)
    parser.add_argument("--response-length-ratio", type=float, default=1.0)
    parser.add_argument("--response-note-ratio", type=float, default=1.0)
    parser.add_argument("--debug", action="store_true", help="print verbose AMT sampling debug")
    return parser


def validate_args(args: argparse.Namespace) -> None:
    if args.trials < 1:
        raise SystemExit("--trials must be at least 1.")
    if args.max_calls is not None and args.max_calls < 1:
        raise SystemExit("--max-calls must be at least 1 when provided.")
    if args.min_response_seconds <= 0 or args.max_response_seconds <= 0:
        raise SystemExit("--min-response-seconds and --max-response-seconds must be positive.")
    if args.min_response_seconds > args.max_response_seconds:
        raise SystemExit("--min-response-seconds cannot exceed --max-response-seconds.")
    if args.bpm <= 0:
        raise SystemExit("--bpm must be positive.")
    if args.bars < 1:
        raise SystemExit("--bars must be at least 1.")
    if args.beats_per_bar < 1:
        raise SystemExit("--beats-per-bar must be at least 1.")
    if args.style == STYLE_PENTATONIC_TWO_BAR:
        styled_seconds = style_duration_seconds(args)
        if styled_seconds > args.max_response_seconds:
            args.max_response_seconds = styled_seconds
        if styled_seconds < args.min_response_seconds:
            args.min_response_seconds = styled_seconds
    if args.response_seconds != "auto":
        try:
            float(args.response_seconds)
        except ValueError as exc:
            raise SystemExit("--response-seconds must be 'auto' or a number.") from exc
    if args.pitch_min < 0 or args.pitch_max > 127 or args.pitch_min > args.pitch_max:
        raise SystemExit("--pitch-min/--pitch-max must define a valid MIDI pitch range.")
    if args.max_events < 1:
        raise SystemExit("--max-events must be at least 1.")
    if not 0 <= args.pentatonic_root <= 127:
        raise SystemExit("--pentatonic-root must be a MIDI pitch from 0 to 127.")
    if args.max_melodic_leap < 0:
        raise SystemExit("--max-melodic-leap cannot be negative.")
    if args.same_pitch_limit < 1:
        raise SystemExit("--same-pitch-limit must be at least 1.")
    if not 0.0 < args.dominant_pitch_max_share <= 1.0:
        raise SystemExit("--dominant-pitch-max-share must be in (0, 1].")
    if args.aria_prompt_tokens < 1:
        raise SystemExit("--aria-prompt-tokens must be at least 1.")
    if args.aria_max_new_tokens < 1:
        raise SystemExit("--aria-max-new-tokens must be at least 1.")
    if args.aria_temperature <= 0:
        raise SystemExit("--aria-temperature must be positive.")
    if not 0.0 < args.aria_top_p <= 1.0:
        raise SystemExit("--aria-top-p must be in (0, 1].")


def main() -> None:
    args = build_parser().parse_args()
    validate_args(args)
    generate_samples(args)


if __name__ == "__main__":
    main()
