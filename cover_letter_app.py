"""
Cover letter generator — Streamlit UI.

Usage:
    streamlit run cover_letter_app.py
"""

import os
from datetime import date
from pathlib import Path

import anthropic
import streamlit as st
import yaml
from dotenv import load_dotenv

from src.cover_letter import generate_draft, generate_pdf
from src.cv_processor import load_cv

load_dotenv()

st.set_page_config(page_title="Cover Letter Generator", layout="wide", page_icon="📝")
st.title("📝 Cover Letter Generator")

# ── Session state defaults ────────────────────────────────────────────────────
_DEFAULTS: dict[str, str] = {
    "sender_info": (
        "Your Name\n"
        "Your Street, City, Country\n"
        "your.email@example.com  |  +XX XXX XXX XXXX"
    ),
    "address_block": "Company Name\nHiring Manager\nStreet\nCity, Country",
    "subject": "Application for [Position]",
    "main_text": "",
}
for _k, _v in _DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

# Auto-populate sender name from compressed CV if still at default
if st.session_state.sender_info == _DEFAULTS["sender_info"]:
    _cv_yaml = Path("cv/cv_compressed.yaml")
    if _cv_yaml.exists():
        try:
            _data = yaml.safe_load(_cv_yaml.read_text())
            _name = (
                _data.get("name")
                or _data.get("full_name")
                or (_data.get("personal") or {}).get("name")
                or (_data.get("candidate") or {}).get("name")
                or ""
            )
            if _name:
                _lines = st.session_state.sender_info.splitlines()
                st.session_state.sender_info = "\n".join([_name] + _lines[1:])
        except Exception:
            pass

# ── Layout ─────────────────────────────────────────────────────────────────────
left, right = st.columns([5, 4], gap="large")

# ── LEFT: inputs ───────────────────────────────────────────────────────────────
with left:
    st.subheader("Inputs")

    # CV selection
    cv_options = [
        p for p in ["cv/cv_compressed.yaml", "cv/cv.pdf", "cv/cv.txt"]
        if Path(p).exists()
    ]
    if not cv_options:
        st.warning(
            "No CV found. Place your CV at `cv/cv.pdf` "
            "or run `python run_search.py --compress-cv` first."
        )
        cv_path = None
    else:
        cv_path = st.selectbox("CV file", cv_options)

    # Optional: load job description from score store
    score_store = Path("results/.score_store.csv")
    job_description = ""
    if score_store.exists():
        try:
            import pandas as pd
            _df = pd.read_csv(score_store)
            _df = _df[_df["fit_score"].notna() & (_df["fit_score"] >= 50)]
            _df = _df.sort_values("fit_score", ascending=False).head(30)
            if not _df.empty:
                _opts = ["— paste manually —"] + [
                    f"{r['title']} @ {r['company']}  ({int(r['fit_score'])}%)"
                    for _, r in _df.iterrows()
                ]
                _sel = st.selectbox("Load from score store (optional)", _opts)
                if _sel != "— paste manually —":
                    _idx = _opts.index(_sel) - 1
                    _row = _df.iloc[_idx]
                    job_description = str(_row.get("description", ""))
        except Exception:
            pass

    job_description = st.text_area(
        "Job description",
        value=job_description,
        height=220,
        placeholder="Paste the full job description here…",
    )
    draft_notes = st.text_area(
        "Draft / notes (optional)",
        height=90,
        placeholder="Bullet points, a rough draft, or specific things to highlight…",
    )

    generate_btn = st.button(
        "✨ Generate with Claude",
        type="primary",
        disabled=not (cv_path and job_description.strip()),
    )

    if generate_btn:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            st.error("ANTHROPIC_API_KEY not found — add it to your .env file.")
        else:
            with st.spinner("Generating with claude-sonnet-4-6…"):
                cv_text = load_cv(cv_path)
                client = anthropic.Anthropic(api_key=api_key)
                candidate_name = st.session_state.sender_info.splitlines()[0].strip()
                draft = generate_draft(
                    cv_text, job_description, draft_notes, client, candidate_name
                )

            # Update editable fields from Claude's output
            addr_parts = [
                draft.get("company_name", ""),
                draft.get("hiring_manager", ""),
                draft.get("company_address", ""),
            ]
            st.session_state.address_block = "\n".join(p for p in addr_parts if p)
            st.session_state.subject = draft.get("subject", st.session_state.subject)
            st.session_state.main_text = draft.get("main_text", "")
            st.rerun()

# ── RIGHT: edit & export ───────────────────────────────────────────────────────
with right:
    st.subheader("Edit")

    st.text_area("Sender info", key="sender_info", height=85)

    col_a, col_b = st.columns(2)
    with col_a:
        st.text_area("Recipient address", key="address_block", height=105)
    with col_b:
        letter_date = st.date_input("Date", value=date.today())
        st.text_input("Subject line", key="subject")

    st.text_area("Cover letter text", key="main_text", height=290)

    # Word count feedback
    wc = len(st.session_state.main_text.split()) if st.session_state.main_text else 0
    if wc > 280:
        st.warning(f"⚠️ {wc} words — may overflow one page (aim for ≤ 250)")
    elif wc > 0:
        colour = "green" if wc <= 250 else "orange"
        st.caption(f":{colour}[{wc} words]")

    st.divider()

    # Always render PDF so download is instant — only if there's text
    if st.session_state.main_text.strip():
        pdf_bytes = generate_pdf(
            sender_info=st.session_state.sender_info,
            address_block=st.session_state.address_block,
            date_str=letter_date.strftime("%d %B %Y"),
            subject=st.session_state.subject,
            main_text=st.session_state.main_text,
        )
        company_slug = (
            st.session_state.address_block.splitlines()[0]
            .strip()
            .lower()
            .replace(" ", "_")[:30]
        )
        st.download_button(
            label="⬇️ Download PDF",
            data=pdf_bytes,
            file_name=f"cover_letter_{company_slug}_{letter_date.isoformat()}.pdf",
            mime="application/pdf",
            type="primary",
        )
        st.caption("Font auto-scales 11 → 9 pt to fit one A4 page.")
    else:
        st.button("⬇️ Download PDF", disabled=True)
        st.caption("Generate or type a cover letter to enable PDF export.")
