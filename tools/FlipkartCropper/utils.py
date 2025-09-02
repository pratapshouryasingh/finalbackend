import requests
import json
import sys
import shutil
import os
import re
from pdfrw import PdfReader, PdfWriter
from tqdm import tqdm
import fitz
from datetime import datetime
import pandas as pd
from io import StringIO
from pdfminer.converter import TextConverter
from pdfminer.layout import LAParams
from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdfinterp import PDFResourceManager, PDFPageInterpreter
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfparser import PDFParser

# ---------------------- Check Server Status ----------------------
def check_status():
    url = "https://raw.githubusercontent.com/sagar9995/meesho_file/main/lockv2.json"
    r = requests.get(url=url)
    if r.status_code == 200 and r.json().get("Status", False):
        return None
    else:
        sys.exit("❌ Server locked. Exiting.")

# ---------------------- Create Directories ----------------------
def create_filedir(temp_path="temp", output_path="output"):
    shutil.rmtree(temp_path, ignore_errors=True)
    shutil.rmtree(output_path, ignore_errors=True)
    os.makedirs(temp_path, exist_ok=True)
    os.makedirs(output_path, exist_ok=True)

# ---------------------- Check Input PDF ----------------------
def check_input_file(filepath):
    all_pdf = []
    for x in os.listdir(filepath):
        path = os.path.join(filepath, x)
        if not path.lower().endswith(".pdf"):
            continue
        try:
            with open(path, "rb") as f:
                header = f.read(4)
                if header != b"%PDF":
                    print(f"Skipping invalid PDF: {x}")
                    continue
            all_pdf.append(path)
        except:
            print(f"Skipping unreadable file: {x}")
    if not all_pdf:
        sys.exit(f"No valid PDF files found in {filepath}")
    return all_pdf

# ---------------------- Read Config ----------------------
def read_config():
    with open("config.json", "r") as f:
        return json.load(f)

# ---------------------- Merge PDF ----------------------
def pdf_merger(all_path, save_path):
    writer = PdfWriter()
    for path in all_path:
        reader = PdfReader(path)
        for page in reader.pages:
            writer.addpage(page)
    writer.write(save_path)
    return save_path

# ---------------------- Convert PDF to String (pdfminer) ----------------------
def convert_pdf_to_string(file_path):
    all_page = []
    with open(file_path, "rb") as in_file:
        parser = PDFParser(in_file)
        doc = PDFDocument(parser)
        for page in PDFPage.create_pages(doc):
            output_string = StringIO()
            rsrcmgr = PDFResourceManager()
            device = TextConverter(rsrcmgr, output_string, laparams=LAParams())
            interpreter = PDFPageInterpreter(rsrcmgr, device)
            interpreter.process_page(page)
            all_page.append(output_string.getvalue())
    return all_page

# ---------------------- Extraction Helpers ----------------------
def quantity_extract(page):
    lines = [x.strip() for x in page.split("\n") if x.strip()]
    for i, line in enumerate(lines):
        if "qty" in line.lower() or "quantity" in line.lower():
            try:
                val = int(re.findall(r'\d+', lines[i+1])[0])
                return val, (val > 1)
            except:
                return 1, False
    return 1, False

def courier_extract(page):
    lines = [x.strip() for x in page.split("\n") if x.strip()]
    try:
        return lines[2]
    except:
        return ""

def sku_extract(page):
    lines = [x.strip() for x in page.split("\n") if x.strip()]
    for l in lines:
        if "sku" in l.lower():
            return l.split(":")[-1].strip()
    return ""

def soldBy_extract(page):
    lines = [x.strip() for x in page.split("\n") if x.strip()]
    for l in lines:
        if "sold by" in l.lower():
            return l.replace("Sold By:", "").strip()
    return ""

def size_extract(page):
    for l in page.split("\n"):
        if "size" in l.lower():
            return l.split(":")[-1].strip()
    return ""

def color_extract(page):
    for l in page.split("\n"):
        if "color" in l.lower():
            return l.split(":")[-1].strip()
    return ""

