"""
Deterministic diet and appliance rules, shared between routes.py (pre-filtering
the recipe pool before it reaches GPT) and recipe_validator.py (re-checking
GPT's actual output afterward). Living in one place means both checks are
guaranteed to agree -- a recipe can't pass the pre-filter under one definition
of "vegan" and then get re-checked under a different one.
"""

import re

VEGAN_FORBIDDEN = [
    "egg", "eggs", "milk", "cheese", "butter", "cream", "yogurt",
    "honey", "chicken", "beef", "pork", "fish", "salmon", "tuna",
    "shrimp", "crab", "lobster", "turkey", "gelatin"
]

VEGETARIAN_FORBIDDEN = [
    "chicken", "beef", "pork", "fish", "salmon", "tuna",
    "shrimp", "crab", "lobster", "turkey", "eggs"
]


def _contains_whole_word(text, word):
    """Whole-word match so 'egg' doesn't match inside 'eggplant', etc.
    A plain substring check was the source of real false positives --
    eggplant recipes were wrongly excluded from vegan results."""
    return re.search(rf"\b{re.escape(word)}\b", text) is not None


def passes_diet_filter(recipe, diet_type):
    diet_type = (diet_type or "").lower().strip()

    ingredients = recipe.get("ingredients", [])
    if isinstance(ingredients, list):
        text = " ".join(ingredients).lower()
    else:
        text = str(ingredients).lower()

    if diet_type == "vegan":
        return not any(_contains_whole_word(text, item) for item in VEGAN_FORBIDDEN)

    if diet_type in ["veg", "vegetarian"]:
        return not any(_contains_whole_word(text, item) for item in VEGETARIAN_FORBIDDEN)

    if diet_type in ["non veg", "non-veg", "nonvegetarian"]:
        return True

    return True


# --- Appliance matching -----------------------------------------------------
# Recipes don't carry a structured "appliances needed" field, so it's inferred
# from the preparation text. Substitutions mirror what the prompt used to ask
# the LLM to reason about on its own -- doing it here means it's applied the
# same way every time, instead of depending on the model remembering the rule.

APPLIANCE_PATTERNS = {
    "Oven": [r"\boven\b", r"\bbak(e|ed|ing)\b", r"\broast(ed|ing)?\b"],
    "Stove": [
        r"\bstove(top)?\b", r"\bskillet\b", r"\bsaut[ée]\b", r"\bsimmer\b",
        r"\bpan[- ]fry\b", r"\bfrying pan\b", r"\bsaucepan\b", r"\bboil\b",
        r"\bcook over\b", r"\bwarm(ing)? (the )?oil\b", r"\bheat(ing)? (the )?oil\b",
        # generic "pan" counts as stovetop, but not "baking pan"/"sheet pan"/
        # "cake pan" which are oven bakeware, not stovetop cookware
        r"(?<!baking )(?<!sheet )(?<!cake )\bpan\b",
    ],
    "Microwave": [r"\bmicrowave\b"],
    "Blender": [r"\bblend(er)?\b", r"\bpuree\b", r"\bfood processor\b"],
    "Air fryer": [r"\bair[- ]fryer\b"],
    "Grill": [r"\bgrill(ed|ing)?\b"],
}

# "Stove" is deliberately absent from this dict: virtually every kitchen has
# a stovetop, frying pan, or skillet, whatever the form happens to call it
# ("Stove", "Stovetop", "Frying Pan" have all been used at different points),
# so treating it as a real gate was both unrealistic and fragile to exact
# string matching between the form and this file. It's assumed available
# unconditionally -- see check_appliance_compatibility, which discards it
# from the required set before any comparison happens.
#
# It's also removed as a listed substitute for Oven/Microwave/Grill/Air
# fryer: previously, "no oven, but has stove" silently passed ANY oven
# recipe with an adapted note, which meant Oven never really gated anything
# once Stove was in play. Since Stove is now unconditionally available,
# leaving it as a substitute here would make Oven/Microwave/Grill/Air fryer
# meaningless too. Those four are meant to genuinely gate on what the
# student actually selected.
APPLIANCE_SUBSTITUTES = {
    "Oven": [],
    "Microwave": [],
    "Grill": [],
    "Air fryer": [],
    "Stove": [],
    "Blender": [],
}


def infer_required_appliances(recipe):
    """Scan preparation text for appliance keywords. Returns an empty set for
    no-cook recipes (salads, etc.), which are compatible with any kitchen."""
    steps = recipe.get("preparation", [])
    text = " ".join(steps).lower() if isinstance(steps, list) else str(steps).lower()

    required = set()
    for appliance, patterns in APPLIANCE_PATTERNS.items():
        if any(re.search(pattern, text) for pattern in patterns):
            required.add(appliance)
    return required


def check_appliance_compatibility(recipe, available_appliances):
    """Returns (is_compatible, note). Stove is assumed to always be
    available (see note above) and is never a reason to reject a recipe.
    Blender-only requirements are always compatible too (hand tools assumed
    available). Oven/Microwave/Grill/Air fryer genuinely gate: missing means
    rejected, since Stove no longer counts as a substitute for them."""
    available = set(available_appliances or [])
    required = infer_required_appliances(recipe)
    required.discard("Stove")  # always assumed available, never gates

    missing = required - available
    if not missing:
        return True, None

    substitution_notes = []
    for appliance in missing:
        if appliance == "Blender":
            substitution_notes.append("blender steps done by hand (fork/whisk/masher)")
            continue

        substitutes = APPLIANCE_SUBSTITUTES.get(appliance, [])
        usable_substitute = next((s for s in substitutes if s in available), None)
        if usable_substitute:
            substitution_notes.append(f"{appliance} steps adapted to {usable_substitute}")
        else:
            return False, f"requires {appliance}, no available substitute"

    return True, "; ".join(substitution_notes)


# ingredient count -- a recipe with 5 missing ingredients could still be
# labeled "10/10". These items are assumed to always be on hand (per the
# prompt's own DEFAULT_PANTRY_RULE), so they should never count against a
# recipe even if GPT's own "additional ingredients needed" list wrongly
# includes them.
DEFAULT_PANTRY_ITEMS = [
    "salt", "pepper", "cumin", "chili powder", "garlic powder",
    "vegetable oil", "oil", "sugar",
    "yogurt", "plain yogurt", "vanilla yogurt", "strawberry yogurt",
    "low-fat yogurt", "low fat yogurt", "greek yogurt",
]


def _is_pantry_default(ingredient_name):
    text = str(ingredient_name).lower()

    # A user who checks "Yogurt" implicitly means they have any yogurt variant,
    # including vanilla, strawberry, low-fat, and Greek yogurt. Treat all of
    # these as the same pantry item so they never get counted as missing.
    if "yogurt" in text:
        return True

    return any(_contains_whole_word(text, item) for item in DEFAULT_PANTRY_ITEMS)


def count_real_missing_ingredients(recipe):
    """The number of genuinely missing ingredients, excluding pantry defaults
    GPT may have mistakenly listed anyway."""
    missing_list = recipe.get("additional_ingredients_needed", [])
    if not isinstance(missing_list, list):
        return 0
    return sum(1 for item in missing_list if not _is_pantry_default(str(item)))


def compute_ingredient_score(recipe):
    """Deterministic score from the actual missing-ingredient count, following
    the same tiers the prompt describes but enforced in code instead of
    trusted from the model's own text output."""
    missing_count = count_real_missing_ingredients(recipe)

    if missing_count == 0:
        return 10
    if missing_count == 1:
        return 8
    if missing_count in (2, 3):
        return 6
    if missing_count in (4, 5):
        return 4
    return 2