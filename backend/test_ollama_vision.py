import base64
import requests
from pathlib import Path

OLLAMA_BASE_URL = "http://localhost:11434"
VISION_MODEL = "llava"

# Change this path to your actual screenshot/image path
image_path = Path(r"C:\Users\osama\OneDrive\New folder\trading strateges\AI GENRATED\GBPUSD_2026-05-04_12-38-34_dfa02.png")

if not image_path.exists():
    raise FileNotFoundError(f"Image not found: {image_path}")

image_base64 = base64.b64encode(image_path.read_bytes()).decode("utf-8")
		
response = requests.post(
    f"{OLLAMA_BASE_URL}/api/generate",
    json={
        "model": VISION_MODEL,
        "prompt": "Describe this trading chart image briefly. Mention trend direction, candles, support, resistance, and any visible order block or demand/supply area.",
        "images": [image_base64],
        "stream": False,
    },
    timeout=180,
)

response.raise_for_status()
data = response.json()

print("\n--- OLLAMA VISION RESPONSE ---\n")
print(data.get("response", "No response returned"))