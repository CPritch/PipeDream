import sys
import time

LOCATIONS = {
    "start": {
        "desc": "You are standing in an open field west of a white house. A dark forest looms to the north.",
        "exits": {"north": "forest", "enter": "house", "in": "house", "east": "house"}
    },
    "forest": {
        "desc": "You are deep in a dark, ancient forest. Twisted trees block out the sky.",
        "exits": {"south": "start"}
    },
    "house": {
        "desc": "You are inside the white house. It is dusty, with a single lantern glowing on a table.",
        "exits": {"out": "start", "exit": "start", "west": "start"}
    }
}

def main():
    current_room = "start"
    
    print("\n" + "="*50)
    print(" WELCOME TO PIPEDREAM (DEMO CARTRIDGE)")
    print(" Try moving around to see the AI generate scenes.")
    print(" Commands: north, south, enter, exit, quit")
    print("="*50 + "\n")
    
    print(LOCATIONS[current_room]["desc"])

    while True:
        try:
            command = input("\n> ").lower().strip()
            
            if command in ["q", "quit", "exit game"]:
                print("Goodbye.")
                break
            
            if command in ["look", "l"]:
                print(LOCATIONS[current_room]["desc"])
                continue

            # Movement Logic
            room_data = LOCATIONS[current_room]
            if command in room_data["exits"]:
                new_room = room_data["exits"][command]
                current_room = new_room
                print(LOCATIONS[current_room]["desc"])
            else:
                print(f"You can't go '{command}' from here.")
                
        except KeyboardInterrupt:
            break

if __name__ == "__main__":
    main()