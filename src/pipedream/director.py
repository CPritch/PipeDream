import json
import requests

# llama-server constrains the reply to this schema, so there's no free text to parse
SCENE_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": ["meta", "action_failed", "no_change", "scene_changed"]},
        "location": {"type": "string"},
        "prompt": {"type": "string"},
    },
    "required": ["category", "location", "prompt"],
}

class Director:
    def __init__(self, engine, llm_url, style_prompt=None, img2img=False):
        self.engine = engine
        self.llm_url = llm_url
        self.style = style_prompt or "Oil painting, dark fantasy, atmospheric"
        self.img2img = img2img

    def describe_scene(self, raw_text, previous_text=""):
        if self.img2img:
            spatial_rule = "Describe the new scene as a visual progression from the previous one, keeping the environment consistent."
        else:
            spatial_rule = "Describe the scene from scratch."

        system_prompt = (
            "You are the visual director for a text adventure game. "
            "Compare the CURRENT TEXT with the PREVIOUS TEXT and classify the outcome.\n\n"
            "CATEGORIES:\n"
            "- meta: game titles, copyright text, instructions, questions like 'Would you like instructions?', or other out-of-game text.\n"
            "- action_failed: the player tried to move or act but was blocked.\n"
            "- no_change: inventory checks, system messages, or a functionally identical room description.\n"
            "- scene_changed: the player entered a new area, or the room's visual state changed.\n\n"
            "location: a short canonical name for where the player is.\n"
            "prompt: empty unless scene_changed. Otherwise under 60 words describing only the environment as the player sees it: "
            "no people and no 'you'. Include visible exits and paths with their compass directions. "
            f"{spatial_rule}"
        )
        user_content = f"PREVIOUS TEXT: {previous_text}\n\nCURRENT TEXT: {raw_text}"

        try:
            response = requests.post(f"{self.llm_url}/v1/chat/completions", timeout=120, json={
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                "temperature": 0.2,
                "max_tokens": 300,
                "response_format": {"type": "json_schema", "json_schema": {"name": "scene", "schema": SCENE_SCHEMA}},
            })
            response.raise_for_status()
            scene = json.loads(response.json()["choices"][0]["message"]["content"])
        except (requests.RequestException, KeyError, ValueError) as e:
            print(f"[!] Director Error: {e}")
            return None

        if scene["category"] != "scene_changed" or len(scene["prompt"].strip()) < 5:
            return None

        # Style first: the image model weights the start of the prompt most
        return f"{self.style}. {scene['prompt'].strip()}"
