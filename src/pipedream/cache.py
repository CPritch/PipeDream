import os
import json
import hashlib
import shutil
import platformdirs

# e.g. ~/.cache/pipedream on Linux, rather than wherever the user launched from
DEFAULT_CACHE_DIR = platformdirs.user_cache_dir("pipedream", appauthor=False)

class SmartCache:
    def __init__(self, engine, generator, session_id="default", style_prompt=None, cache_dir=None, img2img=False):
        self.engine = engine
        self.generator = generator
        cache_dir = cache_dir or DEFAULT_CACHE_DIR
        self.cache_dir = cache_dir
        self.img2img = img2img
        self.images_dir = os.path.join(cache_dir, f"images_{session_id}")
        self.map_file = os.path.join(cache_dir, f"mapping_{session_id}.json")
        self.memory = {}
        self.style = style_prompt or "Oil painting, dark fantasy, atmospheric"

        os.makedirs(self.images_dir, exist_ok=True)
        self._load_map()

    def _load_map(self):
        if os.path.exists(self.map_file):
            with open(self.map_file, 'r') as f:
                self.memory = json.load(f)

    def _save_map(self):
        with open(self.map_file, 'w') as f:
            json.dump(self.memory, f, indent=2)

    def _get_hash(self, text):
        combined = f"{text}||{self.style}"
        return hashlib.md5(combined.encode('utf-8')).hexdigest()
    
    def clear(self):
        """Wipes the cache directory and memory."""
        print("[*] Clearing Cache...")
        if os.path.exists(self.images_dir):
            shutil.rmtree(self.images_dir)
        os.makedirs(self.images_dir, exist_ok=True)
        self.memory = {}
        self._save_map()
        print("[*] Cache Cleared.")

    def lookup(self, raw_text):
        """
        Check if we already have an image for this EXACT game text.
        Returns: Path to image (str) or None.
        """
        text_hash = self._get_hash(raw_text)
        
        if text_hash in self.memory:
            image_path = self.memory[text_hash]
            if os.path.exists(image_path):
                print(f"[*] Cache Hit: {raw_text[:30]}...")
                return image_path
        
        return None

    def generate(self, raw_text, visual_prompt, reference_image=None):
        """
        Generates the image, saves it using the RAW TEXT hash, and returns path.
        """
        text_hash = self._get_hash(raw_text)
        
        if text_hash in self.memory:
            if os.path.exists(self.memory[text_hash]):
                print(f"[*] SmartCache Hit: {self.memory[text_hash]}")
                return self.memory[text_hash]

        filename = f"{text_hash}.png"
        filepath = os.path.join(self.images_dir, filename)

        self.engine.report_status("Generating Image...")
        print("[*] Generating Image...")

        references = []
        if self.img2img and reference_image and os.path.exists(reference_image):
            print("   > Injecting reference image for img2img transition...")
            references.append(reference_image)

        try:
            img_data = self.generator.generate(visual_prompt, references)
        except Exception as e:
            print(f"[!] Image Gen Error: {e}")
            self.engine.report_status("Error")
            return None

        with open(filepath, 'wb') as f:
            f.write(img_data)
        self.memory[text_hash] = filepath
        self._save_map()
        self.engine.report_status("Ready")
        return filepath
