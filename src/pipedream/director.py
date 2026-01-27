import os
import warnings
from litellm import completion
from dotenv import load_dotenv

load_dotenv()

warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

class Director:
    def __init__(self, style_prompt=None):
        self.model = os.getenv("LLM_MODEL", "gemini/gemini-2.5-flash")
        self.style = style_prompt or "Oil painting, dark fantasy, atmospheric"
        
    def describe_scene(self, raw_text):
        """
        Converts raw game output into a visual art prompt.
        Returns None if the text is not a visual scene.
        """
        system_prompt = (
            "You are a background process for a text adventure game. "
            "Your job is to read the game text and output a VISUAL PROMPT for an image generator. "
            "Rules:\n"
            "1. If the text describes a location, atmosphere, or event, output a concise (under 40 words) visual description. "
            f"Style: {self.style}.\n"
            "2. If the text is an error message (e.g., 'I don't know that word'), a menu, a save confirmation, or just a short dialogue with no visual changes, output exactly: NO_SCENE\n"
            "3. Do NOT chat. Do NOT ask for more input. Only output the description or NO_SCENE."
        )

        try:
            response = completion(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"GAME TEXT: {raw_text}"}
                ]
            )
            clean_prompt = response.choices[0].message.content.strip()
            
            if "NO_SCENE" in clean_prompt or len(clean_prompt) < 5:
                return None
                
            return clean_prompt
            
        except Exception as e:
            print(f"[!] Director Error: {e}")
            return None