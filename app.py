"""
Some of the code and design patterns in this file are adapted from the official OpenAI Python SDK
realtime examples: https://github.com/openai/openai-python/tree/main/examples/realtime

Significant modifications were made to fit this project's requirements (3D game loop, Pygame/OpenGL integration,
and in-game real-time speech/NPC logic). The OpenAI examples provided guidance on async handling,
audio streaming, and realtime session management.

See OpenAI's repo for their original implementations and documentation.
"""

class DialogueSystem:
    def init(self):
        self.active = False
        self.user_input = ""
        pygame.font.init()
        self.font = pygame.font.Font(None, 24)
        self.npc_message = ""
        self.input_active = False
        self.last_npc_text = ""
        self.last_input_text = ""
        self.conversation_history = []

        self.ui_surface = pygame.Surface((WINDOW_WIDTH, WINDOW_HEIGHT), pygame.SRCALPHA).convert_alpha()
        self.ui_texture = glGenTextures(1)
        self.current_npc = None
        self.initial_player_pos = None

        self.api_key = os.environ.get("OPENAI_API_KEY", None)
        if not self.api_key:
            print("[DialogueSystem] WARNING: No OPENAI_API_KEY found in environment!")
            sys.exit(1)

        self.realtime_conn = None
        self.speech_mode = False
        self.last_audio_item_id = None
        self.acc_items = {}
        self.audio_player = AudioPlayerAsync()
        self.sent_audio_once = False
        self.client = AsyncOpenAI(api_key=self.api_key)
        self.loop = asyncio.new_event_loop()
        self.loop_thread = threading.Thread(target=self.run_asyncio_loop, daemon=True)
        self.loop_thread.start()
        self.speech_active = False  # Track if user is speaking

    def run_asyncio_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    async def initialize_realtime(self):
        print("[DialogueSystem] Attempting Realtime API connection...")
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                npc_name = "Michael Chen" if self.current_npc == "CEO" else "Sarah Chen"
                npc_role = "CEO" if self.current_npc == "CEO" else "HR Director"
                npc_tone = "confident" if self.current_npc == "CEO" else "warm"
                npc_voice = "ballad" if self.current_npc == "CEO" else "alloy"
                print(f"[DEBUG] Setting NPC as {npc_name} ({npc_role}) with tone {npc_tone} and voice {npc_voice}")
                instructions = (
                    f"You are {npc_name}, {npc_role} at Venture Builder AI. "
                    f"In your first response, say exactly 'Hello there, I am {npc_name}, {npc_role} at Venture Builder AI.' "
                    f"Then ask how you can assist the user. For subsequent responses, do not reintroduce yourself. "
                    f"Address the user as 'you' or 'user', and respond naturally to their queries in a {npc_tone} tone."
                )
                print(f"[DEBUG] Instructions sent: {instructions}")

                async with self.client.beta.realtime.connect(model="gpt-4o-realtime-preview-2024-10-01") as conn:
                    self.realtime_conn = conn
                    await conn.session.update(session={
                        "turn_detection": {
                            "type": "server_vad",
                            "threshold": 0.7,  # Lowered for sensitivity
                            "prefix_padding_ms": 600,
                            "silence_duration_ms": 1200  # Shorter for quicker response
                        },
                        "modalities": ["text", "audio"],
                        "voice": npc_voice,
                        "instructions": instructions
                    })
                    print("[Realtime] Session configured.")

                    async for event in conn:
                        await self.handle_realtime_event(event)
            except Exception as e:
                print(f"[DialogueSystem] Realtime connection attempt {attempt+1} failed: {e}")
                self.realtime_conn = None
                if attempt < max_attempts - 1:
                    await asyncio.sleep(1)
                else:
                    print("[DialogueSystem] Max connection attempts reached. Speech mode disabled.")
                    self.speech_mode = False
                    break


    async def handle_realtime_event(self, event):
        try:
            if event.type == "session.created":
                print(f"[Realtime] Session created: {event.session.id}")
            elif event.type == "session.updated":
                print("[Realtime] Session updated.")
            elif event.type == "response.audio.delta":
                print("[DEBUG Realtime Event] Received audio delta.")
                if event.item_id != self.last_audio_item_id:
                    self.audio_player.reset_frame_count()
                    self.last_audio_item_id = event.item_id
                bytes_data = base64.b64decode(event.delta)
                self.audio_player.add_data(bytes_data)
            elif event.type == "response.audio_transcript.delta":
                print("[DEBUG Realtime Event] Received transcript delta.")
                so_far = self.acc_items.get(event.item_id, "")
                so_far += event.delta
                self.acc_items[event.item_id] = so_far
                print(f"[Realtime] Assistant partial transcript: {so_far}")
            elif event.type == "response.audio_transcript.done":
                self.npc_message = self.acc_items.get(event.item_id, "")
                print(f"[Realtime] Full transcript: {self.npc_message}")
                self.conversation_history.append({"role": "assistant", "content": self.npc_message})
            elif event.type == "input_audio_buffer.speech_started":
                print("[Realtime] User speech detected.")
                self.speech_active = True
                self.audio_player.stop()
                self.npc_message = ""
                self.acc_items.clear()
                self.conversation_history.append({"role": "user", "content": "[Speech input started]"})
            elif event.type == "input_audio_buffer.speech_done":
                print("[Realtime] User speech ended.")
                self.speech_active = False
                await self.realtime_conn.input_audio_buffer.commit()
                await self.realtime_conn.response.create(response={"modalities": ["text", "audio"]})
            elif event.type == "input_audio_buffer.commit.done":
                print("[Realtime] Audio buffer committed.")
            elif event.type == "response.done":
                print("[Realtime] Response completed.")
            elif event.type == "error":
                print(f"[Realtime] Error: {event.error}")
                self.speech_mode = False
                self.realtime_conn = None
        except Exception as e:
            print(f"[DialogueSystem] Error handling realtime event: {e}")

    async def record_mic_audio(self):
        print("[DialogueSystem] Starting microphone audio recording...")
        read_size = int(SAMPLE_RATE * 0.02)  # Match audio_util.py's 20ms chunks
        try:
            stream = sd.InputStream(channels=CHANNELS, samplerate=SAMPLE_RATE, dtype="int16")
            stream.start()
            print("[DialogueSystem] Audio input stream started with 20ms chunks.")
            while True:
                if not self.speech_mode or not self.realtime_conn:
                    await asyncio.sleep(0.01)
                    continue
                if stream.read_available < read_size:
                    await asyncio.sleep(0.01)
                    continue
                data, overflowed = stream.read(read_size)
                if overflowed:
                    print("[DialogueSystem] Warning: Audio buffer overflowed.")
                if data.size > 0:
                    b64_audio = base64.b64encode(data.tobytes()).decode("utf-8")
                    print("[DEBUG] Sending audio chunk to Realtime API.")
                    await self.realtime_conn.input_audio_buffer.append(audio=b64_audio)
                    self.sent_audio_once = True
                await asyncio.sleep(0)
        except Exception as e:
            print(f"[DialogueSystem] Microphone error: {e}")
            self.speech_mode = False
        finally:
            if 'stream' in locals():
                stream.stop()
                stream.close()
                print("[DialogueSystem] Audio input stream closed.")



    async def send_message(self, text_input):
        if not self.conversation_history:
            print("[DialogueSystem] No conversation history to send.")
            return

        try:
            if self.speech_mode and self.realtime_conn:
                await self.realtime_conn.conversation.item.create(
                    item={
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": text_input}]
                    }
                )
                await self.realtime_conn.response.create(
                    response={"modalities": ["text", "audio"]}
                )
            else:
                response = await self.client.chat.completions.create(
                    model="gpt-4-0125-preview",
                    messages=self.conversation_history,
                    temperature=0.85,
                    max_tokens=150,
                )
                ai_message = response.choices[0].message.content
                self.conversation_history.append({
                    "role": "assistant",
                    "content": ai_message
                })
                self.npc_message = ai_message
                print(f"[DialogueSystem] NPC says: {self.npc_message}")
        except Exception as e:
            self.npc_message = "I apologize, but I'm having trouble connecting to our systems right now."
            print(f"[DialogueSystem] Error: {e}")

    


    def start_conversation(self, npc_role="HR", player_pos=None):
        self.active = True
        self.input_active = True
        self.current_npc = npc_role
        self.initial_player_pos = [player_pos[0], player_pos[1], player_pos[2]] if player_pos else [0, 0.5, 0]
        print(f"[DialogueSystem] Dialogue started with {npc_role}")

        if self.audio_player:
            print("[DialogueSystem] Stopping existing audio playback...")
            self.audio_player.stop()
            self.audio_player.reset_frame_count()
            self.last_audio_item_id = None
            self.acc_items.clear()

        if self.realtime_conn:
            print("[DialogueSystem] Closing existing Realtime API connection...")
            self.realtime_conn.close()
            self.realtime_conn = None

        asyncio.run_coroutine_threadsafe(self.initialize_realtime(), self.loop)
        asyncio.run_coroutine_threadsafe(self.record_mic_audio(), self.loop)

        base_prompt = """Interaction Framework:
            - Maintain consistent personality throughout conversation
            - Remember previous context within the dialogue
            - Use natural speech patterns with occasional filler words
            - Show emotional intelligence in responses
            - Keep responses concise but meaningful (2-3 sentences)
            - React appropriately to both positive and negative interactions
            """

        if npc_role == "HR":
            system_prompt = f"""{base_prompt}
                You are Sarah Chen, HR Director at Venture Builder AI. Core traits:
                - Warm and approachable
                - Focus on employee well-being and company culture
            """
        else:
            system_prompt = f"""{base_prompt}
                You are Michael Chen, CEO of Venture Builder AI. Core traits:
                - Confident and visionary
                - Focus on innovation and leadership
            """

        npc_name = "Michael Chen" if npc_role == "CEO" else "Sarah Chen"
        npc_title = "CEO" if npc_role == "CEO" else "HR Director"
        initial_message = {
            "HR": f"Hello there, I am Sarah Chen, HR Director at Venture Builder AI. How can I assist you today?",
            "CEO": f"Hello there, I am Michael Chen, CEO at Venture Builder AI. What can I do for you today?"
        }

        self.npc_message = initial_message[npc_role]
        self.conversation_history = [{
            "role": "system",
            "content": system_prompt
        }]

        if self.realtime_conn:
            asyncio.run_coroutine_threadsafe(
                self.realtime_conn.response.create(
                    response={"modalities": ["text", "audio"], "instructions": self.npc_message}
                ),
                self.loop
            )
            print(f"[DialogueSystem] Sent initial greeting to Realtime API: {self.npc_message}")

    def handle_input(self, event):
        if not self.active or not self.input_active:
            return

        if event.type == pygame.KEYDOWN:
            keys = pygame.key.get_pressed()
            if keys[pygame.K_LSHIFT] and event.key == pygame.K_q:
                self.active = False
                self.input_active = False
                self.speech_mode = False
                if self.realtime_conn:
                    self.realtime_conn.close()
                    self.realtime_conn = None
                print("[DialogueSystem] Chat ended")
                return {"command": "move_player_back", "position": self.initial_player_pos}

            if event.key == pygame.K_F5:
                self.speech_mode = not self.speech_mode
                if self.speech_mode:
                    self.sent_audio_once = False
                    print("[DialogueSystem] Speech mode enabled: Real-time conversation started.")
                    if not self.realtime_conn:
                        print("[DialogueSystem] Realtime connection not active. Reinitializing...")
                        asyncio.run_coroutine_threadsafe(self.initialize_realtime(), self.loop)
                        asyncio.run_coroutine_threadsafe(self.record_mic_audio(), self.loop)
                else:
                    print("[DialogueSystem] Speech mode disabled.")
                    self.audio_player.stop()
                    self.speech_active = False

            if event.key == pygame.K_RETURN and self.user_input.strip():
                print(f"[DialogueSystem] User typed: {self.user_input}")
                self.conversation_history.append({"role": "user", "content": self.user_input.strip()})
                asyncio.run_coroutine_threadsafe(self.send_message(self.user_input.strip()), self.loop)
                self.user_input = ""
            elif event.key == pygame.K_BACKSPACE:
                self.user_input = self.user_input[:-1]
            elif event.unicode.isprintable():
                self.user_input += event.unicode

    def render(self):
        if not self.active:
            return

        self.ui_surface.fill((0, 0, 0, 0))
        if self.active:
            box_height = 200
            box_y = WINDOW_HEIGHT - box_height - 20
            box_color = (0, 0, 0, 230)
            pygame.draw.rect(self.ui_surface, box_color, (20, box_y, WINDOW_WIDTH - 40, box_height))
            pygame.draw.rect(self.ui_surface, (255, 255, 255, 255), (20, box_y, WINDOW_WIDTH - 40, box_height), 2)

            quit_text_surface = self.font.render("Press Shift+Q to exit, Press F5 to toggle speech mode", True, (255, 255, 255))
            self.ui_surface.blit(quit_text_surface, (40, box_y + 10))

            if self.npc_message:
                self.render_text(self.ui_surface, self.npc_message, 40, box_y + 40)

            if self.input_active:
                input_prompt = "> " + self.user_input + "_"
                input_surface = self.font.render(input_prompt, True, (255, 255, 255))
                self.ui_surface.blit(input_surface, (40, box_y + box_height - 40))

        texture_data = pygame.image.tostring(self.ui_surface, "RGBA", True)
        glPushAttrib(GL_ALL_ATTRIB_BITS)
        glMatrixMode(GL_PROJECTION)
        glPushMatrix()
        glLoadIdentity()
        glOrtho(0, WINDOW_WIDTH, 0, WINDOW_HEIGHT, -1, 1)
        glMatrixMode(GL_MODELVIEW)
        glPushMatrix()
        glLoadIdentity()

        glDisable(GL_DEPTH_TEST)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glEnable(GL_TEXTURE_2D)

        glBindTexture(GL_TEXTURE_2D, self.ui_texture)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, WINDOW_WIDTH, WINDOW_HEIGHT, 0, GL_RGBA, GL_UNSIGNED_BYTE, texture_data)

        glBegin(GL_QUADS)
        glTexCoord2f(0, 0); glVertex2f(0, 0)
        glTexCoord2f(1, 0); glVertex2f(WINDOW_WIDTH, 0)
        glTexCoord2f(1, 1); glVertex2f(WINDOW_WIDTH, WINDOW_HEIGHT)
        glTexCoord2f(0, 1); glVertex2f(0, WINDOW_HEIGHT)
        glEnd()

        glMatrixMode(GL_PROJECTION)
        glPopMatrix()
        glMatrixMode(GL_MODELVIEW)
        glPopMatrix()
        glPopAttrib()

    def render_text(self, surface, text, x, y):
        max_width = WINDOW_WIDTH - 40
        line_height = 25

        words = text.split()
        lines = []
        current_line = []
        current_width = 0

        text_color = (255, 255, 255)

        for word in words:
            word_surface = self.font.render(word + ' ', True, text_color)
            word_width = word_surface.get_width()
            if current_width + word_width <= max_width:
                current_line.append(word)
                current_width += word_width
            else:
                lines.append(' '.join(current_line))
                current_line = [word]
                current_width = word_width

        if current_line:
            lines.append(' '.join(current_line))

        for i, line in enumerate(lines):
            text_surface = self.font.render(line, True, text_color)
            surface.blit(text_surface, (x, y + i * line_height))

        return len(lines) * line_height


