# Architecture

Goal: from a screen recording **and** its tap log, output for every tap the **exact frame**
the finger touched down and the **exact (x, y) pixel**.

```
 PHONE (TapScreenRecorder app)                 PC (this script)
 ─────────────────────────────                 ──────────────────────────────────────────────
 screen  ──► TapRec_X.mp4 ─────────────┐
 taps    ──► TapRec_X.json (tap log) ──┤
                                       ▼
                               extract_taps.py  (command line)
                                       │
                                       ▼
                               extractor.py  extract_taps_with_log()
                                       │
      ┌──────────────┬─────────────────┼─────────────────┬──────────────┐
      ▼              ▼                 ▼                 ▼              ▼
  1 log_io.py   2 candidates.py   3 locator.py     4 classifier.py   5 extractor.py
  read log      where/when        find the         TAP / SWIPE /     write taps.json,
                to look           circle           LONG_PRESS        report, PNGs
                                       │
                          uses: video_io.py (frames)
                                detector.py (circle template)
                                config.py   (all thresholds)
```

---

## Stage by stage

### 0. `extract_taps.py`: command line
| | |
|---|---|
| **In** | `video.mp4`, `--log video.json`, `--out folder`, `--calibration folder`, `--debug` |
| **Does** | Parses the arguments, calls `extract_taps_with_log()` (or `extract_taps()` without `--log`), prints a summary |
| **Out** | Exit code, plus the files written by stage 5 |

### 1. `log_io.py`: read the tap log
| | |
|---|---|
| **In** | Path to the JSON log, the video's name and size |
| **Does** | Checks the schema version, that the logger was on and that there are events. Warns if the log names a different video. Scales coordinates if the video size differs from the log's screen size |
| **Out** | `TapLog`: events sorted by time, keyboard snapshots (key label and box), and a function that turns log boxes into video pixels |

Example log event going in:
```json
{"videoTimeMs": 25150, "type": "VIEW_TEXT_CHANGED", "beforeText": "", "text": ["b"], "addedCount": 1}
```

### 2. `candidates.py`: decide where and when to look
| | |
|---|---|
| **In** | `TapLog`, `Config` |
| **Does** | Turns log events into "look here" candidates, then merges duplicates less than 150 ms apart |
| **Out** | List of `Candidate` |

| Log event | Search box | Time window | Confidence |
|---|---|---|---|
| Click (`VIEW_CLICKED`, `APP_STOP_TAPPED`) | the clicked element + 60 px | 0.8 s before → 0.15 s after | high |
| Typed one letter (`VIEW_TEXT_CHANGED`) | that key's box on the keyboard | 0.6 s before → 0.15 s after | high |
| New screen, no click just before | whole screen | 1 s before | low |

`Candidate` = `{log_event_ms, window, box, source, confidence, element: {text, resourceId, className}}`

### 3. `locator.py`: find the circle in the video
| | |
|---|---|
| **In** | One `Candidate` and the open video |
| **Does** | a) Decodes only the frames in the window (`video_io.py`)<br>b) Compares each frame with the previous one inside the box and looks for a **circle appearing**: inside the circle changed, around it did not<br>c) Confirms with the circle template (`detector.py`); the score must jump<br>d) Keeps the latest touch-down, then follows the circle forward until it fades (the finger lifted) |
| **Out** | `Located`: `status` (found / not_found_in_video), `touch_ms`, `lift_ms`, `x`, `y`, `score`, `points` (x, y in every frame while down) |

Helpers:
- `video_io.py`: gives frames with **exact timestamps** (the phone records at a variable frame rate), plus the list of all frame times.
- `detector.py`: the circle template learned by `calibrate.py`; returns a match score for every pixel.

### 4. `classifier.py`: what kind of touch
| | |
|---|---|
| **In** | The traced touch (`points`, start, end) |
| **Does** | Moved more than ~28 px → **SWIPE**; longer than 400 ms → **LONG_PRESS**; otherwise → **TAP** |
| **Out** | `label` plus a `reason`, e.g. `"displacement 0px <= 28px, duration 149ms <= 400ms"` |

### 5. `extractor.py`: collect and write results
| | |
|---|---|
| **In** | All candidates, their `Located` results and labels |
| **Does** | Removes duplicates (two log events that found the same touch). For each TAP, grabs 3 frames: before, touch, after. Converts `touch_ms` to a frame number |
| **Out** | Files in `--out` (below) |

---

## Output files

```
out/
  taps.json            only the TAPs: frame, time, pixel, what was tapped
  events_report.json   every candidate: TAP / SWIPE / LONG_PRESS / NOT_FOUND + reason
  taps/tap_001_before.png   screen just before the touch
  taps/tap_001_touch.png    the exact touch-down frame
  taps/tap_001_after.png    screen after the finger lifted
  debug/annotated.mp4       (--debug) video with every touch circled and labelled
```

One `taps.json` entry:
```json
{
  "id": 1,
  "touch_frame_index": 1498,
  "touch_ms": 24965.2,
  "lift_ms": 25265.2,
  "tap_x": 730, "tap_y": 2288,
  "match_score": 1.0,
  "source": "keyboard",
  "confidence": "high",
  "log_event_ms": 25150,
  "element": {"text": "b", "resourceId": null, "className": "key"},
  "before": "taps/tap_001_before.png",
  "touch": "taps/tap_001_touch.png",
  "after": "taps/tap_001_after.png"
}
```

---

## Other files

| File | Role |
|---|---|
| `config.py` | Every threshold in one place (window sizes, score limits, tap/swipe limits) |
| `calibrate.py` | Run once per phone: cuts the circle template from a frame → `calibration/` |
| `tracker.py` | Video-only mode: joins circle detections across frames into touches |
| `evaluate.py` | Compares `events_report.json` with a hand-made list of real taps: precision, recall, pixel error |
| `selftest.py` | Makes a fake video and log, runs both modes, checks the answers |

## Video-only mode (no `--log`)

`extract_taps()` scans **every** frame for the circle (`detector.py`), joins the detections
into touches (`tracker.py`) and classifies them. It needs no log but is slower and much less
reliable (1 of 14 taps on the sample).
