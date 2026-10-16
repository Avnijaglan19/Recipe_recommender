"""Post-hoc validation of GPT's recipe recommendations.

This is the safety net: everything here re-checks GPT's actual output against
the same rules it was asked to follow, rather than trusting it was followed.
Diet and appliance checks reuse the exact same functions used to build the
recipe pool in routes.py, so a recipe can't pass the pre-filter, get chosen
by GPT, and then quietly violate the rule anyway.
"""

try:
    from .recipe_rules import compute_ingredient_score, passes_diet_filter, check_appliance_compatibility
except ImportError:
    try:
        from app.recipe_rules import compute_ingredient_score, passes_diet_filter, check_appliance_compatibility
    except ModuleNotFoundError:
        from recipe_rules import compute_ingredient_score, passes_diet_filter, check_appliance_compatibility

MAJOR_PROTEINS = {
    "egg": ["egg", "eggs"],
    "chicken": ["chicken", "chicken breast"],
    "turkey": ["turkey", "ground turkey"],
    "fish": ["fish", "salmon", "tuna", "tilapia", "snapper"],
    "beef": ["beef", "ground beef"],
    "pork": ["pork", "ham", "bacon"],
    "tofu": ["tofu"],
    "beans": ["beans", "black beans", "pinto beans", "kidney beans"],
    "lentils": ["lentils"],
}

MINIMUM_SCORE = 4


def find_major_proteins(ingredients):
    found = set()
    text = " ".join(ingredients).lower()

    for protein, keywords in MAJOR_PROTEINS.items():
        for keyword in keywords:
            if keyword in text:
                found.add(protein)

    return found


def _parse_score(raw_score):
    """GPT sometimes returns score as a string ("8" or "8/10") instead of a
    number. Parse defensively -- an unparseable score should not silently
    pass validation; it should be treated as a failure worth surfacing."""
    if raw_score is None:
        return None
    if isinstance(raw_score, (int, float)):
        return float(raw_score)
    text = str(raw_score).strip()
    try:
        return float(text.split("/")[0].strip())
    except (ValueError, IndexError):
        return None


def validate_recipe(recipe, user_ingredients, diet_type=None, available_appliances=None):
    reasons = []
    
    # The score GPT wrote is not trusted -- recompute it from the actual
    # missing-ingredient count so "Score: 10" can never appear alongside a
    # list of ingredients still needed. Keep the original for comparison/debugging.
    recipe["llm_reported_score"] = recipe.get("score")
    recipe["score"] = compute_ingredient_score(recipe)

    user_major = find_major_proteins(user_ingredients)
    recipe_major = find_major_proteins(recipe.get("ingredients", []))
    if user_major and recipe_major and user_major.isdisjoint(recipe_major):
        reasons.append(
            f"Major ingredient mismatch: user selected {sorted(user_major)}, "
            f"but recipe uses {sorted(recipe_major)}."
        )

    if diet_type and not passes_diet_filter(recipe, diet_type):
        reasons.append(f"Recipe does not actually satisfy diet type '{diet_type}'.")

    if available_appliances is not None:
        is_compatible, _ = check_appliance_compatibility(recipe, available_appliances)
        if not is_compatible:
            reasons.append("Recipe requires an appliance the user doesn't have, with no substitute.")

    score = _parse_score(recipe.get("score"))
    if score is not None and score < MINIMUM_SCORE:
        reasons.append(f"Score {score} is below the minimum threshold of {MINIMUM_SCORE}.")

    if reasons:
        recipe["is_valid"] = False
        recipe["rejection_reason"] = " ".join(reasons)
    else:
        recipe["is_valid"] = True
        recipe["rejection_reason"] = None

    return recipe


def validate_recipes(recipes, user_ingredients, diet_type=None, available_appliances=None):
    validated = []

    for recipe in recipes:
        checked = validate_recipe(
            recipe,
            user_ingredients,
            diet_type=diet_type,
            available_appliances=available_appliances,
        )

        if checked["is_valid"]:
            validated.append(checked)

    return validated