import os
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = "hide"

import pygame
from pygame.locals import *
from OpenGL.GL import *
from OpenGL.GLU import *
import math
import numpy as np
import sys
import textwrap
from openai import OpenAI
from dotenv import load_dotenv
import time
import pygame
import sounddevice as sd
import asyncio
import threading
import base64
from audio_util import CHANNELS, SAMPLE_RATE, AudioPlayerAsync
from openai import AsyncOpenAI
from openai.resources.beta.realtime.realtime import AsyncRealtimeConnection

# Load environment variables
load_dotenv()
# Ensure OpenAI API Key is loaded
api_key = os.getenv('OPENAI_API_KEY')
if not api_key:
    print("[OpenAI] API key not found. Please set OPENAI_API_KEY in your .env file.")
    sys.exit(1)
client = OpenAI(api_key=api_key)
print("[OpenAI] API key loaded successfully.")

# Initialize Pygame with macOS specific settings
pygame.init()
display = (800, 600)
pygame.display.set_mode(display, DOUBLEBUF|OPENGL)
screen = pygame.display.get_surface()

# Set up the camera and perspective
glEnable(GL_DEPTH_TEST)
glMatrixMode(GL_PROJECTION)
glLoadIdentity()
gluPerspective(45, (display[0]/display[1]), 0.1, 50.0)
glMatrixMode(GL_MODELVIEW)

