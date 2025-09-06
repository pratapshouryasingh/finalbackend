import os
import sys
import json
import argparse
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed
from tempfile import TemporaryDirectory
from pdfrw import PdfReader, PdfWriter
from shutil import copy

from utils import (
    check_status,
    check_input_file,
    pdf_merger,
    convert_pdf_to_string,
    extract_data,
    pdf_whitespace,
    pdf_cropper,
    create_count_excel,
)

# ---------------------- Config Loader ----------------------
def load_config(config_path=None):
    """Load config.json if provided, otherwise use defaults."""
    default_config = {
        "sku_sort": True,
        "courier_sort": False,
        "soldBy_sort": False,
        "add_date_on_top": False,
        "keep_invoice": True,
        "sku_order_count": False
    }

    if config_path and os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                user_config = json.load(f)
            # Merge defaults + user overrides
            return {**default_config, **user_config}
        except Exception as e:
            print(f"[WARNING] Failed to load config {config_path}: {e}", file=sys.stderr)
            return default_config
    return default_config


# ---------------------- Process Folder ----------------------
def process_folder(input_path, output_path, config):
    folder_name = os.path.basename(input_path)
    print(f"\n=== Processing folder: {folder_name} ===")

    try:
        with TemporaryDirectory() as temp_path:
            os.makedirs(output_path, exist_ok=True)

            # Get PDFs
            all_pdfs = check_input_file(input_path)
            if not all_pdfs:
                print(f"[INFO] No PDFs found in {input_path}")
                return

            # Merge PDFs
            merged_pdf = os.path.join(temp_path, "merged.pdf")
            pdf_merger(all_pdfs, merged_pdf)
            print(f"[INFO] Merge Completed -> {merged_pdf}")

            # Convert merged PDF to text pages & extract data
            all_page = convert_pdf_to_string(merged_pdf)
            df = extract_data(all_page)
            if df.empty:
                print(f"[WARNING] No data extracted from PDFs in {folder_name}")
                return

            # Clean dataframe
            for col in ["sku", "courier", "soldBy"]:
                if col in df.columns:
                    df[col] = df[col].astype(str).str.strip().fillna("")
            df["sku_lower"] = df["sku"].str.lower()

            # Sorting logic
            sort_list, ascending_list = ["multi"], [True]
            if config.get("sku_sort"):
                sort_list.append("sku_lower")
                ascending_list.append(False)
            if config.get("courier_sort"):
                sort_list.append("courier")
                ascending_list.append(True)
            if config.get("soldBy_sort"):
                sort_list.append("soldBy")
                ascending_list.append(True)

            df = df.sort_values(by=sort_list, ascending=ascending_list, na_position="last")
            df = df.drop(columns=["sku_lower"])
            whole_data = df.copy(deep=True)

            # Sort PDF pages
            reader_input = PdfReader(merged_pdf)
            writer_output = PdfWriter()
            for page_no in df.page.values:
                writer_output.addpage(reader_input.pages[page_no])
            sorted_pdf_path = os.path.join(temp_path, "sorted.pdf")
            writer_output.write(sorted_pdf_path)
            print(f"[INFO] Sorted PDF created -> {sorted_pdf_path}")

            # Whitespace & crop
            print("[INFO] Removing whitespace...")
            whitespace_pdf = pdf_whitespace(sorted_pdf_path)
            print("[INFO] Cropping PDF...")
            cropped_pdf = pdf_cropper(whitespace_pdf, config)

            # Save final PDF
            final_pdf_name = f"result_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.pdf"
            final_pdf = os.path.join(output_path, final_pdf_name)
            copy(cropped_pdf, final_pdf)

            # Save Excel
            excel_name = f"summary_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.xlsx"
            excel_path = os.path.join(output_path, excel_name)
            create_count_excel(whole_data, excel_path)

            print(f"[INFO] PDF saved -> {final_pdf}")
            print(f"[INFO] Excel saved -> {excel_path}")

    except Exception as e:
        print(f"[ERROR] Error processing {input_path}: {e}")
        import traceback
        traceback.print_exc()


# ---------------------- Main ----------------------
def main():
    parser = argparse.ArgumentParser(description="MeshooCropper PDF Processor")
    parser.add_argument("--input", default="input", help="Input root folder")
    parser.add_argument("--output", default="output", help="Output root folder")
    parser.add_argument("--config", required=False, help="Optional config.json path")
    args = parser.parse_args()

    config = load_config(args.config)
    print(f"[INFO] Using config: {config}")

    input_root, output_root = args.input, args.output
    os.makedirs(output_root, exist_ok=True)

    subfolders = [
        f for f in os.listdir(input_root)
        if os.path.isdir(os.path.join(input_root, f))
    ]
    if not subfolders:
        print(f"[INFO] No subfolders found in '{input_root}'")
        return

    # Parallel processing
    with ProcessPoolExecutor(max_workers=os.cpu_count()) as executor:
        futures = [
            executor.submit(
                process_folder,
                os.path.join(input_root, f),
                os.path.join(output_root, f),
                config
            )
            for f in subfolders
        ]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                print(f"[WARNING] Process error: {e}")

    print("\n✅ All folders processed successfully.")


if __name__ == "__main__":
    check_status()
    main()

