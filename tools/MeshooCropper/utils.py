import requests
import json
import sys
import shutil
import os
import re
from pdfrw import PdfReader, PdfWriter
from pdfminer.converter import TextConverter
from pdfminer.layout import LAParams
from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdfinterp import PDFResourceManager, PDFPageInterpreter
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfparser import PDFParser
from io import StringIO
import pandas as pd
from tqdm import tqdm
import fitz
from datetime import datetime

# ---------- SERVER STATUS CHECK ----------
def check_status():
    url = "https://raw.githubusercontent.com/sagar9995/meesho_file/main/lockv2.json"
    r = requests.get(url=url, timeout=5)
    if r.status_code == 200 and r.json().get("Status") is True:
        return None
    sys.exit("Server locked. Exiting.")

# ---------- FOLDER MANAGEMENT ----------
def create_filedir():
    shutil.rmtree("temp", ignore_errors=True)
    os.makedirs("temp", exist_ok=True)
    os.makedirs("output", exist_ok=True)

def check_input_file(filepath):
    all_pdf = [os.path.join(filepath, x) for x in os.listdir(filepath) if x.endswith(".pdf")]
    if not all_pdf:
        sys.exit("No PDF files found in input folder.")
    return all_pdf

def read_config():
    with open("config.json", "r") as f:
        return json.load(f)

# ---------- PDF MERGING ----------
def pdf_merger(all_path, save_path=os.path.join("temp", "merged_all.pdf")):
    writer = PdfWriter()
    for path in all_path:
        reader = PdfReader(path)
        writer.addpages(reader.pages)
    writer.write(save_path)
    return save_path

# ---------- PDF TO TEXT ----------
def convert_pdf_to_string(file_path):
    all_page = []
    with open(file_path, "rb") as in_file:
        parser = PDFParser(in_file)
        doc = PDFDocument(parser)
        rsrcmgr = PDFResourceManager()
        laparams = LAParams()
        for page in PDFPage.create_pages(doc):
            output_string = StringIO()
            device = TextConverter(rsrcmgr, output_string, laparams=laparams)
            interpreter = PDFPageInterpreter(rsrcmgr, device)
            interpreter.process_page(page)
            all_page.append(output_string.getvalue())
            device.close()
    return all_page

# ---------- FIELD EXTRACTION (ROBUST) ----------
re_digits = re.compile(r'\d+')

def quantity_extract(page):
    page_lines = [x for x in page.split("\n") if x]
    for i, line in enumerate(page_lines):
        low = line.lower()
        if "qty" in low or "quantity" in low:
            if i + 1 < len(page_lines):
                match = re_digits.search(page_lines[i+1])
                if match:
                    val = int(match.group())
                    return val, (val != 1)
    return 0, False

def courier_extract(page):
    page_lines = [x for x in page.split("\n") if x]
    for i, line in enumerate(page_lines):
        if "pickup" in line.lower() and i + 1 < len(page_lines):
            courier = page_lines[i+1].strip()
            if courier.lower() in {"c", "lsh-r0", "lhs-r0", ""}:
                return "valmo"
            return courier
    return ""

def sku_extract(page):
    for i, line in enumerate(page.split("\n")):
        if "sku" in line.lower():
            return page.split("\n")[i+1].strip() if i+1 < len(page.split("\n")) else ""
    return ""

def soldBy_extract(page):
    for i, line in enumerate(page.split("\n")):
        if "if undelivered, return to:" in line.lower():
            return page.split("\n")[i+1].strip() if i+1 < len(page.split("\n")) else ""
    return ""

def size_extract(page):
    for i, line in enumerate(page.split("\n")):
        if "size" in line.lower():
            return page.split("\n")[i+1].strip() if i+1 < len(page.split("\n")) else ""
    return ""

def color_extract(page):
    for i, line in enumerate(page.split("\n")):
        if "color" in line.lower():
            return page.split("\n")[i+1].strip() if i+1 < len(page.split("\n")) else ""
    return ""

# ---------- EXTRACT DATA FROM PDF TEXT ----------
def extract_data(text):
    rows = []
    error_pages = []
    for idx, page in tqdm(enumerate(text), desc="Extracting Data", unit="page", total=len(text)):
        try:
            sku = sku_extract(page)
            if not sku:
                error_pages.append(idx)
            qty, mqty = quantity_extract(page)
            rows.append({
                "page": idx,
                "sku": sku,
                "qty": qty,
                "multi": mqty,
                "courier": courier_extract(page),
                "soldBy": soldBy_extract(page),
                "size": size_extract(page),
                "color": color_extract(page)
            })
        except Exception:
            error_pages.append(idx)
            continue

    df = pd.DataFrame(rows)

    if error_pages:
        try:
            reader_input = PdfReader("temp/output.pdf")
            writer = PdfWriter()
            for page in error_pages:
                writer.addpage(reader_input.pages[page])
            writer.write("output/error_pages.pdf")
        except Exception:
            pass

    return df

# ---------- PDF WHITESPACE REMOVAL ----------
def pdf_whitespace(pdf_path):
    doc = fitz.open(pdf_path)
    for page in doc:
        try:
            instances = page.search_for("for online payments (as applicable)")
            if instances:
                text_instances = instances[0]
                page_crop_rect = fitz.Rect(0, 0, page.rect.width - 8, text_instances.y0 + 20)
                page.set_cropbox(page_crop_rect)
        except Exception:
            continue
    save_path = pdf_path.replace(".pdf", "_whitespace.pdf")
    doc.save(save_path, garbage=4, deflate=True, clean=True)
    doc.close()
    os.remove(pdf_path)
    return save_path

