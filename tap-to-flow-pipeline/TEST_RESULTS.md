# Test results: `zomato_new_test`

Test date: 2026-09-26

## What was tested

One screen recording, put through the whole pipeline.

| Input | Details |
|---|---|
| Video | `zomato_new_test.mp4`: 85 s long, 1220 × 2712 px, 4,311 frames |
| Tap log | `zomato_new_test.json`: 772 logged events |
| Phone | Xiaomi 23122PCD1I (Android SDK 36) |

The pipeline ran on a CPU (no GPU).

The pipeline ran **twice**:

- **Run 1** used the pipeline as it was first built.
- **Run 3** ran after fixing the 5 problems that Run 1 showed.
- Run 2 was only half finished (stopped on purpose), so it is not reported.

Output folders: `runs/zomato_new_test/` (Run 1) and `runs/zomato_new_test_v3/` (Run 3).

### How results were checked

There is no hand-made answer key for this recording. Results were checked in two ways:

1. **Typed text:** compared with the text the phone's log recorded in the search box.
2. **Taps:** looked at each tap's "before" screenshot, with the tap point and the chosen
   element drawn on it, and judged whether the chosen element is what was tapped.

Anything that could not be checked either way is marked **not checked**.

---

## Summary

| | Run 1 | Run 3 |
|---|---|---|
| Total time | 392.6 s (6 min 33 s) | **249.6 s (4 min 10 s)** |
| Steps produced | 17 | 17 |
| Typed text correct | 1 of 3 | **3 of 3** |
| Taps grounded to the right element | 13 of 14 | **14 of 14** |

---

## Time taken

| Stage | What it does | Run 1 | Run 3 |
|---|---|---|---|
| 1. Tap extraction | Finds each tap in the video | 33.6 s | 34.3 s |
| 2. Flow builder | Turns taps into steps | 0.5 s | 0.5 s |
| 3 + 4. Segmentation + grounding | Finds the UI element under each tap (14 screenshots) | 358.5 s | 214.8 s |
| **Total** | | **392.6 s** | **249.6 s** |

Stage 3 + 4 went from 25.6 s to 15.3 s per screenshot, because the AI models are now loaded
once instead of once per screenshot.

---

## Typed text

Checked against the text recorded in the phone's log.

| What the log shows | Run 1 | Run 3 |
|---|---|---|
| "Tap" deleted, then "Zoma" typed | ❌ `TSearch for appsZma` | ✅ `Zoma` (and records that 3 old letters were deleted) |
| "pizza" typed | ❌ `iza` | ✅ `pizza` |
| "chicken" typed | ✅ `chicken` | ✅ `chicken` |

**Why Run 1 got it wrong:**
- Deletions were read as typed letters.
- The second "z" of "pizza" was lost, because the video does not show two separate presses.
- "o" and "p" were thrown away as "long presses", because the tracker kept following the key's
  letter after the finger lifted.

**Letters in Run 3 that came from the log only** (no screenshot exists for them): 3.
- The "e" in "chicken" was not found in the video.
- The second "z" in "pizza".
- One backspace.

---

## Taps

### Step by step (Run 3)

"Log said" is what the phone's log recorded as the tapped element. "Pipeline chose" is what the
pipeline found by looking at the screenshot.

| Step | Log said | Pipeline chose | Right? |
|---|---|---|---|
| 0 | *(nothing)* | Launcher search bar ("Tap") | ✅ (closest element; the tap was just outside all detected boxes) |
| 2 | Zomato | Zomato app icon | ✅ |
| 3 | Remove | "Remove" button | ✅ |
| 4 | Search "comfort food" | Search bar, showing "binge night" | ✅ (the search hint text changes by itself) |
| 6 | Zomato *(screen name)* | "See all restaurants" | ✅ |
| 7 | Zomato *(screen name)* | Pizza Hut restaurant card | ✅ |
| 8 | Search "bread" | Search bar | ✅ |
| 10 | American Nashville Chicken Pizza | That pizza's card | ✅ |
| 11 | Zomato *(screen name)* | "Add item" button | ✅ |
| 12 | *(nothing)* | "1 item added / Continue" bar | ✅ |
| 13 | Zomato *(screen name)* | Round "×" close button of a pop-up | ✅ |
| 14 | Zomato *(screen name)* | Arrow on the "Delivery at Home" row | ✅ |
| 15 | TapScreenRecorder | The TapScreenRecorder app card (in the recent-apps screen) | ✅ Run 3 only |
| 16 | Stop | "Stop" button | ✅ |

- **Step 15 in Run 1:** it chose the small "Stop" button *inside* the app's preview picture.
  Run 3 uses the size of the tapped element recorded in the log, so it now chooses the whole
  card.
- **Screen-name taps:** in 6 steps the log only knows the screen name ("Zomato"), not the
  button. The pipeline found the real button in every one of them.

### Missed or skipped events (Run 3)

The log has 42 events that might be taps. What happened to them:

| Result | Count |
|---|---|
| Found as a tap | 30 (8 clicks, 16 key presses, 6 screen changes) |
| Found, but it was a swipe (correctly not counted as a tap) | 3 |
| Not found in the video | 9 |

The 9 not found:

| Time | Log event | Checked? |
|---|---|---|
| 19.6 s | Click on an icon (log shows only an icon-font glyph) | **Missed tap.** The log says a click happened, and the video search did not find it. |
| 50.5 s | Letter "e" | Missed in the video, but added back from the log (see Typed text) |
| 11.9 s, 12.5 s | Zomato app opening screens | **Not checked** whether a tap happened |
| 64.1 s | Pop-up appeared | **Not checked** |
| 72.7 s, 76.5 s | Screen changes | **Not checked** |
| 81.5 s, 81.7 s | Going back to the phone's home screen | **Not checked** |

Two more key presses were not found separately in the video, because they were pressed too
soon after the previous one. Both are covered by the log:
- The second "z" of "pizza".
- One backspace.

---

## Known problems still left

1. **One click missed:** the icon click at 19.6 s is not in the result.
2. **First backspace:** the video has no separate press for it. The text is still right,
   because the log fills the gap, but its screenshots show the next backspace press.
3. **The "p" in "pizza" is borderline:** its press measured 352 ms, and anything over
   400 ms counts as a long press.
4. **Flags for manual review:** 6 steps are flagged because the log text and the text read
   from the screen differ:
   - Steps 6, 7 and 11: the log only had the screen name.
   - Step 15: the log gives the app name, and the screenshot also shows its preview text.
   - Step 4: the search hint changed.
   - Step 10: the screen text is read in a different word order.

   None of these steps chose the wrong element.
5. **Speed:** about 15 s per screenshot is still spent running the AI models. This test used a
   CPU only.

---

## What was fixed between Run 1 and Run 3

| # | Problem in Run 1 | Fix |
|---|---|---|
| 1 | Deleting letters was read as typing letters | Deletions are now backspace presses, and the text is rebuilt in order |
| 2 | Double letters (the "zz" in "pizza") lost one letter | The missing press is taken from the log |
| 3 | Some key presses were thrown away as "long presses" | Each key press now ends when the log says the key was released |
| 4 | Step 15 chose a button inside the app preview | Uses the size of the tapped element from the log when the screenshot detector misses it |
| 5 | Slow: AI models reloaded for every screenshot | Models load once for all screenshots |
