import os
import json
import hashlib
import requests
from litellm import image_generation
from dotenv import load_dotenv

load_dotenv()

class SmartCache:
    def __init__(self, cache_dir="cache"):
        self.cache_dir = cache_dir
        self.images_dir = os.path.join(cache_dir, "images")
        self.map_file = os.path.join(cache_dir, "mapping.json")
        self.memory = {}
        self.model = os.getenv("IMAGE_MODEL", "gemini/gemini-2.5-flash-image")
        
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
        return hashlib.md5(text.encode('utf-8')).hexdigest()

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

    def generate(self, raw_text, visual_prompt):
        """
        Generates the image, saves it using the RAW TEXT hash, and returns path.
        """
        text_hash = self._get_hash(raw_text)
        filename = f"{text_hash}.png"
        filepath = os.path.join(self.images_dir, filename)

        print(f"[*] Generating Image for: {visual_prompt[:40]}...")

        try:
            response = image_generation(
                model=self.model,
                prompt=visual_prompt
            )
            
            image_url = response.data[0].url
            
            print(f"   > Downloading from {self.model}...")
            img_data = requests.get(image_url).content
            with open(filepath, 'wb') as f:
                f.write(img_data)
                
            self.memory[text_hash] = filepath
            self._save_map()
            
            return filepath
            
        except Exception as e:
            print(f"[!] Image Gen Error: {e}")
            return None