# Set up basic lighting
glEnable(GL_LIGHTING)
glEnable(GL_LIGHT0)
glLightfv(GL_LIGHT0, GL_POSITION, [0, 5, 5, 1])
glLightfv(GL_LIGHT0, GL_AMBIENT, [0.5, 0.5, 0.5, 1])
glLightfv(GL_LIGHT0, GL_DIFFUSE, [1.0, 1.0, 1.0, 1])

# Enable blending for transparency
glEnable(GL_BLEND)
glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

# Initial camera position
glTranslatef(0.0, 0.0, -5)

# Constants
WINDOW_WIDTH = 800
WINDOW_HEIGHT = 600
TILE_SIZE = 32
FPS = 60

# Colors
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
BLUE = (0, 0, 255)
GREEN = (0, 255, 0)
BROWN = (139, 69, 19)
RED = (255, 0, 0)
GRAY = (128, 128, 128)

# Game map
GAME_MAP = [
    "WWWWWWWWWWWWWWWWWWWW",
    "W..................W",
    "W..................W",
    "W........N.........W",
    "W..................W",
    "W..................W",
    "W..................W",
    "W....P.............W",
    "W..................W",
    "W..................W",
    "W..................W",
    "W..................W",
    "WWWWWWWWWWWWWWWWWWWW"
]

# Add these constants near the other constants
TITLE = "Venture Builder AI"
SUBTITLE = "Our Digital Employees"
MENU_BG_COLOR = (0, 0, 0)  # Black background
MENU_TEXT_COLOR = (0, 255, 0)  # Matrix-style green
MENU_HIGHLIGHT_COLOR = (0, 200, 0)  # Slightly darker green for effects

def draw_cube():
    vertices = [
        # Front face
        [-0.5, -0.5,  0.5],
        [ 0.5, -0.5,  0.5],
        [ 0.5,  0.5,  0.5],
        [-0.5,  0.5,  0.5],
        # Back face
        [-0.5, -0.5, -0.5],
        [-0.5,  0.5, -0.5],
        [ 0.5,  0.5, -0.5],
        [ 0.5, -0.5, -0.5],
    ]
    
    surfaces = [
        [0, 1, 2, 3],  # Front
        [3, 2, 6, 5],  # Top
        [0, 3, 5, 4],  # Left
        [1, 7, 6, 2],  # Right
        [4, 5, 6, 7],  # Back
        [0, 4, 7, 1],  # Bottom
    ]
    
    glBegin(GL_QUADS)
    for surface in surfaces:
        glNormal3f(0, 0, 1)  # Simple normal for lighting
        for vertex in surface:
            glVertex3fv(vertices[vertex])
    glEnd()

def draw_sphere(radius, slices, stacks):
    for i in range(stacks):
        lat0 = math.pi * (-0.5 + float(i) / stacks)
        z0 = math.sin(lat0)
        zr0 = math.cos(lat0)
        
        lat1 = math.pi * (-0.5 + float(i + 1) / stacks)
        z1 = math.sin(lat1)
        zr1 = math.cos(lat1)
        
        glBegin(GL_QUAD_STRIP)
        for j in range(slices + 1):
            lng = 2 * math.pi * float(j) / slices
            x = math.cos(lng)
            y = math.sin(lng)
            
            glNormal3f(x * zr0, y * zr0, z0)
            glVertex3f(x * zr0 * radius, y * zr0 * radius, z0 * radius)
            glNormal3f(x * zr1, y * zr1, z1)
            glVertex3f(x * zr1 * radius, y * zr1 * radius, z1 * radius)
        glEnd()


