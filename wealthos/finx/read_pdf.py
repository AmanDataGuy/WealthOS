"""
# read_pdf.py - read the words on every PDF page

Opens a PDF and returns, for each page, every word with its position on the page (a box: left, top, right,
bottom), its font size and whether it is bold. Everything else in finx is built from this list of words.

The reader is pypdfium2 (Google's PDFium engine, permissive licence): it gives each character with its box, font size
and weight, and the characters are joined into words where PDFium marks a gap.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

SPACES = re.compile(r"\s+")  # any run of spaces, including non-breaking ones
SOFT_MINUS = re.compile(r"\u00ad(?=[\d$(.])|(?<=\d)\u00ad$|^\u00ad+$")  # a soft hyphen standing in for a minus sign


# One word: its box on the page, its text, font size and boldness.
@dataclass
class Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    size: float = 0.0
    bold: bool = False

    # Vertical centre of the word.
    @property
    def yc(self) -> float:
        return (self.y0 + self.y1) / 2


# One page: its number (1 = first page), its size, and all its words.
@dataclass
class PageData:
    no: int
    width: float
    height: float
    words: list[Word] = field(default_factory=list)


# Tidy a word: remove broken-glyph marks, restore line-break hyphens, collapse odd spaces.
# Some PDFs print the minus sign as a soft hyphen: next to a number (or alone, as a dash) it is kept as "-".
def clean(text: str) -> str:
    text = SOFT_MINUS.sub("-", text.replace("�", "").replace("￾", "-"))
    return SPACES.sub(" ", text.replace("­", "")).strip()


# Read every page of a PDF: its size and its words (a password-protected or broken PDF is refused with a reason).
def load_pages(path: str) -> list[PageData]:
    import ctypes

    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    out = []
    try:
        pdf = pdfium.PdfDocument(path)
    except pdfium.PdfiumError as e:
        raise ValueError(f"the PDF could not be opened (password-protected or damaged): {e}") from e
    try:
        for n in range(1, len(pdf) + 1):
            page = pdf[n - 1]
            w, h = page.get_size()
            tp = page.get_textpage()
            left, bottom, right, top = (ctypes.c_double() for _ in range(4))
            words: list[Word] = []
            cur = None  # word being built: [x0, y0, x1, y1, text, size_sum, bold_count, char_count]

            # Finish the word being built and add it to the page.
            def flush():
                nonlocal cur
                if cur and clean(cur[4]):
                    words.append(Word(cur[0], cur[1], cur[2], cur[3], clean(cur[4]), cur[5] / cur[7], cur[6] * 2 >= cur[7]))
                cur = None

            for i in range(tp.count_chars()):
                code = raw.FPDFText_GetUnicode(tp, i)
                ch = chr(code) if code else " "
                if code == 0xFFFE:
                    continue  # hyphen at a line break: the word continues on the next line
                if raw.FPDFText_IsGenerated(tp, i) or ch.isspace() or code == 0xFFFD:
                    flush()  # PDFium inserts a generated space wherever the layout shows a word gap
                    continue
                raw.FPDFText_GetCharBox(tp, i, left, right, bottom, top)
                x0, x1, y0, y1 = left.value, right.value, h - top.value, h - bottom.value
                size, bold = raw.FPDFText_GetFontSize(tp, i), raw.FPDFText_GetFontWeight(tp, i) >= 600
                if cur is not None and abs(y1 - cur[3]) > 0.6 * (cur[3] - cur[1]):
                    flush()  # the character is on another line
                if cur is None:
                    cur = [x0, y0, x1, y1, ch, size, int(bold), 1]
                else:
                    cur[:4] = min(cur[0], x0), min(cur[1], y0), max(cur[2], x1), max(cur[3], y1)
                    cur[4] += ch
                    cur[5] += size
                    cur[6] += int(bold)
                    cur[7] += 1
            flush()
            out.append(PageData(n, float(w), float(h), words))
            tp.close()
            page.close()
    finally:
        pdf.close()
    return out

