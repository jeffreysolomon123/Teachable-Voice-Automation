# How AppAgent Works

This walks through the actual mechanics of `AppAgent/`, based on the code in `scripts/`. There are three pipelines: **autonomous exploration**, **human demonstration**, and **deployment**. All three share the same building blocks — screen perception, element labeling, and an LLM "Observation → Thought → Action → Summary" loop.

## The shared building blocks

### 1. Perceiving the screen (`and_controller.py`)

Everything is driven through `adb` shell commands (`subprocess.run`, no special SDK):

- `get_screenshot()` — `adb shell screencap` then `adb pull` to fetch a PNG.
- `get_xml()` — `adb shell uiautomator dump` then `adb pull` to fetch the current view hierarchy as XML.
- `traverse_tree(xml_path, elem_list, attrib, add_index)` walks that XML and collects every element whose `attrib` (`"clickable"` or `"focusable"`) is `"true"`, computing a center point and bounding box for each. Elements whose centers are within `MIN_DIST` pixels of an already-collected element are dropped, to avoid duplicate/overlapping tags.
- Each surviving element becomes an `AndroidElement(uid, bbox, attrib)`, where `uid` is derived from its `resource-id` (or `class_name + width + height` if no resource id) plus a short `content-desc` suffix — this uid is what documentation files get named after, so the same UI element is recognized consistently across runs.
- Action primitives: `tap(x, y)`, `text(str)`, `long_press(x, y)`, `swipe(x, y, direction, dist)`, `swipe_precise(start, end)`, `back()` — all just shell out to `adb shell input ...`.

### 2. Labeling the screen (`utils.py`)

`draw_bbox_multi()` overlays numeric tags (red = clickable, blue = focusable) at each element's bounding box on a copy of the screenshot — this labeled image is what actually gets sent to the vision model, never the raw screenshot. `draw_grid()` produces an alternative fallback view: the screen divided into a numbered grid, used when no tagged element matches what's needed (see "grid mode" below).

### 3. Talking to the model (`model.py`)

`OpenAIModel` / `QwenModel` both implement `get_model_response(prompt, images) -> (success, text)`, sending the prompt text plus one or more base64-encoded images as a single multimodal chat request. Every model call gets one labeled screenshot (or two, for reflection — before/after).

The model is instructed (via `prompts.py`) to always answer in a fixed four-field format:
```
Observation: <what's on screen>
Thought: <reasoning about the next step>
Action: <one function call, e.g. tap(5), text("hello"), swipe(3,"up","medium"), or FINISH>
Summary: <running summary of actions taken so far>
```
`parse_explore_rsp()` / `parse_grid_rsp()` / `parse_reflect_rsp()` in `model.py` regex-parse these fields back out. The `Summary` becomes `last_act`, which is re-injected into the next prompt so the agent has short-term memory of what it's already tried — there's no other conversation history kept between rounds.

### 4. The action space

From `prompts.task_template` (deployment) and `self_explore_task_template` (exploration):
- `tap(element)` / `long_press(element)` — element is a numeric tag
- `text(input_str)` — types into whatever input field currently has focus (requires a keyboard visible)
- `swipe(element, direction, dist)` — direction ∈ {up,down,left,right}, dist ∈ {short,medium,long}
- `FINISH` — the model signals the task is complete
- `grid()` — deployment only: escape hatch when the element needed isn't tagged. Switches the next round to grid mode (`task_template_grid`), where the model instead calls `tap(area, subarea)` / `swipe(start_area, start_subarea, end_area, end_subarea)` against a numbered grid with 9 sub-positions each (`area_to_xy()` in `task_executor.py` converts that back to pixel coordinates). It reverts to normal tagged mode after one grid action.

## Pipeline 1: Autonomous Exploration (`scripts/self_explorer.py`)

Entered via `learn.py` → option 1. Given a task description, runs up to `MAX_ROUNDS` (config) rounds of:

