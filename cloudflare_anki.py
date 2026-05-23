"""
Build an Anki deck of language-learning vocabulary cards with AI-generated
images, powered end-to-end by Cloudflare Workers AI.

Workflow per row:
  1. Read the English meaning from the Excel.
  2. Ask @cf/meta/llama-3.1-8b-instruct (text) for a short, vivid image prompt.
  3. Send that prompt to @cf/black-forest-labs/flux-1-schnell (image) and save
     the bytes to a temp file.
  4. After all rows are processed, package everything into one .apkg with
     genanki and delete the temp images.
"""

import os
import re
import sys
import shutil
import base64
import tempfile
from pathlib import Path

import pandas as pd
import requests
import genanki
from dotenv import load_dotenv


load_dotenv()

INPUT_DIR = Path("./input")
OUTPUT_DIR = Path("./output")
ROWS_TO_PROCESS = 5

ANKI_MODEL_ID = 1714203855
ANKI_DECK_ID = 1714203856

TEXT_MODEL = "@cf/meta/llama-3.1-8b-instruct"
IMAGE_MODEL = "@cf/black-forest-labs/flux-1-schnell"

CF_API_BASE = "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/"


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        print(f"ERROR: {name} is not set. Add it to your .env file.")
        sys.exit(1)
    return value


CLOUDFLARE_ACCOUNT_ID = require_env("CLOUDFLARE_ACCOUNT_ID")
CLOUDFLARE_API_TOKEN = require_env("CLOUDFLARE_API_TOKEN")

CF_RUN_URL = CF_API_BASE.format(account_id=CLOUDFLARE_ACCOUNT_ID)
CF_HEADERS = {"Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}"}


def sanitize_filename(name: str) -> str:
    """Lowercase, ASCII-only, spaces/punctuation -> underscores."""
    lowered = name.lower()
    cleaned = re.sub(r"[^a-z0-9\-]+", "_", lowered)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_-")
    return cleaned or "card"


def find_excel_file() -> Path:
    candidates = [f for f in INPUT_DIR.glob("*.xlsx") if not f.name.startswith("~$")]
    if not candidates:
        print(f"ERROR: No .xlsx file found in {INPUT_DIR.resolve()}")
        sys.exit(1)
    return candidates[0]


def deck_name_from_path(excel_path: Path) -> str:
    """`Japanese_Vocab.xlsx` -> `Japanese Vocab`."""
    return excel_path.stem.replace("_", " ").replace("-", " ").strip()


