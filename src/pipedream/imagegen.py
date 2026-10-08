import base64
import time
import requests

class ImageGenerator:
    """Client for stable-diffusion.cpp's sd-server native async API."""

    def __init__(self, base_url, sampling, width=768, height=576, timeout=600):
        self.base_url = base_url
        self.sampling = sampling
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
