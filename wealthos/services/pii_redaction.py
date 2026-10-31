"""
# pii_redaction.py - hide personal details in document text

Replaces PAN, Aadhaar, phone numbers, account numbers, emails, PIN codes and a few label-based names ("Dear X,",
"To," blocks, "S/O:" / "D/O:") with placeholders before the text is stored or sent to an AI model.
Plain regex, not name recognition: names are only caught in those labelled spots, and street / city text is kept.
"""

import re

_NAME = r"[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){1,3}"

# Order matters: specific formats (email, PAN, phone, Aadhaar) run before the generic long-digit "account" pattern,
# so a phone or Aadhaar number keeps its own label. Name patterns run last (they work on capture groups).
_PATTERNS = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}"), "[REDACTED_EMAIL]"),
    (re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b"), "[REDACTED_PAN]"),
    # Indian mobile: optional +91 / 0 prefix, then a 10-digit number starting 6-9.
    (re.compile(r"(?<!\d)(?:\+91[-\s]?|0)?[6-9]\d{9}(?!\d)"), "[REDACTED_PHONE]"),
    # Generic international: + followed by 7-15 digits (with optional separators).
    (re.compile(r"\+\d{1,3}[-\s]?\d{2,4}[-\s]?\d{3,4}[-\s]?\d{3,4}\b"), "[REDACTED_PHONE]"),
    # Aadhaar-style 12-digit number, optionally grouped in 4s with spaces.
    (re.compile(r"\b\d{4}\s\d{4}\s\d{4}\b"), "[REDACTED_AADHAAR]"),
    # Bank/loan account numbers: long unbroken digit runs (9-18 digits).
    # Legitimate rupee figures like "1,20,000" have commas and are shorter, so
    # they don't collide with this pattern.
    (re.compile(r"(?<!\d)\d{9,18}(?!\d)"), "[REDACTED_ACCOUNT]"),
    # Indian PIN code, as it actually appears at the end of an address line
    # (e.g. "Uttar Pradesh – 201301") — dash/en-dash prefix narrows this to
    # the address-suffix context instead of any bare 6-digit number.
    (re.compile(r"[\-–]\s*\d{6}\b"), "[REDACTED_PINCODE]"),
]

# Label-based name redaction: replace only the captured name group, keep the
# label ("Dear ", "S/O:", etc.) so the surrounding sentence still reads fine.
_NAME_PATTERNS = [
    re.compile(rf"(Dear\s+)({_NAME})(?=[,.]?\s)"),
    re.compile(rf"(To,\s*\n\s*)({_NAME})(?=,)"),
    re.compile(rf"([SD]/O:?\s*)({_NAME})"),
]


# Replace Indian-financial-document personal details in `text` with placeholders.
def redact_pii(text: str) -> str:
    if not text:
        return text
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    for pattern in _NAME_PATTERNS:
        text = pattern.sub(r"\1[REDACTED_NAME]", text)
    return text


if __name__ == "__main__":
    cases = [
        ("Contact me at john.doe@example.com for details.",
         "Contact me at [REDACTED_EMAIL] for details."),
        ("PAN: ABCDE1234F on file.",
         "PAN: [REDACTED_PAN] on file."),
        ("Call +91 9876543210 or 9876543210 anytime.",
         "Call [REDACTED_PHONE] or [REDACTED_PHONE] anytime."),
        ("Aadhaar 1234 5678 9012 verified.",
         "Aadhaar [REDACTED_AADHAAR] verified."),
        ("Loan A/C No: 123456789012 outstanding.",
         "Loan A/C No: [REDACTED_ACCOUNT] outstanding."),
        ("EMI amount due: ₹1,20,000 this month.",
         "EMI amount due: ₹1,20,000 this month."),  # currency figure untouched
        ("Dear Aman Sharma, your statement is ready.",
         "Dear [REDACTED_NAME], your statement is ready."),
        ("To,\nAman Sharma,\nB-14, Sector 18",
         "To,\n[REDACTED_NAME],\nB-14, Sector 18"),
        ("S/O: Rajesh Sharma, B-14, Sector 18,\nNoida, Uttar Pradesh – 201301",
         "S/O: [REDACTED_NAME], B-14, Sector 18,\nNoida, Uttar Pradesh [REDACTED_PINCODE]"),
    ]
    for src, expected in cases:
        actual = redact_pii(src)
        assert actual == expected, f"FAILED for {src!r}\n  got:      {actual!r}\n  expected: {expected!r}"
    print("pii_redaction self-check passed.")
