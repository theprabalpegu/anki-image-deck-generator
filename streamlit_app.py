import io
import os
import shutil
import tempfile
from pathlib import Path

import genanki
import pandas as pd
import requests
import streamlit as st

try:
    os.environ["CLOUDFLARE_ACCOUNT_ID"] = st.secrets["CLOUDFLARE_ACCOUNT_ID"]
    os.environ["CLOUDFLARE_API_TOKEN"] = st.secrets["CLOUDFLARE_API_TOKEN"]
except (KeyError, FileNotFoundError):
    pass

try:
    from cloudflare_anki import (
        ANKI_DECK_ID,
        CF_HEADERS,
        CF_RUN_URL,
        TEXT_MODEL,
        build_model,
        generate_image,
        sanitize_filename,
    )
except SystemExit:
    st.error(
        "Cloudflare credentials not found. "
        "Add them to Streamlit secrets (for Cloud) or a .env file (for local)."
    )
    st.stop()

MAX_ROWS = 50
REQUIRED_COLUMNS = {"WORD", "MEANING IN ENGLISH"}

STYLE_PROMPTS = {
    "Clear and simple": (
        "You write image prompts for memory flashcards. "
        "Given the English meaning of a vocabulary word, write ONE image prompt "
        "that clearly and simply depicts that meaning — a viewer should be able "
        "to guess the meaning from the image alone. Keep it clean, minimal, and "
        "easy to understand at a glance. "
        "Single paragraph, under 50 words. Output ONLY the prompt itself — no "
        "preamble, no quotes, no labels."
    ),
    "Slightly exaggerated (better for memory)": (
        "You write image prompts for memory flashcards. "
        "Given the English meaning of a vocabulary word, write ONE image prompt "
        "that clearly depicts that meaning — a viewer should be able to guess "
        "the meaning from the image alone. A slightly unusual or exaggerated "
        "detail is good for memory, but clarity comes first. "
        "Single paragraph, under 50 words. Output ONLY the prompt itself — no "
        "preamble, no quotes, no labels."
    ),
    "Artistic and abstract": (
        "You write image prompts for memory flashcards. "
        "Given the English meaning of a vocabulary word, write ONE image prompt "
        "that depicts that meaning with an artistic, painterly style. Use vivid "
        "colors, interesting compositions, and creative visual metaphors while "
        "keeping the core meaning recognizable. "
        "Single paragraph, under 50 words. Output ONLY the prompt itself — no "
        "preamble, no quotes, no labels."
    ),
}


