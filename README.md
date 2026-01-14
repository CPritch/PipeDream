# PipeDream

PipeDream is a utility that adds a visual dimension to text-based interactive fiction. It operates by capturing the standard output of terminal games and generating real-time illustrations of the current scene using generative AI.

## Mission

Text adventures offer infinite resolution for the imagination, modern generative models adds a fun layer allowing us to see those worlds. PipeDream bridges this gap by acting as a universal adapter between legacy text engines and modern image generation APIs.

## Core Goals

1. **Universal Compatibility**
   If a game runs in a terminal and outputs text to stdout, PipeDream should be able to visualize it. This includes Z-Machine interpreters (Frotz), Python text adventures, or custom binaries.

2. **Real-Time Responsiveness**
   The visualization process must be fast enough to keep pace with the gameplay loop. The architecture prioritizes low latency to maintain immersion.

3. **Visual Consistency**
   The tool aims to maintain narrative coherence. If a player drops an item or revisits a room, the generated imagery should reflect the previous state rather than hallucinating a completely new environment.

## Current Status

**Status: Pipeline Integrated (Alpha)**

The core architecture is now fully connected (`src/pipedream/engine.py`). The application:
* Spawns game processes via `pexpect` (supports Linux/macOS and Windows).
* **Director Layer:** Analyzes game text using an LLM (via `litellm`) to extract visual scene descriptions while ignoring system text/menus.
* **Cache Layer:** Hashes prompts to manage state. Currently generates placeholder images to validate the pipeline logic.
* **Engine:** Orchestrates the full loop: Input -> Text Analysis -> Cache Check -> Image Path.

## Quick Start (Dev)

To test the engine loop with the included mock Zork game:

1. Install dependencies:
   ```bash
   pip install .

```

2. Configure Environment:
Create a `.env` file in the root directory to configure the LLM (required for the Director module):
```ini
# Example for Gemini (default in code)
GEMINI_API_KEY=your_api_key_here
LLM_MODEL=gemini/gemini-2.5-flash

```


3. Run the engine:
```bash
python src/pipedream/engine.py

```


4. Type commands (e.g., `look`, `west`) to see the engine capture text, generate visual prompts, and return image paths.