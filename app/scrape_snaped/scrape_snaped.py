import json
import re
import time
import requests
from bs4 import BeautifulSoup
 
BASE = "https://snaped.fns.usda.gov"
 
SEASONAL_PAGES = {
    "Spring": f"{BASE}/nutrition-education/snap-ed-recipes/spring-recipes",
    "Summer": f"{BASE}/nutrition-education/snap-ed-recipes/summer-recipes",
    "Fall":   f"{BASE}/resources/nutrition-education-materials/recipes/fall-recipes",
    "Winter": f"{BASE}/nutrition-education/snap-ed-recipes/winter-recipes",
}
 
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 "
                  "recipe-generator-scraper/1.0"
}
 
REQUEST_DELAY = 1.5  # seconds between page fetches, be polite to a .gov server
 
OUTPUT_FILE = "SNAPEd_recipes.json"
 
 
def clean_text(text):
    if not text:
        return ""
    return text.strip()
 
 
def parse_servings(text):
    match = re.search(r"Makes\s+(\d+)\s+serving", text, re.IGNORECASE)
    return int(match.group(1)) if match else None
 
 
def get_soup(url, session, debug=False):
    resp = session.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
 
    if debug:
        print(f"    status: {resp.status_code}, bytes: {len(resp.text)}")
        with open("_debug_last_page.html", "w", encoding="utf-8") as f:
            f.write(resp.text)
        # quick sanity checks so we can see what's actually in the response
        print("    contains 'Ingredients':", "Ingredients" in resp.text)
        print("    contains 'myplate.gov':", "myplate.gov" in resp.text)
        print("    <h3> count:", resp.text.count("<h3"))
        print("    <h2> count:", resp.text.count("<h2"))
 
        # dump the actual markup of every h3 so we can see the real structure
        debug_soup = BeautifulSoup(resp.text, "html.parser")
        print("    ---- h3 tags found ----")
        for i, h3 in enumerate(debug_soup.find_all("h3")):
            print(f"    [{i}] {h3}")
        print("    ------------------------")
 
    return BeautifulSoup(resp.text, "html.parser")
 
 
def parse_seasonal_page(season, url, session, debug=False):
    soup = get_soup(url, session, debug=debug)
    recipes = []
 
    # Walk the page as a flat, in-document-order stream of the tags we care
    # about. find_all() returns elements in document order regardless of
    # nesting depth, so this is robust to whatever container div wraps each
    # recipe block -- unlike sibling-walking, which broke because the
    # description/ingredients/directions are nested inside a wrapper div,
    # not flat siblings of the <h3>.
    flat = soup.find_all(["h3", "h4", "p", "ul", "ol", "hr", "img"])
 
    current = None       # the recipe dict currently being built
    description_parts = []
    section = None
    pending_image = ""
 
    def finalize():
        nonlocal current, description_parts
        if current and current["ingredients"]:
            current["description"] = " ".join(description_parts).strip()
            recipes.append(current)
        current = None
        description_parts = []
 
    for tag in flat:
        if tag.name == "hr":
            finalize()
            continue
 
        if tag.name == "img":
            src = tag.get("src", "")
            if src:
                pending_image = BASE + src if src.startswith("/") else src
            continue
 
        if tag.name == "h3":
            link = tag.find("a")
            if link and "myplate.gov" in (link.get("href") or ""):
                finalize()  # in case a recipe block wasn't hr-terminated
                current = {
                    "title": clean_text(link.get_text()),
                    "url": url,
                    "source": "USDA SNAP-Ed",
                    "categories": [season],
                    "image_url": pending_image,
                    "servings": None,
                    "ingredients": [],
                    "preparation": [],
                    "nutrition": {},
                    "myplate_url": link["href"],
                }
                section = "description"
            # non-recipe h3 (nav headings, "SNAP-Ed Connection" footer, etc.)
            # -- just ignored, current recipe (if any) stays open until <hr>
            continue
 
        if current is None:
            continue  # not currently inside a recipe block
 
        if tag.name == "h4":
            heading = tag.get_text(strip=True).lower()
            if "ingredient" in heading:
                section = "ingredients"
            elif "direction" in heading:
                section = "directions"
            continue
 
        text = clean_text(tag.get_text(" ", strip=True))
        if not text or text.lower().startswith("for nutrition information"):
            continue
 
        if tag.name == "p" and text.lower().startswith("makes") and "serving" in text.lower():
            current["servings"] = parse_servings(text)
        elif tag.name == "p" and section == "description":
            description_parts.append(text)
        elif tag.name in ("ul", "ol"):
            items = [clean_text(li.get_text(" ", strip=True)) for li in tag.find_all("li")]
            if section == "ingredients":
                current["ingredients"].extend(items)
            elif section == "directions":
                current["preparation"].extend(items)
        elif tag.name == "p" and section == "ingredients":
            for line in tag.get_text("\n", strip=True).split("\n"):
                line = clean_text(line)
                if line:
                    current["ingredients"].append(line)
        elif tag.name == "p" and section == "directions":
            current["preparation"].append(text)
 
    finalize()  # catch the last recipe if the page doesn't end with <hr>
 
    return recipes
 
 
def main():
    session = requests.Session()
    all_recipes = []
 
    for season, url in SEASONAL_PAGES.items():
        print(f"Fetching {season} recipes from {url} ...")
        try:
            # debug=True only on the first page, so we get one diagnostic dump
            recipes = parse_seasonal_page(season, url, session, debug=(season == "Spring"))
        except requests.RequestException as e:
            print(f"  FAILED to fetch {season} page: {e}")
            continue
 
        print(f"  Found {len(recipes)} recipes")
        all_recipes.extend(recipes)
        time.sleep(REQUEST_DELAY)
 
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_recipes, f, indent=4, ensure_ascii=False)
 
    print(f"\nDONE. {len(all_recipes)} total recipes saved to {OUTPUT_FILE}")
 
 
if __name__ == "__main__":
    main()
 