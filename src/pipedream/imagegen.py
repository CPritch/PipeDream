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

    def generate(self, prompt, reference_images=(), seed=-1, init_image=None, mask=None):
        """Returns PNG bytes. Reference and init images are file paths; the mask is PNG bytes, white where to paint."""
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
        if init_image and mask:
            body["init_image"] = base64.b64encode(open(init_image, "rb").read()).decode()
            body["mask_image"] = base64.b64encode(mask).decode()
            body["strength"] = 1.0  # repaint the masked area completely; everything outside it is kept as is

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

    def edit(self, image, instruction, boxes=(), min_change=None, attempts=1, first_attempt=0):
        """Edit a picture. With boxes (0-1000 coordinates, from the director), only those areas are repainted.

        Returns the new picture's path, or None if no attempt changed the picture by at least `min_change`.
        """
        min_change = _VISIBLE_CHANGE if min_change is None else min_change
        # Without the style and "keep everything else", edits drift towards a brighter, cleaner look
        prompt = f"{self.style}. {instruction} Keep everything else exactly the same."
        width, height = self.generator.width, self.generator.height
        mask = mask_png(width, height, boxes) if boxes else None
        # The edit model sometimes returns the picture practically unchanged; another seed often takes
        for attempt in range(first_attempt, first_attempt + attempts):
            seed = self.seed + attempt if self.seed >= 0 else -1
            png = self.generator.generate(prompt, [image], seed=seed, init_image=image if mask else None, mask=mask)
            png = paste(image, png, boxes) if mask else paste_changes(image, png)
            if changed(image, png, boxes) >= min_change:
                return self._save(png)
        return None

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


# Largest mean difference between corresponding cells of a coarse grid (0-255) for an edit to count as visible.
# No-op edits measure 15-40; edits that did what was asked, 50 and up. A removal clears its whole box, so it
# measures far more (170+) than an object repainted in place (30-40).
_VISIBLE_CHANGE = 45
REMOVAL_CHANGE = 80


