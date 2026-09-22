# What's in this directory

`teachable-agentic-ai/appagent/` contains a single subfolder, **`AppAgent/`**, which is a clean git clone of the upstream project [TencentQQGYLab/AppAgent](https://github.com/TencentQQGYLab/AppAgent) (mirror of `mnotgod96/AppAgent`, CHI 2025 paper). Working tree is clean, no local commits or edits yet — this looks like a fresh checkout meant as a starting point for your own "teachable agentic AI" work.

## What AppAgent is

An LLM-based multimodal agent that operates Android apps the way a human would — tapping and swiping via `adb` — instead of using app-specific back-end APIs. It's powered by a vision-capable LLM (GPT-4V by default, or Qwen-VL as a free alternative) that looks at screenshots with numbered UI element tags and decides what action to take next.

It works in two phases:

1. **Exploration / Learning** (`learn.py`) — builds a knowledge base of how an app's UI elements behave, either by:
   - **Autonomous exploration**: the agent tries a task itself and reflects on whether its actions worked, or
   - **Human demonstration**: you perform the task on the device while the tool records your taps/swipes and asks you to label what each one did.
   
   Either way, it generates a **documentation file per UI element** describing what it does.

2. **Deployment** (`run.py`) — given a new task, the agent loads the documentation generated in phase 1 and uses it (plus live screenshots) to decide actions and complete the task end-to-end.

## Directory layout

```
AppAgent/
├── learn.py                # Entry point for exploration/demonstration phase
├── run.py                  # Entry point for deployment (task execution) phase
├── config.yaml             # Model choice (OpenAI/Qwen), API keys, timing, device dirs
├── requirements.txt
├── scripts/
│   ├── and_controller.py   # adb wrapper — screenshots, taps, swipes, XML UI dumps
│   ├── model.py             # LLM client wrappers (OpenAI GPT-4V, Qwen-VL)
│   ├── prompts.py           # Prompt templates for exploration/deployment
│   ├── self_explorer.py     # Autonomous exploration loop
│   ├── step_recorder.py     # Human-demonstration recorder
│   ├── document_generation.py # Turns explored/demonstrated actions into element docs
│   ├── task_executor.py     # Deployment-phase agent loop
│   ├── utils.py, config.py
├── assets/                  # README images, license text, eval benchmark description
└── LICENSE / README.md
```

## Requirements to actually run it

- `adb` installed, and an Android device (USB debugging) or Android Studio emulator
- Python 3 + `pip install -r requirements.txt`
- An API key in `config.yaml`: either an OpenAI key (for `gpt-4-vision-preview`, paid — ~$0.03/request) or a Dashscope key (for `qwen-vl-max`, free but weaker)

`config.yaml` currently has placeholder keys (`sk-`) — you'll need to fill in a real key before either script will work.

## Where things likely go from here

Since the parent folder is named `teachable-agentic-ai`, this checkout is probably the base you intend to extend or customize (e.g. new model backends via `scripts/model.py`, new interaction modes, etc.) rather than a finished project — right now it's byte-for-byte the upstream repo.
