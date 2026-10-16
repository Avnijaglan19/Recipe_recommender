import json
import os
import re
from fractions import Fraction
from pathlib import Path
from openai import OpenAI
from flask import render_template, request, redirect, url_for, session, flash
from dotenv import load_dotenv

try:
    from .recipe_validator import validate_recipes
    from .prompts import build_system_prompt, build_user_message
    from .recipe_rules import passes_diet_filter, check_appliance_compatibility
    from .auth import signup_user, login_user, logout_user, login_required, save_recipe_history, get_recipe_history
    from .permissions import staff_required
    from .inventory import (
        extract_items_from_multiple_invoices,
        add_inventory_items,
        get_active_inventory,
        remove_inventory_item,
        get_pantry_item_names,
    )
except ImportError:
    try:
        from app.recipe_validator import validate_recipes
        from app.prompts import build_system_prompt, build_user_message
        from app.recipe_rules import passes_diet_filter, check_appliance_compatibility
        from app.auth import signup_user, login_user, logout_user, login_required, save_recipe_history, get_recipe_history
        from app.permissions import staff_required
        from app.inventory import (
            extract_items_from_multiple_invoices,
            add_inventory_items,
            get_active_inventory,
            remove_inventory_item,
            get_pantry_item_names,
        )
    except ModuleNotFoundError:
        from recipe_validator import validate_recipes
        from prompts import build_system_prompt, build_user_message
        from recipe_rules import passes_diet_filter, check_appliance_compatibility
        from auth import signup_user, login_user, logout_user, login_required, save_recipe_history, get_recipe_history
        from permissions import staff_required
        from inventory import (
            extract_items_from_multiple_invoices,
            add_inventory_items,
            get_active_inventory,
            remove_inventory_item,
            get_pantry_item_names,
        )

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Sending every diet/appliance-eligible recipe to GPT on every request was the
# real cause of slow responses -- a fairly ordinary request still left 150+
# full recipe objects (~55,000 tokens) in the prompt before generation could
# even start. Narrowing to recipes that actually share ingredients with what
# the user picked, capped at a shortlist size, fixes both latency and cost.
MAX_RECIPES_SENT_TO_GPT = 30


def normalize_ingredient_aliases(ingredients):
    """Expand pantry equivalents so 'Basic Spices' behaves like salt + pepper,
    and 'ground pepper' matches the base 'pepper' ingredient, while all yogurt
    variants count as the same pantry item."""
    expanded = []
    for ingredient in ingredients or []:
        value = str(ingredient).strip().lower()
        if value in {"basic spices", "basic spice"}:
            expanded.extend(["salt", "pepper"])
        elif value in {"ground pepper", "black pepper", "pepper flakes"}:
            expanded.append("pepper")
        elif "yogurt" in value:
            expanded.append("yogurt")
        else:
            expanded.append(value)
    return expanded


def count_ingredient_overlap(recipe, user_ingredients, pantry_ingredients=None):
    """How many ingredients this recipe uses that the student either already
    has, OR could grab from the pantry. Pantry items are weighted at half a
    point rather than a full point: a recipe using 3 of the student's own
    ingredients should still outrank one using 3 pantry ingredients and
    nothing the student actually owns, but a recipe that's "almost there"
    on the student's own picks and just needs 1-2 pantry items to complete
    should still surface instead of being filtered out at zero overlap.

    Whole-word matching, same reasoning as the diet filter: a substring check
    would let 'egg' match inside 'eggplant' and inflate the overlap count."""
    if not user_ingredients and not pantry_ingredients:
        return 0

    ingredients = recipe.get("ingredients", [])
    text = " ".join(ingredients).lower() if isinstance(ingredients, list) else str(ingredients).lower()

    normalized_user = normalize_ingredient_aliases(user_ingredients)
    normalized_pantry = normalize_ingredient_aliases(pantry_ingredients)

    own_matches = sum(
        1 for ingredient in normalized_user
        if re.search(rf"\b{re.escape(ingredient.lower())}\b", text)
    )

    pantry_matches = sum(
        1 for ingredient in normalized_pantry
        if re.search(rf"\b{re.escape(ingredient.lower())}\b", text)
    )

    return own_matches + (0.5 * pantry_matches)


