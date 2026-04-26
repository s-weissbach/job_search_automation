from pathlib import Path
import anthropic


def load_cv(cv_path: str) -> str:
    path = Path(cv_path)
    if not path.exists():
        raise FileNotFoundError(f"CV not found: {cv_path}")

    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        pages = [page.extract_text() for page in reader.pages]
        return "\n".join(p for p in pages if p)

    return path.read_text(encoding="utf-8")


def compress_cv(cv_text: str, client: anthropic.Anthropic, model: str) -> str:
    """One-time operation: compress raw CV text to a token-efficient YAML profile.

    The compressed form is ~300-500 words vs potentially thousands in a PDF.
    Useful as a reviewed, edited canonical profile for all future runs.
    Caching still applies to whichever form you use — compression just reduces
    the cached token count, lowering cost per cache write.
    """
    response = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": f"""Extract and compress this CV into a token-efficient YAML profile for AI job matching.

Structure (only these keys):
- name: keep name, omit address/email/phone
- skills: list of technical skills, tools, languages, computational methods
- experience:
  - "[Title] at [Org] ([dates]): key achievements in 1-2 lines"
- education:
  - "[Degree], [Field], [Institution] ([year])"
- domains: list of research/industry domain keywords

Target: 300-500 words total. Preserve domain-specific terminology exactly.

CV:
{cv_text}

Return only valid YAML, no markdown fences."""
        }]
    )
    return response.content[0].text.strip()


def save_compressed_cv(content: str, path: str = "cv/cv_compressed.yaml") -> None:
    Path(path).write_text(content, encoding="utf-8")
