from playwright.sync_api import sync_playwright, TimeoutError
import json
import re
from urllib.parse import urljoin

BASE_URL = "https://calfreshhealthyliving.cdph.ca.gov"
START_URL = "https://calfreshhealthyliving.cdph.ca.gov/en/recipes/Pages/default.aspx"

CATEGORIES = [
    "Breakfast",
    "Desserts",
    "Drinks and Smoothies",
    "Kids Can Cook",
    "Main Dishes",
    "Quick Meals",
    "Salads",
    "Sandwiches and Wraps",
    "Side Dishes",
    "Snacks",
    "Soups and Stews",
]

OUTPUT_FILE = "CalFresh_recipes2.json"


def clean_text(text):
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def safe_goto(page, url):
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)
        return True
    except TimeoutError:
        print("TIMEOUT:", url)
        return False
    except Exception as e:
        print("ERROR opening:", url)
        print("Reason:", e)
        return False


def get_recipe_links_from_category(page, category):
    print("\nCATEGORY:", category)

    if not safe_goto(page, START_URL):
        return []

    try:
        page.get_by_label("CATEGORY").select_option(label=category)
        page.wait_for_timeout(1000)

        page.get_by_role("button", name="Search").click(timeout=10000)
        page.wait_for_timeout(4000)
    except Exception as e:
        print("Could not select category:", category)
        print(e)
        return []

    links = page.locator("a").evaluate_all(
        """
        els => els
            .map(a => a.href)
            .filter(h => h.includes('/en/recipes/Pages/') && h.endsWith('.aspx'))
        """
    )

    links = list(set(links))

    bad_words = [
        "default.aspx",
        "home",
        "beverage-breakdown",
        "drink-more-water",
        "eat-healthy",
        "be-active",
        "get-involved"
    ]

    recipe_links = [
        link for link in links
        if not any(bad in link.lower() for bad in bad_words)
    ]

    print("Recipe links found:", len(recipe_links))
    return recipe_links


def extract_section(text, start_word, end_words):
    start = text.lower().find(start_word.lower())

    if start == -1:
        return ""

    section = text[start + len(start_word):]

    end_positions = []

    for word in end_words:
        pos = section.lower().find(word.lower())
        if pos != -1:
            end_positions.append(pos)

    if end_positions:
        section = section[:min(end_positions)]

    return section.strip()


def split_ingredients(text):
    bad_lines = [
        "INGREDIENTS",
        "SHARE",
        "Calories",
        "Serv"
    ]

    lines = text.split("\n")
    clean_lines = []

    for line in lines:
        line = clean_text(line)

        if not line:
            continue

        if line in bad_lines:
            continue

        if line.isdigit():
            continue

        clean_lines.append(line)

    return clean_lines

def parse_nutrition(text):
    nutrition = {}

    patterns = {
        "calories": r"Calories:\s*([\d]+)",
        "total_fat": r"Total Fat:\s*([\d\.]+\s*g)",
        "saturated_fat": r"Saturated Fat:\s*([\d\.]+\s*g)",
        "trans_fat": r"Trans Fat:\s*([\d\.]+\s*g)",
        "cholesterol": r"Cholesterol:\s*([\d\.]+\s*mg)",
        "sodium": r"Sodium:\s*([\d\.]+\s*mg)",
        "total_carbohydrate": r"Total Carbohydrate:\s*([\d\.]+\s*g)",
        "dietary_fiber": r"Dietary Fiber:\s*([\d\.]+\s*g)",
        "protein": r"Protein:\s*([\d\.]+\s*g)",
    }

    for key, pattern in patterns.items():
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            nutrition[key] = match.group(1)

    return nutrition


def scrape_recipe_details(page, url, categories):
    if not safe_goto(page, url):
        return None

    body_text = page.locator("body").inner_text()

    try:
        title = page.locator("h2").first.inner_text().strip()
    except:
        title = ""

    if not title:
        try:
            title = clean_text(page.locator("h2").first.inner_text())
        except:
            title = "Unknown Recipe"

    ingredients_text = extract_section(
        body_text,
        "INGREDIENTS",
        ["PREPARATION", "DIRECTIONS", "NUTRITIONAL INFORMATION"]
    )

    preparation_text = extract_section(
        body_text,
        "PREPARATION",
        ["NUTRITIONAL INFORMATION", "NUTRITION INFORMATION"]
    )

    nutrition_text = extract_section(
        body_text,
        "NUTRITIONAL INFORMATION",
        ["Source", "Recipe"]
    )

    if not nutrition_text:
        nutrition_text = body_text

    try:
        image_url = page.locator("img").evaluate_all(
            """
            imgs => {
                let found = imgs.find(img =>
                    img.src &&
                    img.src.includes('/recipes/') &&
                    !img.src.includes('logo')
                );
                return found ? found.src : "";
            }
            """
        )
    except:
        image_url = ""

    recipe = {
        "categories": categories,
        "title": title,
        "url": url,
        "image_url": image_url,
        "ingredients": split_ingredients(ingredients_text),
        "preparation": split_ingredients(preparation_text),
        "nutrition": parse_nutrition(nutrition_text)
    }

    print("Saved:", title)
    return recipe


def main():
    all_recipes = []
    seen_urls = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()

        for category in CATEGORIES:
            recipe_links = get_recipe_links_from_category(page, category)

            for url in recipe_links:
                if url in seen_urls:
                    seen_urls[url].append(category)
                    continue

                seen_urls[url] = [category]

                recipe = scrape_recipe_details(page, url, [category])

                if recipe:
                    all_recipes.append(recipe)

                    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                        json.dump(all_recipes, f, indent=4, ensure_ascii=False)

        browser.close()

    for recipe in all_recipes:
        recipe["categories"] = seen_urls.get(recipe["url"], recipe["categories"])

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_recipes, f, indent=4, ensure_ascii=False)

    print("\nDONE")
    print("Total recipes saved:", len(all_recipes))
    print("Saved file:", OUTPUT_FILE)


if __name__ == "__main__":
    main()