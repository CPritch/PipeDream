import os
import warnings
from litellm import completion, completion_cost
from dotenv import load_dotenv

load_dotenv()

warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

class Director:
    def __init__(self, engine, style_prompt=None, img2img=False):
        self.engine = engine
        self.model = os.getenv("LLM_MODEL", "gemini/gemini-2.5-flash")
        self.style = style_prompt or "Oil painting, dark fantasy, atmospheric"
        self.img2img = img2img

    def describe_scene(self, raw_text, previous_text=""):
        # Determine the spatial rule based on the mode
        if self.img2img:
            spatial_rule = "CRITICAL SPATIAL RULE: You are guiding an image-to-image transformation. Describe the new scene as a visual progression from the previous scene. Emphasize what changes visually while maintaining core environmental consistency (e.g., 'Moving forward from the forest, the trees part to reveal a stone well house...'). Explicitly include all visible exits and paths mapped to their cardinal directions."
        else:
            spatial_rule = "CRITICAL SPATIAL RULE: You MUST explicitly include all visible exits and paths mapped to their cardinal directions in the prompt (e.g., 'A dark tunnel leads East, a heavy wooden door sits on the North wall')."

        system_prompt = (
            "You are a visual director for a text adventure game. "
            "Analyze the CURRENT TEXT compared to the PREVIOUS TEXT and determine the visual outcome.\n\n"
            "CATEGORIES:\n"
            "- META/TITLE: Game titles, copyright text, prompts like 'Would you like instructions?', or out-of-character text.\n"
            "- ACTION_FAILED: The player tried to move or act, but was blocked.\n"
            "- NO_CHANGE: System messages, inventory checks, or functionally identical room descriptions.\n"
            "- SCENE_CHANGED: The player successfully entered a new area or the room's visual state changed.\n\n"
            "RULES:\n"
            "1. If the category is META/TITLE, ACTION_FAILED, or NO_CHANGE, output exactly: NO_SCENE\n"
            f"2. If the category is SCENE_CHANGED, output a concise (under 40 words) visual description. {spatial_rule} "
            f"Style: {self.style}.\n"
            "3. Do NOT chat. Only output NO_SCENE or the prompt description."
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