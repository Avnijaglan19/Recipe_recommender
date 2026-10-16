from app.recipe_rules import _is_pantry_default, count_real_missing_ingredients
from app.routes import normalize_ingredient_aliases, count_ingredient_overlap


def test_basic_spices_and_ground_pepper_aliases():
    assert normalize_ingredient_aliases(["Basic Spices", "ground pepper"]) == [
        "salt",
        "pepper",
        "pepper",
    ]

    recipe = {"ingredients": ["chicken", "pepper", "garlic"]}
    assert count_ingredient_overlap(recipe, ["Basic Spices"]) == 1.0
    assert _is_pantry_default("ground pepper") is True
    assert _is_pantry_default("pepper") is True

    assert normalize_ingredient_aliases(["Yogurt", "vanilla yogurt", "strawberry yogurt", "low-fat yogurt"]) == [
        "yogurt",
        "yogurt",
        "yogurt",
        "yogurt",
    ]
    assert _is_pantry_default("vanilla yogurt") is True
    assert _is_pantry_default("strawberry yogurt") is True
    assert _is_pantry_default("low-fat yogurt") is True
    assert _is_pantry_default("plain greek yogurt") is True

    recipe = {"additional_ingredients_needed": ["vanilla yogurt", "strawberry yogurt", "low-fat yogurt", "banana"]}
    assert count_real_missing_ingredients(recipe) == 1
