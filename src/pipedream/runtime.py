"""Local AI backends: download, verify and run llama.cpp and stable-diffusion.cpp servers."""
import atexit
import hashlib
import os
import platform
import re
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import threading
import time
import zipfile
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from typing import NamedTuple

import platformdirs
import requests

DATA_DIR = platformdirs.user_data_dir("pipedream", appauthor=False)
RUNTIMES_DIR = os.path.join(DATA_DIR, "runtimes")
MODELS_DIR = os.path.join(DATA_DIR, "models")
LOGS_DIR = platformdirs.user_log_dir("pipedream", appauthor=False)


class Download(NamedTuple):
    name: str
    url: str
    size: int
    sha256: str


def _github(repo, tag, asset, size, sha256):
    return Download(asset, f"https://github.com/{repo}/releases/download/{tag}/{asset}", size, sha256)


def _hf(repo, path, size, sha256):
    return Download(path.rsplit("/", 1)[-1], f"https://huggingface.co/{repo}/resolve/main/{path}", size, sha256)


_SD = ("leejet/stable-diffusion.cpp", "master-945-a1ded76")
_LLAMA = ("ggml-org/llama.cpp", "b11510")

# Pinned server builds per (platform.system(), platform.machine()). Only Linux x86_64 has been tested so far.
RUNTIMES = {
    ("Linux", "x86_64"): {
        "sd": _github(*_SD, "sd-master-a1ded76-bin-Linux-Ubuntu-24.04-x86_64-vulkan.zip", 36929645,
                      "e65f3c4cf3a0c4c5aed5120ab449be7c883db6c70648bcc56ca55b246b9d8161"),
        "llama": _github(*_LLAMA, "llama-b11510-bin-ubuntu-vulkan-x64.tar.gz", 31750376,
                         "c496f53589b3505f5082cfafbaa9298b8bd19204694e6a471ee00207cbdb8822"),
    },
    ("Windows", "AMD64"): {
        "sd": _github(*_SD, "sd-master-a1ded76-bin-win-vulkan-x64.zip", 30089445,
                      "e3ee0aefa925cdf270148b03d67dc40eab025a44080601afe990bf123e308bcd"),
        "llama": _github(*_LLAMA, "llama-b11510-bin-win-vulkan-x64.zip", 33446147,
                         "1137d7ffa61103f651d9d0e3e8f43b97d4df1c2fa44f768bf8275701b4d38891"),
    },
    ("Darwin", "arm64"): {
        "sd": _github(*_SD, "sd-master-a1ded76-bin-Darwin-macOS-26.6.2-arm64.zip", 35100530,
                      "b530f858657a0b2a0b11313bea89a0b48398f6db783279c096a8307456cc8b1b"),
        "llama": _github(*_LLAMA, "llama-b11510-bin-macos-arm64.tar.gz", 12047446,
                         "7367be3138fd39c857675cc37028e3164f1c2446f9a0ca4ce9ef7f565a2e0a4f"),
    },
}

class ImageProfile(NamedTuple):
    files: dict  # role (diffusion, vae, llm, optional vision and director) -> Download
    sampling: dict  # sd-server sample_params
    quantize: str = ""  # quantize the downloaded diffusion weights to this ggml type on first install
    devices: tuple = ()  # ggml device prefixes the model is known to render correctly on; empty means any
    edits: bool = True  # whether the model can edit a picture it's given, for changes within a place
    # Repaint only the box around what changes (Mage-Flow redraws whatever it's given); False for models that keep
    # the rest of a picture intact by themselves (FLUX.2 klein)
    confine_edits: bool = True


FOUR_STEP = {"sample_method": "euler", "sample_steps": 4, "guidance": {"txt_cfg": 1.0}}

# Viggle's Qwen-Image-2.1 turbo schedule: 6 steps, no CFG. sd.cpp wants the terminal 0 included
# (steps + 1 sigmas); without it the last step is dropped and the image is left noisy.
QWEN_TURBO = {"sample_method": "euler", "sample_steps": 6, "custom_sigmas": [1.0, 0.9375, 0.875, 0.75, 0.5, 0.25, 0.0],
              "guidance": {"txt_cfg": 1.0}}