def generate_image_prompt_styled(meaning: str, style: str) -> str:
    payload = {
        "messages": [
            {"role": "system", "content": STYLE_PROMPTS[style]},
            {"role": "user", "content": f'The word means: "{meaning}"'},
        ]
    }
    resp = requests.post(
        CF_RUN_URL + TEXT_MODEL,
        headers=CF_HEADERS,
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success", True):
        raise RuntimeError(f"Cloudflare text API error: {data}")
    text = data["result"]["response"].strip()
    if len(text) >= 2 and text[0] in {'"', "'"} and text[-1] == text[0]:
        text = text[1:-1].strip()
    return text


st.set_page_config(page_title="Anki Image Deck Generator")

st.markdown(
    """<style>
    .stApp {
        background: linear-gradient(180deg, #1a3a6e 0%, #0d1b2a 100%);
    }
    </style>""",
    unsafe_allow_html=True,
)

st.title("Anki Image Deck Generator")
st.markdown(
    "**Upload your vocabulary list, get an Anki deck with AI-generated images**"
)
st.markdown(
    "Works with any language. Your Excel file must have exactly 3 columns: "
    "**WORD** (in your target language), **MEANING IN ENGLISH**, "
    "and a third column of your choice (like Kanji, Gender, etc.)"
)
st.caption(f"Maximum {MAX_ROWS} words per session.")


def build_sample_excel() -> bytes:
    sample = pd.DataFrame(
        {
            "WORD": ["neko", "inu", "sakana", "tori"],
            "MEANING IN ENGLISH": ["cat", "dog", "fish", "bird"],
            "KANJI": ["猫", "犬", "魚", "鳥"],
        }
    )
    buf = io.BytesIO()
    sample.to_excel(buf, index=False)
    return buf.getvalue()


st.download_button(
    label="Download Sample Excel Sheet",
    data=build_sample_excel(),
    file_name="sample_vocab.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

uploaded_file = st.file_uploader("Upload your vocabulary Excel file", type=["xlsx"])

if uploaded_file is not None:
    if st.session_state.get("_last_file") != uploaded_file.file_id:
        st.session_state["_last_file"] = uploaded_file.file_id
        for key in ("result_apkg", "result_filename", "result_success",
                     "result_total", "result_previews"):
            st.session_state.pop(key, None)

    df = pd.read_excel(uploaded_file)
    columns = list(df.columns)
    col_upper = {str(c).strip().upper(): str(c) for c in columns}

    if len(columns) != 3:
        st.error(
            "Your Excel file must have exactly 3 columns: "
            "WORD, MEANING IN ENGLISH, and one extra column. "
            f"Found {len(columns)} column(s). Please re-upload with the correct format."
        )
    elif not REQUIRED_COLUMNS.issubset(col_upper):
        missing = REQUIRED_COLUMNS - set(col_upper)
        st.error(
            f"Missing required column(s): **{', '.join(sorted(missing))}**. "
            "Your Excel must have columns named WORD and MEANING IN ENGLISH. "
            "Please re-upload with the correct format."
        )
    else:
        word_col = col_upper["WORD"]
        meaning_col = col_upper["MEANING IN ENGLISH"]
        extra_col = next(
            str(c) for c in columns
            if str(c).strip().upper() not in REQUIRED_COLUMNS
        )

        st.subheader("Preview")
        st.dataframe(df.head(5), use_container_width=True)
        st.caption(f"Detected columns: {', '.join(str(c) for c in columns)}")

        total_rows = len(df)
        process_count = min(total_rows, MAX_ROWS)
        df = df.head(process_count)

        if total_rows > MAX_ROWS:
            st.error(
                f"Your file has {total_rows} words. "
                f"Only the first {MAX_ROWS} will be processed — the rest will be ignored."
            )

        st.markdown(
            f"**{process_count} word{'s' if process_count != 1 else ''} detected**"
        )

        style = st.selectbox(
            "Image style", options=list(STYLE_PROMPTS.keys()), index=1
        )

        if st.button("Generate Deck", type="primary", use_container_width=True):

            model = build_model(
                meaning_header=str(meaning_col),
                extra_header=str(extra_col) if extra_col else "Extra",
            )

            deck_name = (
                uploaded_file.name.rsplit(".", 1)[0]
                .replace("_", " ")
                .replace("-", " ")
                .strip()
            )
            deck = genanki.Deck(ANKI_DECK_ID, deck_name)

            temp_dir = Path(tempfile.mkdtemp(prefix="st_anki_"))
            media_files: list[str] = []
            used_filenames: set[str] = set()
            preview_image_bytes: list[bytes] = []
            success_count = 0

            def unique_filename(base: str) -> str:
                candidate = f"{base}.png"
                n = 2
                while candidate in used_filenames:
                    candidate = f"{base}_{n}.png"
                    n += 1
                used_filenames.add(candidate)
                return candidate

            progress_bar = st.progress(0)
            apkg_bytes = None
            deck_filename = ""

            try:
                for i, (idx, row) in enumerate(df.iterrows()):
                    word = str(row[word_col]).strip()
                    meaning = str(row[meaning_col]).strip()
                    extra = ""
                    if extra_col is not None:
                        raw = row[extra_col]
                        if not pd.isna(raw):
                            extra = str(raw).strip()

                    image_tag = ""
                    try:
                        prompt = generate_image_prompt_styled(meaning, style)
                        filename = unique_filename(sanitize_filename(meaning))
                        image_path = temp_dir / filename
                        generate_image(prompt, image_path)
                        image_tag = f'<img src="{filename}">'
                        media_files.append(str(image_path))
                        if len(preview_image_bytes) < 4:
                            preview_image_bytes.append(image_path.read_bytes())
                        success_count += 1
                    except Exception:
                        pass

                    note = genanki.Note(
                        model=model, fields=[word, meaning, extra, image_tag]
                    )
                    deck.add_note(note)
                    pct = int((i + 1) / process_count * 100)
                    progress_bar.progress((i + 1) / process_count, text=f"{pct}%")

                apkg_path = temp_dir / (sanitize_filename(deck_name) + ".apkg")
                package = genanki.Package(deck)
                package.media_files = media_files
                package.write_to_file(str(apkg_path))
                apkg_bytes = apkg_path.read_bytes()
                deck_filename = apkg_path.name

            except Exception as e:
                st.error(f"Something went wrong: {e}")
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

            if apkg_bytes is not None:
                st.session_state["result_apkg"] = apkg_bytes
                st.session_state["result_filename"] = deck_filename
                st.session_state["result_success"] = success_count
                st.session_state["result_total"] = process_count
                st.session_state["result_previews"] = preview_image_bytes
                st.rerun()

        if "result_apkg" in st.session_state:
            s_count = st.session_state["result_success"]
            s_total = st.session_state["result_total"]
            if s_count == 0:
                st.warning(
                    "The deck was created but no images could be generated. "
                    "Cloudflare may be unavailable — try again later."
                )
            else:
                st.success(f"{s_count} of {s_total} cards created with images.")

            st.download_button(
                label="Download Anki Deck (.apkg)",
                data=st.session_state["result_apkg"],
                file_name=st.session_state["result_filename"],
                mime="application/octet-stream",
                type="primary",
                use_container_width=True,
            )

            previews = st.session_state.get("result_previews", [])
            if previews:
                st.markdown("**Sample images from your deck:**")
                cols = st.columns(len(previews))
                for col, img in zip(cols, previews):
                    col.image(img, use_container_width=True)

st.divider()
st.caption("Built by an Anki user")
