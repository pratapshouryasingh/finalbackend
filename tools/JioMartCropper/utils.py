import requests
import json
import sys
import shutil
import os
import re
import pandas as pd
from tqdm import tqdm
import fitz  # PyMuPDF
from datetime import datetime
from pdfminer.converter import TextConverter
from pdfminer.layout import LAParams
from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdfinterp import PDFResourceManager, PDFPageInterpreter
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfparser import PDFParser
from io import StringIO

# ---------------------- Check Server Status ----------------------
def check_status():
    url = "https://raw.githubusercontent.com/sagar9995/meesho_file/main/lockv2.json"
    try:
        r = requests.get(url=url, timeout=10)
        if r.status_code == 200 and r.json().get("Status", False):
            return
    except:
        pass
    print("❌ Server locked or offline")
    sys.exit()

# ---------------------- Check Input PDF ----------------------
def check_input_file(filepath):
    all_pdf = []
    for x in os.listdir(filepath):
        path = os.path.join(filepath, x)
        if not path.lower().endswith(".pdf"):
            continue
        try:
            with open(path, "rb") as f:
                if f.read(4) != b"%PDF":
                    print(f"Skipping invalid PDF: {x}")
                    continue
            all_pdf.append(path)
        except:
            print(f"Skipping unreadable PDF: {x}")
    if not all_pdf:
        print(f"No valid PDFs found in {filepath}")
    return all_pdf

# ---------------------- Read Config ----------------------
def read_config():
    default_config = {
        "sku_sort": True,
        "courier_sort": True,
        "soldBy_sort": True,
        "keep_invoice": False,
        "keep_invoice_4x4": False,
        "4x4": False,
        "add_date_on_top": False
    }
    
    try:
        with open("config.json", "r") as f:
            config = json.load(f)
            # Merge with defaults
            for key in default_config:
                if key not in config:
                    config[key] = default_config[key]
            return config
    except:
        print("Using default config")
        return default_config

# ---------------------- Merge PDFs (fitz only) ----------------------
def pdf_merger(all_path, save_path):
    result = fitz.open()
    for path in all_path:
        try:
            doc = fitz.open(path)
            result.insert_pdf(doc)
            doc.close()
        except Exception as e:
            print(f"Error merging {path}: {e}")
    result.save(save_path, garbage=4, deflate=True, clean=True)
    result.close()

# ---------------------- Convert PDF to Text ----------------------
def convert_pdf_to_string(file_path):
    all_page = []
    try:
        with open(file_path, "rb") as in_file:
            parser = PDFParser(in_file)
            doc = PDFDocument(parser)
            rsrcmgr = PDFResourceManager()
            
            for page in PDFPage.create_pages(doc):
                output_string = StringIO()
                device = TextConverter(rsrcmgr, output_string, laparams=LAParams())
                interpreter = PDFPageInterpreter(rsrcmgr, device)
                interpreter.process_page(page)
                
                # Clean up
                text = output_string.getvalue()
                text = re.sub(r'\s+', ' ', text)
                text = text.replace('\x00', '')
                all_page.append(text)
    except Exception as e:
        print(f"Error converting PDF to text: {e}")
    
    return all_page

# ---------------------- Extraction Helpers ----------------------
def quantity_extract(page):
    try:
        patterns = [
            r"Qty[:\s]*(\d+)",
            r"Quantity[:\s]*(\d+)",
            r"Shipment Qty[:\s]*(\d+)",
            r"QTY[:\s]*(\d+)"
        ]
        for pattern in patterns:
            match = re.search(pattern, page, re.IGNORECASE)
            if match:
                qty = int(match.group(1))
                return qty, qty > 1
        return 1, False
    except:
        return 1, False

