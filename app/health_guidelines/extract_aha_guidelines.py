import os
import json
import re
from dotenv import load_dotenv
from google import genai
from google.genai.types import Tool, GenerateContentConfig


load_dotenv()

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

AHA_URL = "https://www.heart.org/en/healthy-living/company-collaboration/heart-check-certification/heart-check-in-the-grocery-store/heart-check-food-certification-program-nutrition-requirements"

PROMPT = f"""
You are an information extraction system.

Open this AHA webpage:
{AHA_URL}

Extract ONLY the limits from:
"Standard Certification (FDA-regulated products)"

Return ONLY valid JSON in this exact format:

{{
  "certification_type": "Standard Certification FDA-regulated products",
  "total_fat_g": null,
  "saturated_fat_g": null,
  "saturated_fat_percent_calories": null,
  "trans_fat_g": null,
  "partially_hydrogenated_oils_allowed": null,
  "cholesterol_mg": null,
  "sodium_limits_mg": [],
  "beneficial_nutrients_daily_value_percent": null,
  "beneficial_nutrients": []
}}
"""

tools = [
    Tool(url_context={})
]

response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents=PROMPT,
    config=GenerateContentConfig(
        tools=tools,
        
    )
)

print("Gemini call finished")

raw_text = response.text

if raw_text is None:
    print("ERROR: response.text is None")
    print(response)
    exit()

print("RAW TEXT:")
print(raw_text)

import re

match = re.search(r"\{.*\}", raw_text, re.DOTALL)

if not match:
    print("ERROR: No JSON found")
    print(raw_text)
    exit()

clean_text = match.group(0)

data = json.loads(clean_text)

output_path = "health_guidelines/aha_guidelines.json"

with open(output_path, "w") as file:
    json.dump(data, file, indent=4)

print("Saved:", output_path)
print(json.dumps(data, indent=4))