class DialogueSystem:
    def __init__(self):
        self.active = False
        self.user_input = ""
        pygame.font.init()
        self.font = pygame.font.Font(None, 24)
        self.npc_message = ""
        self.input_active = False
        self.last_npc_text = ""
        self.last_input_text = ""
        self.conversation_history = []
        self.is_npc_responding = False  # Track if NPC is responding

        self.ui_surface = pygame.Surface((WINDOW_WIDTH, WINDOW_HEIGHT), pygame.SRCALPHA).convert_alpha()
        self.ui_texture = glGenTextures(1)
        self.current_npc = None
        self.initial_player_pos = None

        self.api_key = os.environ.get("OPENAI_API_KEY", None)
        if not self.api_key:
            print("[DialogueSystem] Error: No OPENAI_API_KEY found in environment.")
            sys.exit(1)

        self.realtime_conn = None
        self.speech_mode = False
        self.last_audio_item_id = None
        self.acc_items = {}
        self.audio_player = AudioPlayerAsync()
        self.sent_audio_once = False
        self.client = AsyncOpenAI(api_key=self.api_key)
        self.loop = asyncio.new_event_loop()
        self.loop_thread = threading.Thread(target=self.run_asyncio_loop, daemon=True)
        self.loop_thread.start()
        self.speech_active = False  # Track if user is speaking
        self.realtime_initialized = False  # Track Realtime API status

    def run_asyncio_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    async def initialize_realtime(self):
        print("[DialogueSystem] Attempting Realtime API connection...")
        self.realtime_initialized = False
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                npc_name = "Michael Chen" if self.current_npc == "CEO" else "Sarah Chen"
                npc_role = "CEO" if self.current_npc == "CEO" else "HR Director"
                npc_tone = "confident" if self.current_npc == "CEO" else "warm"
                npc_voice = "ballad" if self.current_npc == "CEO" else "alloy"
                print(f"[DEBUG] Setting NPC: name={npc_name}, role={npc_role}, tone={npc_tone}, voice={npc_voice}")

                instructions = (
                    f"You are {npc_name}, {npc_role} at Venture Builder AI. "
                    f"In your first response, say exactly 'Hello there, I am {npc_name}, {npc_role} at Venture Builder AI.' "
                    f"Then ask how you can assist the user. For subsequent responses, do not reintroduce yourself. "
                    f"Address the user as 'you' or 'user', and respond naturally in a {npc_tone} tone."
                )
                print(f"[DEBUG] Instructions: {instructions[:100]}...")

                print("[DEBUG] Connecting to Realtime API...")
                async with self.client.beta.realtime.connect(model="gpt-4o-realtime-preview-2024-10-01") as conn:
                    self.realtime_conn = conn
                    print("[DEBUG] Realtime connection established.")
                    await conn.session.update(session={
                        "turn_detection": {
                            "type": "server_vad",
                            "threshold": 0.5,
                            "prefix_padding_ms": 300,
                            "silence_duration_ms": 800
                        },
                        "modalities": ["text", "audio"],
                        "voice": npc_voice,
                        "instructions": instructions
                    })
                    print("[Realtime] Session configured.")
                    self.realtime_initialized = True

                    async for event in conn:
                        await self.handle_realtime_event(event)
            except Exception as e:
                print(f"[DialogueSystem] Realtime connection attempt {attempt+1}/{max_attempts} failed: {str(e)}")
                self.realtime_conn = None
                self.realtime_initialized = False
                if attempt < max_attempts - 1:
                    print("[DialogueSystem] Retrying after 2 seconds...")
                    await asyncio.sleep(2)
                else:
                    print("[DialogueSystem] Max connection attempts reached. Speech mode disabled.")
                    self.speech_mode = False
                    break

    async def handle_realtime_event(self, event):
        try:
            print(f"[DEBUG Realtime Event] Type: {event.type}")
            if event.type == "session.created":
                print(f"[Realtime] Session created: {event.session.id}")
            elif event.type == "session.updated":
                print("[Realtime] Session updated.")
            elif event.type == "response.audio.delta":
                print("[DEBUG] Received audio delta.")
                self.is_npc_responding = True
                if event.item_id != self.last_audio_item_id:
                    self.audio_player.reset_frame_count()
                    self.last_audio_item_id = event.item_id
                bytes_data = base64.b64decode(event.delta)
                self.audio_player.add_data(bytes_data)
            elif event.type == "response.audio_transcript.delta":
                so_far = self.acc_items.get(event.item_id, "") + event.delta
                self.acc_items[event.item_id] = so_far
                print(f"[Realtime] Assistant partial transcript: {so_far}")
            elif event.type == "response.audio_transcript.done":
                self.npc_message = self.acc_items.get(event.item_id, "")
                print(f"[Realtime] Full transcript: {self.npc_message}")
                self.conversation_history.append({"role": "assistant", "content": self.npc_message})
                self.is_npc_responding = False
            elif event.type == "input_audio_buffer.speech_started":
                print("[Realtime] User speech detected.")
                self.speech_active = True
                if self.is_npc_responding:
                    print("[DialogueSystem] Interrupting NPC audio response...")
                    await self.cancel_ongoing_response()
                self.audio_player.stop()
                self.npc_message = ""
                self.acc_items.clear()
                self.conversation_history.append({"role": "user", "content": "[Speech input started]"})
            elif event.type == "input_audio_buffer.speech_done":
                print("[Realtime] User speech ended.")
                self.speech_active = False
                await self.realtime_conn.input_audio_buffer.commit()
                await self.realtime_conn.response.create(response={"modalities": ["text", "audio"]})
            elif event.type == "input_audio_buffer.commit.done":
                print("[Realtime] Audio buffer committed.")
            elif event.type == "response.done":
                print("[Realtime] Response completed.")
                self.is_npc_responding = False
            elif event.type == "error":
                print(f"[Realtime] Error: {event.error}")
                if "response_cancel_not_active" in str(event.error):
                    print("[DialogueSystem] No active response to cancel; continuing.")
                else:
                    self.speech_mode = False
                    self.realtime_conn = None
                    self.realtime_initialized = False
            else:
                print(f"[DEBUG] Unhandled event: {event.type}")
        except Exception as e:
            print(f"[DialogueSystem] Error handling realtime event: {e}")
            self.is_npc_responding = False

    async def record_mic_audio(self):
        print("[DialogueSystem] Starting microphone audio recording...")
        read_size = int(SAMPLE_RATE * 0.02)  # 20ms chunks
        try:
            stream = sd.InputStream(channels=CHANNELS, samplerate=SAMPLE_RATE, dtype="int16")
            stream.start()
            print("[DialogueSystem] Audio input stream started with 20ms chunks.")
            while True:
                if not self.speech_mode or not self.realtime_conn or not self.realtime_initialized:
                    await asyncio.sleep(0.1)
                    continue
                if stream.read_available < read_size:
                    await asyncio.sleep(0.01)
                    continue
                data, overflowed = stream.read(read_size)
                if overflowed:
                    print("[DialogueSystem] Warning: Audio buffer overflowed; skipping chunk.")
                    continue
                if data.size > 0:
                    b64_audio = base64.b64encode(data.tobytes()).decode("utf-8")
                    print("[DEBUG] Sending audio chunk to Realtime API.")
                    await self.realtime_conn.input_audio_buffer.append(audio=b64_audio)
                    self.sent_audio_once = True
                await asyncio.sleep(0)
        except Exception as e:
            print(f"[DialogueSystem] Microphone error: {e}")
            self.speech_mode = False
            self.realtime_initialized = False
        finally:
            if 'stream' in locals():
                stream.stop()
                stream.close()
                print("[DialogueSystem] Audio input stream closed.")

    async def send_message(self, text_input):
        if not self.conversation_history:
            print("[DialogueSystem] No conversation history to send.")
            return

        try:
            if self.speech_mode and self.realtime_conn and self.realtime_initialized:
                if self.is_npc_responding:
                    await self.cancel_ongoing_response()
                await self.realtime_conn.conversation.item.create(
                    item={
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": text_input}]
                    }
                )
                await self.realtime_conn.response.create(
                    response={"modalities": ["text", "audio"]}
                )
            else:
                response = await self.client.chat.completions.create(
                    model="gpt-4-0125-preview",
                    messages=self.conversation_history,
                    temperature=0.85,
                    max_tokens=150,
                )
                ai_message = response.choices[0].message.content
                self.conversation_history.append({
                    "role": "assistant",
                    "content": ai_message
                })
                self.npc_message = ai_message
                self.is_npc_responding = True
                print(f"[DialogueSystem] NPC says: {self.npc_message}")
        except Exception as e:
            self.npc_message = "I apologize, but I'm having trouble connecting to our systems right now."
            print(f"[DialogueSystem] Error: {e}")
            self.is_npc_responding = False

    async def cancel_ongoing_response(self):
        """Cancel ongoing NPC response and reset state."""
        if self.is_npc_responding or self.audio_player.is_playing():
            try:
                if self.realtime_conn and self.speech_mode:
                    await self.realtime_conn.response.cancel()
            except Exception as e:
                if "response_cancel_not_active" not in str(e):
                    print(f"[DialogueSystem] Error cancelling response: {e}")
            self.audio_player.stop()
            self.audio_player.reset_frame_count()
            self.npc_message = ""
            self.acc_items.clear()
            self.last_audio_item_id = None
            self.is_npc_responding = False
            print("[DialogueSystem] Ongoing NPC response cancelled.")

    def start_conversation(self, npc_role="HR", player_pos=None):
        self.active = True
        self.input_active = True
        self.current_npc = npc_role
        self.initial_player_pos = [player_pos[0], player_pos[1], player_pos[2]] if player_pos else [0, 0.5, 0]
        print(f"[DialogueSystem] Dialogue started with {npc_role}")

        if self.audio_player:
            print("[DialogueSystem] Stopping existing audio playback...")
            self.audio_player.stop()
            self.audio_player.reset_frame_count()
            self.last_audio_item_id = None
            self.acc_items.clear()

        if self.realtime_conn:
            print("[DialogueSystem] Closing existing Realtime API connection...")
            self.realtime_conn.close()
            self.realtime_conn = None
            self.realtime_initialized = False

        asyncio.run_coroutine_threadsafe(self.initialize_realtime(), self.loop)
        asyncio.run_coroutine_threadsafe(self.record_mic_audio(), self.loop)

        base_prompt = """Interaction Framework:
            - Maintain consistent personality throughout conversation
            - Remember previous context within the dialogue
            - Use natural speech patterns with occasional filler words
            - Show emotional intelligence in responses
            - Keep responses concise but meaningful (2-3 sentences)
            - React appropriately to both positive and negative interactions
            """

        if npc_role == "HR":
            system_prompt = f"""{base_prompt}
                You are Sarah Chen, HR Director at Venture Builder AI. Core traits:
                - Warm and approachable
                - Focus on employee well-being and company culture
            """
        else:
            system_prompt = f"""{base_prompt}
                You are Michael Chen, CEO of Venture Builder AI. Core traits:
                - Confident and visionary
                - Focus on innovation and leadership
            """

        npc_name = "Michael Chen" if npc_role == "CEO" else "Sarah Chen"
        npc_title = "CEO" if npc_role == "CEO" else "HR Director"
        initial_message = {
            "HR": f"Hello there, I am Sarah Chen, HR Director at Venture Builder AI. How can I assist you today?",
            "CEO": f"Hello there, I am Michael Chen, CEO at Venture Builder AI. What can I do for you today?"
        }

        self.npc_message = initial_message[npc_role]
        self.conversation_history = [{
            "role": "system",
            "content": system_prompt
        }]

        if self.realtime_conn and self.realtime_initialized:
            asyncio.run_coroutine_threadsafe(
                self.realtime_conn.response.create(
                    response={"modalities": ["text", "audio"], "instructions": self.npc_message}
                ),
                self.loop
            )
            print(f"[DialogueSystem] Sent initial greeting to Realtime API: {self.npc_message}")

    def handle_input(self, event):
        if not self.active or not self.input_active:
            return

        if event.type == pygame.KEYDOWN:
            keys = pygame.key.get_pressed()
            if keys[pygame.K_LSHIFT] and event.key == pygame.K_q:
                self.active = False
                self.input_active = False
                self.speech_mode = False
                if self.realtime_conn:
                    asyncio.run_coroutine_threadsafe(self.cancel_ongoing_response(), self.loop)
                    self.realtime_conn.close()
                    self.realtime_conn = None
                    self.realtime_initialized = False
                print("[DialogueSystem] Chat ended")
                return {"command": "move_player_back", "position": self.initial_player_pos}

            if event.key == pygame.K_F5:
                self.speech_mode = not self.speech_mode
                if self.speech_mode:
                    self.sent_audio_once = False
                    print("[DialogueSystem] Speech mode enabled: Real-time conversation started.")
                    if not self.realtime_conn or not self.realtime_initialized:
                        print("[DialogueSystem] Realtime connection not active. Initializing...")
                        asyncio.run_coroutine_threadsafe(self.initialize_realtime(), self.loop)
                        asyncio.run_coroutine_threadsafe(self.record_mic_audio(), self.loop)
                else:
                    print("[DialogueSystem] Speech mode disabled.")
                    asyncio.run_coroutine_threadsafe(self.cancel_ongoing_response(), self.loop)
                    self.audio_player.stop()
                    self.speech_active = False
                    if self.realtime_conn:
                        self.realtime_conn.close()
                        self.realtime_conn = None
                        self.realtime_initialized = False

            if event.key == pygame.K_RETURN and self.user_input.strip():
                print(f"[DialogueSystem] User typed: {self.user_input}")
                if self.is_npc_responding:
                    print("[DialogueSystem] Interrupting NPC response...")
                    asyncio.run_coroutine_threadsafe(self.cancel_ongoing_response(), self.loop)
                self.conversation_history.append({"role": "user", "content": self.user_input.strip()})
                asyncio.run_coroutine_threadsafe(self.send_message(self.user_input.strip()), self.loop)
                self.user_input = ""
            elif event.key == pygame.K_BACKSPACE:
                self.user_input = self.user_input[:-1]
            elif event.unicode.isprintable():
                self.user_input += event.unicode

    def render(self):
        if not self.active:
            return

        self.ui_surface.fill((0, 0, 0, 0))
        if self.active:
            box_height = 200
            box_y = WINDOW_HEIGHT - box_height - 20
            box_color = (0, 0, 0, 230)
            pygame.draw.rect(self.ui_surface, box_color, (20, box_y, WINDOW_WIDTH - 40, box_height))
            pygame.draw.rect(self.ui_surface, (255, 255, 255, 255), (20, box_y, WINDOW_WIDTH - 40, box_height), 2)

            quit_text_surface = self.font.render("Press Shift+Q to exit, Press F5 to toggle speech mode", True, (255, 255, 255))
            self.ui_surface.blit(quit_text_surface, (40, box_y + 10))

            if self.npc_message:
                self.render_text(self.ui_surface, self.npc_message, 40, box_y + 40)

            if self.input_active:
                input_prompt = "> " + self.user_input + "_"
                input_surface = self.font.render(input_prompt, True, (255, 255, 255))
                self.ui_surface.blit(input_surface, (40, box_y + box_height - 40))

        texture_data = pygame.image.tostring(self.ui_surface, "RGBA", True)
        glPushAttrib(GL_ALL_ATTRIB_BITS)
        glMatrixMode(GL_PROJECTION)
        glPushMatrix()
        glLoadIdentity()
        glOrtho(0, WINDOW_WIDTH, 0, WINDOW_HEIGHT, -1, 1)
        glMatrixMode(GL_MODELVIEW)
        glPushMatrix()
        glLoadIdentity()

        glDisable(GL_DEPTH_TEST)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glEnable(GL_TEXTURE_2D)

        glBindTexture(GL_TEXTURE_2D, self.ui_texture)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, WINDOW_WIDTH, WINDOW_HEIGHT, 0, GL_RGBA, GL_UNSIGNED_BYTE, texture_data)

        glBegin(GL_QUADS)
        glTexCoord2f(0, 0); glVertex2f(0, 0)
        glTexCoord2f(1, 0); glVertex2f(WINDOW_WIDTH, 0)
        glTexCoord2f(1, 1); glVertex2f(WINDOW_WIDTH, WINDOW_HEIGHT)
        glTexCoord2f(0, 1); glVertex2f(0, WINDOW_HEIGHT)
        glEnd()

        glMatrixMode(GL_PROJECTION)
        glPopMatrix()
        glMatrixMode(GL_MODELVIEW)
        glPopMatrix()
        glPopAttrib()

    def render_text(self, surface, text, x, y):
        max_width = WINDOW_WIDTH - 40
        line_height = 25

        words = text.split()
        lines = []
        current_line = []
        current_width = 0

        text_color = (255, 255, 255)

        for word in words:
            word_surface = self.font.render(word + ' ', True, text_color)
            word_width = word_surface.get_width()
            if current_width + word_width <= max_width:
                current_line.append(word)
                current_width += word_width
            else:
                lines.append(' '.join(current_line))
                current_line = [word]
                current_width = word_width

        if current_line:
            lines.append(' '.join(current_line))

        for i, line in enumerate(lines):
            text_surface = self.font.render(line, True, text_color)
            surface.blit(text_surface, (x, y + i * line_height))

        return len(lines) * line_height


