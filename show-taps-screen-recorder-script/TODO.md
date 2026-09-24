# TODO: log-guided tap extraction (`--log` mode)

Goal: given a screen recording **and** the accessibility log from TapScreenRecorder, output for
every tap the **exact frame the finger touched down** and the **exact (x, y) point** in that frame.

```
python extract_taps.py TapRec_X.mp4 --log TapRec_X.json --out output_dir [--debug]
```

## Why both inputs

| Source | Gives | Accuracy |
|---|---|---|
| Accessibility log | *That* a tap happened, roughly *when* (at finger-up), *which element* (its box), and what it was (text, key) | Time: 100-200 ms late. Place: element box, not the finger |
| Video (show-taps circle) | Exact touch-down frame and exact finger point | Frame-exact, pixel-exact, but a whole-video search found only 1 of 14 taps |

The log says **where and when to look**. The video gives **the exact answer**.

Evidence from `samples/TapRec_20260925_000001` (14 real taps, checked by hand):
- log alone: 11 of 14 taps reported; 10 with an element box
- log-guided local video search: circle found for 10 of 10, scores 0.64-1.00, versus at most 0.48 in tap-free areas
- video-only detector: 1 of 14

---

## Build steps

### 1. `log_io.py`: read the log
- [ ] Load the JSON (schemaVersion 1) and check that `video.fileName` matches the video; warn if not.
- [ ] Check `video.width/height` against the actual video. Scale the log's coordinates if they differ (e.g. a WhatsApp-compressed copy).
- [ ] Clear errors for: file missing, wrong schema, `logger.enabledAtStart == false`, zero events.

### 2. `candidates.py`: turn log events into tap candidates
Each candidate has a **search box** (screen pixels), a **search time window**, a **source** and a **confidence**.

| Log evidence | Search box | Time window | Confidence |
|---|---|---|---|
| `VIEW_CLICKED` / `VIEW_LONG_CLICKED` with bounds | `clickableAncestor.bounds` or `source.bounds`, plus 60 px | [t-800 ms, t+150 ms] | high |
| `VIEW_CLICKED` without bounds | whole screen | [t-800 ms, t+150 ms] | medium |
| `VIEW_TEXT_CHANGED`, `addedCount == 1`, previous text + 1 char | key box for that character, from the latest `keyboardSnapshots` entry before t | [t-600 ms, t+150 ms] | high |
| `WINDOW_STATE_CHANGED` for a new screen with no click in the previous 1 s | whole screen | [t-1000 ms, t] | low (a tap *probably* caused it) |

- [ ] Merge candidates less than 150 ms apart that point to the same place (e.g. a click plus a text change).
- [ ] Ignore our own app's events (`com.example.taprecorder`) except the Stop tap (see app changes).
- [ ] Map characters to keys case-insensitively. Skip text changes that paste or autocomplete several characters at once (`addedCount > 1`); those came from a suggestion tap, which becomes a whole-keyboard candidate.

### 3. `locator.py`: find the circle for each candidate
- [ ] Decode only the frames in the candidate's time window (`VideoReader.frames_between`).
- [ ] Run the existing masked template matcher **only inside the search box**, at full resolution. The box is small, so this is fast.
- [ ] Take the highest-scoring detection. Accept it if the score is >= `local_match_threshold` (start at 0.60).
- [ ] From that detection, walk **backwards** frame by frame while the circle is still found near the same point: the earliest such frame is the **touch-down frame**. Walk **forwards** the same way to find the **lift frame**.
- [ ] Output: `touch_frame_index`, `touch_ms`, `lift_ms`, `x`, `y`, `score`.
- [ ] If no circle is found, keep the candidate as `status: "not_found_in_video"` with the log's time and box centre, so it isn't silently lost.

### 4. Classify (reuse `classifier.py`)
- [ ] Build a `TouchEvent` from the frames found in step 3 and run the existing classifier (TAP / LONG_PRESS / SWIPE).
- [ ] Only TAPs go to `taps.json`. Everything else goes to `events_report.json` with its reason, as now.

### 5. Output (extend `extractor.py`)
- [ ] Same outputs as now: `taps/tap_NNN_{before,touch,after}.png`, `taps.json`, `events_report.json`, `debug/annotated.mp4`.
- [ ] Add to each `taps.json` entry:
  ```json
  {
    "touch_frame_index": 1498,
    "touch_ms": 25131.0,
    "lift_ms": 25301.0,
    "tap_x": 730, "tap_y": 2288,
    "match_score": 0.97,
    "source": "keyboard",            // click | keyboard | window_change | video_only
    "confidence": "high",
    "log_event_ms": 25150,
    "element": {"text": "b", "resourceId": null, "className": "key"}
  }
  ```
- [ ] Taps that were found only from a window change get `"confidence": "low"`.

### 6. `evaluate.py`: check against a hand-labelled list
- [ ] Accept an optional `x`, `y` per ground-truth entry and report the position error in pixels.
- [ ] Report precision and recall per `source` (click / keyboard / window_change).
- [ ] Add `samples/TapRec_20260925_000001_ground_truth.json` with the 14 hand-checked taps:

  | # | Time (s) | Tap |
  |---|---|---|
  | 1 | 3.5 | Zomato card (recent apps) |
  | 2 | 9.5 | Back arrow |
  | 3 | 17.6 | Restaurant card |
  | 4 | 21.4 | Pop-up ✕ |
  | 5 | 23.3 | Search field |
  | 6-12 | 25.0-26.5 | Keys b, i, r, y, a, n, i |
  | 13 | 30.7 | TapScreenRecorder card (recent apps) |
  | 14 | 31.7 | Stop button |

  Plus the non-taps, for SWIPE checks: swipe up at 1.5-2.4 s, back gesture at about 12.7 s, scrolls at 14.7-17.2 s, swipe up at 28.8-29.5 s, recent-apps swipe at 30.0-30.3 s.

### 7. Recorder app changes (`show-taps-screen-recording-test`)
- [ ] Log our own Stop tap (button or notification) as an event **before** `TapLog.end()` is called, so the last tap isn't lost.
- [ ] Log the time of the first video frame, if MediaRecorder can report it, to replace the "a few tens of ms" anchor uncertainty.
- [ ] Optional: debounce `WINDOW_CONTENT_CHANGED` (826 of 1010 events in the sample) to keep files small.

---

## Done when
- [ ] On the sample recording: **>= 11 of 14 taps** found with the exact touch frame, and position error **<= 10 px** against the hand-labelled points.
- [ ] No SWIPE or back gesture reported as a TAP.
- [ ] Tested on at least **3 more recordings** (different apps; light and dark screens; lots of typing) with hand-labelled ground truth.
- [ ] Runtime at most roughly equal to the video's length (the search is local, so it should be much faster than today's full-video pass).

## Known limits (won't be fixed by this)
- Taps that produce **no log event and no screen change** (e.g. tapping blank space) can only be found by the video-only detector, which is weak on this phone.
- The log must come from the **same recording**, uncompressed. A WhatsApp copy is 576×1296 and shrinks the circle to about 13 px, so use the original file (pull it with `adb pull /sdcard/Movies/TapScreenRecorder/...`).
- Gboard exposes its key positions, but other keyboards may not. Without key positions, key taps fall back to searching the whole keyboard area.
