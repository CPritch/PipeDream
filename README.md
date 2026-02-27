# PipeDream

PipeDream is a utility that adds a visual dimension to text-based interactive fiction. It operates by capturing the standard output of terminal games and generating real-time illustrations of the current scene using generative AI.

![Alt text](/screenshots/screenshot-1.png?raw=true "Example run of adventure game.")

## Quick Start (30 Seconds)

Experience the visualization immediately with the built-in demo cartridge.

**1. Install**
```bash
pip install pipedream-fiction

```

**2. Get an API Key**
You need a Gemini API key for the image generation (Free tier available).

* Get one here: [Google AI Studio](https://aistudio.google.com/app/apikey)
* Set it in your terminal:

```bash
# Linux / macOS
export GEMINI_API_KEY="AISaSy..."

# Windows (PowerShell)
$env:GEMINI_API_KEY="AISaSy..."
```

**3. Run the Demo**
Launch the GUI without arguments to play the internal mock game.

```bash
pipedream-gui
```

---

## Running Real Games

PipeDream wraps **any** console command. If you can run a game in your terminal, PipeDream can visualize it.

### Example: Colossal Cave Adventure

The perfect test bed for PipeDream.

1. **Install the game globally:**

```bash
uv tool install adventure
# Windows users: uv tool install adventure --with pyreadline3 --force
```

2. **Launch with PipeDream:**

```bash
pipedream-gui adventure
```

### Example: Interactive Fiction (Frotz)

Play classic Z-Machine games like *Zork*.

```bash
pipedream-gui frotz games/zork1.z5
```

---

## Features

* **Universal Compatibility:** Works with Python scripts, binaries, and interpreters (Frotz, Glulxe).
* **State-Aware Navigator:** A graph-based system tracks movement. If you leave a room and come back, PipeDream restores the previous image.
* **Smart Cost-Saving Cache (v0.3.0):** If you backtrack to a previously visited room, PipeDream bypasses the LLM analysis entirely and instantly loads the cached image, saving time and API costs.
* **Image-to-Image Consistency (v0.3.0):** An optional mode that uses the previous scene as a structural guide to maintain lighting, style, and architectural consistency as you move between rooms.
* **Spatial Awareness (v0.3.0):** The AI director maps physical paths and exits to their cardinal directions so the generated rooms accurately reflect the game's geography.
* **Graceful Shutdown:** Safely handles terminal interrupts (CTRL+C) and window closures without leaving zombie processes behind.

### Customizing Styles

You can override the default art style ("Oil painting, dark fantasy") with the `--art-style` flag.

```bash
# Pixel Art Style
pipedream-gui --art-style "Retro 8-bit pixel art, green monochrome" adventure

# Pencil Sketch
pipedream-gui --art-style "Rough pencil sketch on parchment" adventure
```

### Visual Consistency (Image-to-Image)

To create a more cohesive visual journey where rooms visually morph into one another rather than generating from scratch, enable the img2img pipeline:

```bash
pipedream-gui --img2img adventure
```

### Cache Management

PipeDream caches aggressively to save money. Cache data is namespaced by your game command and art style. To wipe the world map and start fresh for a specific game:

```bash
# Note: Ensure the flag comes before the game command
pipedream-gui --clear-cache adventure
```

---

## Development

If you want to play around with the source code:

1. **Clone the repo:**

```bash
git clone [https://github.com/yourusername/pipedream.git](https://github.com/yourusername/pipedream.git)
cd pipedream
```

2. **Install in editable mode:**

```bash
pip install -e .
```

3. **Configure Environment:**
Create a `.env` file in the root:

```ini
GEMINI_API_KEY=AIzaSy...
LLM_MODEL=gemini/gemini-2.5-flash
IMAGE_MODEL=gemini/gemini-2.5-flash-image
```

## Troubleshooting

* **Windows "Shim" Errors:** If a Python game crashes immediately on Windows, try wrapping the command to force path resolution:

```powershell
pipedream-gui cmd /c adventure
```