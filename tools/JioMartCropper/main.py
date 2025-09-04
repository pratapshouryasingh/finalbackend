import os
import shutil
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed
from tempfile import TemporaryDirectory
from pdfrw import PdfReader, PdfWriter
import traceback
import pandas as pd

from utils import (
    check_status,
    check_input_file,
    pdf_merger,
    convert_pdf_to_string,
    read_config,
    extract_data,
    pdf_whitespace,
    pdf_cropper,
    create_count_excel,
)


# ---------------------- Process Folder ----------------------
def process_folder(input_path, output_path):
    folder_name = os.path.basename(input_path)
    print(f"\n=== Processing folder: {folder_name} ===")

    try:
        with TemporaryDirectory() as temp_path:
            os.makedirs(output_path, exist_ok=True)

            # Get all PDFs
            all_pdfs = check_input_file(input_path)
            if not all_pdfs:
                print(f"No PDFs found in {input_path}")
                return

            config = read_config()

            # Merge PDFs
            merged_pdf = os.path.join(temp_path, "merged.pdf")
            pdf_merger(all_pdfs, merged_pdf)
            print(f"Merge Completed -> {merged_pdf}")

            # Convert to text & extract data
            all_page = convert_pdf_to_string(merged_pdf)
            df = extract_data(all_page)
            if df.empty:
                print(f"No data extracted from PDFs in {folder_name}")
                return

            # Clean dataframe safely
            df["sku"] = df["sku"].astype(str).str.strip()
            df["courier"] = df["courier"].astype(str).str.strip()
            df["soldBy"] = df["soldBy"].astype(str).str.strip()
            df["sku_lower"] = df["sku"].str.lower()

            # Sorting logic
            sort_list = ["multi"]
            ascending_list = [True]  # multi first: False = put True multipacks first
            if config.get("sku_sort"):
                sort_list.append("sku_lower")
                ascending_list.append(True)  # True = A→Z
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
            for page in df.page.values:
                writer_output.addpage(reader_input.pages[page])
            sorted_pdf_path = os.path.join(temp_path, "sorted.pdf")
            writer_output.write(sorted_pdf_path)

            # Remove whitespace & crop PDF
            print("Removing whitespace...")
            whitespace_pdf = pdf_whitespace(sorted_pdf_path)
            print("Cropping PDF...")
            cropped_pdf = pdf_cropper(whitespace_pdf, config, df)

            # Save final PDF (use move to avoid leftover temp files)
            final_pdf = os.path.join(
                output_path, f"result_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.pdf"
            )
            shutil.move(cropped_pdf, final_pdf)

            # Create Excel summary
            summary_excel = create_count_excel(whole_data, output_path)
            print(f"✅ PDF -> {final_pdf}")
            print(f"✅ Excel -> {summary_excel}")

    except Exception as e:
        error_log = os.path.join(output_path, f"error_{folder_name}.log")
        with open(error_log, "w") as f:
            traceback.print_exc(file=f)
        print(f"❌ Error processing {input_path}: {e}. See log -> {error_log}")


# ---------------------- Main ----------------------
def main():
    check_status()

    input_root = "input"
    output_root = "output"
    os.makedirs(output_root, exist_ok=True)

    subfolders = [f for f in os.listdir(input_root) if os.path.isdir(os.path.join(input_root, f))]
    if not subfolders:
        print("No subfolders in 'input'")
        return

    max_workers = max(1, min(os.cpu_count() or 1, 4))
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(
                process_folder,
                os.path.join(input_root, f),
                os.path.join(output_root, f)
            ) for f in subfolders
        ]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                print(f"❌ Process error: {e}")

    print("\n🎉 All folders processed successfully.")


if __name__ == "__main__":
    main()
