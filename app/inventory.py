"""
Pantry inventory: staff upload weekly invoices (PDFs), GPT extracts line
items, staff reviews/confirms before anything is saved. Confirmed items
live in Supabase's pantry_inventory table and are cross-referenced against
each recipe's `additional_ingredients_needed` when generating student
recommendations (see routes.py).

Nothing here auto-publishes GPT's extraction straight to students -- an
OCR/vision misread (wrong quantity, mangled product name) would otherwise
propagate into recipe recommendations unchecked. The review step in
routes.py is a deliberate safety net, not an extra click for its own sake.
"""

import base64
import json
import os
from io import BytesIO

from openai import OpenAI

try:
    from .auth import get_supabase, get_user_client, _retry_on_expired_jwt
except ImportError:
    try:
        from app.auth import get_supabase, get_user_client, _retry_on_expired_jwt
    except ModuleNotFoundError:
        from auth import get_supabase, get_user_client, _retry_on_expired_jwt
try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

EXTRACTION_SYSTEM_PROMPT = """You are reading a food pantry supplier invoice image.

Extract every distinct food/grocery line item you can find. For each item, return:
- "item_name": a clean, simple ingredient name a home cook would recognize
  (e.g. "Chicken Breast", not "CHKN BRST BNLS 40LB CS")
- "quantity": the quantity/case count as it appears, as a short string (e.g. "40 lb", "2 cases", "1")
- "raw_line": the original invoice line text, unmodified, so a human can verify your read

Skip non-food lines (delivery fees, taxes, account numbers, page totals).
If you cannot confidently read a line, still include it with your best guess
and set "low_confidence": true on that item.

Respond ONLY with JSON in this exact shape, nothing else:
{
  "items": [
    {"item_name": "...", "quantity": "...", "raw_line": "...", "low_confidence": false}
  ]
}
"""


def _pdf_to_page_images(pdf_bytes: bytes, max_pages: int = 6):
    """Render a PDF's pages to PNG images (as base64 data URLs) for GPT vision.

    Capped at max_pages: invoices are rarely longer than a few pages, and
    capping keeps a single upload from blowing up token usage/cost if
    someone accidentally uploads a huge multi-page PDF.
    """
    if fitz is None:
        raise RuntimeError(
            "PyMuPDF (fitz) isn't installed. Run: pip install PyMuPDF"
        )

    images = []
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    for page_index in range(min(len(doc), max_pages)):
        page = doc.load_page(page_index)
        pix = page.get_pixmap(dpi=200)
        png_bytes = pix.tobytes("png")
        b64 = base64.b64encode(png_bytes).decode("utf-8")
        images.append(f"data:image/png;base64,{b64}")
    doc.close()
    return images


def extract_items_from_invoice(pdf_bytes: bytes, filename: str = "invoice.pdf"):
    """Run one PDF through GPT vision and return (ok, items_or_error)."""
    try:
        page_images = _pdf_to_page_images(pdf_bytes)
    except Exception as exc:
        return False, f"Could not read {filename}: {exc}"

    if not page_images:
        return False, f"{filename} appears to have no pages."

    content = [
        {"type": "text", "text": f"Invoice file: {filename}. Extract the line items."}
    ]
    for img in page_images:
        content.append({"type": "image_url", "image_url": {"url": img}})

    try:
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            response_format={"type": "json_object"},
            temperature=0,
            messages=[
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
        )
        result = json.loads(response.choices[0].message.content)
    except Exception as exc:
        return False, f"GPT could not process {filename}: {exc}"

    items = result.get("items", [])
    for item in items:
        item["source_file"] = filename
    return True, items


def extract_items_from_multiple_invoices(files):
    """files: list of (filename, pdf_bytes). Returns (all_items, errors)."""
    all_items = []
    errors = []
    for filename, pdf_bytes in files:
        ok, result = extract_items_from_invoice(pdf_bytes, filename)
        if ok:
            all_items.extend(result)
        else:
            errors.append(result)
    return all_items, errors


def add_inventory_items(items: list):
    """Save confirmed items (from invoice review or manual entry) to Supabase.

    Each item: {"item_name": str, "quantity": str, "source": "invoice"|"manual"}
    """
    if not items:
        return
    rows = [
        {
            "item_name": item["item_name"].strip(),
            "quantity": item.get("quantity", "").strip(),
            "source": item.get("source", "manual"),
        }
        for item in items
        if item.get("item_name", "").strip()
    ]
    if rows:
        try:
            get_user_client().table("pantry_inventory").insert(rows).execute()
        except Exception as exc:
            from .auth import _retry_on_expired_jwt
            if "JWT expired" in str(exc) or "PGRST303" in str(exc):
                _retry_on_expired_jwt(
                    lambda: get_user_client().table("pantry_inventory").insert(rows).execute(),
                    on_failure=lambda: None,
                )
            else:
                raise


def get_active_inventory():
    """Current pantry stock, most recently added first."""
    

    def _fetch():
        result = (
            get_user_client()
            .table("pantry_inventory")
            .select("*")
            .order("created_at", desc=True)
            .execute()
        )
        return result.data or []

    return _retry_on_expired_jwt(_fetch, on_failure=lambda: [])


def remove_inventory_item(item_id: str):
    

    def _delete():
        get_user_client().table("pantry_inventory").delete().eq("id", item_id).execute()

    _retry_on_expired_jwt(_delete, on_failure=lambda: None)


def get_pantry_item_names():
    """Just the item names, lowercased, for cheap matching against recipe
    ingredient lists elsewhere (routes.py)."""
    return [item["item_name"].lower() for item in get_active_inventory()]