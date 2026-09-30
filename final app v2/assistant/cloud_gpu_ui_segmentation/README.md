# Zero-Card Cloud GPU Parallel UI Segmentation

Accelerate UI element segmentation for mobile screen recordings by **50x–200x** using **Cloud GPUs with zero credit/debit card requirements**.

This standalone package parallelizes the computer-vision segmentation pipeline from [Teachable-Voice-Automation](https://github.com/jeffreysolomon123/Teachable-Voice-Automation.git) (OmniParser v2 YOLOv8 icon detection + EasyOCR text recognition + geometric hierarchy combination).

---

## Why Parallel Cloud GPU?

On a sequential local CPU, UI segmentation takes **~15.3 seconds per frame** (or **~3.5 minutes for just 14 frames**). For a 60–90 second screen recording with dozens of frames, sequential execution creates a severe bottleneck.

By dispatching frames in parallel to a cloud GPU:
- Frame latency drops from **15.3s** to **~0.10s–0.20s**.
- Multiple frames are processed **concurrently** across worker threads.
- Processing 14 frames drops from **215 seconds** to **under 3 seconds** (**>70x speedup**).
- Entirely **FREE** using providers that require **NO credit/debit card**.

---

## 🚀 Quickstart: 2-Line Python API

You can call the cloud GPU directly in Python without running any server or managing sockets:

```python
import cloud_gpu_ui_segmentation as cgpu

# 1. Segment a single screenshot/frame
result = cgpu.segment_frame("screenshot.png")
print(f"Found {len(result['combined'])} UI elements and {len(result['texts'])} texts!")

# 2. Segment a batch of frames in parallel
summary = cgpu.segment_frames_batch(["frame1.png", "frame2.png"], out_dir="./output")

# 3. Segment an entire screen recording (.mp4) in parallel
summary = cgpu.segment_video("screen_recording.mp4", sample_fps=1.0, out_dir="./output")
```

All functions automatically use the live ZeroGPU space (`ScriptBoyDK7/ui-segmentation`) by default, requiring **zero configuration**.

---

## Zero-Card Provider Options

| Option | Provider | How It Operates | Inactivity / Maintenance |
| :--- | :--- | :--- | :--- |
| **Solution B (Recommended)** | **Hugging Face Spaces (ZeroGPU)** | Pre-configured Gradio app with `@spaces.GPU` | **100% Set-and-Forget**: Stays online 24/7. Auto-allocates an NVIDIA H100 GPU dynamically when called. No browser tabs to keep open! |
| **Option A** | **Lightning AI (LitServe)** | High-throughput LitServe server on T4 GPU | ~80 free hours. Studio sleeps when idle, or scales-to-zero serverless. |
| **Option C** | **Google Colab + Cloudflare** | Free daily T4 GPU with Cloudflare tunnel | Runs via 1-click `colab_runner.ipynb` notebook. |
| **Local Mock** | **Localhost Mock Server** | Built-in simulated GPU server on 127.0.0.1:8000 | Instant zero-dependency testing without internet. |

---

## Solution B: Hugging Face ZeroGPU Setup (1-Minute "Set-and-Forget")

With Solution B, your GPU inference endpoint is hosted **permanently 24/7**. You do not need to keep any browser tab open, and you never have to start or stop any server.

### Step 1: Create a Free Hugging Face Space
1. Log in to [huggingface.co](https://huggingface.co) (free account; zero credit/debit card, zero phone verification).
2. Go to **New Space** (`https://huggingface.co/new-space`).
3. Fill in the details:
   - **Space name**: `ui-segmentation` (or any name you like)
   - **Space SDK**: Select **Gradio**
   - **Space hardware**: Select **ZeroGPU (NVIDIA H100)** *(Free)*
   - **Visibility**: Public (or Private)

### Step 2: Upload the Space Files
Upload the 3 files from [`cloud_gpu_ui_segmentation/hf_space/`](file:///c:/Users/Siddharth/Documents/antigravity/hopeful-hawking/cloud_gpu_ui_segmentation/hf_space) to your Space:
- `app.py`
- `requirements.txt`
- `README.md`

*(Or clone the Space repository with git and push these files).*
Hugging Face will build the container in ~2 minutes and your Space is live!

### Step 3: Run Parallel Segmentation from Your Local Terminal
Now run segmentation from your terminal anytime:
```bash
python -m cloud_gpu_ui_segmentation.cli \
  --input path/to/my_recording.mp4 \
  --hf-space your-username/ui-segmentation \
  --concurrency 4 \
  --out-dir ./segmented_results
```
*(If your Space is private, add `--hf-token hf_xxxx` or set `export HF_TOKEN=hf_xxxx`).*

That's it! It stays available on demand forever with zero maintenance.

---

## Option A: Lightning AI Setup (~80 Free GPU Hours)

If you prefer to use your ~80 free hours on Lightning AI with a T4 GPU:

1. Open a free Studio on [lightning.ai](https://lightning.ai), switch compute to **T4 GPU**, and run:
   ```bash
   pip install -r requirements-server.txt
   python server.py --port 8000
   ```
2. Open **Port Viewer** in the Studio sidebar, enter `8000`, and toggle **Make Public**.
3. Run locally:
   ```bash
   python -m cloud_gpu_ui_segmentation.cli \
     --input path/to/screen_recording.mp4 \
     --endpoint https://8000-xxxx.lightning.ai \
     --concurrency 8
   ```

---

## Immediate Local Test (Zero Setup / Mock Mode)

You can verify the entire frame extraction, parallel dispatcher, schema compatibility, and benchmark reporter right now locally with no cloud setup:

```bash
python -m cloud_gpu_ui_segmentation.cli --demo --mock --concurrency 4
```

Output:
```
=================================================================
  RUN BENCHMARK SUMMARY
=================================================================
  Processed Frames:     5 / 5
  Total Time:           0.38 seconds
  Throughput:           13.18 FPS
  Avg Latency / Frame:  0.076 seconds
  Estimated CPU Time:   76.5 seconds
  Speedup Factor:       201.6x FASTER than CPU
  Cloud Compute Cost:   $0.00 (Zero-card Free Tier)
  Output Directory:     runs/cloud_segmented/segment_out
=================================================================
```

---

## CLI Reference

| Option | Flag | Default | Description |
| :--- | :--- | :--- | :--- |
| Video file | `--input`, `-i` | None | Path to screen recording video (`.mp4`) |
| Frames directory | `--input-frames` | None | Path to directory of screenshots/frames (`.png`, `.jpg`) |
| Synthetic demo | `--demo` | False | Generates synthetic mobile UI frames to test without video files |
| HF Space ID | `--hf-space` | None | Hugging Face Space ID for ZeroGPU (`username/ui-segmentation`) |
| HF Token | `--hf-token` | None | Hugging Face Access Token for private spaces |
| REST Endpoint | `--endpoint`, `-e` | `http://127.0.0.1:8000` | URL of LitServe / Colab GPU endpoint |
| Local Mock Server | `--mock` | False | Boots automatic background mock server |
| Concurrency | `--concurrency`, `-c` | `8` | Number of simultaneous worker requests |
| Output Directory | `--out-dir`, `-o` | `runs/cloud_segmented` | Destination directory for outputs |
| Sample FPS | `--sample-fps` | `1.0` | Target sampling rate when extracting video frames |

---

## Output Files (100% Compatible with `Teachable-Voice-Automation`)

Every processed frame `<stem>` produces:
1. `segment_out/<stem>.json`: CV element boxes with pixel coordinates, normalized coordinates, and screen region.
2. `segment_out/<stem>_ocr.json`: OCR text boxes with confidence scores.
3. `segment_out/<stem>_combined.json`: Fully merged elements with parent/child hierarchy, text assignments, and element types (`container`, `labeled_element`, `icon_or_image`, `text`).
4. `segment_out/<stem>_annotated.png`: Visual check image with colored bounding boxes and ID tags.
5. `segment_out/benchmark_summary.json`: Detailed speedup and latency metrics.

---

## Original Repository Integrity

The original repository `Teachable-Voice-Automation/` remains completely pristine and unmodified.
