import os
import shutil
import datetime
import sys
from utils import load_config, crop_pdf_all_pages

# Force UTF-8 stdout to handle any Unicode characters safely on Windows
sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)

INPUT_DIR = "input"
TEMP_DIR = "temp"
OUTPUT_DIR = "output"
CONFIG_FILE = "config.json"

def main():
    config = load_config(CONFIG_FILE)
    crop_box = config.get("crop", None)
    if not crop_box:
        print("[WARN] No crop box found in config.json. Exiting.")
        return

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    processed_temp_subdirs = set()

    # Ensure input directory exists
    os.makedirs(INPUT_DIR, exist_ok=True)
    
    # Process all PDF files in input directory
    for root, dirs, files in os.walk(INPUT_DIR):
        for file in files:
            if not file.lower().endswith(".pdf"):
                continue

            rel_path = os.path.relpath(root, INPUT_DIR)
            if rel_path == ".":
                rel_path = ""

            input_file = os.path.join(root, file)

            # mirror folder in temp + output
            temp_subdir = os.path.join(TEMP_DIR, rel_path)
            output_subdir = os.path.join(OUTPUT_DIR, rel_path)
            os.makedirs(temp_subdir, exist_ok=True)
            os.makedirs(output_subdir, exist_ok=True)

            name, ext = os.path.splitext(file)
            temp_file = os.path.join(temp_subdir, f"{name}_{timestamp}{ext}")
            output_file = os.path.join(output_subdir, f"{name}_{timestamp}{ext}")

            # process PDF
            try:
                crop_pdf_all_pages(input_file, output_file, crop_box)
                # Also save to temp for backup
                crop_pdf_all_pages(input_file, temp_file, crop_box)
                
                print(f"[DONE] Processed: {input_file}")
                print(f"       → Temp: {temp_file}")
                print(f"       → Output: {output_file}")
                
                processed_temp_subdirs.add(temp_subdir)
            except Exception as e:
                print(f"[ERROR] Failed to process {input_file}: {e}")

    # cleanup temp folders
    for temp_dir in processed_temp_subdirs:
        try:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
                print(f"[CLEANUP] Deleted temp folder: {temp_dir}")
        except Exception as e:
            print(f"[WARN] Failed to delete temp folder {temp_dir}: {e}")

if __name__ == "__main__":
    main()