# input/router.py
#
# This is the entry point for all user inputs.
# Whatever the user sends — text, voice, image or PDF —
# this file figures out what it is and routes it to the
# right handler. Everything comes out as a NormalizedInput
# so the agents don't need to worry about input types.

import os
from pydantic import BaseModel
from typing import Optional

# This is the standard format every input gets converted to.
# Agents only ever see this — they don't care if it came from voice or PDF.
class NormalizedInput(BaseModel):
    query: str                          # the actual question/text
    source: str                         # "text", "voice", "image", "pdf"
    financial_data: Optional[dict] = None  # extracted data if image/pdf was uploaded


class InputRouter:

    async def route(self, input_type: str, data: dict) -> NormalizedInput:
        """
        Takes raw input and returns a clean NormalizedInput.

        Args:
            input_type: one of "text", "voice", "image", "pdf"
            data: dict containing the raw input data

        Returns:
            NormalizedInput ready for the agents
        """

        if input_type == "text":
            return self._handle_text(data)

        elif input_type == "voice":
            return await self._handle_voice(data)

        elif input_type == "image":
            return await self._handle_image(data)

        elif input_type == "pdf":
            return await self._handle_pdf(data)

        else:
            raise ValueError(f"Unknown input type: {input_type}")


    def _handle_text(self, data: dict) -> NormalizedInput:
        # simplest case — just wrap the text
        return NormalizedInput(
            query=data["query"],
            source="text"
        )


    async def _handle_voice(self, data: dict) -> NormalizedInput:
        from input.whisper_handler import transcribe_audio

        transcript = await transcribe_audio(data["audio_path"])

        return NormalizedInput(
            query=transcript,
            source="voice"
        )


    async def _handle_image(self, data: dict) -> NormalizedInput:
        from input.vision_handler import extract_from_image

        financial_data = await extract_from_image(data["image_path"])

        return NormalizedInput(
            query=data.get("query", "Analyze this financial document"),
            source="image",
            financial_data=financial_data
        )


    async def _handle_pdf(self, data: dict) -> NormalizedInput:
        from input.pdf_processor import extract_from_pdf

        financial_data = await extract_from_pdf(data["pdf_path"])

        return NormalizedInput(
            query=data.get("query", "Analyze this financial document"),
            source="pdf",
            financial_data=financial_data
        )