def format_decimal_measurement(value: float) -> str:
    if value <= 0.01:
        return "0"
    
    rounded_int = int(round(value))
    if rounded_int != 0 and abs(value - rounded_int) < 0.12:
        return str(int(round(value)))

    best = None
    best_diff = float("inf")
    for denominator in (8, 4, 2):
        candidate_n = round(value * denominator)
        if candidate_n == 0:
            # A tiny positive value shouldn't collapse to zero -- floor it to
            # the smallest usable kitchen fraction for this denominator
            # instead (e.g. 1/8 teaspoon), the same way a real recipe would.
            candidate_n = 1
        candidate_value = candidate_n / denominator
        diff = abs(value - candidate_value)
        if diff < best_diff:
            best_diff = diff
            best = Fraction(candidate_n, denominator)

    if best is not None and best_diff <= 0.125:
        whole = best.numerator // best.denominator
        remainder = best.numerator % best.denominator
        if remainder == 0:
            return str(whole)
        if whole == 0:
            return f"{remainder}/{best.denominator}"
        return f"{whole} {remainder}/{best.denominator}"

    rounded = round(value, 2)
    return f"{rounded:g}"

def format_ingredient_line(line: str) -> str:
    def replace_number(match):
        num = float(match.group(0))
        return format_decimal_measurement(num)

    return re.sub(r"(?<!\d)(\d+\.\d+)(?!\d)", replace_number, line)


def normalize_adjusted_amounts(recipe):
    amounts = recipe.get("adjusted_ingredient_amounts")
    if not isinstance(amounts, list):
        return recipe

    recipe["adjusted_ingredient_amounts"] = [
        format_ingredient_line(str(item)) for item in amounts
    ]
    return recipe


