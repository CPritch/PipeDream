# PipeDream

PipeDream is a utility that adds a visual dimension to text-based interactive fiction. It operates by capturing the standard output of terminal games and generating real-time illustrations of the current scene using generative AI.

![Alt text](/screenshots/screenshot-1.png?raw=true "Example run of adventure game.")

## Core Goals

1. **Universal Compatibility**
   If a game runs in a terminal and outputs text to stdout, PipeDream should be able to visualize it. This includes Z-Machine interpreters (Frotz), Python text adventures, or custom binaries.

2. **Real-Time Responsiveness**
   The visualization process must be fast enough to keep pace with the gameplay loop. The architecture prioritizes low latency to maintain immersion.

3. **Visual Consistency**
   The tool aims to maintain narrative coherence. If a player drops an item or revisits a room, the generated imagery should reflect the previous state rather than hallucinating a completely new environment.

## Current Status

**Status: Functional Prototype (GUI)**

The project now features a functional GUI that separates the game terminal from the visual output.

* **Engine:** Captures game output via `pexpect` (Unix) or `Popen` (Windows).
* **Director:** Uses an LLM to interpret game text into visual prompts.
* **Generator:** Calls external image generation APIs (via `litellm`) and saves results to a local cache.
* **Interface:** A PySide6 (Qt/QML) application providing a split-view experience: game terminal on the right, AI visualization on the left.

## Quick Start (Dev)

### 1. Install Dependencies
Install the package in editable mode to register the `pipedream-gui` command.
```bash
   pip install -e .
```

2. **Configure Environment:**
Create a `.env` file in the root directory. You need keys for both the LLM (Director) and the Image Generator.
```ini
GEMINI_API_KEY=your_api_key_here
LLM_MODEL=gemini/gemini-2.5-flash
IMAGE_MODEL=gemini/gemini-2.5-flash-image
```

3. **Run a Game:**
PipeDream works by wrapping any console command.
### Example: Colossal Cave Adventure (Python Port)


First, install the game so it is available globally (outside the project environment). Using `uv` is recommended:
```bash
uv tool install adventure
```


*(Windows User Note: If you encounter readline errors, install with the Windows patch: `uv tool install adventure --with pyreadline3 --force`)*

**Launch with PipeDream:**
```bash
pipedream-gui adventure
```


### Example: Generic Use


You can wrap other interpreters (like Frotz) similarly:
```bash
# Syntax: pipedream-gui <command_to_run_game>
pipedream-gui frotz games/zork1.z5
```


### Troubleshooting

Windows "Shim" Errors: If you get errors running a Python game on Windows, try wrapping the command in cmd /c to force proper path resolution: `pipedream-gui cmd /c adventure`