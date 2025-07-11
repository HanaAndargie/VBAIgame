# audio_util.py
from __future__ import annotations
import io
import base64
import asyncio
import threading
from typing import Callable, Awaitable

import numpy as np
import pyaudio
import sounddevice as sd
from pydub import AudioSegment

from openai.resources.beta.realtime.realtime import AsyncRealtimeConnection

# ========== Constants ==========

CHUNK_LENGTH_S = 0.05      # Audio chunk length in seconds (50ms)
SAMPLE_RATE = 24000        # Audio sample rate (Hz)
FORMAT = pyaudio.paInt16   # 16-bit audio format
CHANNELS = 1               # Mono audio

# ===============================
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

def audio_to_pcm16_base64(audio_bytes: bytes) -> bytes:
    """
    Convert a raw audio file (any format) to raw 16-bit PCM audio bytes at 24kHz mono.

    Args:
        audio_bytes (bytes): Original audio file in any format (e.g. mp3, wav).
    Returns:
        bytes: PCM-encoded raw audio bytes (16-bit, 24kHz, mono).
    """
    # Decode the audio file using pydub (which uses ffmpeg behind the scenes)
    audio = AudioSegment.from_file(io.BytesIO(audio_bytes))
    print(f"Loaded audio: {audio.frame_rate=} {audio.channels=} {audio.sample_width=} {audio.frame_width=}")
    # Resample to 24kHz mono PCM16, set sample width to 2 bytes (16 bits)
    pcm_audio = audio.set_frame_rate(SAMPLE_RATE).set_channels(CHANNELS).set_sample_width(2).raw_data
    return pcm_audio

class AudioPlayerAsync:
    """
    Asynchronous audio playback utility.
    Queues PCM16 audio data and plays it using sounddevice, in a thread-safe manner.
    """

    def __init__(self):
        self.queue = []                 # List of audio chunks to play (numpy arrays)
        self.lock = threading.Lock()    # For thread-safe queue access
        self.stream = sd.OutputStream(
            callback=self.callback,
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=np.int16,
            blocksize=int(CHUNK_LENGTH_S * SAMPLE_RATE),
        )
        self.playing = False
        self._frame_count = 0           # For debugging, counts frames played

    def callback(self, outdata, frames, time, status):
        """
        Called by sounddevice for each audio block. Sends audio data to speaker.
        """
        with self.lock:
            data = np.empty(0, dtype=np.int16)

            # Fill the audio buffer with data from the queue, up to 'frames' samples
            while len(data) < frames and len(self.queue) > 0:
                item = self.queue.pop(0)
                frames_needed = frames - len(data)
                data = np.concatenate((data, item[:frames_needed]))
                if len(item) > frames_needed:
                    self.queue.insert(0, item[frames_needed:])

            self._frame_count += len(data)

            # If not enough data, fill the rest with silence (zeros)
            if len(data) < frames:
                data = np.concatenate((data, np.zeros(frames - len(data), dtype=np.int16)))

        outdata[:] = data.reshape(-1, 1)

    def reset_frame_count(self):
        """Resets the internal frame counter (for debugging/timing)."""
        self._frame_count = 0

    def get_frame_count(self):
        """Returns the number of frames played since last reset."""
        return self._frame_count

    def add_data(self, data: bytes):
        """
        Add raw PCM16 audio data to the queue for playback.

        Args:
            data (bytes): PCM16 audio (mono).
        """
        with self.lock:
            np_data = np.frombuffer(data, dtype=np.int16)
            self.queue.append(np_data)
            if not self.playing:
                self.start()

    def start(self):
        """Start playback if not already playing."""
        self.playing = True
        self.stream.start()

    def stop(self):
        """Stop playback and clear the queue."""
        self.playing = False
        self.stream.stop()
        with self.lock:
            self.queue = []

    def terminate(self):
        """Close the audio stream."""
        self.stream.close()

async def send_audio_worker_sounddevice(
    connection: AsyncRealtimeConnection,
    should_send: Callable[[], bool] | None = None,
    start_send: Callable[[], Awaitable[None]] | None = None,
):
    """
    Asynchronously records microphone audio and streams it to the OpenAI Realtime API.

    Args:
        connection: AsyncRealtimeConnection object.
        should_send: Optional function to decide when to send audio chunks.
        start_send: Optional coroutine to call before sending audio.
    """
    sent_audio = False

    # Print available audio devices for debugging
    device_info = sd.query_devices()
    print(device_info)

    read_size = int(SAMPLE_RATE * 0.02)  # Read 20ms chunks (as required by Realtime API)

    stream = sd.InputStream(
        channels=CHANNELS,
        samplerate=SAMPLE_RATE,
        dtype="int16",
    )
    stream.start()

    try:
        while True:
            if stream.read_available < read_size:
                await asyncio.sleep(0)
                continue

            data, _ = stream.read(read_size)

            if should_send() if should_send else True:
                if not sent_audio and start_send:
                    await start_send()
                await connection.send(
                    {
                        "type": "input_audio_buffer.append",
                        "audio": base64.b64encode(data).decode("utf-8"),
                    }
                )
                sent_audio = True

            elif sent_audio:
                print("Done, triggering inference")
                await connection.send({"type": "input_audio_buffer.commit"})
                await connection.send({"type": "response.create", "response": {}})
                sent_audio = False

            await asyncio.sleep(0)

    except KeyboardInterrupt:
        pass
    finally:
        stream.stop()
        stream.close()
