# PipeDream

PipeDream is a utility that adds a visual dimension to text-based interactive fiction. It operates by capturing the standard output of terminal games and generating real-time illustrations of the current scene using generative AI.

Everything runs locally on your own machine: no API keys, no accounts, no per-image costs.

![Alt text](/screenshots/screenshot-1.png?raw=true "Example run of adventure game.")

## Quick Start

Experience the visualization immediately with the built-in demo cartridge.

**1. Install**
```bash
pip install pipedream-fiction
```

**2. Run the Demo**
Launch the GUI without arguments to play the internal mock game.

```bash
pipedream-gui
```

The first launch downloads the AI models and the local inference servers into your user data folder (`~/.local/share/pipedream` on Linux), then prepares the image model for your GPU. That's up to 11 GB of downloads depending on the model, ending up as 7–8 GB on disk. Downloads use parallel connections, are checked against a pinned SHA-256, and resume where they left off if interrupted.

### Requirements

* A GPU with **8 GB of VRAM** and Vulkan drivers (NVIDIA, AMD or Intel), and 16 GB of RAM (recommended).
* Tested on Linux x86_64. Windows (Vulkan) and Apple Silicon (Metal) builds are wired up but not yet tested.
* If you have a second GPU (e.g. a laptop's integrated graphics), the scene-analysis model runs there; otherwise it runs on the CPU.

The game never waits for a picture: you can keep typing while one paints, and pictures for scenes you've already left are skipped. On an RTX 5060 Laptop GPU with the default model, a new place appears about 4.5 s after you press Enter (about 2 s to read the scene, 2 s to paint it), a change to the current place about 5.5 s, and a place you've seen before instantly.

### Image models

Pick one with `--image-model` and set the size with `--size` (default `768x576`). Painting time per scene on an RTX 5060 Laptop GPU:

| `--image-model` | Look | CUDA | Vulkan | Edits changes | Licence |
|---|---|---|---|---|---|
| `mage-flow` (default) | Oil-painterly, very faithful to the scene | ~1.8 s | not yet* | yes, ~4 s | MIT |
| `flux2-klein` | Vivid digital painting | ~4.4 s | ~6.3 s | yes | Apache 2.0 |
| `qwen-image` | Darkest and moodiest | ~12 s | ~17 s | no | Non-commercial |

\* stable-diffusion.cpp's Vulkan backend currently renders Mage-Flow as noise, so PipeDream needs a CUDA build of `sd-server` for it (see `PIPEDREAM_SD_SERVER` below). Without one it falls back to `flux2-klein` and says so. The bundled servers are Vulkan builds.

```bash
pipedream-gui --image-model qwen-image --size 640x480 adventure
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
* **Remembers the World:** Places are recognised by the room titles games print (or named by the AI director in games without them), never by guessing where a direction leads. Come back by any route, even a one-way chimney, and you see the same picture.
* **Pictures Follow the Story:** Open a window, move a rug or defeat a troll, and the place's picture is edited to match instead of being painted from scratch. When the lights go out, the picture goes dark.
* **Repaint:** If a picture comes out wrong, the Repaint button (or Ctrl+R) paints that place again, keeping any changes the story has made to it.
* **Fully Local:** Scenes are painted by Microsoft's [Mage-Flow Edit Turbo](https://github.com/microsoft/Mage) via [stable-diffusion.cpp](https://github.com/leejet/stable-diffusion.cpp), and read via [llama.cpp](https://github.com/ggml-org/llama.cpp) by the same Qwen3-VL model the image model uses as its text encoder.
* **Graceful Shutdown:** Safely handles terminal interrupts (CTRL+C) and window closures without leaving zombie processes behind.

### Customizing Styles

You can override the default art style ("Oil painting, dark fantasy") with the `--art-style` flag.

```bash
# Pixel Art Style
pipedream-gui --art-style "Retro 8-bit pixel art, green monochrome" adventure

# Pencil Sketch
pipedream-gui --art-style "Rough pencil sketch on parchment" adventure
```

### Cache Management

PipeDream remembers every place and picture, per game and art style, in your user cache folder (`~/.cache/pipedream` on Linux, `~/Library/Caches/pipedream` on macOS, `%LOCALAPPDATA%\pipedream\Cache` on Windows). To wipe the world map and start fresh for a specific game:

```bash
# Note: Ensure the flag comes before the game command
pipedream-gui --clear-cache adventure
```

---

## Development

If you want to play around with the source code:

1. **Clone the repo:**

```bash
git clone https://github.com/CPritch/PipeDream.git
cd PipeDream
```

2. **Install with [uv](https://docs.astral.sh/uv/):**

```bash
uv sync
uv run pytest
```

3. **Keep the models loaded between runs (optional):**
Loading the models takes a few seconds each launch. While iterating, start the servers once and point PipeDream at them. Logs for managed servers are written to `~/.local/state/pipedream/log`.

```bash
pipedream-gui --llm-url http://127.0.0.1:8080 --image-url http://127.0.0.1:1234 adventure
```

Environment overrides:

* `PIPEDREAM_SD_SERVER` / `PIPEDREAM_LLAMA_SERVER`: use your own server binaries, e.g. a CUDA build of stable-diffusion.cpp, which Mage-Flow needs for now and which is about 30% faster than Vulkan on NVIDIA (`cmake -B build -DSD_CUDA=ON && cmake --build build`).
* `PIPEDREAM_LLM_DEVICE`: the ggml device for the scene-analysis model (`Vulkan1`, `CUDA0`, or `none` for CPU).
* `PIPEDREAM_SEED`: a fixed image seed, so replaying the same game paints comparable pictures.

## Troubleshooting

* **Windows "Shim" Errors:** If a Python game crashes immediately on Windows, try wrapping the command to force path resolution:

```powershell
pipedream-gui cmd /c adventure
```
## Credits & Licences

PipeDream itself is MIT licensed. The models it downloads have their own licences:

* [Mage-Flow](https://github.com/microsoft/Mage) (default) is MIT.
* [Qwen3-VL-4B](https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct-GGUF), [Qwen3-VL-8B](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct-GGUF) and [Qwen3-4B](https://huggingface.co/unsloth/Qwen3-4B-GGUF) are Apache 2.0.
* [FLUX.2 klein 4B](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B) and the [FLUX.2 small decoder](https://huggingface.co/black-forest-labs/FLUX.2-small-decoder) are Apache 2.0.
* **Built with Qwen.** [Qwen-Image-2.1](https://huggingface.co/Qwen/Qwen-Image-2.1) and the [Viggle turbo](https://huggingface.co/Viggle/Qwen-Image-2.1-viggle-turbo) distillation (`--image-model qwen-image`) are released under the Qwen Research License Agreement, which permits **non-commercial use only**.