def sku_extract(page):
    try:
        patterns = [
            r"SKU[:\s]*([A-Za-z0-9\-]+)",
            r"Shipment SKU[:\s]*([A-Za-z0-9\-]+)",
            r"Item Code[:\s]*([A-Za-z0-9\-]+)",
            r"Product Code[:\s]*([A-Za-z0-9\-]+)"
        ]
        for pattern in patterns:
            match = re.search(pattern, page, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return ""
    except:
        return ""

def courier_extract(page):
    try:
        patterns = [
            r"Shipping Agent[:\s]*([A-Za-z\s]+)",
            r"Courier[:\s]*([A-Za-z\s]+)",
            r"Delivery Partner[:\s]*([A-Za-z\s]+)",
            r"Pickup[:\s]*([A-Za-z\s]+)"
        ]
        for pattern in patterns:
            match = re.search(pattern, page, re.IGNORECASE)
            if match:
                courier = match.group(1).strip().lower()
                if courier in ["c", "lsh-r0", "lhs-r0", ""]:
                    return "valmo"
                return courier
        return ""
    except:
        return ""

def soldBy_extract(page):
    try:
        patterns = [
            r"Sold By[:\s]*([A-Za-z\s]+)",
            r"Seller[:\s]*([A-Za-z\s]+)",
            r"Vendor[:\s]*([A-Za-z\s]+)",
            r"Merchant[:\s]*([A-Za-z\s]+)"
        ]
        for pattern in patterns:
            match = re.search(pattern, page, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return ""
    except:
        return ""

def size_extract(page):
    try:
        lines = [x for x in page.split("\n") if len(x) != 0]
        courier_idx = [x for x in range(len(lines)) if "Size" in lines[x]][0]
        return lines[courier_idx + 1]
    except:
        return ""

def color_extract(page):
    try:
        lines = [x for x in page.split("\n") if len(x) != 0]
        courier_idx = [x for x in range(len(lines)) if "Color" in lines[x]][0]
        return lines[courier_idx + 1]
    except:
        return ""

def extract_data(text):
    df = pd.DataFrame()
    error_pages = []
    for idx, page in tqdm(enumerate(text)):
        try:
            sku = sku_extract(page)
            if sku == "":
                error_pages.append(idx)
            qty, mqty = quantity_extract(page)
            courier = courier_extract(page)
            soldBy = soldBy_extract(page)
            size = size_extract(page)
            color = color_extract(page)
            df_dictionary = pd.DataFrame([{
                "page": idx,
                "sku": sku,
                "qty": qty,
                "multi": mqty,
                "courier": courier,
                "soldBy": soldBy,
                "size": size,
                "color": color,
            }])
            df = pd.concat([df, df_dictionary], ignore_index=True)
        except Exception as e:
            print(f"Error extracting data from page {idx}: {e}")
    if len(error_pages) != 0:
        print(f"Found {len(error_pages)} pages with extraction errors")
    return df

# ---------------------- PDF Whitespace ----------------------
def pdf_whitespace(pdf_path):
    doc = fitz.open(pdf_path)
    for page_no in tqdm(range(len(doc))):
        try:
            page = doc[page_no]
            text_instances = page.search_for("for online payments (as applicable)")
            if text_instances:
                crop_y = text_instances[0].y0 + 20
                page_crop_rect = fitz.Rect(0, 0, page.rect.width, crop_y)
                page.set_cropbox(page_crop_rect)
        except Exception as e:
            print(f"Error cropping whitespace on page {page_no}: {e}")
    save_path = pdf_path.replace(".pdf", "_whitespace.pdf")
    doc.save(save_path, garbage=4, deflate=True, clean=True)
    doc.close()
    return save_path

# ---------------------- PDF Cropper ----------------------
def pdf_cropper(pdf_path, config, df=None):
    import fitz
    from datetime import datetime
    from tqdm import tqdm

    # Base width/height
    FIXED_WIDTH = 3.5 * 72
    FIXED_HEIGHT = 4.25 * 72

    # Adjustments
    EXTRA_HEIGHT = 0.75 * 72
    REDUCE_RIGHT_MARGIN = 0.45 * 72
    EXTRA_TOP_MARGIN = 0.2 * 72  # Space for datetime at top

    now = datetime.now()
    formatted_datetime = now.strftime("%d-%m-%y %I:%M %p")

    main = fitz.open(pdf_path)
    result = fitz.open()

    for page_no in tqdm(range(len(main))):
        page = main[page_no]

        # Detect content bounding box (union of all text & image bboxes)
        rects = []

        # Text blocks
        for b in page.get_text("blocks"):
            rects.append(fitz.Rect(b[:4]))

        # Images
        for img in page.get_images(full=True):
            xref = img[0]
            try:
                rect = page.get_image_bbox(xref)
                rects.append(rect)
            except:
                pass

        if rects:
            # Union of all rects
            bbox = rects[0]
            for r in rects[1:]:
                bbox |= r

            min_x, min_y, max_x, max_y = bbox

            # Adjust dimensions
            max_x = min(min_x + FIXED_WIDTH - REDUCE_RIGHT_MARGIN, page.rect.width)
            max_y = min(min_y + FIXED_HEIGHT + EXTRA_HEIGHT, page.rect.height)

            crop_rect = fitz.Rect(min_x, min_y, max_x, max_y)

            # Create new page with extra top margin
            new_width = crop_rect.width
            new_height = crop_rect.height + EXTRA_TOP_MARGIN
            new_page = result.new_page(width=new_width, height=new_height)

            # Show cropped content
            new_page.show_pdf_page(
                fitz.Rect(0, EXTRA_TOP_MARGIN, crop_rect.width, crop_rect.height + EXTRA_TOP_MARGIN),
                main,
                page_no,
                clip=crop_rect
            )

            # Add datetime in top margin
            if config.get("add_date_on_top", False):
                new_page.insert_text(
                    fitz.Point(new_width - 80, EXTRA_TOP_MARGIN / 2),
                    formatted_datetime,
                    fontsize=9
                )
        else:
            # If no content detected, insert original page
            result.insert_pdf(main, from_page=page_no, to_page=page_no)

    main.close()

    cropped_path = pdf_path.replace(".pdf", "_cropped.pdf")
    result.save(cropped_path, garbage=4, deflate=True, clean=True)
    result.close()

    return cropped_path

# ---------------------- Excel Report ----------------------
def create_count_excel(df, output_path="output"):
    os.makedirs(output_path, exist_ok=True)

    sku_df = df[["qty", "soldBy", "color", "sku"]].value_counts().reset_index()
    sku_df.columns = ["Qty", "SoldBy", "Color", "SKU", "Count"]
    sku_df["SKU_lower"] = sku_df["SKU"].str.lower()
    sku_df = sku_df.sort_values(by=["Count", "SKU_lower", "Qty"], ascending=[False, True, True])
    sku_df = sku_df.drop(columns=["SKU_lower"]).reset_index(drop=True)

    courierSold_df = df[["courier", "soldBy"]].value_counts().reset_index()
    courierSold_df.columns = ["Courier", "SoldBy", "Packages"]
    courierSold_df = courierSold_df.sort_values(by=["Packages", "Courier"], ascending=[False, True]).reset_index(drop=True)
    
    courier_df = df[["courier"]].value_counts().reset_index()
    courier_df.columns = ["Courier", "Packages"]
    courier_df = courier_df.sort_values(by=["Packages", "Courier"], ascending=[False, True]).reset_index(drop=True)

    soldby_df = df[["soldBy"]].value_counts().reset_index()
    soldby_df.columns = ["SoldBy", "Packages"]
    soldby_df = soldby_df.sort_values(by=["Packages", "SoldBy"], ascending=[False, True]).reset_index(drop=True)

    now_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    summary_path = os.path.join(output_path, f"summary_report_{now_str}.xlsx")

    with pd.ExcelWriter(summary_path, engine="xlsxwriter") as writer:
        workbook = writer.book
        worksheet = workbook.add_worksheet("Summary")
        writer.sheets["Summary"] = worksheet

        bold_format = workbook.add_format({'bold': True, 'font_size': 12})
        header_format = workbook.add_format({'bold': True, 'bg_color': '#DDEEFF', 'border': 1, 'text_wrap': True})
        wrap_format = workbook.add_format({'text_wrap': True})
        timestamp_format = workbook.add_format({'italic': True, 'font_color': 'gray'})

        row = 0
        worksheet.write(row, 0, f"Report Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", timestamp_format)
        row += 2

        def write_block(title, df_block):
            nonlocal row
            worksheet.write(row, 0, title, bold_format)
            row += 1
            for col_num, value in enumerate(df_block.columns):
                worksheet.write(row, col_num, value, header_format)
            row += 1
            for r in df_block.values:
                for col_num, value in enumerate(r):
                    worksheet.write(row, col_num, value, wrap_format)
                row += 1
            for i, col in enumerate(df_block.columns):
                max_len = max([len(str(col))] + [len(str(val)) for val in df_block[col]])
                worksheet.set_column(i, i, min(max_len + 2, 30))
            row += 2

        write_block("SKU REPORT", sku_df)
        write_block("COURIER + SOLD BY REPORT", courierSold_df)
        write_block("COURIER", courier_df)
        write_block("SOLD BY REPORT", soldby_df)

    print("summary report generated at", summary_path)
    return summary_path