# ---------- PDF CROPPING ----------
def pdf_cropper(pdf_path, config, df=None):
    now = datetime.now()
    formatted_datetime = now.strftime("%d-%m-%y %I:%M %p")
    source_pdf = fitz.open(pdf_path)
    result = fitz.open()

    page_order = df.sort_values(by="qty", ascending=False)["page"].tolist() if df is not None and "qty" in df.columns else list(range(len(source_pdf)))
    error_pages = []

    for page_no in page_order:
        try:
            page = source_pdf[page_no]
            try:
                label_pos = page.search_for("TAX INVOICE")[0]
                label_crop_rect = fitz.Rect(0, 0, page.rect.width, max(label_pos.y0 - 1, 1))
            except Exception:
                label_crop_rect = fitz.Rect(0, 0, page.rect.width, max(page.rect.height / 4, 1))

            try:
                invoice_pos = page.search_for("TAX INVOICE")[0].y1
            except Exception:
                invoice_pos = page.rect.height / 2
            try:
                online_payment_pos = page.search_for("for online payments (as applicable)")[0].y0 + 20
            except Exception:
                online_payment_pos = page.rect.height

            invoice_crop_rect = fitz.Rect(0, max(invoice_pos - 18, 0), page.rect.width, online_payment_pos)
            if invoice_crop_rect.height <= 0:
                invoice_crop_rect = fitz.Rect(0, 0, page.rect.width, page.rect.height / 2)

            if config.get("keep_invoice With 4x4") or config.get("4x4"):
                combined_page = result.new_page(width=page.rect.width, height=label_crop_rect.height + invoice_crop_rect.height)
                combined_page.show_pdf_page(fitz.Rect(0, 0, page.rect.width, label_crop_rect.height), source_pdf, page_no, clip=label_crop_rect)
                combined_page.show_pdf_page(fitz.Rect(0, label_crop_rect.height, page.rect.width, combined_page.rect.height), source_pdf, page_no, clip=invoice_crop_rect)
            elif config.get("keep_invoice"):
                label_page = result.new_page(width=page.rect.width, height=label_crop_rect.height)
                if label_crop_rect.height > 0:
                    label_page.show_pdf_page(fitz.Rect(0, 0, label_crop_rect.width, label_crop_rect.height), source_pdf, page_no, clip=label_crop_rect)

                invoice_page = result.new_page(width=page.rect.width, height=invoice_crop_rect.height)
                if invoice_crop_rect.height > 0:
                    invoice_page.show_pdf_page(fitz.Rect(0, 0, invoice_crop_rect.width, invoice_crop_rect.height), source_pdf, page_no, clip=invoice_crop_rect)
            else:
                label_page = result.new_page(width=page.rect.width, height=label_crop_rect.height)
                if label_crop_rect.height > 0:
                    label_page.show_pdf_page(fitz.Rect(0, 0, label_crop_rect.width, label_crop_rect.height), source_pdf, page_no, clip=label_crop_rect)

            if config.get("add_date_on_top"):
                result[-1].insert_text(fitz.Point(12, 10), formatted_datetime, fontsize=11)

        except Exception as e:
            error_pages.append(page_no)

    if error_pages:
        for page_no in error_pages:
            result.insert_pdf(source_pdf, from_page=page_no, to_page=page_no)

    output_filename = os.path.join("temp", "result_temp.pdf")
    result.save(output_filename, garbage=4, deflate=True, clean=True)
    result.close()
    source_pdf.close()
    return output_filename

# ---------- EXCEL REPORT GENERATION ----------
def create_count_excel(df, save_path):
    sku_df = df[["qty", "soldBy", "color", "sku"]].value_counts().reset_index()
    sku_df.columns = ["Qty", "SoldBy", "Color", "SKU", "Count"]
    sku_df["SKU_lower"] = sku_df["SKU"].str.lower()
    sku_df = sku_df.sort_values(by=["Count", "SKU_lower", "Qty"], ascending=[False, True, True]).drop(columns=["SKU_lower"]).reset_index(drop=True)

    courierSold_df = df[["courier", "soldBy"]].value_counts().reset_index()
    courierSold_df.columns = ["Courier", "SoldBy", "Packages"]
    courierSold_df = courierSold_df.sort_values(by=["Packages", "Courier"], ascending=[False, True]).reset_index(drop=True)

    courier_df = df[["courier"]].value_counts().reset_index()
    courier_df.columns = ["Courier", "Packages"]
    courier_df = courier_df.sort_values(by=["Packages", "Courier"], ascending=[False, True]).reset_index(drop=True)

    soldby_df = df[["soldBy"]].value_counts().reset_index()
    soldby_df.columns = ["SoldBy", "Packages"]
    soldby_df = soldby_df.sort_values(by=["Packages", "SoldBy"], ascending=[False, True]).reset_index(drop=True)

    with pd.ExcelWriter(save_path, engine="xlsxwriter") as writer:
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
            worksheet.write_row(row, 0, df_block.columns, header_format)
            row += 1
            for r in df_block.itertuples(index=False):
                worksheet.write_row(row, 0, r, wrap_format)
                row += 1
            for i, col in enumerate(df_block.columns):
                max_len = max(len(str(col)), *(len(str(val)) for val in df_block[col]))
                worksheet.set_column(i, i, min(max_len + 2, 30))
            row += 2

        write_block("SKU REPORT", sku_df)
        write_block("COURIER + SOLD BY REPORT", courierSold_df)
        write_block("COURIER", courier_df)
        write_block("SOLD BY REPORT", soldby_df)

    print(f"Excel generated -> {save_path}")
    return save_path
