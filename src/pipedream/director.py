import os
import warnings
from litellm import completion, completion_cost
from dotenv import load_dotenv

load_dotenv()

warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

class Director:
    def __init__(self, engine, style_prompt=None):
        self.engine = engine
        self.model = os.getenv("LLM_MODEL", "gemini/gemini-2.5-flash")
        self.style = style_prompt or "Oil painting, dark fantasy, atmospheric"

    def describe_scene(self, raw_text, previous_text=""):
        """
        Converts raw game output into a visual art prompt.
        Returns None if the text is not a visual scene.
        """
        system_prompt = (
            "You are a background process for a text adventure game. "
            "Your job is to read the game text and output a VISUAL PROMPT for an image generator. "
            "Rules:\n"
            "1. COMPARE the current text with the previous text.\n"
            "2. If the current text is an error (e.g. 'You can't go that way'), a system response ('OK'), or functionally identical to the previous text (re-printing the room description), output exactly: NO_SCENE\n"
            "3. If the scene has CHANGED (new location, new item, different time of day), output a concise (under 40 words) visual description. "
            f"Style: {self.style}.\n"
            "4. Do NOT chat. Only output the description or NO_SCENE."
        )

        user_content = f"PREVIOUS TEXT: {previous_text}\n\nCURRENT TEXT: {raw_text}"

        try:
            response = completion(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content}                
                ]
            )
            try:
                cost = completion_cost(completion_response=response)
                # Only log if > 0 to reduce spam
                if cost > 0:
                    print(f"[$$$] Director Cost: ${cost:.6f}")
                    self.engine.report_cost(cost)
            except:
                pass
            
            clean_prompt = response.choices[0].message.content.strip()
            
            if "NO_SCENE" in clean_prompt or len(clean_prompt) < 5:
                return None
                
            return clean_prompt
            
        except Exception as e:
            print(f"[!] Director Error: {e}")
            return None