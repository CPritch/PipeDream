import os
import json
import hashlib
from litellm import image_generation

class SmartCache:
    def __init__(self, cache_dir="cache"):
        self.cache_dir = cache_dir
        self.images_dir = os.path.join(cache_dir, "images")
        self.map_file = os.path.join(cache_dir, "mapping.json")
        self.memory = {}
        
        # Ensure directories exist
        os.makedirs(self.images_dir, exist_ok=True)
        self._load_map()

    def _load_map(self):
        if os.path.exists(self.map_file):
            with open(self.map_file, 'r') as f:
                self.memory = json.load(f)

    def _save_map(self):
        with open(self.map_file, 'w') as f:
            json.dump(self.memory, f, indent=2)

    def get_image(self, prompt):
        """
        Returns path to image. 
        If cached -> returns local path.
        If new -> Generates (mock for now), saves, returns path.
        """
        # Create a stable hash of the prompt
        prompt_hash = hashlib.md5(prompt.encode('utf-8')).hexdigest()

        if prompt_hash in self.memory:
            print(f"[*] Cache Hit: {prompt[:30]}...")
            return self.memory[prompt_hash]

        print(f"[*] Cache Miss. Generating for: {prompt[:30]}...")
        
        # --- IMAGE GENERATION LOGIC ---
        image_path = self._generate_and_save(prompt, prompt_hash)
        # ------------------------------

        self.memory[prompt_hash] = image_path
        self._save_map()
        return image_path

    def _generate_and_save(self, prompt, file_hash):
        filename = f"{file_hash}.png"
        filepath = os.path.join(self.images_dir, filename)

        try:
            # TODO: UNCOMMENT THIS BLOCK FOR REAL GENERATION
            # response = image_generation(
            #     model=os.getenv("IMAGE_MODEL", "dall-e-3"),
            #     prompt=prompt
            # )
            # image_url = response.data[0].url
            # For now, create a dummy file to prove the loop works
            with open(filepath, 'w') as f:
                f.write("Placeholder Image Data")
                
            # In real implementation: Download image_url content to filepath here
            
            return filepath
        except Exception as e:
            print(f"[!] Gen Error: {e}")
            return None