# Lets the director see a picture, to find what a change applies to (e.g. the lamp the player just took)
_QWEN3VL_4B_VISION = _hf("Qwen/Qwen3-VL-4B-Instruct-GGUF", "mmproj-Qwen3VL-4B-Instruct-F16.gguf",
                         836180256, "256f3a43bd4205ffef48d6b92715e1e70b5b0e9aef06522584967513a9985331")

# Where it's good enough, the image model's text encoder doubles as the director LLM, so there's only one
# language model to download. Otherwise a profile names its own "director".
# Timings are for a 768x576 scene on an RTX 5060 Laptop GPU.
PROFILES = {
    # Microsoft Mage-Flow Edit Turbo (MIT): ~1.8 s for a new scene, ~4 s for an edit. The edit model paints from text
    # as well as the plain Turbo model does, so one model covers both. The published int8 weights only run on CUDA,
    # so the bf16 weights are quantized to q8_0 locally. Even then sd.cpp's Vulkan backend renders Mage-Flow as
    # noise (as of master-945, on NVIDIA and AMD alike), so it's CUDA-only for now.
    "mage-flow": ImageProfile({
        "diffusion": _hf("Comfy-Org/Mage-Flow", "diffusion_models/mage_flow_edit_turbo_bf16.safetensors",
                         8231536760, "29c3726ecd64afe149eef28af3e27b6b40de52646bfd16757a37da4b6fbcf288"),
        "vae": _hf("Comfy-Org/Mage-Flow", "vae/mage_flow_vae_bf16.safetensors",
                   345053056, "34e076dc1e8a15321e1e07be5111d59cf16dd10b804b7c7e20b4de29013427e0"),
        "llm": _hf("Qwen/Qwen3-VL-4B-Instruct-GGUF", "Qwen3VL-4B-Instruct-Q4_K_M.gguf",
                   2497281664, "66358cb18bb6b3b1b6675aa412c7a88ef01d228f481184d13668e5201c730a0a"),
        "director_vision": _QWEN3VL_4B_VISION,
    }, FOUR_STEP, quantize="q8_0", devices=("CUDA",)),
    # Qwen-Image-2.1 with Viggle's 6-step turbo (non-commercial), the moodiest look, ~12 s
    "qwen-image": ImageProfile({
        "diffusion": _hf("Viggle/Qwen-Image-2.1-viggle-turbo", "Qwen-Image-2.1-viggle-turbo-v0.3-6step-Q4_K_M.gguf",
                         4335931552, "047dd62db7c7a55e6bb796e89710cb31457e3b944e0f5f94d0ebe71d1b9c8cae"),
        "vae": _hf("Comfy-Org/Qwen-Image-2.1", "vae/qwen_image_2.1_vae_bf16.safetensors",
                   675509688, "bb21f7473051e1ac368515dd3f2e15cd44d7a11748ee8823e1ddca3e4876b7c9"),
        "llm": _hf("Qwen/Qwen3-VL-8B-Instruct-GGUF", "Qwen3VL-8B-Instruct-Q4_K_M.gguf",
                   5027784800, "67d1659bfe71b89d50b45a4ad1a9e5b997e5bb16ce5da66a6a6167abd569e9e2"),
        "vision": _hf("Qwen/Qwen3-VL-8B-Instruct-GGUF", "mmproj-Qwen3VL-8B-Instruct-F16.gguf",
                      1159029824, "ca524100ebf825c9a870db1c580d03879e0da0ab2541697e2458e64891cf9d38"),
    }, QWEN_TURBO, edits=False),
    # FLUX.2 klein 4B (Apache 2.0) with BFL's small decoder, ~4.4 s
    "flux2-klein": ImageProfile({
        "diffusion": _hf("leejet/FLUX.2-klein-4B-GGUF", "flux-2-klein-4b-Q4_0.gguf",
                         2460378560, "d1023499ef3f2f82ff7c50e6778495195c1b6cc34835741778868428111f9ff4"),
        "vae": _hf("black-forest-labs/FLUX.2-small-decoder", "full_encoder_small_decoder.safetensors",
                   249519092, "ea4273f02d1fafbf8e1d1c2cf6018ed8748652eb0bf34f2dd91171f16f15ab62"),
        "llm": _hf("unsloth/Qwen3-4B-GGUF", "Qwen3-4B-Q4_K_M.gguf",
                   2497281312, "f6f851777709861056efcdad3af01da38b31223a3ba26e61a4f8bf3a2195813a"),
        # Plain Qwen3-4B misreads openings and copies "You are..." into prompts, which paints the player in
        "director": _hf("Qwen/Qwen3-VL-4B-Instruct-GGUF", "Qwen3VL-4B-Instruct-Q4_K_M.gguf",
                        2497281664, "66358cb18bb6b3b1b6675aa412c7a88ef01d228f481184d13668e5201c730a0a"),
        "director_vision": _QWEN3VL_4B_VISION,
    }, FOUR_STEP, confine_edits=False),
}
DEFAULT_PROFILE = "mage-flow"
FALLBACK_PROFILE = "flux2-klein"  # ~6 s on Vulkan

