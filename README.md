# Anki Image Deck Generator

Upload a vocabulary list in any language — Japanese, Korean, Spanish, German, or whatever you're learning — and get back an Anki deck with AI-generated images for every word. The images are designed to make meanings stick: see the word, see the picture, remember it faster.

## How to use

1. Prepare an Excel file (`.xlsx`) with exactly 3 columns: the word in your target language, its meaning in English, and an optional extra column (like Kanji, gender, etc.)
2. Upload it to the app
3. Pick an image style (clear, exaggerated, or artistic)
4. Hit **Generate Deck**, download the `.apkg` file, and import it into Anki

## Run locally

```bash
git clone <repo-url>
cd <repo-folder>
pip install -r requirements_streamlit.txt
```

Create a `.env` file:

```
CLOUDFLARE_ACCOUNT_ID=your_account_id
CLOUDFLARE_API_TOKEN=your_api_token
```

```bash
streamlit run streamlit_app.py
```

Tech stack: Python, Streamlit, Cloudflare Workers AI (Llama 3.1 + FLUX Schnell), genanki

---

Built by an Anki enthusiast
