import sys
import pexpect
if sys.platform == 'win32':
    from pexpect.popen_spawn import PopenSpawn

class PipeDream:
    def __init__(self, command):
        self.command = command
        self.process = None
        self.prompt_pattern = r'[>:]\s*$' 

    def start(self):
        print(f"[*] Launching: {self.command}")
        
        if sys.platform == 'win32':
            self.process = PopenSpawn(self.command, encoding='utf-8')
        else:
            self.process = pexpect.spawn(self.command, encoding='utf-8')
            
        try:
            self.read_until_prompt()
            
            while True:
                user_input = input("USER > ")
                self.process.sendline(user_input)
                
                if user_input.strip().lower() in ['quit', 'exit', 'q']:
                    break
                
                self.read_until_prompt()
                
        except pexpect.EOF:
            print("\n[*] Game process ended.")
        except KeyboardInterrupt:
            print("\n[*] Stopping PipeDream.")
            # PopenSpawn doesn't have close(), uses wait() usually, 
            if self.process:
                self.process.terminate()

    def read_until_prompt(self):
        """
        Reads output from the game until the prompt pattern matches.
        Extracts the 'story' text for the image pipeline.
        """
        try:
            # expecting the regex pattern (the game cursor)
            self.process.expect(self.prompt_pattern, timeout=5)
            
            # process.before contains everything BEFORE the match (the story text)
            raw_output = self.process.before.strip()

            clean_text = self.clean_output(raw_output)
            print(f"\n--- GAME OUTPUT ---\n{clean_text}\n-------------------")
            
            # TODO: THIS IS WHERE WE WILL HOOK IN THE LLM + IMAGE GEN
            self.trigger_pipeline(clean_text)
            
        except pexpect.TIMEOUT:
            print("[!] Timeout waiting for game response.")

    def clean_output(self, text):
        """Removes the echoed command from the output if present."""
        lines = text.splitlines()
        if lines and self.process.before in lines[0]: 
            # Sometimes pexpect captures the input echo
            return "\n".join(lines[1:])
        return text

    def trigger_pipeline(self, text):
        """Placeholder for the 'Director' logic"""
        #captured the specific chunk we need
        print(f"\n[PIPEDREAM] Captured for Image Gen: '{text[:50]}...'")

if __name__ == "__main__":
    engine = PipeDream("python -u games/mock_game.py")
    engine.start()