SERVER_BINARIES = {"sd": "sd-server", "llama": "llama-server"}
CHUNK_SIZE = 64 << 20
CONNECTIONS = 8
EXE = ".exe" if sys.platform == "win32" else ""


def _gb(n):
    return f"{n / 1e9:.1f} GB"


def download(item, dest, progress=print):
    """Fetch `item` to `dest` over parallel ranged connections, then check the sha256.

    A single long-lived connection to the Hugging Face CDN can be throttled to a few MB/s while parallel ranges
    each get tens of MB/s. Finished chunks are recorded, so an interrupted download resumes where it stopped.
    """
    part, state = dest + ".part", dest + ".part.chunks"
    chunks = [(start, min(start + CHUNK_SIZE, item.size) - 1) for start in range(0, item.size, CHUNK_SIZE)]
    done = set()
    if os.path.exists(part) and os.path.exists(state):
        with open(state) as f:
            done = {int(line) for line in f if line.strip()}
    else:
        with open(part, "wb") as f:
            f.truncate(item.size)
        open(state, "w").close()

    lock = threading.Lock()
    received = [sum(chunks[i][1] - chunks[i][0] + 1 for i in done)]

    def fetch(index, attempts=3):
        start, end = chunks[index]
        for attempt in range(attempts):
            try:
                with requests.get(item.url, headers={"Range": f"bytes={start}-{end}"}, stream=True, timeout=60) as r:
                    r.raise_for_status()
                    if r.status_code != 206:
                        raise RuntimeError(f"{item.url} does not support ranged downloads")
                    with open(part, "r+b") as f:
                        f.seek(start)
                        for block in r.iter_content(1 << 20):
                            f.write(block)
                            with lock:
                                received[0] += len(block)
                break
            except requests.RequestException:
                if attempt == attempts - 1:
                    raise
        with lock, open(state, "a") as f:
            f.write(f"{index}\n")

    with ThreadPoolExecutor(CONNECTIONS) as pool:
        futures = [pool.submit(fetch, i) for i in range(len(chunks)) if i not in done]
        while futures:
            finished, pending = wait(futures, timeout=1, return_when=FIRST_EXCEPTION)
            progress(f"Downloading {item.name}: {_gb(received[0])} / {_gb(item.size)}")
            failed = [f for f in finished if f.exception()]
            if failed:
                pool.shutdown(cancel_futures=True)
                raise failed[0].exception()
            futures = list(pending)

    progress(f"Verifying {item.name}...")
    digest = hashlib.sha256()
    with open(part, "rb") as f:
        for block in iter(lambda: f.read(8 << 20), b""):
            digest.update(block)
    if os.path.getsize(part) != item.size or digest.hexdigest() != item.sha256:
        os.remove(part)
        os.remove(state)
        raise RuntimeError(f"Download of {item.name} was corrupt (size or checksum mismatch), please try again")
    os.replace(part, dest)
    os.remove(state)