class World:
    def __init__(self):
        self.size = 5
        # Define office furniture colors
        self.colors = {
            'floor': (0.76, 0.6, 0.42),  # Light wood color
            'walls': (0.85, 0.85, 0.85),  # Changed to light gray (from 0.95)
            'desk': (0.6, 0.4, 0.2),  # Brown wood
            'chair': (0.2, 0.2, 0.2),  # Dark grey
            'computer': (0.1, 0.1, 0.1),  # Black
            'plant': (0.2, 0.5, 0.2),  # Green
            'partition': (0.3, 0.3, 0.3)  # Darker solid gray for booth walls
        }
        
    def draw_desk(self, x, z, rotation=0):
        glPushMatrix()
        glTranslatef(x, 0, z)  # Start at floor level
        glRotatef(rotation, 0, 1, 0)
        
        # Desk top (reduced size)
        glColor3f(*self.colors['desk'])
        glBegin(GL_QUADS)
        glVertex3f(-0.4, 0.4, -0.3)
        glVertex3f(0.4, 0.4, -0.3)
        glVertex3f(0.4, 0.4, 0.3)
        glVertex3f(-0.4, 0.4, 0.3)
        glEnd()
        
        # Desk legs (adjusted for new height)
        for x_offset, z_offset in [(-0.35, -0.25), (0.35, -0.25), (-0.35, 0.25), (0.35, 0.25)]:
            glBegin(GL_QUADS)
            glVertex3f(x_offset-0.02, 0, z_offset-0.02)
            glVertex3f(x_offset+0.02, 0, z_offset-0.02)
            glVertex3f(x_offset+0.02, 0.4, z_offset-0.02)
            glVertex3f(x_offset-0.02, 0.4, z_offset-0.02)
            glEnd()
        
        # Computer monitor (smaller)
        glColor3f(*self.colors['computer'])
        glTranslatef(-0.15, 0.4, 0)
        glBegin(GL_QUADS)
        glVertex3f(-0.1, 0, -0.05)
        glVertex3f(0.1, 0, -0.05)
        glVertex3f(0.1, 0.2, -0.05)
        glVertex3f(-0.1, 0.2, -0.05)
        glEnd()
        
        glPopMatrix()
    
    def draw_chair(self, x, z, rotation=0):
        glPushMatrix()
        glTranslatef(x, 0, z)
        glRotatef(rotation, 0, 1, 0)
        glColor3f(*self.colors['chair'])
        
        # Seat (lowered and smaller)
        glBegin(GL_QUADS)
        glVertex3f(-0.15, 0.25, -0.15)
        glVertex3f(0.15, 0.25, -0.15)
        glVertex3f(0.15, 0.25, 0.15)
        glVertex3f(-0.15, 0.25, 0.15)
        glEnd()
        
        # Back (adjusted height)
        glBegin(GL_QUADS)
        glVertex3f(-0.15, 0.25, -0.15)
        glVertex3f(0.15, 0.25, -0.15)
        glVertex3f(0.15, 0.5, -0.15)
        glVertex3f(-0.15, 0.5, -0.15)
        glEnd()
        
        # Chair legs (adjusted height)
        for x_offset, z_offset in [(-0.12, -0.12), (0.12, -0.12), (-0.12, 0.12), (0.12, 0.12)]:
            glBegin(GL_QUADS)
            glVertex3f(x_offset-0.02, 0, z_offset-0.02)
            glVertex3f(x_offset+0.02, 0, z_offset-0.02)
            glVertex3f(x_offset+0.02, 0.25, z_offset-0.02)
            glVertex3f(x_offset-0.02, 0.25, z_offset-0.02)
            glEnd()
            
        glPopMatrix()
    
    def draw_plant(self, x, z):
        glPushMatrix()
        glTranslatef(x, 0, z)
        
        # Plant pot (smaller)
        glColor3f(0.4, 0.2, 0.1)  # Brown pot
        pot_radius = 0.1
        pot_height = 0.15
        segments = 8
        
        # Draw the pot sides
        glBegin(GL_QUADS)
        for i in range(segments):
            angle1 = (i / segments) * 2 * math.pi
            angle2 = ((i + 1) / segments) * 2 * math.pi
            x1 = math.cos(angle1) * pot_radius
            z1 = math.sin(angle1) * pot_radius
            x2 = math.cos(angle2) * pot_radius
            z2 = math.sin(angle2) * pot_radius
            glVertex3f(x1, 0, z1)
            glVertex3f(x2, 0, z2)
            glVertex3f(x2, pot_height, z2)
            glVertex3f(x1, pot_height, z1)
        glEnd()
        
        # Plant leaves (smaller)
        glColor3f(*self.colors['plant'])
        glTranslatef(0, pot_height, 0)
        leaf_size = 0.15
        num_leaves = 6
        for i in range(num_leaves):
            angle = (i / num_leaves) * 2 * math.pi
            x = math.cos(angle) * leaf_size
            z = math.sin(angle) * leaf_size
            glBegin(GL_TRIANGLES)
            glVertex3f(0, 0, 0)
            glVertex3f(x, leaf_size, z)
            glVertex3f(z, leaf_size/2, -x)
            glEnd()
        
        glPopMatrix()
        
    def draw(self):
        # Set material properties
        glEnable(GL_COLOR_MATERIAL)
        glColorMaterial(GL_FRONT_AND_BACK, GL_AMBIENT_AND_DIFFUSE)
        
        # Draw floor at Y=0
        glBegin(GL_QUADS)
        glColor3f(*self.colors['floor'])
        glNormal3f(0, 1, 0)
        glVertex3f(-self.size, 0, -self.size)
        glVertex3f(-self.size, 0, self.size)
        glVertex3f(self.size, 0, self.size)
        glVertex3f(self.size, 0, -self.size)
        glEnd()
        
        # Draw walls starting from floor level
        glBegin(GL_QUADS)
        glColor3f(*self.colors['walls'])
        
        # Front wall
        glVertex3f(-self.size, 0, -self.size)
        glVertex3f(self.size, 0, -self.size)
        glVertex3f(self.size, 2, -self.size)
        glVertex3f(-self.size, 2, -self.size)
        
        # Back wall
        glVertex3f(-self.size, 0, self.size)
        glVertex3f(self.size, 0, self.size)
        glVertex3f(self.size, 2, self.size)
        glVertex3f(-self.size, 2, self.size)
        
        # Left wall
        glVertex3f(-self.size, 0, -self.size)
        glVertex3f(-self.size, 0, self.size)
        glVertex3f(-self.size, 2, self.size)
        glVertex3f(-self.size, 2, -self.size)
        
        # Right wall
        glVertex3f(self.size, 0, -self.size)
        glVertex3f(self.size, 0, self.size)
        glVertex3f(self.size, 2, self.size)
        glVertex3f(self.size, 2, -self.size)
        glEnd()
        
        # Draw office furniture in a more realistic arrangement
        # HR Area (left side)
        self.draw_desk(-4, -2, 90)
        self.draw_chair(-3.5, -2, 90)
        self.draw_partition_walls(-4, -2)  # Add booth walls for HR
        
        # CEO Area (right side)
        self.draw_desk(4, 1, -90)
        self.draw_chair(3.5, 1, -90)
        self.draw_partition_walls(4, 1)  # Add booth walls for CEO
        
        # Plants in corners (moved closer to walls)
        self.draw_plant(-4.5, -4.5)
        self.draw_plant(4.5, -4.5)
        self.draw_plant(-4.5, 4.5)
        self.draw_plant(4.5, 4.5)

    def draw_partition_walls(self, x, z):
        """Draw booth partition walls - all surfaces in solid gray"""
        glColor3f(0.3, 0.3, 0.3)  # Solid gray for all walls
        
        # Back wall (smaller and thinner)
        glPushMatrix()
        glTranslatef(x, 0, z)
        glScalef(0.05, 1.0, 1.0)  # Thinner wall, normal height, shorter length
        draw_cube()  # Replace glutSolidCube with draw_cube
        glPopMatrix()
        
        # Side wall (smaller and thinner)
        glPushMatrix()
        glTranslatef(x, 0, z + 0.5)  # Moved closer
        glRotatef(90, 0, 1, 0)
        glScalef(0.05, 1.0, 0.8)  # Thinner wall, normal height, shorter length
        draw_cube()  # Replace glutSolidCube with draw_cube
        glPopMatrix()

