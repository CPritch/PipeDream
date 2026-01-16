# PipeDream

PipeDream is a utility that adds a visual dimension to text-based interactive fiction. It operates by capturing the standard output of terminal games and generating real-time illustrations of the current scene using generative AI.

## Mission

Text adventures offer infinite resolution for the imagination, but modern generative models allow us to see those worlds. PipeDream bridges this gap by acting as a universal adapter between legacy text engines and modern image generation APIs.

## Core Goals

1. **Universal Compatibility**
   If a game runs in a terminal and outputs text to stdout, PipeDream should be able to visualize it. This includes Z-Machine interpreters (Frotz), Python text adventures, or custom binaries.

2. **Real-Time Responsiveness**
   The visualization process must be fast enough to keep pace with the gameplay loop. The architecture prioritizes low latency to maintain immersion.

3. **Visual Consistency**
   The tool aims to maintain narrative coherence. If a player drops an item or revisits a room, the generated imagery should reflect the previous state rather than hallucinating a completely new environment.

## Current Status

**Status: Functional Prototype**

The pipeline is fully operational and generating live imagery.

* **Engine:** Captures game output via `pexpect`.
* **Director:** Uses an LLM to interpret game text into visual prompts.
* **Generator:** Calls external image generation APIs (via `litellm`) and saves results to a local cache.
* **Viewer:** A separate OpenCV process (`cv2`) monitors the queue and renders the generated images in a window alongside the terminal.

## Quick Start (Dev)

1. **Install dependencies:**
   ```bash
   pip install .

```

2. **Configure Environment:**
Create a `.env` file in the root directory. You need keys for both the LLM (Director) and the Image Generator.
```ini
GEMINI_API_KEY=your_api_key_here
LLM_MODEL=gemini/gemini-2.5-flash
IMAGE_MODEL=gemini/gemini-2.5-flash-image

```


3. **Run the engine:**
```bash
python src/pipedream/engine.py

```


This will launch the mock game in the terminal and open the "PipeDream Visualizer" window. Type commands (e.g., `look`, `west`) to trigger generation.