def _extract(archive, dest):
    if archive.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            z.extractall(dest)
    else:
        with tarfile.open(archive) as t:
            t.extractall(dest, filter="data")


def _find_binary(root, name):
    for folder, _, files in os.walk(root):
        if name + EXE in files:
            return os.path.join(folder, name + EXE)
    raise RuntimeError(f"{name} not found in {root}")


def _quantize(sd_cli, source, dest, weight_type, progress):
    progress("Optimising the image model for your GPU (one-time, a couple of minutes)...")
    partial = dest[:-len(".gguf")] + ".partial.gguf"
    result = subprocess.run([sd_cli, "-M", "convert", "-m", source, "-o", partial, "--type", weight_type],
                            capture_output=True, text=True)
    if result.returncode != 0 or not os.path.exists(partial):
        raise RuntimeError(f"Could not quantize {os.path.basename(source)}:\n{result.stdout[-1500:]}")
    os.replace(partial, dest)
    os.remove(source)  # the full-precision download is no longer needed


def install(profile=DEFAULT_PROFILE, progress=print):
    """Fetch any missing server builds and models. Returns {key: path} for everything needed to run."""
    return install_models(profile, install_runtimes(progress), progress)


def install_runtimes(progress=print):
    key = (platform.system(), platform.machine())
    if key not in RUNTIMES:
        raise RuntimeError(f"No local AI runtime is available for {key[0]} {key[1]} yet")

    paths = {}
    for kind, item in RUNTIMES[key].items():
        folder = os.path.join(RUNTIMES_DIR, re.sub(r"\.(zip|tar\.gz)$", "", item.name))
        marker = os.path.join(folder, ".complete")
        if not os.path.exists(marker):
            os.makedirs(RUNTIMES_DIR, exist_ok=True)
            archive = os.path.join(RUNTIMES_DIR, item.name)
            download(item, archive, progress)
            shutil.rmtree(folder, ignore_errors=True)
            _extract(archive, folder)
            os.remove(archive)
            open(marker, "w").close()
        binary = _find_binary(folder, SERVER_BINARIES[kind])
        os.chmod(binary, 0o755)  # zip archives don't keep the executable bit
        # e.g. PIPEDREAM_SD_SERVER=/path/to/sd-server for a local CUDA build (~30% faster than Vulkan on NVIDIA)
        paths[kind] = os.getenv(f"PIPEDREAM_{kind.upper()}_SERVER") or binary
        if kind == "sd":
            paths["sd_cli"] = _find_binary(folder, "sd-cli")
            os.chmod(paths["sd_cli"], 0o755)
    return paths


def install_models(profile, paths, progress=print):
    paths = dict(paths)
    os.makedirs(MODELS_DIR, exist_ok=True)
    chosen = PROFILES[profile]
    for role, item in chosen.files.items():
        path = os.path.join(MODELS_DIR, item.name)
        quantized = os.path.splitext(path)[0] + f"-{chosen.quantize}.gguf" if role == "diffusion" and chosen.quantize else None
        if quantized and os.path.exists(quantized):
            paths[role] = quantized
            continue
        # A matching size is enough here; the checksum was verified when the file was downloaded
        if not (os.path.exists(path) and os.path.getsize(path) == item.size):
            download(item, path, progress)
        if quantized:
            _quantize(paths["sd_cli"], path, quantized, chosen.quantize, progress)
            path = quantized
        paths[role] = path
    return paths


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def list_devices(server):
    """GPU device names a server can use, e.g. ["Vulkan0", "Vulkan1"] or ["CUDA0"]."""
    out = subprocess.run([server, "--list-devices"], capture_output=True, text=True, timeout=60).stdout
    # llama-server prints "  Vulkan0: NAME", sd-server prints "Vulkan0<tab>NAME"
    return re.findall(r"^\s*([A-Za-z]+\d+)[:\t]", out, re.MULTILINE)


def choose_profile(requested, devices, progress=print):
    """Fall back to a model that renders correctly when the requested one doesn't support these devices."""
    allowed = PROFILES[requested].devices
    if allowed and not any(d.startswith(allowed) for d in devices):
        progress(f"{requested} needs a {'/'.join(allowed)} build of sd-server for now, using {FALLBACK_PROFILE} instead")
        return FALLBACK_PROFILE
    return requested


