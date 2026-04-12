# input/vision_handler.py
#
# Handles image input — receipts, salary slips, screenshots etc.
# Sends the image to Claude and asks it to extract
# all financial data and return it as structured JSON.

import anthropic
import base64
import json
import os

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


async def extract_from_image(image_path: str) -> dict:
    """
    Takes an image path, sends it to Claude Vision,
    and returns extracted financial data as a dict.

    Args:
        image_path: path to the image file (jpg, png etc.)

    Returns:
        dict with extracted financial info
    """

    # read and encode the image to base64
    with open(image_path, "rb") as f:
        image_data = base64.b64encode(f.read()).decode("utf-8")

    # figure out the file type
    extension = image_path.split(".")[-1].lower()
    media_type_map = {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "gif": "image/gif",
        "webp": "image/webp"
    }
    media_type = media_type_map.get(extension, "image/jpeg")

    # send to Claude and ask for structured extraction
    response = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=1000,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_data
                        }
                    },
                    {
                        "type": "text",
                        "text": """Extract all financial data from this image.
                        Return ONLY a JSON object with these fields (use null if not found):
                        {
                            "merchant": "store or company name",
                            "amount": 0.00,
                            "date": "YYYY-MM-DD",
                            "category": "food/transport/shopping/utilities/other",
                            "description": "brief description of what this is"
                        }"""
                    }
                ]
            }
        ]
    )

    # parse the JSON response
    raw_text = response.content[0].text
    return json.loads(raw_text)