def register_routes(app):
    base_dir = Path(__file__).resolve().parent

    @app.route("/signup", methods=["GET", "POST"])
    def signup():
        if request.method == "GET":
            return render_template("signup.html")

        email = request.form.get("email", "")
        password = request.form.get("password", "")
        first_name = request.form.get("first_name", "")
        last_name = request.form.get("last_name", "")

        ok, result = signup_user(email, password, first_name, last_name)
        if not ok:
            flash(result)
            return render_template("signup.html")

        # Try logging them straight in. If your Supabase project has email
        # confirmation turned on, sign-in will fail until they confirm --
        # in that case, send them to the login page with a clear message.
        logged_in, error = login_user(email, password)
        if logged_in:
            return redirect(url_for("index"))

        flash("Account created! Please check your email to confirm, then log in.")
        return redirect(url_for("login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "GET":
            return render_template("login.html")

        email = request.form.get("email", "")
        password = request.form.get("password", "")

        ok, error = login_user(email, password)
        if not ok:
            flash(error or "Invalid email or password.")
            return render_template("login.html")

        return redirect(url_for("index"))

    @app.route("/logout")
    def logout():
        logout_user()
        return redirect(url_for("login"))

    @app.route("/history")
    @login_required
    def history():
        past_requests = get_recipe_history()
        return render_template("history.html", past_requests=past_requests)

    # ---------- Staff: pantry inventory ----------

    @app.route("/inventory", methods=["GET"])
    @staff_required
    def inventory():
        items = get_active_inventory()
        return render_template("inventory.html", items=items)

    @app.route("/inventory/upload", methods=["POST"])
    @staff_required
    def inventory_upload():
        uploaded_files = request.files.getlist("invoices")
        uploaded_files = [f for f in uploaded_files if f and f.filename]

        if not uploaded_files:
            flash("Please choose at least one PDF invoice to upload.")
            return redirect(url_for("inventory"))

        files_for_extraction = []
        for f in uploaded_files:
            if not f.filename.lower().endswith(".pdf"):
                flash(f"Skipped {f.filename} -- only PDF invoices are supported.")
                continue
            files_for_extraction.append((f.filename, f.read()))

        extracted_items, errors = extract_items_from_multiple_invoices(files_for_extraction)

        for error in errors:
            flash(error)

        if not extracted_items:
            flash("No items could be extracted from the uploaded invoice(s).")
            return redirect(url_for("inventory"))

        return render_template("inventory_review.html", items=extracted_items)

    @app.route("/inventory/confirm", methods=["POST"])
    @staff_required
    def inventory_confirm():
        item_names = request.form.getlist("item_name")
        quantities = request.form.getlist("quantity")
        confirmed_flags = request.form.getlist("confirm")  # values are the row indices that were checked

        confirmed_indices = {int(i) for i in confirmed_flags}

        items_to_save = []
        for i, name in enumerate(item_names):
            if i in confirmed_indices and name.strip():
                items_to_save.append({
                    "item_name": name,
                    "quantity": quantities[i] if i < len(quantities) else "",
                    "source": "invoice",
                })

        add_inventory_items(items_to_save)
        flash(f"Added {len(items_to_save)} item(s) to pantry inventory.")
        return redirect(url_for("inventory"))

    @app.route("/inventory/manual-add", methods=["POST"])
    @staff_required
    def inventory_manual_add():
        item_name = request.form.get("item_name", "").strip()
        quantity = request.form.get("quantity", "").strip()

        if not item_name:
            flash("Please enter an item name.")
            return redirect(url_for("inventory"))

        add_inventory_items([{
            "item_name": item_name,
            "quantity": quantity,
            "source": "manual",
        }])
        flash(f"Added {item_name} to pantry inventory.")
        return redirect(url_for("inventory"))

    @app.route("/inventory/remove/<item_id>", methods=["POST"])
    @staff_required
    def inventory_remove(item_id):
        remove_inventory_item(item_id)
        return redirect(url_for("inventory"))

    @app.route("/", methods=["GET"])
    @login_required
    def index():
        return render_template("index.html", submitted=False)

    @app.route("/results", methods=["POST"])
    @login_required
    def results():
        selected_appliances = (
            request.form.getlist("appliance_type")
            or request.form.getlist("appliances")
            or request.form.getlist("Appliance_type")
        )
        selected_health_issues = (
            request.form.getlist("health_issues")
            or request.form.getlist("health_Issues")
        )

        preferences = {
            "ingredients": request.form.getlist("ingredients"),
            "meal_type": request.form.get("meal_type"),
            "servings": request.form.get("servings"),
            "diet_type": request.form.get("diet_type"),
            "appliances": selected_appliances,
            "available_appliances": selected_appliances,
            "cost_range": request.form.get("cost_range"),
            "health_issues": selected_health_issues,
            "cuisine": request.form.get("cuisine"),
        }

        health_goals = [
            goal.lower().strip()
            for goal in preferences.get("health_issues", [])
        ]

        active_health_guidelines = {}

        # Load CDC diabetes guideline only if diabetes is selected
        if "diabetes" in health_goals:
            cdc_guidelines_path = base_dir / "cdc_guidelines.json"
            if cdc_guidelines_path.exists():
                with cdc_guidelines_path.open("r", encoding="utf-8") as file:
                    active_health_guidelines["cdc_diabetes_guidelines"] = json.load(file)

        # Load AHA guideline only if heart-related goal is selected
        if any(goal in health_goals for goal in ["heart health", "hypertension", "high cholesterol"]):
            aha_guidelines_path = base_dir / "aha_guidelines.json"
            if aha_guidelines_path.exists():
                with aha_guidelines_path.open("r", encoding="utf-8") as file:
                    active_health_guidelines["aha_guidelines"] = json.load(file)

        calfresh_path = base_dir / "scrape_calfresh" / "CalFresh_recipes2.json"
        cdc_path = base_dir / "scrape_pdf_cdc" / "CDC_diabetes_recipes.json"
        snaped_path = base_dir / "scrape_snaped" / "SNAPEd_recipes.json"

        with calfresh_path.open("r", encoding="utf-8") as file:
            calfresh_recipes = json.load(file)

        if cdc_path.exists():
            with cdc_path.open("r", encoding="utf-8") as file:
                cdc_recipes = json.load(file)
        else:
            cdc_recipes = []

        if snaped_path.exists():
            with snaped_path.open("r", encoding="utf-8") as file:
                snaped_recipes = json.load(file)
        else:
            snaped_recipes = []

        recipes_json = calfresh_recipes + cdc_recipes + snaped_recipes
        recipes_json = [
            recipe
            for recipe in recipes_json
            if passes_diet_filter(recipe, preferences.get("diet_type"))
        ]

        # Appliance filtering happens deterministically in Python now, not by
        # asking the LLM to reason about substitutions every time. Compatible
        # recipes get annotated with an appliance_note so GPT can just state
        # it rather than figure it out itself; incompatible ones are dropped
        # from the pool entirely.
        appliance_filtered = []
        for recipe in recipes_json:
            is_compatible, note = check_appliance_compatibility(
                recipe, preferences.get("available_appliances")
            )
            if is_compatible:
                if note:
                    recipe["appliance_note"] = note
                appliance_filtered.append(recipe)
        recipes_json = appliance_filtered

        # Narrow to a shortlist by actual ingredient overlap before this ever
        # reaches GPT. This is the retrieve-then-rank pattern: cheap Python
        # narrows candidates, the LLM only ranks/explains a manageable set.
        #
        # Pantry stock is fetched here -- BEFORE shortlisting, not after --
        # so a recipe the student could complete with 1-2 pantry items
        # actually gets a nonzero overlap score and survives the "> 0" cutoff
        # below. Previously pantry stock was only checked after recipes were
        # already picked, so a recipe using zero of the student's own
        # ingredients was filtered out here regardless of how well the
        # pantry could have covered it.
        try:
            pantry_items = get_pantry_item_names()
        except Exception:
            pantry_items = []  # e.g. Supabase not configured; fail open silently

        user_ingredients = normalize_ingredient_aliases(preferences.get("ingredients", []))
        for recipe in recipes_json:
            recipe["_overlap_count"] = count_ingredient_overlap(recipe, user_ingredients, pantry_items)

        recipes_json = [r for r in recipes_json if r["_overlap_count"] > 0]
        recipes_json.sort(key=lambda r: r["_overlap_count"], reverse=True)
        recipes_json = recipes_json[:MAX_RECIPES_SENT_TO_GPT]

        for recipe in recipes_json:
            del recipe["_overlap_count"]

        servings_value = preferences.get("servings")
        try:
            requested_servings = int(servings_value)
        except (TypeError, ValueError):
            if isinstance(servings_value, str) and "more" in servings_value.lower():
                requested_servings = 6
            else:
                requested_servings = 1

        for recipe in recipes_json:
            original_servings = recipe.get("servings", requested_servings)

            if original_servings:
                recipe["scale_factor"] = requested_servings / original_servings
            else:
                recipe["scale_factor"] = 1

            recipe["requested_servings"] = requested_servings

        system_prompt = build_system_prompt(
            requested_servings=preferences.get("servings", "1"),
            active_health_guidelines=active_health_guidelines,
        )
        user_message = build_user_message(preferences, recipes_json)

        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            response_format={"type": "json_object"},
            temperature=0.2,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        )

        result = response.choices[0].message.content
        try:
            result_json = json.loads(result)
        except json.JSONDecodeError:
            result_json = {"recipes": []}

        ai_recipes = [normalize_adjusted_amounts(recipe) for recipe in result_json.get("recipes", [])]

        recommended_path = base_dir / "recommended_recipes.json"
        with recommended_path.open("w", encoding="utf-8") as f:
            json.dump(ai_recipes, f, indent=4)

        validated_recipes = validate_recipes(
            ai_recipes,
            preferences["ingredients"],
            diet_type=preferences.get("diet_type"),
            available_appliances=preferences.get("available_appliances"),
        )

        # Sort validated recipes by score (highest first) so top matches appear
        # at the top of the results page. Scores may be numeric or strings like
        # "8/10", so parse defensively.
        def _score_key(r):
            v = r.get("score", 0)
            try:
                return float(v)
            except Exception:
                try:
                    return float(str(v).split("/")[0].strip())
                except Exception:
                    return 0

        validated_recipes.sort(key=_score_key, reverse=True)

        # Cross-reference each recipe's missing ingredients against current
        # pantry stock. Done here in plain Python (word-boundary matching,
        # same approach as count_ingredient_overlap) rather than asking GPT --
        # it's a deterministic lookup, so there's no reason to spend extra
        # tokens or risk GPT inventing a match that isn't really there.
        #
        # Pantry items are stored the way staff typed/uploaded them, which is
        # usually plural ("Onions", "Carrots", "Diced Tomatoes"); recipes
        # usually ask for the singular ("onion", "carrot", "tomato"). Naive
        # word-boundary matching treats those as different words entirely, so
        # every match silently failed. _singularize() strips common English
        # plural endings before comparing, so "onion" can match "Onions".
        def _singularize(word: str) -> str:
            if word.endswith("ies") and len(word) > 4:
                return word[:-3] + "y"          # berries -> berry
            if word.endswith("oes") and len(word) > 4:
                return word[:-2]                # potatoes -> potato, tomatoes -> tomato
            if word.endswith("es") and len(word) > 4:
                return word[:-2]                # dishes -> dish
            if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
                return word[:-1]                # onions -> onion, eggs -> egg
            return word

        def _normalize_phrase(phrase: str) -> str:
            words = re.findall(r"[a-z]+", phrase.lower())
            return " ".join(_singularize(w) for w in words)

        # Reuse the SAME pantry_items fetched earlier for shortlisting --
        # no need to hit Supabase a second time in the same request.
        normalized_pantry_items = [_normalize_phrase(p) for p in pantry_items]

        if normalized_pantry_items:
            for recipe in validated_recipes:
                needed = recipe.get("additional_ingredients_needed") or []
                available_at_pantry = []
                for needed_ingredient in needed:
                    needed_norm = _normalize_phrase(str(needed_ingredient))
                    for pantry_norm in normalized_pantry_items:
                        if re.search(rf"\b{re.escape(pantry_norm)}\b", needed_norm) or \
                           re.search(rf"\b{re.escape(needed_norm)}\b", pantry_norm):
                            available_at_pantry.append(needed_ingredient)
                            break
                if available_at_pantry:
                    recipe["pantry_available_ingredients"] = available_at_pantry

        validated_path = base_dir / "validated_recipes.json"
        with validated_path.open("w", encoding="utf-8") as f:
            json.dump(validated_recipes, f, indent=4)

        save_recipe_history(preferences, validated_recipes)

        final_result = json.dumps(
            {"recipes": validated_recipes},
            indent=4,
        )

        return render_template(
            "results.html",
            preferences=preferences,
            result=final_result,
            recipes=validated_recipes,
        )