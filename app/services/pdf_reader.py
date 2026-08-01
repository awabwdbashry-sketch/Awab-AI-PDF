# app/services/pdf_reader.py
import os
import json

import fitz


# ---------------------------------------------------------------------------
# extract_text() returns STRUCTURED data instead of a plain string, so page
# numbers survive into chunking/citation:
#
#   [{"page": 1, "text": "..."}, {"page": 2, "text": "..."}, ...]
#
# Pages are 1-indexed to match what a human sees in a PDF viewer.
# Empty pages (no extractable text) are skipped.
# ---------------------------------------------------------------------------
def extract_text(pdf_path):

    document = fitz.open(pdf_path)

    pages = []

    for page_number, page in enumerate(document, start=1):

        text = page.get_text()

        if text.strip():

            pages.append({
                "page": page_number,
                "text": text
            })

    document.close()

    return pages


# ---------------------------------------------------------------------------
# Persists the structured page data to disk as JSON so vector_store.py can
# read it back in during create_vector_store().
# ---------------------------------------------------------------------------
def save_pages_json(pages, output_path):

    os.makedirs(
        os.path.dirname(output_path),
        exist_ok=True
    )

    with open(
        output_path,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            pages,
            f,
            ensure_ascii=False
        )
