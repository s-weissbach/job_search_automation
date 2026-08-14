import re


def clean_description(text: str | None) -> str:
    """Collapse whitespace left over from prettified-HTML-to-markdown conversion
    (jobspy runs BeautifulSoup's .prettify() before markdown conversion, which
    bakes indentation and blank lines into the text)."""
    if not text or not isinstance(text, str):
        return text or ""
    lines = [line.rstrip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
