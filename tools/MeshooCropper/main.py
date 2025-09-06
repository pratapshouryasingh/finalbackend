import os
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

            # Get PDFs
            all_pdfs = check_input_file(input_path)
            if not all_pdfs:
                print(f"No PDFs found in {input_path}")
                return

            config = read_config()

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
            print("Removing whitespace...")
            whitespace_pdf = pdf_whitespace(sorted_pdf_path)
            print("Cropping PDF...")
            cropped_pdf = pdf_cropper(whitespace_pdf, config)

            # Save final PDF
            final_pdf_name = f"result_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.pdf"
            final_pdf = os.path.join(output_path, final_pdf_name)
            copy(cropped_pdf, final_pdf)

            # Save Excel
            excel_name = f"summary_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.xlsx"
            excel_path = os.path.join(output_path, excel_name)
            create_count_excel(whole_data, excel_path)

            print(f"[INFO] PDF -> {final_pdf}")
            print(f"[INFO] Excel -> {excel_path}")

    except Exception as e:
        print(f"[ERROR] Error processing {input_path}: {e}")
        import traceback
        traceback.print_exc()

# ---------------------- Main ----------------------
def main():
    parser = argparse.ArgumentParser(description="MeshooCropper PDF Processor")
    parser.add_argument("--input", default="input", help="Input root folder")
    parser.add_argument("--output", default="output", help="Output root folder")
    args = parser.parse_args()

    input_root, output_root = args.input, args.output
    os.makedirs(output_root, exist_ok=True)

    subfolders = [
        f for f in os.listdir(input_root)
        if os.path.isdir(os.path.join(input_root, f))
    ]
    if not subfolders:
        print(f"No subfolders in '{input_root}'")
        return

    # Parallel processing
    with ProcessPoolExecutor(max_workers=os.cpu_count()) as executor:
        futures = [
            executor.submit(
                process_folder,
                os.path.join(input_root, f),
                os.path.join(output_root, f)
            )
            for f in subfolders
        ]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                print(f"⚠ Process error: {e}")

    print("\nAll folders processed successfully.")

if __name__ == "__main__":
    check_status()
    main()

