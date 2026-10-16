import re
import json
import requests
import fitz  # PyMuPDF
from pathlib import Path

PDF_URL = "https://www.cdc.gov/diabetes/pdfs/managing/Tasty_Recipes_for_People_with_Diabetes-508.pdf"
PDF_FILE = "cdc_diabetes_recipes.pdf"
OUTPUT_JSON = "CDC_diabetes_recipes.json"

RECIPE_TITLES = [
    "SPANISH OMELET",
    "CARIBBEAN FIESTA",
    "TURKEY STEW",
    "CARIBBEAN RED SNAPPER",
    "TWO CHEESE PIZZA",
    "CUBAN BEANS AND RICE",
    "RICE WITH CHICKEN, SPANISH STYLE",
    "POZOLE",
    "MANGO SALSA",
    "AVOCADO TACOS",
    "TROPICAL FRUITS FANTASIA",
]

def download_pdf():
    r = requests.get(PDF_URL, timeout=30)
    r.raise_for_status()
    Path(PDF_FILE).write_bytes(r.content)

def extract_text():
    doc = fitz.open(PDF_FILE)
    text = ""
    for page in doc:
        text += page.get_text("text") + "\n"
    return text

def clean_text(text):
    text = text.replace("\x02", "-")
    text = text.replace("∕", "/")
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def extract_recipe_block(text, title, next_title=None):
    start = text.find(title)
    if start == -1:
        return None

    end = text.find(next_title, start + len(title)) if next_title else len(text)
    if end == -1:
        end = len(text)

    return text[start:end].strip()

def parse_recipe(block, title):
    ingredients = []
    directions = ""
    servings = None
    nutrition = {}

    desc = ""
    if "Ingredients" in block:
        desc = block.split("Ingredients")[0].replace(title, "").strip()

    ing_part = ""
    if "Ingredients" in block and "Directions" in block:
        ing_part = block.split("Ingredients", 1)[1].split("Directions", 1)[0].strip()

    if ing_part:
        ingredients = re.split(r" (?=\d|¼|½|¾|1/|2/|3/|Dash|Pinch|Vegetable|Fresh|black pepper)", ing_part)
        ingredients = [i.strip(" ,") for i in ingredients if len(i.strip()) > 2]

    if "Directions" in block:
        directions = block.split("Directions", 1)[1]
        directions = directions.split("Total Servings")[0].strip()

    match_servings = re.search(r"Total Servings:\s*(\d+)", block)
    if match_servings:
        servings = int(match_servings.group(1))

    nutrition_fields = {
        "calories": r"Calories\s+(\d+)",
        "total_fat": r"Total Fat\s+([\d.]+g)",
        "saturated_fat": r"Saturated Fat\s+([\d.]+g)",
        "cholesterol": r"Cholesterol\s+(\d+mg)",
        "sodium": r"Sodium\s+(\d+mg)",
        "total_carbohydrate": r"Total Carbohydrate\s+(\d+g)",
        "dietary_fiber": r"Dietary Fiber\s+(\d+g)",
        "sugars": r"Sugars\s+(\d+g)",
        "protein": r"Protein\s+(\d+g)",
    }

    for key, pattern in nutrition_fields.items():
        m = re.search(pattern, block)
        nutrition[key] = m.group(1) if m else None

    return {
        "title": title.title(),
        "source": "CDC - Tasty Recipes for People with Diabetes and Their Families",
        "url": PDF_URL,
        "health_goals": ["diabetes"],
        "description": desc,
        "ingredients": ingredients,
        "preparation": directions,
        "servings": servings,
        "nutrition": nutrition
    }

def main():
    download_pdf()
    text = clean_text(extract_text())

    recipes = []

    for i, title in enumerate(RECIPE_TITLES):
        next_title = RECIPE_TITLES[i + 1] if i + 1 < len(RECIPE_TITLES) else None
        block = extract_recipe_block(text, title, next_title)

        if block:
            recipes.append(parse_recipe(block, title))

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(recipes, f, indent=2, ensure_ascii=False)

    print(f"Saved {len(recipes)} recipes to {OUTPUT_JSON}")

if __name__ == "__main__":
    main()