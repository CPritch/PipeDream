import time

def main():
    print("WELCOME TO ZORK (Mock Version)")
    print("You are standing in an open field west of a white house.")
    
    while True:
        command = input("\n> ") 
        
        if command.lower() in ["q", "quit"]:
            print("Goodbye.")
            break
        elif "look" in command.lower():
            print("It is a nice house. The windows are boarded up.")
        else:
            print(f"I don't know how to '{command}'.")

if __name__ == "__main__":
    main()