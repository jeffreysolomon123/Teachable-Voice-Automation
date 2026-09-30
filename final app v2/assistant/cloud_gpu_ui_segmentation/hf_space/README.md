---
title: UI Segmentation ZeroGPU
emoji: 📱
colorFrom: indigo
colorTo: purple
sdk: gradio
app_file: app.py
pinned: false
license: mit
---

# UI Segmentation on ZeroGPU (NVIDIA H100)

Turn mobile app screenshots and video frames into structured, machine-readable UI elements:
- Element detection (OmniParser v2 YOLOv8 `icon_detect`)
- Text recognition (EasyOCR)
- Containment hierarchy & classification (`container`, `labeled_element`, `icon_or_image`, `text`)
- Annotated overlay visualization

Accelerated dynamically using **ZeroGPU (NVIDIA H100)** on Hugging Face Spaces.
100% Free, zero credit/debit card required.
