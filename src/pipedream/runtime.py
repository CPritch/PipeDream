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
import time
import zipfile
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

# Qwen-Image-2.1 (Viggle 6-step turbo) for images. Its Qwen3-VL-8B text encoder doubles as the director LLM.
MODELS = {
    "diffusion": _hf("Viggle/Qwen-Image-2.1-viggle-turbo", "Qwen-Image-2.1-viggle-turbo-v0.3-6step-Q4_K_M.gguf",
                     4335931552, "047dd62db7c7a55e6bb796e89710cb31457e3b944e0f5f94d0ebe71d1b9c8cae"),
    "vae": _hf("Comfy-Org/Qwen-Image-2.1", "vae/qwen_image_2.1_vae_bf16.safetensors",
               675509688, "bb21f7473051e1ac368515dd3f2e15cd44d7a11748ee8823e1ddca3e4876b7c9"),
    "llm": _hf("Qwen/Qwen3-VL-8B-Instruct-GGUF", "Qwen3VL-8B-Instruct-Q4_K_M.gguf",
               5027784800, "67d1659bfe71b89d50b45a4ad1a9e5b997e5bb16ce5da66a6a6167abd569e9e2"),
    "vision": _hf("Qwen/Qwen3-VL-8B-Instruct-GGUF", "mmproj-Qwen3VL-8B-Instruct-F16.gguf",
                  1159029824, "ca524100ebf825c9a870db1c580d03879e0da0ab2541697e2458e64891cf9d38"),
}

SERVER_BINARIES = {"sd": "sd-server", "llama": "llama-server"}
EXE = ".exe" if sys.platform == "win32" else ""


def _gb(n):
    return f"{n / 1e9:.1f} GB"


def download(item, dest, progress=print):
    """Stream `item` to `dest`, resuming a previous partial download and checking the sha256."""
    part = dest + ".part"
    done = os.path.getsize(part) if os.path.exists(part) else 0
    digest = hashlib.sha256()
    if done:
        with open(part, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                digest.update(chunk)

    headers = {"Range": f"bytes={done}-"} if done else {}
    with requests.get(item.url, headers=headers, stream=True, timeout=60) as r:
        r.raise_for_status()
        if done and r.status_code != 206:  # server ignored the Range header, start again
            done, digest = 0, hashlib.sha256()
        last_report = 0.0
        with open(part, "ab" if done else "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if time.monotonic() - last_report > 1:
                    progress(f"Downloading {item.name}: {_gb(done)} / {_gb(item.size)}")
                    last_report = time.monotonic()

    if done != item.size or digest.hexdigest() != item.sha256:
        os.remove(part)
        raise RuntimeError(f"Download of {item.name} was corrupt (size or checksum mismatch), please try again")
    os.replace(part, dest)


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


def install(progress=print):
    """Fetch any missing server builds and models. Returns {key: path} for everything needed to run."""
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

    os.makedirs(MODELS_DIR, exist_ok=True)
    for kind, item in MODELS.items():
        path = os.path.join(MODELS_DIR, item.name)
        # A matching size is enough here; the checksum was verified when the file was downloaded
        if not (os.path.exists(path) and os.path.getsize(path) == item.size):
            download(item, path, progress)
        paths[kind] = path
    return paths


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _list_gpus(llama_server):
    """ggml device names (e.g. Vulkan0, Vulkan1); both servers share the same naming."""
    out = subprocess.run([llama_server, "--list-devices"], capture_output=True, text=True, timeout=60).stdout
    return re.findall(r"^\s+(\w+\d+):", out, re.MULTILINE)


def _die_with_parent():
    # Linux only: take the server down if PipeDream dies without cleaning up. The signal fires when the
    # spawning *thread* exits, so servers must be started from a thread that lives as long as the session.
    import ctypes
    ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG


class LocalBackends:
    """Starts whichever of the director LLM and image servers wasn't supplied as a URL."""

    def __init__(self, llm_url=None, image_url=None):
        self.llm_url = llm_url
        self.image_url = image_url
        self.processes = []
        atexit.register(self.stop)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.stop()

    def start(self, progress=print):
        if self.llm_url and self.image_url:
            return self
        paths = install(progress)
        gpus = _list_gpus(paths["llama"])
        # Image generation needs the big GPU. Give the LLM a second GPU if there is one (e.g. an iGPU), else the CPU.
        llm_device = os.getenv("PIPEDREAM_LLM_DEVICE") or (gpus[1] if len(gpus) > 1 else "none")

        if not self.image_url:
            port = _free_port()
            self._spawn("sd-server", [
                paths["sd"], "--listen-port", str(port),
                "--diffusion-model", paths["diffusion"], "--vae", paths["vae"],
                "--llm", paths["llm"], "--llm_vision", paths["vision"], "--diffusion-fa",
            ])
            self.image_url = f"http://127.0.0.1:{port}"
        if not self.llm_url:
            port = _free_port()
            self._spawn("llama-server", [
                paths["llama"], "--port", str(port), "-m", paths["llm"],
                "--device", llm_device, "-c", "8192", "-np", "1",
            ])
            self.llm_url = f"http://127.0.0.1:{port}"

        progress("Loading AI models...")
        self._wait_ready(self.llm_url + "/health")
        self._wait_ready(self.image_url + "/sdcpp/v1/capabilities")
        return self

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
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        self.processes = []