# ---------------------- Extract Data ----------------------
def extract_data(text, merged_pdf_path, output_path):
    df_list = []
    error_pages = []

    for idx, page in tqdm(list(enumerate(text)), desc="Extracting Data", unit="page"):
        try:
            sku = sku_extract(page)
            qty, mqty = quantity_extract(page)
            courier = courier_extract(page)
            soldBy = soldBy_extract(page)
            size = size_extract(page)
            color = color_extract(page)
            if not sku:
                error_pages.append(idx)

            df_list.append({
                "page": idx,
                "sku": sku,
                "qty": qty,
                "multi": (mqty or qty > 1),
                "courier": courier,
                "soldBy": soldBy,
                "size": size,
                "color": color
            })
        except:
            error_pages.append(idx)

    df = pd.DataFrame(df_list)

    # Save error pages if any
    if error_pages:
        error_filename = os.path.join(output_path, "error_pages.pdf")
        reader_input = PdfReader(merged_pdf_path)
        writer = PdfWriter()
        for page in error_pages:
            writer.addpage(reader_input.pages[page])
        writer.write(error_filename)

    return df

# ---------------------- PDF Whitespace ----------------------
def pdf_whitespace(pdf_path, temp_path):
    doc = fitz.open(pdf_path)
    for page_no in tqdm(range(len(doc)), desc="Removing whitespace"):
        try:
            text_instances = doc[page_no].search_for("TAX INVOICE")[0]
            crop_rect = fitz.Rect(0, 0, doc[page_no].rect.width - 8, text_instances.y0 + 20)
            doc[page_no].set_cropbox(crop_rect)
        except:
            pass
    save_path = os.path.join(temp_path, "whitespace_removed.pdf")
    doc.save(save_path, garbage=4, deflate=True, clean=True)
    doc.close()
    return save_path

# ---------------------- PDF Cropper ----------------------
def pdf_cropper(pdf_path, config, temp_path):
    now = datetime.now().strftime("%d-%m-%y %I:%M %p")
    doc = fitz.open(pdf_path)
    result = fitz.open()

    for page_no in tqdm(range(len(doc)), desc="Cropping pages"):
        try:
            if config.get("keep_invoice", False):
                result.insert_pdf(doc, from_page=page_no, to_page=page_no)
                result.insert_pdf(doc, from_page=page_no, to_page=page_no)

                label_page = result[-2]
                invoice_page = result[-1]

                text_instances = label_page.search_for("Order Id:")
                if text_instances:
                    label_rect = fitz.Rect(185, 15, label_page.rect.width - 185, text_instances[0].y0 - 10)
                    label_page.set_cropbox(label_rect)

                if config.get("add_date_on_top", False):
                    label_page.insert_text(fitz.Point(12, 10), now, fontsize=11)

                text_instances = invoice_page.search_for("TAX INVOICE")
                if text_instances:
                    invoice_rect = fitz.Rect(0, text_instances[0].y0 - 10, invoice_page.rect.width, invoice_page.rect.height)
                    invoice_page.set_cropbox(invoice_rect)
            else:
                result.insert_pdf(doc, from_page=page_no, to_page=page_no)
                label_page = result[-1]
                text_instances = label_page.search_for("Order Id:")
                if text_instances:
                    label_rect = fitz.Rect(185, 15, label_page.rect.width - 185, text_instances[0].y0 - 10)
                    label_page.set_cropbox(label_rect)

                if config.get("add_date_on_top", False):
                    label_page.insert_text(fitz.Point(12, 10), now, fontsize=11)
        except Exception as e:
            print(f"⚠️ Error cropping page {page_no}: {e}")
            result.insert_pdf(doc, from_page=page_no, to_page=page_no)

    output_filename = os.path.join(temp_path, "cropped_final.pdf")
    result.save(output_filename, garbage=4, deflate=True, clean=True)
    result.close()
    doc.close()
    return output_filename

# ---------------------- Create Count Excel ----------------------
def create_count_excel(df, output_path):
    df["sku"] = df["sku"].astype(str).str.strip().replace({"nan": "", "None": ""})
    df["soldBy"] = df["soldBy"].astype(str).fillna("")
    df["color"] = df.get("color", "")
    df["size"] = df.get("size", "")

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

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"summary_report_{timestamp}.xlsx"
    summary_path = os.path.join(output_path, filename)

    with pd.ExcelWriter(summary_path, engine="xlsxwriter") as writer:
        workbook = writer.book
        worksheet = workbook.add_worksheet("Summary")
        writer.sheets["Summary"] = worksheet

        bold_format = workbook.add_format({'bold': True, 'font_size': 12})
        header_format = workbook.add_format({'bold': True, 'bg_color': '#DDEEFF', 'border': 1, 'text_wrap': True})
        wrap_format = workbook.add_format({'text_wrap': True})

        row = 0
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
        write_block("COURIER REPORT", courier_df)
        write_block("SOLD BY REPORT", soldby_df)

    print(f"✅ Excel generated -> {summary_path}")
    return summary_path

