# services/pii_redaction.py
# Regex-based PII redaction for personal financial documents (loan statements,
# EMI receipts) before their text is chunked/embedded/stored in Qdrant or sent
# to an LLM. Indian-financial-document context: PAN, Aadhaar, Indian mobile
# numbers, bank/loan account numbers, emails, and (added after a live test on
# a real loan statement surfaced the gap) the borrower's name and PIN code.
#
# ponytail: regex-based, not full NER. Name detection here is deliberately
# narrow — it only catches names in the two label-based spots these lender
# documents reliably use ("Dear X," greetings, "To," blocks, "S/O:"/"D/O:"
# father's/mother's name) rather than trying to find every name anywhere in
# free text, which needs real NER to do without a high false-positive rate.
# Considered nltk's built-in NE chunker (already a transitive dependency,
# no new package needed) instead, but its data packages aren't installed
# locally, it needs a ~50MB download on first use, and its English-news-
# trained model is mediocre on Indian names — real fragility for a solo
# project. If this project ever needs full free-text name/address coverage,
# swap in a real NER library (e.g. presidio) — not worth it today.
# Street/city/state text is NOT redacted (a city or state name alone isn't
# meaningfully identifying, and pattern-matching a full street address
# reliably without over-matching legitimate content is its own project).

import re

# Order matters: redact narrow/specific formats (email, PAN, phone, Aadhaar)
# before the generic long-digit-run "account number" catch-all, so a phone or
# Aadhaar number gets its own clearer label instead of being swallowed by the
# generic account-number pattern first. Name patterns run last since they
# operate on capture groups, not the whole match.
_NAME = r"[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){1,3}"

_PATTERNS = [
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}"), "[REDACTED_EMAIL]"),
    ("PAN", re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b"), "[REDACTED_PAN]"),
    # Indian mobile: optional +91 / 0 prefix, then a 10-digit number starting 6-9.
    ("PHONE_INDIA", re.compile(r"(?<!\d)(?:\+91[-\s]?|0)?[6-9]\d{9}(?!\d)"), "[REDACTED_PHONE]"),
    # Generic international: + followed by 7-15 digits (with optional separators).
    ("PHONE_INTL", re.compile(r"\+\d{1,3}[-\s]?\d{2,4}[-\s]?\d{3,4}[-\s]?\d{3,4}\b"), "[REDACTED_PHONE]"),
    # Aadhaar-style 12-digit number, optionally grouped in 4s with spaces.
    ("AADHAAR", re.compile(r"\b\d{4}\s\d{4}\s\d{4}\b"), "[REDACTED_AADHAAR]"),
    # Bank/loan account numbers: long unbroken digit runs (9-18 digits).
    # Legitimate rupee figures like "1,20,000" have commas and are shorter, so
    # they don't collide with this pattern.
    ("ACCOUNT", re.compile(r"(?<!\d)\d{9,18}(?!\d)"), "[REDACTED_ACCOUNT]"),
    # Indian PIN code, as it actually appears at the end of an address line
    # (e.g. "Uttar Pradesh – 201301") — dash/en-dash prefix narrows this to
    # the address-suffix context instead of any bare 6-digit number.
    ("PINCODE", re.compile(r"[\-–]\s*\d{6}\b"), "[REDACTED_PINCODE]"),
]

# Label-based name redaction: replace only the captured name group, keep the
# label ("Dear ", "S/O:", etc.) so the surrounding sentence still reads fine.
_NAME_PATTERNS = [
    re.compile(rf"(Dear\s+)({_NAME})(?=[,.]?\s)"),
    re.compile(rf"(To,\s*\n\s*)({_NAME})(?=,)"),
    re.compile(rf"([SD]/O:?\s*)({_NAME})"),
]


def redact_pii(text: str) -> str:
    """Replace common Indian-financial-document PII in `text` with placeholders.

    Applied once to freshly-extracted document text, before chunking/embedding,
    so raw PII never reaches Qdrant storage or an LLM prompt.
    """
    if not text:
        return text
    for _name, pattern, replacement in _PATTERNS:
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