class Player:
    def __init__(self):
        self.pos = [0, 0.5, 0]  # Lowered Y position to be just above floor
        self.rot = [0, 0, 0]
        self.speed = 0.3
        self.mouse_sensitivity = 0.5
        
    def move(self, dx, dz):
        # Convert rotation to radians (negative because OpenGL uses clockwise rotation)
        angle = math.radians(-self.rot[1])
        
        # Calculate movement vector
        move_x = (dx * math.cos(angle) + dz * math.sin(angle)) * self.speed
        move_z = (-dx * math.sin(angle) + dz * math.cos(angle)) * self.speed
        
        # Calculate new position
        new_x = self.pos[0] + move_x
        new_z = self.pos[2] + move_z
        
        # Wall collision check (room is 10x10)
        room_limit = 4.5  # Slightly less than room size/2 to prevent wall clipping
        if abs(new_x) < room_limit:
            self.pos[0] = new_x
        if abs(new_z) < room_limit:
            self.pos[2] = new_z

    def update_rotation(self, dx, dy):
        # Multiply mouse movement by sensitivity for faster turning
        self.rot[1] += dx * self.mouse_sensitivity

class NPC:
    def __init__(self, x, y, z, role="HR"):
        self.scale = 0.6  # Make NPCs smaller (about 60% of current size)
        # Position them beside the desks, at ground level
        # Adjust Y position to be half their height (accounting for scale)
        self.pos = [x, 0.65, z]  # This puts their feet on the ground
        self.size = 0.5
        self.role = role
        
        # Enhanced color palette
        self.skin_color = (0.8, 0.7, 0.6)  # Neutral skin tone
        self.hair_color = (0.2, 0.15, 0.1) if role == "HR" else (0.3, 0.3, 0.3)  # Dark brown vs gray
        
        # Updated clothing colors
        if role == "HR":
            self.clothes_primary = (0.8, 0.2, 0.2)    # Bright red
            self.clothes_secondary = (0.6, 0.15, 0.15) # Darker red
        else:  # CEO
            self.clothes_primary = (0.2, 0.3, 0.8)    # Bright blue
            self.clothes_secondary = (0.15, 0.2, 0.6)  # Darker blue

    def draw(self):
        glPushMatrix()
        glTranslatef(self.pos[0], self.pos[1], self.pos[2])
        glScalef(self.scale, self.scale, self.scale)
        
        # Head
        glColor3f(*self.skin_color)
        draw_sphere(0.12, 16, 16)
        
        # Hair (slightly larger than head)
        glColor3f(*self.hair_color)
        glPushMatrix()
        glTranslatef(0, 0.05, 0)  # Slightly above head
        draw_sphere(0.13, 16, 16)
        glPopMatrix()
        
        # Body (torso)
        glColor3f(*self.clothes_primary)
        glPushMatrix()
        glTranslatef(0, -0.3, 0)  # Move down from head
        glScalef(0.3, 0.4, 0.2)   # Make it rectangular
        draw_cube()
        glPopMatrix()
        
        # Arms
        glColor3f(*self.clothes_secondary)
        for x_offset in [-0.2, 0.2]:  # Left and right arms
            glPushMatrix()
            glTranslatef(x_offset, -0.3, 0)
            glScalef(0.1, 0.4, 0.1)
            draw_cube()
            glPopMatrix()
        
        # Legs
        glColor3f(*self.clothes_secondary)
        for x_offset in [-0.1, 0.1]:  # Left and right legs
            glPushMatrix()
            glTranslatef(x_offset, -0.8, 0)
            glScalef(0.1, 0.5, 0.1)
            draw_cube()
            glPopMatrix()
        
        glPopMatrix()

