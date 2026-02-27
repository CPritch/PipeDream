import uuid
import json
import os

class Navigator:
    def __init__(self, session_id="default", cache_dir="cache"):
        self.graph_file = os.path.join(cache_dir, f"world_graph_{session_id}.json")
        self.nodes = {}  # { uuid: { image_path: str, edges: { cmd: uuid } } }
        self.current_node_id = None
        self.last_command = None
        
        # Opposites map for auto-backtracking
        self.opposites = {
            'n': 's', 's': 'n', 'north': 'south', 'south': 'north',
            'e': 'w', 'w': 'e', 'east': 'west', 'west': 'east',
            'u': 'd', 'd': 'u', 'up': 'down', 'down': 'up',
            'in': 'out', 'out': 'in', 'enter': 'exit', 'exit': 'enter'
        }
        
        self._load_graph()
        
        if not self.current_node_id:
            self._create_genesis_node()

    def reset(self):
        self.nodes = {}
        self.current_node_id = None
        self._create_genesis_node()
        print("[Navigator] Graph reset.")

    def _create_genesis_node(self):
        """Creates the starting point of the world."""
        node_id = str(uuid.uuid4())
        self.nodes[node_id] = {
            "image_path": None,
            "edges": {}
        }
        self.current_node_id = node_id
        self.save_graph()

    def set_last_command(self, cmd):
        """Call this right before sending input to the game process."""
        self.last_command = cmd.strip().lower()

    def process_move(self, visual_prompt, generator_func, raw_text, pre_cached_image=None):
        """
        Decides whether to move, generate, or stay put.
        Returns: The image path to display.
        """
        
        # VALIDATION: If no visual change AND no cached image, we didn't move.
        if not visual_prompt and not pre_cached_image:
            return self.nodes[self.current_node_id].get("image_path")

        current_node = self.nodes[self.current_node_id]
        cmd = self.last_command

        # TRAVERSAL: Do we already know where this command goes?
        if cmd in current_node["edges"]:
            target_id = current_node["edges"][cmd]
            print(f"[Navigator] Known path! Moving to {target_id}")
            self.current_node_id = target_id
            return self.nodes[target_id].get("image_path")

        # EXPLORATION: This is a new path.
        if pre_cached_image:
            print(f"[Navigator] Linking new path to known cache.")
            image_path = pre_cached_image
        else:
            print(f"[Navigator] New territory! Creating node.")
            current_image = None
            if self.current_node_id and self.current_node_id in self.nodes:
                current_image = self.nodes[self.current_node_id].get("image_path")
                
            image_path = generator_func(raw_text, visual_prompt, reference_image=current_image)
        
        if not image_path:
            return None

        new_node_id = str(uuid.uuid4())
        self.nodes[new_node_id] = {
            "image_path": image_path,
            "edges": {}
        }

        # Link Forward: Current -> New
        self.nodes[self.current_node_id]["edges"][cmd] = new_node_id
        
        # Link Backward: New -> Current (Auto-backtrack)
        if cmd in self.opposites:
            opp_cmd = self.opposites[cmd]
            self.nodes[new_node_id]["edges"][opp_cmd] = self.current_node_id

        # Update State
        self.current_node_id = new_node_id
        self.save_graph()
        
        return image_path

    def _load_graph(self):
        if os.path.exists(self.graph_file):
            try:
                with open(self.graph_file, 'r') as f:
                    data = json.load(f)
                    self.nodes = data.get("nodes", {})
                    self.current_node_id = None
            except Exception as e:
                print(f"[!] Error loading graph: {e}")

    def save_graph(self):
        data = {
            "nodes": self.nodes,
            "current_node_id": self.current_node_id
        }
        with open(self.graph_file, 'w') as f:
            json.dump(data, f, indent=2)