def changed(before_path, after_png, boxes=()):
    """How visibly an edit changed the picture, within the edited boxes if there are any."""
    from PySide6.QtCore import QRect, Qt
    from PySide6.QtGui import QImage

    before, after = QImage(before_path), QImage.fromData(after_png)
    if before.isNull() or after.isNull() or before.size() != after.size():
        return 255  # can't compare, so don't retry
    region = before.rect()
    if boxes:
        x1, y1 = min(b[0] for b in boxes), min(b[1] for b in boxes)
        x2, y2 = max(b[2] for b in boxes), max(b[3] for b in boxes)
        w, h = before.width(), before.height()
        region = QRect(int(x1 * w / 1000), int(y1 * h / 1000), max(1, int((x2 - x1) * w / 1000)), max(1, int((y2 - y1) * h / 1000)))

    def cells(image):
        small = image.copy(region).convertToFormat(QImage.Format_Grayscale8).scaled(
            16, 12, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        return [small.pixelColor(x, y).value() for y in range(12) for x in range(16)]

    return max(abs(a - b) for a, b in zip(cells(before), cells(after)))


def paste(original_path, edited_png, boxes):
    """The original picture with only the edited boxes taken from the edit, blended in at the edges.

    The image model round-trips the whole picture through its VAE even when it only repaints the mask, which
    softens every pixel a little; over several edits that adds up, so everything outside the boxes is kept as is.
    """
    from PySide6.QtCore import QBuffer, Qt
    from PySide6.QtGui import QImage, QPainter

    original = QImage(original_path).convertToFormat(QImage.Format_ARGB32)
    edited = QImage.fromData(edited_png).convertToFormat(QImage.Format_ARGB32)
    if original.isNull() or edited.size() != original.size():
        return edited_png
    w, h = original.width(), original.height()
    mask = QImage.fromData(mask_png(w, h, boxes)).convertToFormat(QImage.Format_Grayscale8)
    # Shrinking and regrowing the mask blurs its edges, so the edit fades into the original over ~12 px
    feathered = mask.scaled(max(1, w // 12), max(1, h // 12), Qt.IgnoreAspectRatio, Qt.SmoothTransformation).scaled(
        w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    edited.setAlphaChannel(feathered)
    painter = QPainter(original)
    painter.drawImage(0, 0, edited)
    painter.end()
    buffer = QBuffer()
    buffer.open(QBuffer.WriteOnly)
    original.convertToFormat(QImage.Format_RGB32).save(buffer, "PNG")
    return bytes(buffer.data())


def paste_changes(original_path, edited_png, threshold=35):
    """The original picture with only what an unconfined edit really changed (a thief it added), not the slight
    redrawing the model does everywhere else."""
    from PySide6.QtCore import QBuffer, Qt
    from PySide6.QtGui import QImage, QPainter

    original = QImage(original_path).convertToFormat(QImage.Format_ARGB32)
    edited = QImage.fromData(edited_png).convertToFormat(QImage.Format_ARGB32)
    if original.isNull() or edited.size() != original.size():
        return edited_png
    gw, gh = 96, 72  # compare coarse cells, so noise and texture don't count, only real changes

    def cells(image):
        small = image.convertToFormat(QImage.Format_Grayscale8).scaled(gw, gh, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        return [small.pixelColor(x, y).value() for y in range(gh) for x in range(gw)]

    hot = [abs(a - b) > threshold for a, b in zip(cells(original), cells(edited))]
    # Grow each changed cell by two in every direction, so the whole new object comes across, edges included
    grown = bytearray(gw * gh)
    for i, on in enumerate(hot):
        if on:
            x, y = i % gw, i // gw
            for yy in range(max(0, y - 2), min(gh, y + 3)):
                grown[yy * gw + max(0, x - 2):yy * gw + min(gw, x + 3)] = b"\xff" * (min(gw, x + 3) - max(0, x - 2))
    mask = QImage(bytes(grown), gw, gh, gw, QImage.Format_Grayscale8).scaled(
        original.width(), original.height(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    edited.setAlphaChannel(mask)
    painter = QPainter(original)
    painter.drawImage(0, 0, edited)
    painter.end()
    buffer = QBuffer()
    buffer.open(QBuffer.WriteOnly)
    original.convertToFormat(QImage.Format_RGB32).save(buffer, "PNG")
    return bytes(buffer.data())


def mask_png(width, height, boxes, pad=0.15):
    """A greyscale PNG, white over each box (0-1000 coordinates) plus a margin, black elsewhere."""
    rects = []
    for x1, y1, x2, y2 in boxes:
        x1, y1, x2, y2 = x1 * width / 1000, y1 * height / 1000, x2 * width / 1000, y2 * height / 1000
        mx, my = (x2 - x1) * pad + 8, (y2 - y1) * pad + 8
        rects.append((max(0, x1 - mx), max(0, y1 - my), min(width, x2 + mx), min(height, y2 + my)))
    rows = []
    for y in range(height):
        spans = [(int(x1), int(x2)) for x1, y1, x2, y2 in rects if y1 <= y < y2]
        row = bytearray(width)
        for a, b in spans:
            row[a:b] = b"\xff" * (b - a)
        rows.append(b"\x00" + bytes(row))
    return _png(width, height, b"".join(rows))


def _png(width, height, rows):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)  # 8-bit greyscale
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def darkness_png(width, height):
    """A near-black picture for when the player can't see: deliberately nothing to look at."""
    cx, cy, reach = width / 2, height / 2, (width * width + height * height) ** 0.5 / 2
    rows = []
    for y in range(height):
        dy = (y - cy) ** 2
        rows.append(b"\x00" + bytes(int(14 * max(0.0, 1 - ((x - cx) ** 2 + dy) ** 0.5 / reach)) for x in range(width)))
    return _png(width, height, b"".join(rows))
