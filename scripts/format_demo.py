from fractions import Fraction
import re


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


if __name__ == '__main__':
    samples = [
        "0.33 pound boneless, skinless chicken breasts, cut into large chunks",
        "0.33 large onion, chopped",
        "0.33 (14 1/2-ounce) can diced tomatoes",
        "0.11 cup tomato paste",
        "0.67 stalks celery, chopped",
        "0.75 teaspoons garlic powder",
        "0.5 cups broccoli florets",
        "Mix 0.33 can diced tomatoes with 0.11 cup paste and cook for 10 minutes."
    ]

    print("Before -> After")
    for s in samples:
        print(f"{s} -> {format_ingredient_line(s)}")
