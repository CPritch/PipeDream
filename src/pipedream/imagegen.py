import base64
import time
import requests

# Viggle's Qwen-Image-2.1 turbo schedule: 6 steps, no CFG. sd.cpp wants the terminal 0 included
# (steps + 1 sigmas); without it the last step is dropped and the image is left noisy.
TURBO_SAMPLING = {
    "sample_method": "euler",
    "sample_steps": 6,
    "custom_sigmas": [1.0, 0.9375, 0.875, 0.75, 0.5, 0.25, 0.0],
    "guidance": {"txt_cfg": 1.0},
}

class ImageGenerator:
    """Client for stable-diffusion.cpp's sd-server native async API."""

    def __init__(self, base_url, width=768, height=576, timeout=600):
        self.base_url = base_url
        self.width = width
        self.height = height
        self.timeout = timeout

    def generate(self, prompt, reference_images=()):
        """Returns PNG bytes. Reference images are file paths."""
        body = {
            "prompt": prompt,
            "width": self.width,
            "height": self.height,
            "seed": -1,
            "sample_params": TURBO_SAMPLING,
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
