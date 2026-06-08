import os
import re
import time
from google import genai
from PIL import Image

# 1. Setup
API_KEY = "GOOGLE_API_KEY_REMOVED"
client = genai.Client(api_key=API_KEY)

def process_notes_with_crops(image_path, output_dir="my_notes"):
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    print("Opening image and calling Gemini...")
    img = Image.open(image_path)
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    
    # پرامپت شما (نسخه اصلاح شده در بالا را اینجا قرار دهید یا از فایل بخوانید)
    with open("prompt.txt", "r", encoding="utf-8") as f:
        system_prompt = f.read()

    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=[img, system_prompt]
    )
    
    final_markdown = response.text
    width, height = img.size

    # 2. یافتن تمام مختصات با الگو: [[ymin, xmin, ymax, xmax]]
    coord_pattern = r'\[\[(\d+),\s*(\d+),\s*(\d+),\s*(\d+)\]\]'
    matches = list(re.finditer(coord_pattern, final_markdown))

    if not matches:
        print("No diagrams detected by Gemini.")
    else:
        print(f"Detected {len(matches)} diagrams. Cropping...")

    # 3. پردازش تک‌تک مختصات یافته شده در متن
    # از آخر به اول جایگزین می‌کنیم تا ایندکس‌های متن به هم نریزد
    for match in reversed(matches):
        try:
            ymin, xmin, ymax, xmax = map(int, match.groups())
            
            # تبدیل مختصات
            left = (xmin * width) / 1000
            top = (ymin * height) / 1000
            right = (xmax * width) / 1000
            bottom = (ymax * height) / 1000
            
            # کراپ کردن
            padding = 10
            crop_box = (max(0, left-padding), max(0, top-padding), min(width, right+padding), min(height, bottom+padding))
            cropped_img = img.crop(crop_box)
            
            # ایجاد نام فایل یونیک (مثلاً بر اساس زمان)
            file_id = int(time.time() * 1000)
            filename = f"image_{file_id}.jpg"
            save_path = os.path.join(output_dir, filename)
            
            cropped_img.save(save_path, "JPEG")
            
            # جایگزینی مختصات در متن با فرمت درخواستی شما ![[filename]]
            replacement_text = f"![[{filename}]]"
            final_markdown = final_markdown[:match.start()] + replacement_text + final_markdown[match.end():]
            
            print(f"Saved and linked: {filename}")
            time.sleep(0.01) # برای جلوگیری از تشابه نام فایل در زمان‌های بسیار سریع
            
        except Exception as e:
            print(f"Error processing a crop: {e}")

    # 4. ذخیره فایل نهایی مارک‌داون
    md_filename = os.path.join(output_dir, "final_notes.md")
    with open(md_filename, "w", encoding="utf-8") as f:
        f.write(final_markdown)
    
    print(f"\nDone! Markdown file saved at: {md_filename}")

# Run
if __name__ == "__main__":
    process_notes_with_crops("image1.jpg")