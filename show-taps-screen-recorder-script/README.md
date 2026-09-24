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

## 3. Evaluate against ground truth

```
python evaluate.py output_dir ground_truth.json
```

`ground_truth.json`: `[{"type": "TAP", "approx_time_s": 2.1}, {"type": "SWIPE", "approx_time_s": 4.0}]`.
Events are matched by nearest time (±0.5 s); prints the table plus TAP precision/recall.

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

Generates a synthetic video (swipe, tap, long press), runs the whole pipeline and checks the labels.

## Pipeline

`video_io` (PyAV frames + timestamps) → `detector` (masked multi-scale template matching,
HoughCircles fallback) → `tracker` (detections → touch events) → `classifier`
(TAP / LONG_PRESS / SWIPE / MULTI_TOUCH / NOISE) → `extractor` (two passes: detect, then fetch
only the needed frames by timestamp).
