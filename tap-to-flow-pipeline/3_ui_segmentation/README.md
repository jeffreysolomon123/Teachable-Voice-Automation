# UI Segmentation

Turn a mobile app screenshot into a structured, machine-readable list of UI elements — **what** each element is, **where** it is (pixel + normalized coordinates), and **what text it carries** — so an agent can reason about a screen and pick tap targets without a view hierarchy or accessibility tree.

The pipeline uses pure vision: a UI-trained object detector for element boxes, OCR for text, and a geometric merge step that joins the two.

## Pipeline

```
screenshot ──► segment_ui.py ──► <stem>.json            (element boxes, CV)
          └──► ocr_text.py   ──► <stem>_ocr.json        (text boxes, OCR)
                                     │
                combine_results.py ◄─┘
                     └──► <stem>_combined.json          (elements + text + hierarchy)
```

| Script | What it does | Model / library |
|---|---|---|
| `segment_ui.py` | Detects UI elements; sorts them top-to-bottom, left-to-right; adds `center`, `size`, `bbox_norm`, `center_norm`, `area_pct`, and a 3×3 screen `region` (e.g. `top-right`). Optional per-element crops (`--crops`), and masks if a segmentation model is supplied. | YOLOv8 via Ultralytics; default weights are **Microsoft OmniParser v2 `icon_detect`** from Hugging Face |
| `ocr_text.py` | Reads all text with confidence ≥ `--min-conf` (0.3) and writes pixel boxes. | EasyOCR |
| `combine_results.py` | Assigns each OCR box to the **smallest** CV element containing ≥ 60% of it; builds parent/child links (a child is ≥ 90% inside a larger element); reclassifies elements as `container`, `labeled_element`, or `icon_or_image`; keeps unmatched text as standalone `text` elements. | OpenCV + plain geometry |

Each step also writes an `_annotated.png` for visual checking.

### Run

```bash
pip install ultralytics huggingface_hub opencv-python numpy easyocr

python segment_ui.py zomato-1.jpeg --crops
python ocr_text.py zomato-1.jpeg
python combine_results.py zomato-1.jpeg

# many screenshots, models loaded once (same outputs per image)
python segment_batch.py a.png b.png c.png --crops -o segment_out
```

Defaults tuned for UI: `--conf 0.05` (UI detectors score low; a normal 0.25 threshold drops real elements), `--iou 0.1` (aggressive NMS to remove overlapping duplicates), `--imgsz 1280`.

## What we tested

| Screenshot | Size | CV elements | OCR boxes | Combined |
|---|---|---|---|---|
| `zomato-1.jpeg` (Zomato search results for "biryani") | 720×1600 | 28 | 42 | 30 (13 labeled, 10 icon/image, 5 containers, 2 standalone text) |
| `Home - Incoming Shipments.png` (logistics app mockup) | 1206×5395 (full-page, very tall) | 25 | — (not run) | — |

Outputs are in `segment_out/`.

## What we proved

1. **Off-the-shelf OmniParser detection works on real app screens with no training.** On the Zomato screenshot it found essentially every actionable element: the back button, search bar, clear (×) and mic buttons, every filter chip, all six restaurant cards, the Ad badge, the bookmark icon, and the rating pill.
2. **CV + OCR merge gives elements a meaning.** After combining, elements carry their text, e.g. chip #12 → `"Highly reordered"`, card #15 → `"4.1 Narmada Chain 45-50 mins"`. That's enough for an agent to resolve a command like "open Narmada Chain" to a tap coordinate (`center`).
3. **Hierarchy can be recovered from geometry alone.** Containment produces useful parent/child links: the search bar (#5) holds the back (#6) and clear (#7) buttons, and restaurant cards contain their small icons (#18, #22, #23).
4. **Coordinates are ready for tapping.** Every element has a pixel center and normalized center, so it can be sent directly to `adb shell input tap` or scaled to another resolution.
5. **Text outside every detected element is still kept.** The "ALL RESTAURANTS" section header wasn't detected as an element, but it survives as standalone OCR `text` elements (#28, #29).

## What we found (limitations and issues)

### Detection
- **Tall full-page screenshots lose a lot of detail.** The 1206×5395 shipments image is shrunk to `imgsz=1280` on its long side (about 4.2× downscale), and many elements are missed: several item chips (e.g. "Notebooks (80)", "First Aid Packets (35)"), "Track Vehicle" links, "View Receipt", and the whole bottom nav bar (Shipments / Alerts / Profile). **Fix to try:** tile the image into roughly phone-height slices (or raise `--imgsz`) and merge the boxes.
- **All detections have type `icon`.** OmniParser's detector is single-class, so button, input, card, and image can't be told apart from the detector alone. The type in the combined output (`container` / `labeled_element` / `icon_or_image`) is inferred from structure and text, not recognized.
- **Confidence scores are low and spread out** (0.05–0.96). The low threshold is needed, but it also lets some weak or partial boxes through, e.g. status-bar fragments.
- **Big cards swallow their contents.** The featured ad card (#24) spans most of the lower screen, so all its text (title, price, restaurant name) gets merged into one long string instead of separate sub-elements.

### OCR (EasyOCR)
- **The rupee sign `₹` is misread** as `?`, `<`, `=`, `7`, or `8` (`"8150 OFF above ?299"` should be `"₹150 OFF above ₹299"`, and `"7235 =299"` should be `"₹235 ₹299"`). Prices can't be trusted without post-processing or another OCR engine.
- **Icons are read as text:** the battery and signal icons became `"D38%"`, and truncated labels come out garbled (`"Chick="`, `"Tast"`, `"Supreme Bon_"`).
- **Word order within an element can be wrong.** `"Near & Fast"` becomes `"Fast Near"`, because text is sorted by top y and then x, and small baseline differences reorder words on the same line. Also, `&` was dropped.
- Small, low-contrast text (the "zomato" watermark in the status bar) is missed.

### Combine step
- Combined output keeps the original CV ids and adds OCR-only ids after them, then sorts by position, so **ids are not in reading order** (#28, #29 appear before #24). Renumbering after sorting would make the output easier for an agent to use.
- The 60% containment and 90% parent thresholds are hand-picked and were only checked on a single screenshot.

## Next steps

- Tile tall screenshots before detection, and run OCR + combine on the shipments screen.
- Group OCR words into lines (y-overlap clustering) before joining them, to fix word order.
- Try a different OCR engine (PaddleOCR, or a cloud vision API) for currency symbols and small text.
- Add a caption/classification pass on crops (e.g. OmniParser's icon-caption model or an LLM) to label icon-only elements like "mic", "bookmark", and "back".
- Renumber combined elements in reading order.
- Build a small hand-labeled set to measure precision and recall instead of judging by eye.
