import pygame
pygame.init()
pygame.display.set_mode((800, 600))  # Needed for surfaces/fonts

from app import DialogueSystem

def main():
    # Create DialogueSystem instance
    dialogue = DialogueSystem()
    print("Dialogue system created.")

    # Print some attributes to check it's loaded
    print(f"Dialogue active? {dialogue.active}")
    print(f"Initial NPC message: {dialogue.npc_message}")
    print(f"Speech mode enabled? {dialogue.speech_mode}")

    # Optional: start a conversation (simulate with CEO role)
    dialogue.start_conversation("CEO", player_pos=[0, 0.5, 0])
    print("Started conversation with CEO.")
    print(f"NPC message: {dialogue.npc_message}")

if __name__ == "__main__":
    main()
    pygame.quit()
