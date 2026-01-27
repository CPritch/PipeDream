import sys
import argparse
import pexpect
from pipedream.director import Director
from pipedream.cache import SmartCache

if sys.platform == 'win32':
    from pexpect.popen_spawn import PopenSpawn

class PipeDream:
    def __init__(self, command, style=None, clear_cache=False):
        self.command = command
        self.process = None
        self.prompt_pattern = r'[>:]\s*$' 

        self.director = Director(style_prompt=style)
        self.cache = SmartCache(style_prompt=style)

        if clear_cache:
            self.cache.clear()

        self.custom_print = print
        self.custom_input = input
        self.custom_image = self.default_image_handler

    def default_image_handler(self, path):
        print(f"[*] IMAGE GENERATED: {path}")

    def start(self):
        self.custom_print(f"[*] Launching: {self.command}")
        
        if sys.platform == 'win32':
            self.process = PopenSpawn(self.command, encoding='utf-8')
        else:
            self.process = pexpect.spawn(self.command, encoding='utf-8')
            
        try:
            self.read_until_prompt()
            
            while True:
                user_input = self.custom_input("USER > ") 
                
                self.process.sendline(user_input)
                
                if user_input.strip().lower() in ['quit', 'exit', 'q']:
                    break
                
                self.read_until_prompt()
                
        except pexpect.EOF:
            self.custom_print("\n[*] Game process ended.")
        except KeyboardInterrupt:
            pass 
        finally:
            self.cleanup()

    def cleanup(self):
        self.custom_print("\n[*] Stopping PipeDream...")
        if self.process:
            try:
                if sys.platform == 'win32':
                    self.process.proc.terminate()
                else:
                    self.process.terminate()
            except Exception:
                pass
        self.custom_print("[*] Cleanup complete.")

    def read_until_prompt(self):
        try:
            self.process.expect(self.prompt_pattern, timeout=5)
            raw_output = self.process.before.strip()
            clean_text = self.clean_output(raw_output)
            
            self.custom_print(clean_text)
            self.trigger_pipeline(clean_text)
            
        except pexpect.TIMEOUT:
            pass

    def clean_output(self, text):
        lines = text.splitlines()
        # Clean up echo if present
        if lines and self.process.before and lines[0] in self.process.before: 
            return "\n".join(lines[1:])
        return text

    def trigger_pipeline(self, text):
        if len(text.strip()) < 25: 
            return

        print(f"\n[PIPEDREAM] Analyzing Scene...")
        
        cached_path = self.cache.lookup(text)
        if cached_path:
            print(f"   > Cache Hit! Skipping LLM.")
            self.custom_image(cached_path)
            return
        
        visual_prompt = self.director.describe_scene(text)
        if not visual_prompt:
            print("   > No visual changes detected.")
            return
        
        print(f"   > Prompt: {visual_prompt}")

        image_path = self.cache.generate(text, visual_prompt)

        if image_path:
            print(f"   > Image ready.")
            self.custom_image(image_path)

def main():
    parser = argparse.ArgumentParser(description="PipeDream: AI Visualizer for Interactive Fiction")
    
    parser.add_argument('--art-style', dest='style', type=str, default=None, help="Visual style prompt")
    parser.add_argument('--clear-cache', action='store_true', help="Wipe cache before starting")
    parser.add_argument('game_command', nargs=argparse.REMAINDER, help="The command to run the game")
    
    args = parser.parse_args()
    if not args.game_command:
        parser.print_help()
        sys.exit(1)

    full_command = " ".join(args.game_command)
    engine = PipeDream(full_command, style=args.style, clear_cache=args.clear_cache)
    engine.start()

if __name__ == "__main__":
    main()