class MenuScreen:
    def __init__(self):
        self.font_large = pygame.font.Font(None, 74)
        self.font_medium = pygame.font.Font(None, 48)
        self.font_small = pygame.font.Font(None, 36)
        self.active = True
        self.start_time = time.time()
        
    def render(self):
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        
        # Create a surface for 2D rendering
        surface = pygame.Surface((WINDOW_WIDTH, WINDOW_HEIGHT), pygame.SRCALPHA)
        
        # Calculate vertical positions
        center_y = WINDOW_HEIGHT // 2
        title_y = center_y - 100
        subtitle_y = center_y - 20
        prompt_y = center_y + 100
        
        # Render title with "typing" effect
        elapsed_time = time.time() - self.start_time
        title_chars = int(min(len(TITLE), elapsed_time * 15))  # Type 15 chars per second
        partial_title = TITLE[:title_chars]
        title_surface = self.font_large.render(partial_title, True, MENU_TEXT_COLOR)
        title_x = (WINDOW_WIDTH - title_surface.get_width()) // 2
        surface.blit(title_surface, (title_x, title_y))
        
        # Render subtitle with fade-in effect
        if elapsed_time > len(TITLE) / 15:  # Start after title is typed
            subtitle_alpha = min(255, int((elapsed_time - len(TITLE) / 15) * 255))
            subtitle_surface = self.font_medium.render(SUBTITLE, True, MENU_TEXT_COLOR)
            subtitle_surface.set_alpha(subtitle_alpha)
            subtitle_x = (WINDOW_WIDTH - subtitle_surface.get_width()) // 2
            surface.blit(subtitle_surface, (subtitle_x, subtitle_y))
        
        # Render "Press ENTER" with blinking effect
        if elapsed_time > (len(TITLE) / 15 + 1):  # Start after subtitle fade
            if int(elapsed_time * 2) % 2:  # Blink every 0.5 seconds
                prompt_text = "Press ENTER to start"
                prompt_surface = self.font_small.render(prompt_text, True, MENU_TEXT_COLOR)
                prompt_x = (WINDOW_WIDTH - prompt_surface.get_width()) // 2
                surface.blit(prompt_surface, (prompt_x, prompt_y))
        
        # Add some retro effects (scanlines)
        for y in range(0, WINDOW_HEIGHT, 4):
            pygame.draw.line(surface, (0, 50, 0), (0, y), (WINDOW_WIDTH, y))
        
        # Convert surface to OpenGL texture
        texture_data = pygame.image.tostring(surface, "RGBA", True)
        
        # Set up orthographic projection for 2D rendering
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        glOrtho(0, WINDOW_WIDTH, WINDOW_HEIGHT, 0, -1, 1)
        glMatrixMode(GL_MODELVIEW)
        glLoadIdentity()

        # Render the texture in OpenGL
        texture = glGenTextures(1)
        glBindTexture(GL_TEXTURE_2D, texture)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, WINDOW_WIDTH, WINDOW_HEIGHT, 0, GL_RGBA, GL_UNSIGNED_BYTE, texture_data)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        
        # Draw the texture
        glEnable(GL_TEXTURE_2D)
        glBegin(GL_QUADS)
        glTexCoord2f(0, 1); glVertex2f(0, 0)
        glTexCoord2f(1, 1); glVertex2f(WINDOW_WIDTH, 0)
        glTexCoord2f(1, 0); glVertex2f(WINDOW_WIDTH, WINDOW_HEIGHT)
        glTexCoord2f(0, 0); glVertex2f(0, WINDOW_HEIGHT)
        glEnd()
        glDisable(GL_TEXTURE_2D)
        
        # Reset OpenGL state for 3D rendering
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        gluPerspective(45, (WINDOW_WIDTH / WINDOW_HEIGHT), 0.1, 50.0)
        glMatrixMode(GL_MODELVIEW)
        glLoadIdentity()
        glEnable(GL_DEPTH_TEST)

        pygame.display.flip()

