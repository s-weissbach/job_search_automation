"""Cover letter generation: Claude API call + PDF rendering."""

import io
import json

import anthropic
from fpdf import FPDF
from fpdf.enums import XPos, YPos

_SCHEMA = {
    "type": "object",
    "properties": {
        "company_name": {
            "type": "string",
            "description": "Company name extracted from the job description",
        },
        "hiring_manager": {
            "type": "string",
            "description": "Hiring manager name if mentioned, otherwise empty string",
        },
        "company_address": {
            "type": "string",
            "description": "Company postal address if mentioned, otherwise empty string",
        },
        "job_title": {
            "type": "string",
            "description": "Exact job title from the posting",
        },
        "subject": {
            "type": "string",
            "description": 'Subject line, e.g. "Application for Bioinformatics Scientist — Roche"',
        },
        "main_text": {
            "type": "string",
            "description": (
                "Full letter body from salutation (Dear ...) through closing signature. "
                "Plain text with \\n\\n between paragraphs. Maximum 250 words."
            ),
        },
    },
    "required": [
        "company_name", "hiring_manager", "company_address",
        "job_title", "subject", "main_text",
    ],
    "additionalProperties": False,
}


def generate_draft(
    cv_text: str,
    job_description: str,
    draft_notes: str,
    client: anthropic.Anthropic,
    candidate_name: str = "",
) -> dict:
    """Call Claude to generate a structured cover letter draft."""
    notes_block = (
        f"\n\nNOTES / DRAFT FROM CANDIDATE:\n{draft_notes.strip()}"
        if draft_notes.strip()
        else ""
    )
    name_hint = f" Candidate name: {candidate_name}." if candidate_name else ""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Write a professional cover letter.{name_hint}\n\n"
                    f"CANDIDATE PROFILE:\n{cv_text}\n\n"
                    f"JOB DESCRIPTION:\n{job_description}"
                    f"{notes_block}\n\n"
                    "Requirements for main_text:\n"
                    "- Salutation through closing — no address block, no date\n"
                    "- Maximum 250 words total\n"
                    "- Three paragraphs: (1) specific interest in this role and company, "
                    "(2) 2–3 concrete achievements or skills directly matching the JD, "
                    "(3) brief closing with a call to action\n"
                    "- No generic filler phrases ('I am excited to...', 'I believe I would be...')\n"
                    "- Close with 'Kind regards,' then a blank line then the candidate name\n"
                    "- Extract company_address from the JD if present, otherwise leave empty"
                ),
            }
        ],
        output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
    )

    return json.loads(response.content[0].text)


def generate_pdf(
    sender_info: str,
    address_block: str,
    date_str: str,
    subject: str,
    main_text: str,
) -> bytes:
    """Render a one-page A4 cover letter PDF.

    Font size reduces from 11pt to 9pt until content fits on a single page.
    Returns the smallest fitting PDF as bytes.
    """
    last_bytes = b""
    for font_size in (11.0, 10.5, 10.0, 9.5, 9.0):
        buf = io.BytesIO()
        pdf = _build(sender_info, address_block, date_str, subject, main_text, font_size)
        pdf.output(buf)
        last_bytes = buf.getvalue()
        if pdf.page == 1:
            break
    return last_bytes


def _build(
    sender_info: str,
    address_block: str,
    date_str: str,
    subject: str,
    main_text: str,
    font_size: float,
) -> FPDF:
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_margins(left=25, top=22, right=25)
    pdf.set_auto_page_break(auto=True, margin=22)
    pdf.add_page()

    # Line height: convert pt to mm with 1.45× leading
    lh = round(font_size * 0.353 * 1.45, 2)

    # Sender info — one point smaller than body
    pdf.set_font("Helvetica", size=font_size - 1)
    for line in sender_info.strip().splitlines():
        pdf.cell(0, lh, line.strip(), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(lh * 1.8)

    # Recipient address block
    pdf.set_font("Helvetica", size=font_size)
    for line in address_block.strip().splitlines():
        pdf.cell(0, lh, line.strip(), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(lh * 1.8)

    # Date
    pdf.cell(0, lh, date_str, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(lh * 1.8)

    # Subject line — bold
    pdf.set_font("Helvetica", style="B", size=font_size)
    pdf.cell(0, lh, subject, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(lh * 0.8)

    # Body text — split by paragraph for proper spacing
    pdf.set_font("Helvetica", size=font_size)
    paragraphs = [p.strip() for p in main_text.strip().split("\n\n") if p.strip()]
    for i, para in enumerate(paragraphs):
        pdf.multi_cell(0, lh, para)
        if i < len(paragraphs) - 1:
            pdf.ln(lh * 0.6)

    return pdf
