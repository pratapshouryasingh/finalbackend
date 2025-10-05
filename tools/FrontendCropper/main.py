import os
import shutil
import datetime
import sys
from utils import load_config, crop_pdf_all_pages
import fitz  # PyMuPDF

# Force UTF-8 stdout to handle any Unicode characters safely on Windows
sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)

INPUT_DIR = "input"
TEMP_DIR = "temp"
OUTPUT_DIR = "output"
CONFIG_FILE = "config.json"

def merge_pdfs_in_folder(pdf_files, output_path):
    """Merge multiple PDF files into a single PDF file using PyMuPDF."""
    if not pdf_files:
        return False
        
    if len(pdf_files) == 1:
        # If only one file, just copy it
        shutil.copy2(pdf_files[0], output_path)
        return True
    
    try:
        # Create a new PDF document
        merged_doc = fitz.open()
        
        # Add all pages from all PDF files
        for pdf_file in sorted(pdf_files):
            try:
                src_doc = fitz.open(pdf_file)
                merged_doc.insert_pdf(src_doc)
                src_doc.close()
            except Exception as e:
                print(f"[WARN] Failed to process {pdf_file}: {e}")
                continue
        
        # Save the merged PDF
        if merged_doc.page_count > 0:
            merged_doc.save(output_path)
            merged_doc.close()
            return True
        else:
            merged_doc.close()
            print("[ERROR] No valid pages found to merge")
            return False
            
    except Exception as e:
        print(f"[ERROR] Failed to merge PDFs: {e}")
        if 'merged_doc' in locals():
            merged_doc.close()
        return False

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
    
    # Process all subdirectories in input directory
    for root, dirs, files in os.walk(INPUT_DIR):
        # Get PDF files in current directory
        pdf_files = [os.path.join(root, f) for f in files if f.lower().endswith(".pdf")]
        
        if not pdf_files:
            continue

        rel_path = os.path.relpath(root, INPUT_DIR)
        if rel_path == ".":
            rel_path = ""

        # Create folder names for merged file
        if rel_path:
            folder_name = os.path.basename(root)
        else:
            folder_name = "merged"
        
        # mirror folder in temp + output
        temp_subdir = os.path.join(TEMP_DIR, rel_path)
        output_subdir = os.path.join(OUTPUT_DIR, rel_path)
        os.makedirs(temp_subdir, exist_ok=True)
        os.makedirs(output_subdir, exist_ok=True)

        # Create merged file names
        merged_filename = f"{folder_name}_{timestamp}.pdf"
        temp_merged_file = os.path.join(temp_subdir, merged_filename)
        output_merged_file = os.path.join(output_subdir, merged_filename)

        # Merge PDFs first
        print(f"[MERGE] Merging {len(pdf_files)} PDF files in: {root}")
        if not merge_pdfs_in_folder(pdf_files, temp_merged_file):
            print(f"[ERROR] Failed to merge PDFs in {root}")
            continue

        # Process the merged PDF
        try:
            crop_pdf_all_pages(temp_merged_file, output_merged_file, crop_box)
            
            print(f"[DONE] Processed merged PDF: {output_merged_file}")
            print(f"       Source PDFs: {len(pdf_files)} files")
            print(f"       → Temp: {temp_merged_file}")
            print(f"       → Output: {output_merged_file}")
            
            processed_temp_subdirs.add(temp_subdir)
        except Exception as e:
            print(f"[ERROR] Failed to process merged PDF {temp_merged_file}: {e}")

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
