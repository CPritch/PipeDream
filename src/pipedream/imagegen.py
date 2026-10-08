import base64
import os
import struct
import time
import zlib

import requests

DEFAULT_STYLE = "Oil painting, dark fantasy, atmospheric"


class ImageGenerator:
    """Client for stable-diffusion.cpp's sd-server native async API."""

    def __init__(self, base_url, sampling, width=768, height=576, timeout=600):
        self.base_url = base_url
        self.sampling = sampling
        self.width = width
        self.height = height
        self.timeout = timeout

    def generate(self, prompt, reference_images=(), seed=-1):
        """Returns PNG bytes. Reference images are file paths."""
        body = {
            "prompt": prompt,
            "width": self.width,
            "height": self.height,
            "seed": seed,
            "sample_params": self.sampling,
            "output_format": "png",
        }
        if reference_images:
            body["ref_images"] = [base64.b64encode(open(p, "rb").read()).decode() for p in reference_images]

        response = requests.post(f"{self.base_url}/sdcpp/v1/img_gen", json=body, timeout=30)
        response.raise_for_status()
        job_url = f"{self.base_url}/sdcpp/v1/jobs/{response.json()['id']}"

        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            job = requests.get(job_url, timeout=30).json()
            if job["status"] == "completed":
                return base64.b64decode(job["result"]["images"][0]["b64_json"])
            if job["status"] in ("failed", "cancelled"):
                raise RuntimeError(f"Image generation {job['status']}: {(job.get('error') or {}).get('message')}")
            time.sleep(0.25)
        raise TimeoutError("Image generation timed out")


class Painter:
    """Paints places and edits their pictures, saving every picture under `folder`."""

    def __init__(self, generator, folder, style=None, edits=True, seed=-1):
        self.generator = generator
        self.folder = folder
        self.style = style or DEFAULT_STYLE
        self.edits = edits  # whether the image model can edit a picture it's given
        self.seed = seed

    def paint(self, prompt, seed=None):
        # Style first: the image model weights the start of the prompt most
        return self._save(self.generator.generate(f"{self.style}. {prompt}", seed=self.seed if seed is None else seed))

    def edit(self, image, instruction):
        # Without the style and "keep everything else", edits drift towards a brighter, cleaner look
        prompt = f"{self.style}. {instruction} Keep everything else exactly the same."
        return self._save(self.generator.generate(prompt, [image], seed=self.seed))

    def darkness(self):
        path = os.path.join(self.folder, "darkness.png")
        if not os.path.exists(path):
            os.makedirs(self.folder, exist_ok=True)
            with open(path, "wb") as f:
                f.write(darkness_png(self.generator.width, self.generator.height))
        return path

    def _save(self, png):
        os.makedirs(self.folder, exist_ok=True)
        path = os.path.join(self.folder, f"{os.urandom(16).hex()}.png")
        with open(path, "wb") as f:
            f.write(png)
        return path


def darkness_png(width, height):
    """A near-black picture for when the player can't see: deliberately nothing to look at."""
    cx, cy, reach = width / 2, height / 2, (width * width + height * height) ** 0.5 / 2
    rows = []
    for y in range(height):
        dy = (y - cy) ** 2
        rows.append(b"\x00" + bytes(int(14 * max(0.0, 1 - ((x - cx) ** 2 + dy) ** 0.5 / reach)) for x in range(width)))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)  # 8-bit greyscale
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b"")