class Game3D:
    def __init__(self):
        self.menu = MenuScreen()
        self.player = Player()
        self.world = World()
        self.dialogue = DialogueSystem()
        self.hr_npc = NPC(-3.3, 0, -2, "HR")  # Moved beside the desk
        self.ceo_npc = NPC(3.3, 0, 1, "CEO")  # Moved beside the desk
        self.interaction_distance = 2.0
        self.last_interaction_time = 0

    def move_player_away_from_npc(self, npc_pos):
        # Calculate direction vector from NPC to player
        dx = self.player.pos[0] - npc_pos[0]
        dz = self.player.pos[2] - npc_pos[2]
        
        # Normalize the vector
        distance = math.sqrt(dx*dx + dz*dz)
        if distance > 0:
            dx /= distance
            dz /= distance
        
        # Move player back by 3 units
        self.player.pos[0] = npc_pos[0] + (dx * 3)
        self.player.pos[2] = npc_pos[2] + (dz * 3)

    def run(self):
        running = True
        while running:
            if self.menu.active:
                print("[DEBUG] Menu is active.") # ADD THIS
                # Menu loop
                for event in pygame.event.get():
                    print(f"[DEBUG] Game Event: {event}") 
                    if event.type == pygame.QUIT:
                        running = False
                    elif event.type == pygame.KEYDOWN:
                        if event.key == pygame.K_RETURN and time.time() - self.menu.start_time > (len(TITLE) / 15 + 1):
                            self.menu.active = False
                            pygame.mouse.set_visible(False)
                            pygame.event.set_grab(True)
                        elif event.key == pygame.K_ESCAPE:
                            running = False
                
                self.menu.render()
            else:
                # Main game loop
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        running = False
                    elif event.type == pygame.KEYDOWN:
                        if event.key == pygame.K_ESCAPE:
                            pygame.mouse.set_visible(True)
                            pygame.event.set_grab(False)
                            running = False
                        
                        # Handle dialogue input and check for exit command
                        if self.dialogue.active:
                            result = self.dialogue.handle_input(event)
                            if isinstance(result, dict) and result.get("command") == "move_player_back":
                                # Move player away from the current NPC
                                current_npc = self.hr_npc if self.dialogue.current_npc == "HR" else self.ceo_npc
                                self.move_player_away_from_npc(current_npc.pos)
                                
                    elif event.type == pygame.MOUSEMOTION:
                        x, y = event.rel
                        self.player.update_rotation(x, y)

                # Handle keyboard input for movement (keep this blocked during dialogue)
                if not self.dialogue.active:
                    keys = pygame.key.get_pressed()
                    if keys[pygame.K_w]: self.player.move(0, -1)
                    if keys[pygame.K_s]: self.player.move(0, 1)
                    if keys[pygame.K_a]: self.player.move(-1, 0)
                    if keys[pygame.K_d]: self.player.move(1, 0)

                # Check NPC interactions
                current_time = time.time()
                if current_time - self.last_interaction_time > 0.5:  # Cooldown on interactions
                    # Check distance to HR NPC
                    dx = self.player.pos[0] - self.hr_npc.pos[0]
                    dz = self.player.pos[2] - self.hr_npc.pos[2]
                    hr_distance = math.sqrt(dx*dx + dz*dz)
                    
                    # Check distance to CEO NPC
                    dx = self.player.pos[0] - self.ceo_npc.pos[0]
                    dz = self.player.pos[2] - self.ceo_npc.pos[2]
                    ceo_distance = math.sqrt(dx*dx + dz*dz)
                    print(f"[DEBUG] Distance to CEO: {ceo_distance:.2f}") # ADDED THIS
                    if hr_distance < self.interaction_distance and not self.dialogue.active:
                        print("[DEBUG] Interacting with HR NPC!") # ADD THIS
                        self.dialogue.start_conversation("HR", self.player.pos)
                        self.last_interaction_time = current_time
                    elif ceo_distance < self.interaction_distance and not self.dialogue.active:
                        self.dialogue.start_conversation("CEO", self.player.pos)
                        self.last_interaction_time = current_time

                # Clear the screen and depth buffer
                glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)

                # Save the current matrix
                glPushMatrix()

                # Apply player rotation and position
                glRotatef(self.player.rot[0], 1, 0, 0)
                glRotatef(self.player.rot[1], 0, 1, 0)
                glTranslatef(-self.player.pos[0], -self.player.pos[1], -self.player.pos[2])

                # Draw the world and NPCs
                self.world.draw()
                self.hr_npc.draw()
                self.ceo_npc.draw()

                # Restore the matrix
                glPopMatrix()

                # Render dialogue system (if active)
                self.dialogue.render()

                # Swap the buffers
                pygame.display.flip()

                # Maintain 60 FPS
                pygame.time.Clock().tick(60)

        pygame.quit()

# Create and run game
game = Game3D()
game.run()