# tap-extractor

Extract the frames where a **tap** happened from an Android screen recording made with
*Developer options → Show taps* enabled. Swipes, long presses and multi-finger gestures
are detected and discarded. Plain command-line scripts; the pipeline lives in importable
functions (`extractor.extract_taps(...)`) so it can be wrapped in an API later.

## Install

```
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

## 1. Calibrate (once per phone / resolution)

Find a moment in the video where a finger is down, then:

```
python calibrate.py video.mp4 --time 3.2
```

Click the circle's center, then click on (or drag to) its edge; press Enter to save.
Without a GUI, give the circle directly:

```
python calibrate.py video.mp4 --time 3.2 --x 540 --y 1200 --radius 28
```

This writes `calibration/frame.png`, `calibration/template.png` and `calibration/circle.json`.

## 2. Extract taps

```
python extract_taps.py video.mp4 --out output_dir [--debug]
```

Output:

| Path | Contents |
|---|---|
| `taps/tap_001_before.png` | Clean frame ~100 ms before the touch (verified circle-free, steps back up to 500 ms) |
| `taps/tap_001_touch.png` | First frame showing the circle |
| `taps/tap_001_after.png` | Frame ~700 ms after the finger lifted (or the last frame) |
| `taps.json` | One entry per tap: `id, tap_x, tap_y, touch_ms, duration_ms, before, touch, after` |
| `events_report.json` | Every event, including discarded ones, with `label` and `reason` |
| `debug/annotated.mp4` | (`--debug`) circles outlined + current event label |

Coordinates are in original video pixels; times are real presentation timestamps (the
recording's variable frame rate is respected).

## 2b. Log-guided extraction (`--log`, recommended)

If the recording was made with TapScreenRecorder's tap logger on, pass its JSON log too:

```
python extract_taps.py TapRec_X.mp4 --log TapRec_X.json --out output_dir [--debug]
```

The log says *that* a tap happened, roughly *when* and *on which element*; the video then
gives the exact touch-down frame and finger point. Each log event becomes a candidate
with a search box and time window:

| Log evidence | Search box | Window | Confidence |
|---|---|---|---|
| `VIEW_CLICKED` / `VIEW_LONG_CLICKED` (and the app's own `APP_STOP_TAPPED`) | element bounds + 60 px (whole screen if none) | t−800 … t+150 ms | high (medium without bounds) |
| `VIEW_TEXT_CHANGED` adding one character | that key's box from the keyboard snapshot | t−600 … t+150 ms | high (whole keyboard, medium, for suggestions / unknown keys) |
| New screen (`WINDOW_STATE_CHANGED`) with no click/key in the second before | whole screen | t−1000 … t | low |

In each window the locator looks for a circle **appearing**: nearly every pixel inside the
disk changes while a ring just outside it does not, and the template score jumps. That
rejects key glyphs that resemble the circle, animations, screen transitions and the fade-out
after lift. The latest touch-down in the window is kept, then traced until the finger lifts.

`taps.json` entries gain `touch_frame_index`, `lift_ms`, `match_score`, `source`
(`click` / `keyboard` / `window_change`), `confidence`, `log_event_ms` and `element`
(`text`, `resourceId`, `className`). `events_report.json` lists every candidate, including
`NOT_FOUND` ones (with the reason) and candidates merged into another (`merged_log_events`).

The log must come from the same recording. A resized copy works (coordinates are scaled),
but a small circle is found less reliably, so prefer the original file
(`adb pull /sdcard/Movies/TapScreenRecorder/...`). Taps with no log event and no screen
change (e.g. on blank space) are not found in this mode.

## 3. Evaluate against ground truth

```
python evaluate.py output_dir ground_truth.json
```

`ground_truth.json`: `[{"type": "TAP", "approx_time_s": 2.1}, {"type": "SWIPE", "approx_time_s": 4.0}]`.
Events are matched by nearest time (±0.5 s); prints the table plus TAP precision/recall.
Optional per entry: `x`, `y` (reports the position error in pixels) and `source` (per-source
precision/recall for `--log` output). See `samples/TapRec_20260925_000001_ground_truth.json`.

## Tuning

All thresholds are in `config.py` (`Config`). The ones you are most likely to touch:

- `match_threshold`: lower it if circles are missed, raise it if background gets detected.
- `tap_max_displacement_px` (25 at 1080 px width, scaled automatically) and `tap_max_duration_ms` (400).
- `max_gap_ms`: how long a circle may go undetected inside one touch.

Check `events_report.json` and `--debug` output to see why each event got its label.

## Self-test

```
python selftest.py
```

Generates a synthetic video (swipe, tap, long press), runs the whole pipeline and checks the labels,
then runs `--log` mode on it with a synthetic tap log.

## Pipeline

`video_io` (PyAV frames + timestamps) → `detector` (masked multi-scale template matching,
HoughCircles fallback) → `tracker` (detections → touch events) → `classifier`
(TAP / LONG_PRESS / SWIPE / MULTI_TOUCH / NOISE) → `extractor` (two passes: detect, then fetch
only the needed frames by timestamp).

With `--log`: `log_io` (load / validate / scale the log) → `candidates` (log events → search
boxes and windows) → `locator` (find the circle appearing, trace to lift) → `classifier` →
`extractor.extract_taps_with_log`. Log-mode thresholds are at the end of `config.py`.

## 4. Build a flow (`flow.json`)

```
python flow_builder_cli.py output_dir/taps.json output_dir/events_report.json --out flow.json
```

Turns the confirmed taps of a `--log` run into an ordered list of semantic steps for the flow
abstractor. It only transforms data: no OCR, no LLM, no network. The before/touch/after frames
each step uses are copied to `flow_frames/` (`<out stem>_frames/` next to `--out`, or
`--frames-dir DIR`) as `step_NNN_before|touch|after.png`, so the flow is self-contained. Pass
`--no-copy-frames` to point at the original `taps/` frames instead. Frame paths are relative
to `flow.json`.

`flow.json` is a list of steps ordered by time, each with `step_index` (0, 1, 2, …) and `type`:

**`type_text`**: two or more consecutive keyboard taps, merged into one step.

| Field | Meaning |
|---|---|
| `text` | Text typed in this step: keystrokes replayed in log order (`type` appends, `delete` removes the last character, `set_field` replaces everything) |
| `deleted_existing_chars` | Backspaces that removed text already in the field before the step (e.g. clearing "Tap" before typing) |
| `field_text_before` / `field_text_after` | Field text before the first and after the last keystroke, from the log (`null` if unknown) |
| `start_ms` / `end_ms` | `touch_ms` of the first keystroke / `lift_ms` of the last |
| `before_frame` / `after_frame` | Before frame of the first keystroke / after frame of the last |
| `source_tap_ids` | ids of the merged taps in `taps.json` |
| `characters` | `[{action, char, source_tap_id, log_event_ms, reconstructed_from_log}]`; `action` is `type`, `delete` (backspace) or `set_field` (a suggestion or autocomplete) |
| `reconstructed_from_log` | `true` if any keystroke came from the log alone |

Keyboard log events that did not become a tap are added back at their log-time position if
their `log_event_ms` falls inside `start_ms … end_ms`. That covers events `NOT_FOUND` in the
video, events found but labelled `LONG_PRESS`/`SWIPE`, and events merged into another touch
(`merged_log_events`: the second press of a double letter, where the circle never disappears
between presses). They get `source_tap_id: null` and `reconstructed_from_log: true`, and have
no frame. A single keystroke that is not next to another one stays a `tap` step.

A click `element` also has `bounds` (the clicked element's box in video pixels, from the log;
`null` if the log had none). In `taps.json` and `events_report.json`, a keyboard `element` also has `field_before` and
`field_after`, and a single-character deletion has `className: "backspace"` with `text: null`.
The backspace key has no label in the keyboard snapshot, so its box is taken as the bottom
letter row to the right of "m" (QWERTY layout). A keystroke's touch is cut
`keyboard_lift_slack_ms` (50 ms) after its log event, because the key's text change fires on
key-up. Without that cut, a trace can run on past the lift, since key glyphs look like the
circle to the template.

**`tap`**: any other tap, passed through as it is.

| Field | Meaning |
|---|---|
| `tap_x`, `tap_y` | Finger position in video pixels |
| `touch_ms`, `lift_ms` | Touch-down and lift times |
| `before_frame`, `touch_frame`, `after_frame` | The tap's three frames |
| `source_tap_id`, `source` | Original tap id and its log source (`click` / `keyboard` / `window_change`) |
| `element` | Log element (`text`, `resourceId`, `className`), unchanged |
| `element_trustworthy` | `true` for `click` / `keyboard`. `false` for `window_change`, where `element` is the *destination screen*, not the tapped control, so locate this step by visual grounding rather than by resourceId/text matching |

At the end the CLI prints the total number of steps, the number of `type_text` and `tap` steps,
and how many taps need visual grounding.
