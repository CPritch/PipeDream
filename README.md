# PipeDream

PipeDream is a utility that adds a visual dimension to text-based interactive fiction. It operates by capturing the standard output of terminal games and generating real-time illustrations of the current scene using generative AI.

## Mission

Text adventures offer infinite worlds for the imagination, but modern generative models can add a fun visual layer to old classics. PipeDream bridges this gap by acting as a universal adapter between legacy text engines and modern image generation APIs.

## Core Goals

1. **Universal Compatibility**
   If a game runs in a terminal and outputs text to stdout, PipeDream should be able to visualize it. This includes Z-Machine interpreters (Frotz), Python text adventures, or custom binaries.

2. **Real-Time Responsiveness**
   The visualization process must be fast enough to keep pace with the gameplay loop. The architecture prioritizes low latency to maintain immersion.

3. **Visual Consistency**
   The tool aims to maintain narrative coherence. If a player drops an item or revisits a room, the generated imagery should reflect the previous state rather than hallucinating a completely new environment.

## Current Status

**Status: Input Scaffold Functional**

The core engine loop (`src/pipedream/engine.py`) is implemented. It currently:
* Spawns game processes via `pexpect` (supports Linux/macOS and Windows).
* Captures standard output from the game.
* Cleans the output and isolates the narrative text.
* Prepares the text for the (upcoming) image generation pipeline.

## Quick Start (Dev)

To test the engine loop with the included mock Zork game:

1. Install dependencies:
```bash
   pip install .

```

2. Run the engine:
```bash
python src/pipedream/engine.py

```


3. Type commands (e.g., `look`, `west`) to see the engine capture and clean the output.