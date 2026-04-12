# input/whisper_handler.py
#
# Handles voice input. Takes an audio file and converts
# it to text using OpenAI's Whisper model.
# The transcript is then treated just like a text query.

import openai
import os

client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


async def transcribe_audio(audio_path: str) -> str:
    """
    Takes an audio file path and returns the transcribed text.

    Args:
        audio_path: path to the audio file (webm, mp3, wav etc.)

    Returns:
        transcribed text as a string
    """

    with open(audio_path, "rb") as audio_file:
        transcript = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file
        )

    return transcript.text