def generate_image_prompt(meaning: str) -> str:
    """Ask the Llama text model for a short, vivid image prompt."""
    system = (
        "You write image prompts for memory flashcards. "
        "Given the English meaning of a vocabulary word, write ONE image prompt "
        "that clearly depicts that meaning — a viewer should be able to guess "
        "the meaning from the image alone. A slightly unusual or exaggerated "
        "detail is good for memory, but clarity comes first. "
        "Single paragraph, under 50 words. Output ONLY the prompt itself — no "
        "preamble, no quotes, no labels."
    )
    payload = {
        "messages": [
            {"role": "system", "content": system},
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
        raise RuntimeError(f"Cloudflare text API returned an error: {data}")
    text = data["result"]["response"].strip()
    # Strip wrapping quotes if the model added any.
    if len(text) >= 2 and text[0] in {'"', "'"} and text[-1] == text[0]:
        text = text[1:-1].strip()
    return text


def generate_image(prompt: str, output_path: Path) -> None:
    """Call FLUX-1-schnell on Cloudflare and save the image to output_path.

    Newer Cloudflare responses for this model are JSON with a base64 `image`
    field; older responses are raw image bytes. Handle both.
    """
    resp = requests.post(
        CF_RUN_URL + IMAGE_MODEL,
        headers=CF_HEADERS,
        json={"prompt": prompt},
        timeout=120,
    )
    resp.raise_for_status()

    content_type = resp.headers.get("Content-Type", "")
    if "application/json" in content_type:
        data = resp.json()
        if not data.get("success", True):
            raise RuntimeError(f"Cloudflare image API returned an error: {data}")
        b64 = data["result"]["image"]
        output_path.write_bytes(base64.b64decode(b64))
    else:
        output_path.write_bytes(resp.content)


def build_model(meaning_header: str, extra_header: str) -> genanki.Model:
    return genanki.Model(
        ANKI_MODEL_ID,
        "Cloudflare Vocab With Image",
        fields=[
            {"name": "Word"},
            {"name": "Meaning"},
            {"name": "Extra"},
            {"name": "Image"},
        ],
        templates=[
            {
                "name": "Card 1",
                "qfmt": '<div class="word">{{Word}}</div>',
                "afmt": (
                    "{{FrontSide}}"
                    '<hr id="answer">'
                    '<div class="row">'
                    f'<span class="label">{meaning_header}:</span> '
                    '<span class="value">{{Meaning}}</span>'
                    "</div>"
                    "{{#Extra}}"
                    '<div class="row">'
                    f'<span class="label">{extra_header}:</span> '
                    '<span class="value extra">{{Extra}}</span>'
                    "</div>"
                    "{{/Extra}}"
                    '<div class="image">{{Image}}</div>'
                ),
            }
        ],
        css=(
            ".card {"
            "  background-color: #fafafa;"
            "  color: #111;"
            "  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;"
            "  font-size: 20px;"
            "  line-height: 1.5;"
            "  text-align: center;"
            "  padding: 20px;"
            "}"
            ".word {"
            "  font-size: 22px;"
            "  font-weight: 600;"
            "}"
            ".row {"
            "  margin: 10px auto;"
            "  max-width: 90%;"
            "}"
            ".label {"
            "  color: #888;"
            "  font-weight: 600;"
            "  margin-right: 4px;"
            "}"
            ".value {"
            "  color: #111;"
            "}"
            ".extra {"
            "  font-family: 'Hiragino Mincho ProN', 'Yu Mincho', serif;"
            "}"
            ".image {"
            "  margin-top: 18px;"
            "}"
            "img {"
            "  max-width: 70%;"
            "  border-radius: 12px;"
            "}"
        ),
    )


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    excel_path = find_excel_file()
    deck_name = deck_name_from_path(excel_path)
    deck_filename = sanitize_filename(deck_name) + ".apkg"
    deck_path = OUTPUT_DIR / deck_filename

    print(f"Reading Excel file: {excel_path}")
    df = pd.read_excel(excel_path).head(ROWS_TO_PROCESS)

    if df.shape[1] < 2:
        print("ERROR: Excel must have at least 2 columns (word, meaning).")
        sys.exit(1)

    columns = list(df.columns)
    word_col = columns[0]
    meaning_col = columns[1]
    extra_col = columns[2] if len(columns) >= 3 else None

    print(f"Loaded {len(df)} row(s).")
    print(f"  Column 1 (word):    {word_col}")
    print(f"  Column 2 (meaning): {meaning_col}")
    print(f"  Column 3 (extra):   {extra_col if extra_col else '(none)'}")
    print(f"Deck name: {deck_name}")

    model = build_model(
        meaning_header=str(meaning_col),
        extra_header=str(extra_col) if extra_col else "Extra",
    )
    deck = genanki.Deck(ANKI_DECK_ID, deck_name)

    temp_dir = Path(tempfile.mkdtemp(prefix="cf_anki_"))
    media_files: list[str] = []
    used_filenames: set[str] = set()

    def unique_filename(base: str) -> str:
        candidate = f"{base}.png"
        n = 2
        while candidate in used_filenames:
            candidate = f"{base}_{n}.png"
            n += 1
        used_filenames.add(candidate)
        return candidate

    try:
        for idx, row in df.iterrows():
            word = str(row[word_col]).strip()
            meaning = str(row[meaning_col]).strip()
            if extra_col is not None:
                raw_extra = row[extra_col]
                extra = "" if pd.isna(raw_extra) else str(raw_extra).strip()
            else:
                extra = ""

            print(f"\n[{idx + 1}/{len(df)}] Processing: {word} ({meaning})...")

            image_tag = ""
            try:
                print("  - Asking Llama 3.1 for an image prompt...")
                prompt = generate_image_prompt(meaning)
                print(f"  - Prompt: {prompt}")

                filename = unique_filename(sanitize_filename(meaning))
                image_path = temp_dir / filename

                print(f"  - Generating image with FLUX-1-schnell...")
                generate_image(prompt, image_path)
                print(f"  - Image saved to temp: {image_path.name}")

                image_tag = f'<img src="{filename}">'
                media_files.append(str(image_path))
            except Exception as exc:
                print(f"  - FAILED: {exc}. Skipping this row's image.")

            note = genanki.Note(
                model=model,
                fields=[word, meaning, extra, image_tag],
            )
            deck.add_note(note)

        print(f"\nWriting Anki deck to {deck_path}")
        package = genanki.Package(deck)
        package.media_files = media_files
        package.write_to_file(str(deck_path))
        print(f"Done. Import {deck_path} into Anki (File > Import).")
    finally:
        # Always clean up the temp images, even if something blew up mid-run.
        shutil.rmtree(temp_dir, ignore_errors=True)
        print("Temporary images cleaned up.")


if __name__ == "__main__":
    main()
