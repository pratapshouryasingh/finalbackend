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
    folder_name = os.path.basename(input_path.rstrip(os.sep))
    print(f"\n=== Processing folder: {folder_name} ===")

    try:
        with TemporaryDirectory() as temp_path:
            os.makedirs(output_path, exist_ok=True)

            # Collect PDFs
            all_pdfs = check_input_file(input_path)
            if not all_pdfs:
                print(f"No PDFs found in {input_path}")
                return

            config = read_config()

            # Merge PDFs
            merged_pdf = os.path.join(temp_path, "merged.pdf")
            pdf_merger(all_pdfs, merged_pdf)
            print(f"[INFO] Merge Completed -> {merged_pdf}")

            # Convert merged PDF to text and extract data
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
            sort_list = ["multi"]
            ascending_list = [True]
            config = config or {}
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
            df = df.drop(columns=["sku_lower"], errors="ignore")
            whole_data = df.copy(deep=True)

            # Sort PDF pages based on dataframe
            reader_input = PdfReader(merged_pdf)
            writer_output = PdfWriter()
            for page_no in df.page.values:
                writer_output.addpage(reader_input.pages[page_no])
            sorted_pdf_path = os.path.join(temp_path, "sorted.pdf")
            writer_output.write(sorted_pdf_path)
            print(f"[INFO] Sorted PDF created -> {sorted_pdf_path}")

            # Remove whitespace & crop PDF
            print("Removing whitespace...")
            whitespace_pdf = pdf_whitespace(sorted_pdf_path)
            print("Cropping PDF...")
            cropped_pdf = pdf_cropper(whitespace_pdf, config)

            # Save final PDF
            final_pdf_name = f"result_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.pdf"
            final_pdf = os.path.join(output_path, final_pdf_name)
            copy(cropped_pdf, final_pdf)

            # Save Excel summary
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
    parser = argparse.ArgumentParser(description="PDF Cropper Tool")
    parser.add_argument("--input", default="input", help="Input root folder (default: input)")
    parser.add_argument("--output", default="output", help="Output root folder (default: output)")
    parser.add_argument("--jobs", action="store_true", help="Treat each subfolder of input as a job")
    args = parser.parse_args()

    check_status()
    os.makedirs(args.output, exist_ok=True)

    if args.jobs:
        # Process all subfolders inside input
        subfolders = [
            f for f in os.listdir(args.input)
            if os.path.isdir(os.path.join(args.input, f))
        ]
        if not subfolders:
            print(f"No subfolders in '{args.input}'")
            return

        with ProcessPoolExecutor(max_workers=os.cpu_count()) as executor:
            futures = [
                executor.submit(
                    process_folder,
                    os.path.join(args.input, f),
                    os.path.join(args.output, f)
                )
                for f in subfolders
            ]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    print(f"⚠ Process error: {e}")
    else:
        # Process input folder directly
        process_folder(args.input, args.output)

    print("\nAll processing completed successfully.")

if __name__ == "__main__":
    main()

