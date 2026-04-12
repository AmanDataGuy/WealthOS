# input/pdf_processor.py
#
# Handles PDF input — bank statements, payslips, tax docs etc.
# Converts each page to an image, sends them all to Claude,
# and merges the extracted data into one clean dict.

import anthropic
import base64
import json
import os
from PIL import Image
import fitz  # PyMuPDF — converts PDF pages to images

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


def pdf_to_images(pdf_path: str) -> list[str]:
    """
    Converts each page of a PDF into a PNG image.
    Returns a list of image paths.
    """

    doc = fitz.open(pdf_path)
    image_paths = []

    for page_num in range(len(doc)):
        page = doc[page_num]
        # render page as image
        pix = page.get_pixmap(dpi=150)
        image_path = f"/tmp/page_{page_num}.png"
        pix.save(image_path)
        image_paths.append(image_path)

    return image_paths


def extract_from_page(image_path: str) -> dict:
    """
    Sends one PDF page image to Claude and extracts transactions.
    """

    with open(image_path, "rb") as f:
        image_data = base64.b64encode(f.read()).decode("utf-8")

    response = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=2000,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": image_data
                        }
                    },
                    {
                        "type": "text",
                        "text": """Extract all financial transactions from this page.
                        Return ONLY a JSON object like this:
                        {
                            "transactions": [
                                {
                                    "date": "YYYY-MM-DD",
                                    "description": "transaction details",
                                    "amount": 0.00,
                                    "type": "credit or debit",
                                    "category": "food/transport/shopping/utilities/other"
                                }
                            ],
                            "summary": {
                                "total_credits": 0.00,
                                "total_debits": 0.00
                            }
                        }"""
                    }
                ]
            }
        ]
    )

    return json.loads(response.content[0].text)


def merge_pages(pages_data: list[dict]) -> dict:
    """
    Merges extracted data from multiple PDF pages into one dict.
    """

    all_transactions = []
    total_credits = 0
    total_debits = 0

    for page in pages_data:
        all_transactions.extend(page.get("transactions", []))
        total_credits += page.get("summary", {}).get("total_credits", 0)
        total_debits += page.get("summary", {}).get("total_debits", 0)

    return {
        "transactions": all_transactions,
        "summary": {
            "total_credits": total_credits,
            "total_debits": total_debits,
            "transaction_count": len(all_transactions)
        }
    }


async def extract_from_pdf(pdf_path: str) -> dict:
    """
    Main function — takes a PDF path, processes every page,
    and returns all extracted financial data merged together.

    Args:
        pdf_path: path to the PDF file

    Returns:
        dict with all transactions and summary
    """

    # convert PDF pages to images
    image_paths = pdf_to_images(pdf_path)

    # extract data from each page
    pages_data = []
    for image_path in image_paths:
        page_data = extract_from_page(image_path)
        pages_data.append(page_data)

    # merge all pages into one result
    return merge_pages(pages_data)