1. Screenshot + XML dump → label clickable/focusable elements.
2. Ask the model (via `self_explore_task_template`) what to do next, given the labeled image and `last_act`.
3. Execute the returned action via `AndroidController`.
4. Take an **after** screenshot and label it too.
5. Ask the model to **reflect** (`self_explore_reflect_template`), showing both before/after images, and classify the action as:
   - `INEFFECTIVE` — screen didn't change; mark the element useless (skipped in future rounds) and reset `last_act`.
   - `BACK` — action went somewhere unhelpful; press back, mark element useless, but still save a doc describing what it actually does.
   - `CONTINUE` — action did something but didn't advance the task; save doc, keep going.
   - `SUCCESS` — action advanced the task; save doc, keep going.
6. Whenever a doc is produced, it's written to `apps/<app>/auto_docs/<element_uid>.txt` as a Python dict literal `{"tap": "...", "text": "...", "v_swipe": "...", "h_swipe": "...", "long_press": "..."}` — one file per element, one field per action type performed on it. Existing non-empty entries aren't overwritten.

Stops early if the model outputs `FINISH`, otherwise runs until `MAX_ROUNDS`.

## Pipeline 2: Human Demonstration (`step_recorder.py` + `document_generation.py`)

Entered via `learn.py` → option 2, which runs both scripts back to back.

**`step_recorder.py`** — a manual loop: each round it screenshots, labels elements, pops the image up via OpenCV (`cv2.imshow`) so you can see the numeric tags, then prompts you in the terminal to type an action (`tap` / `text` / `long press` / `swipe` / `stop`) and which numbered element. It executes that action for real via `AndroidController` and appends a line to `record.txt` (e.g. `tap(5):::com.app.button_ok`), pairing the action with the element's stable `uid`. Screenshots (raw + labeled) and XML dumps for every step are saved so the next stage can diff before/after images.

**`document_generation.py`** — walks `record.txt` step by step, and for each recorded action sends the corresponding before/after labeled screenshots to the model with a doc-writing prompt (`tap_doc_template`, `text_doc_template`, `long_press_doc_template`, or `swipe_doc_template`) asking it to describe *only* what the element generally does (explicitly instructed not to mention specific content like a person's name). Output goes to `apps/<app>/demo_docs/<element_uid>.txt`, same dict-per-element format as exploration. If `DOC_REFINE: true` in config and a doc already exists for that element, the old doc text is appended to the prompt so the model merges/updates it instead of skipping.

## Pipeline 3: Deployment (`scripts/task_executor.py`, via `run.py`)

1. Picks a documentation base: `auto_docs` if only exploration was run, `demo_docs` if only demonstration was run, or asks you to choose if both exist; can also proceed with **no docs** if none exist (lower success rate, explicitly warned).
2. Loop, up to `MAX_ROUNDS`:
   - Screenshot + XML → label elements (or draw grid, if the previous round ended with `grid()`).
   - Build the prompt (`task_template`): for every labeled element that has a matching doc file, its description is inlined into the prompt under `<ui_document>`, telling the model "prioritize these documented elements."
   - Send to the model, parse the action, execute it via `AndroidController`, update `last_act`, sleep `REQUEST_INTERVAL` seconds (rate limiting for the API).
   - `FINISH` ends the loop successfully; hitting `MAX_ROUNDS` ends it as a timeout; any execution/parse error breaks immediately.

## Data layout produced at runtime

```
apps/<app_name>/
  auto_docs/<element_uid>.txt      # from autonomous exploration
  demo_docs/<element_uid>.txt      # from human demonstration
  demos/<demo_name>/...            # raw/labeled screenshots, xml, record.txt, logs
tasks/task_<app>_<timestamp>/
  ...screenshots, xml, log_<app>_<timestamp>.txt   # full prompt/response log per deployment run
```
Every model call, in every phase, is also logged as JSON lines (prompt + image filenames + raw response) — useful for debugging why the agent took a given action, and this is what the README's "inspect/manually revise documentation" tip is pointing at (you can hand-edit any `.txt` doc file directly; it's just a Python dict literal, parsed with `ast.literal_eval`).

## Key design idea

The novelty is that the agent doesn't need API/backend access to an app, and it doesn't need a giant static UI ontology either — it *builds its own manual* for an app (the `*_docs/*.txt` files) once, either by trial-and-error with self-reflection or by watching a human, and then reuses that manual as grounding context on every future task so it doesn't have to re-guess what each button does from the screenshot alone.
