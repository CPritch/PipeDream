import base64
import os
import json
import hashlib
import shutil
import requests
from litellm import completion, image_generation
from dotenv import load_dotenv

load_dotenv(override=True)

class SmartCache:
    def __init__(self, style_prompt=None, cache_dir="cache"):
        self.cache_dir = cache_dir
        self.images_dir = os.path.join(cache_dir, "images")
        self.map_file = os.path.join(cache_dir, "mapping.json")
        self.memory = {}
        self.model = os.getenv("IMAGE_MODEL", "gemini/gemini-2.5-flash-image")
        self.api_key = os.getenv("GEMINI_API_KEY")
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

    def generate(self, raw_text, visual_prompt):
        """
        Generates the image, saves it using the RAW TEXT hash, and returns path.
        """
        text_hash = self._get_hash(raw_text)
        filename = f"{text_hash}.png"
        filepath = os.path.join(self.images_dir, filename)

        print(f"[*] Generating Image ({self.model})...")

        try:
            # GEMINI (AI Studio Route)
            # Gemini image generation uses the completion endpoint with modalities.
            if "gemini" in self.model.lower():
                print("   > Using Gemini 'completion' route (AI Studio)...")
                
                call_model = self.model
                if not call_model.startswith("gemini/"):
                     call_model = f"gemini/{call_model}"

                response = completion(
                    model=call_model,
                    messages=[{"role": "user", "content": visual_prompt}],
                    modalities=["image", "text"],
                    api_key=self.api_key
                )
                
                img_data = None
                message = response.choices[0].message
                
                if hasattr(message, "images") and message.images:
                    first_image = message.images[0]
                    
                    if 'image_url' in first_image and 'url' in first_image['image_url']:
                        url_data = first_image['image_url']['url']
                        
                        if "base64," in url_data:
                            b64_data = url_data.split("base64,")[1]
                        else:
                            b64_data = url_data
                            
                        img_data = base64.b64decode(b64_data)

                if img_data:
                    with open(filepath, 'wb') as f:
                        f.write(img_data)
                    self.memory[text_hash] = filepath
                    self._save_map()
                    return filepath
                else:
                    print(f"[!] Parsing Failed. Could not find ['image_url']['url'] in: {message.images}")
                    return None

            # STANDARD (DALL-E, etc via image_generation)
            else:
                print("   > Using Standard 'image_generation' route...")
                response = image_generation(
                    model=self.model,
                    prompt=visual_prompt
                )
                
                image_url = response.data[0].url
                img_data = requests.get(image_url).content
                with open(filepath, 'wb') as f:
                    f.write(img_data)
                
                self.memory[text_hash] = filepath
                self._save_map()
                return filepath
            
        except Exception as e:
            print(f"[!] Image Gen Error: {e}")
            return None