from google import genai
from PIL import Image
import json
import re
import os

# 1. Setup Client
API_KEY = "GOOGLE_API_KEY_REMOVED"
client = genai.Client(api_key=API_KEY)

def extract_json_from_text(text):
    try:
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        return json.loads(text)
    except Exception as e:
        print(f"JSON Error: {e}")
        return None

def crop_charts_from_image(image_path, output_dir="cropped_charts"):
    # Check if file exists
    if not os.path.exists(image_path):
        print(f"Error: File '{image_path}' not found in current directory.")
        return

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    print("Loading image...")
    img = Image.open(image_path)
    
    # Fix RGBA to RGB for JPEG compatibility
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    print("Sending to Gemini API...")
    
    prompt = """
    Find all the charts, graphs, or signal plots in this image.
    Return ONLY a valid JSON array of arrays: [[ymin, xmin, ymax, xmax], ...]
    Use normalized coordinates (0-1000).
    """

    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=[img, prompt]
    )
    
    coordinates = extract_json_from_text(response.text)
    
    if not coordinates:
        print("No coordinates found.")
        return

    print(f"Found {len(coordinates)} charts. Processing...")

    width, height = img.size
    
    for i, box in enumerate(coordinates):
        try:
            ymin, xmin, ymax, xmax = box
            
            left = (xmin * width) / 1000
            top = (ymin * height) / 1000
            right = (xmax * width) / 1000
            bottom = (ymax * height) / 1000
            
            # Padding
            padding = 15
            left = max(0, left - padding)
            top = max(0, top - padding)
            right = min(width, right + padding)
            bottom = min(height, bottom + padding)
            
            cropped_img = img.crop((left, top, right, bottom))
            output_path = os.path.join(output_dir, f"chart_{i+1}.jpg")
            cropped_img.save(output_path, "JPEG")
            print(f"Saved: {output_path}")
            
        except Exception as e:
            print(f"Error cropping index {i}: {e}")

# Run
if __name__ == "__main__":
    # Ensure this matches your filename exactly
    target_file = "image1.jpg" 
    crop_charts_from_image(target_file)