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
export GEMINI_API_KEY="AIzaSy..."

# Windows (PowerShell)
$env:GEMINI_API_KEY="AIzaSy..."

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
* **Cost Tracking:** The GUI displays your session cost in real-time (via `litellm`), so you can monitor your API usage.
* **Visual Consistency:** The "Director" AI compares new text against previous context to prevent unnecessary regenerations when you mistype a command. (Attempts to at least!)

### Customizing Styles

You can override the default art style ("Oil painting, dark fantasy") with the `--art-style` flag.

```bash
# Pixel Art Style
pipedream-gui --art-style "Retro 8-bit pixel art, green monochrome" adventure

# Pencil Sketch
pipedream-gui --art-style "Rough pencil sketch on parchment" adventure

```

### Cache Management

PipeDream caches aggressively to save money. If you change styles, you can wipe the world map:

```bash
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