def _die_with_parent():
    # Linux only: take the server down if PipeDream dies without cleaning up. The signal fires when the
    # spawning *thread* exits, so servers must be started from a thread that lives as long as the session.
    import ctypes
    ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG


class LocalBackends:
    """Starts whichever of the director LLM and image servers wasn't supplied as a URL."""

    def __init__(self, llm_url=None, image_url=None, profile=DEFAULT_PROFILE):
        self.llm_url = llm_url
        self.image_url = image_url
        self.profile = profile
        self.processes = []
        atexit.register(self.stop)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.stop()

    def start(self, progress=print):
        if self.llm_url and self.image_url:
            return self
        paths = install_runtimes(progress)
        if not self.image_url:
            self.profile = choose_profile(self.profile, list_devices(paths["sd"]), progress)
        paths = install_models(self.profile, paths, progress)
        gpus = list_devices(paths["llama"])
        # Image generation needs the big GPU. Give the LLM a second GPU if there is one (e.g. an iGPU), else the CPU.
        llm_device = os.getenv("PIPEDREAM_LLM_DEVICE") or (gpus[1] if len(gpus) > 1 else "none")

        if not self.image_url:
            port = _free_port()
            vision = ["--llm_vision", paths["vision"]] if "vision" in paths else []
            self._spawn("sd-server", [
                paths["sd"], "--listen-port", str(port),
                "--diffusion-model", paths["diffusion"], "--vae", paths["vae"],
                "--llm", paths["llm"], *vision, "--diffusion-fa",
            ])
            self.image_url = f"http://127.0.0.1:{port}"
        if not self.llm_url:
            port = _free_port()
            sight = ["--mmproj", paths["director_vision"]] if "director_vision" in paths else []
            self._spawn("llama-server", [
                paths["llama"], "--port", str(port), "-m", paths.get("director", paths["llm"]), *sight,
                # Two slots, so the director can look for something in the picture while it reads the reply
                "--device", llm_device, "-c", "8192", "-np", "2",
            ])
            self.llm_url = f"http://127.0.0.1:{port}"

        progress("Loading AI models...")
        self._wait_ready(self.llm_url + "/health")
        self._wait_ready(self.image_url + "/sdcpp/v1/capabilities")
        if self.processes:
            self._warm_up()
        return self

    def _warm_up(self):
        # sd-server loads its weights on the first job. Queue a tiny one now so that happens while the game
        # starts and the director reads the opening, not after. The API is async, so this returns at once.
        try:
            requests.post(f"{self.image_url}/sdcpp/v1/img_gen", timeout=10, json={
                "prompt": "warm up", "width": 256, "height": 256, "sample_params": {"sample_steps": 1}})
        except requests.RequestException:
            pass

    def _spawn(self, name, argv):
        os.makedirs(LOGS_DIR, exist_ok=True)
        log_path = os.path.join(LOGS_DIR, f"{name}.log")
        with open(log_path, "w") as log:
            proc = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    preexec_fn=_die_with_parent if sys.platform == "linux" else None)
        proc.log_path = log_path
        self.processes.append(proc)

    def _wait_ready(self, url, timeout=300):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for proc in self.processes:
                if proc.poll() is not None:
                    with open(proc.log_path) as f:
                        tail = "".join(f.readlines()[-15:])
                    raise RuntimeError(f"{os.path.basename(proc.args[0])} exited with code {proc.returncode}:\n{tail}")
            try:
                if requests.get(url, timeout=2).ok:
                    return
            except requests.ConnectionError:
                pass
            time.sleep(0.5)
        raise RuntimeError(f"Timed out waiting for {url}")

    def stop(self):
        for proc in self.processes:
            if proc.poll() is None:
                proc.terminate()
        for proc in self.processes:
            try:
                proc.wait(timeout=2)  # they hold nothing worth a graceful shutdown, so don't keep the user waiting
            except subprocess.TimeoutExpired:
                proc.kill()
        self.processes = []
