import requests
import json
import sys 
import shutil
import os
from pdfrw import PdfReader, PdfWriter
from pdfminer.converter import TextConverter
from pdfminer.layout import LAParams
from pdfminer.pdfdocument import PDFDocument
from pdfminer.pdfinterp import PDFResourceManager, PDFPageInterpreter
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfparser import PDFParser
from io import StringIO
import re
import pandas as pd
from tqdm import tqdm
import fitz
from datetime import datetime
from pretty_html_table import build_table
import pdfkit



# Create temp and output folder
def create_filedir():
    # Remove and recreate temp folder
    shutil.rmtree("temp", ignore_errors=True)
    os.makedirs("temp", exist_ok=True)

    # Create output folder if not exists
    os.makedirs("output", exist_ok=True)

# Check for input file
def check_input_file(filepath):
    all_pdf = [os.path.join(filepath, x) for x in os.listdir(filepath)]
    if len(all_pdf) == 0:
        print("No pdf files found in input folder")
        sys.exit()
    return all_pdf

# Read config file
def read_config():
    with open("config.json", "r") as f:
        return json.load(f)

def pdf_merger(all_path, save_path=os.path.join("temp", "merged_all.pdf")):
    writer = PdfWriter()
    for path in all_path:
        reader = PdfReader(path)
        for page in reader.pages:
            writer.addpage(page)
    writer.write(save_path)

# Convert pdf to string
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

def quantity_extract(page):
    page = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\xff]", "", page)
    page = page.split("\n")
    try:
        qty_idx = [x for x in range(len(page)) if "Qty" in page[x]][0]
        multiple_qty = page[qty_idx + 2] != ""
        if int(page[qty_idx + 1]) != 1:
            multiple_qty = True
        return int(page[qty_idx + 1]), multiple_qty
    except:
        return 0
    
def courier_extract(page):
    page = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\xff]", "", page)
    page = page.split("\n")
    page = [x for x in page if len(x) != 0]
    try:
        if "Destination Code" not in page:
            courier_idx = [x for x in range(len(page)) if "Pickup" in page[x]][0]
            courier = page[courier_idx + 1].replace("Pickup", "").strip()
        else:
            courier_idx = [x for x in range(len(page)) if "Pickup" in page[x]][0]
            courier = page[courier_idx - 1].replace("Pickup", "").strip()

        # Normalize and fix bad courier values
        courier = courier.lower()
        if courier in ["c", "lsh-r0", "lhs-r0", ""]:
            return "valmo"

        return courier
    except:
        return ""


def sku_extract(page):
    page = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\xff]", "", page)
    page = page.split("\n")
    try:
        qty_idx = [x for x in range(len(page)) if "SKU" in page[x]][0]
        return page[qty_idx + 1].strip()
    except:
        return 0

def soldBy_extract(page):
    page = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\xff]", "", page)
    page = page.split("\n")
    page = [x for x in page if len(x) != 0]
    try:
        courier_idx = [
            x for x in range(len(page)) if "If undelivered, return to:" in page[x]
        ][0]
        return page[courier_idx + 1]
    except:
        return ""

def size_extract(page):
    page = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\xff]", "", page)
    page = page.split("\n")
    page = [x for x in page if len(x) != 0]
    try:
        courier_idx = [x for x in range(len(page)) if "Size" in page[x]][0]
        return page[courier_idx + 1]
    except:
        return ""

def color_extract(page):
    page = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\xff]", "", page)
    page = page.split("\n")
    page = [x for x in page if len(x) != 0]
    try:
        courier_idx = [x for x in range(len(page)) if "Color" in page[x]][0]
        return page[courier_idx + 1]
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
            df_dictionary = pd.DataFrame(
                [
                    {
                        "page": idx,
                        "sku": sku,
                        "qty": qty,
                        "multi": mqty,
                        "courier": courier,
                        "soldBy": soldBy,
                        "size": size,
                        "color": color,
                    }
                ]
            )
            df = pd.concat([df, df_dictionary], ignore_index=True)
        except:
            None
    if len(error_pages) != 0:
        reader_input = PdfReader("temp/output.pdf")
        writer = PdfWriter()
        for page in error_pages:
            writer.addpage(reader_input.pages[page])
        writer.write("output/error_pages.pdf")

    return df

def pdf_whitespace(pdf_path):
    with open(pdf_path, "rb") as f:
        doc = fitz.open(pdf_path)
        pdf = PdfReader(f)
        error_pages = []
        for page_no in tqdm(range(len(pdf.pages))):
            try:
                page = doc[page_no]
                text_instances = page.search_for("for online payments (as applicable)")[
                    0
                ]
                page_crop_rect = fitz.Rect(
                    0, 0, page.rect.width - 8, text_instances.y0 + 20
                )
                page.set_cropbox(page_crop_rect)
            except:
                pass
        save_path = pdf_path.replace(".pdf", "_whitespace.pdf")
        doc.save(save_path, garbage=4, deflate=True, clean=True)
        doc.close()
    os.remove(pdf_path)
    return save_path

def pdf_cropper(pdf_path, config, df=None):
    now = datetime.now()
    formatted_datetime = now.strftime("%d-%m-%y %I:%M %p")

    # Open PDF
    source_pdf = fitz.open(pdf_path)
    result = fitz.open()

    # ===== SORT PAGES BASED ON QTY (Descending) =====
    page_order = list(range(len(source_pdf)))
    if df is not None and "qty" in df.columns:
        page_order = df.sort_values(by="qty", ascending=False)["page"].tolist()

    for page_no in page_order:
        try:
            page = source_pdf[page_no]
            
            # ===== LABEL CROPPING =====
            try:
                label_pos = page.search_for("TAX INVOICE")[0]
                label_crop_rect = fitz.Rect(0, 0, page.rect.width, label_pos.y0 - 1)
            except:
                text_search = page.get_text().replace("\n", " ").strip().split(" ")[-1]
                label_pos = page.search_for(text_search)[0]
                label_crop_rect = fitz.Rect(0, 0, page.rect.width, label_pos.y0 + 20)
            
            # ===== INVOICE CROPPING =====
            try:
                invoice_pos = page.search_for("TAX INVOICE")[0].y1
            except:
                invoice_pos = page.rect.height / 2
                
            try:
                online_payment_pos = page.search_for(
                    "for online payments (as applicable)"
                )[0].y0 + 20
            except:
                online_payment_pos = page.rect.height
                
            invoice_crop_rect = fitz.Rect(
                0, invoice_pos - 18, page.rect.width, online_payment_pos
            )

            # ===== LOGIC =====
            if config.get("keep_invoice With 4x4") or config.get("4x4"):
                combined_page = result.new_page(
                    width=page.rect.width,
                    height=label_crop_rect.height + (invoice_crop_rect.y1 - invoice_crop_rect.y0)
                )
                combined_page.show_pdf_page(
                    fitz.Rect(0, 0, page.rect.width, label_crop_rect.height),
                    source_pdf, page_no, clip=label_crop_rect
                )
                combined_page.show_pdf_page(
                    fitz.Rect(0, label_crop_rect.height, page.rect.width, combined_page.rect.height),
                    source_pdf, page_no, clip=invoice_crop_rect
                )

            elif config.get("keep_invoice"):
                label_page = result.new_page(width=page.rect.width, height=label_crop_rect.height)
                label_page.show_pdf_page(
                    fitz.Rect(0, 0, page.rect.width, label_crop_rect.height),
                    source_pdf, page_no, clip=label_crop_rect
                )
                invoice_page = result.new_page(
                    width=page.rect.width,
                    height=invoice_crop_rect.y1 - invoice_crop_rect.y0
                )
                invoice_page.show_pdf_page(
                    fitz.Rect(0, 0, page.rect.width, invoice_page.rect.height),
                    source_pdf, page_no, clip=invoice_crop_rect
                )

            else:
                label_page = result.new_page(width=page.rect.width, height=label_crop_rect.height)
                label_page.show_pdf_page(
                    fitz.Rect(0, 0, page.rect.width, label_crop_rect.height),
                    source_pdf, page_no, clip=label_crop_rect
                )

            # Add date if required
            if config.get("add_date_on_top"):
                result[-1].insert_text(fitz.Point(12, 10), formatted_datetime, fontsize=11)

        except Exception as e:
            print(f"⚠ Error on page {page_no}: {e}")
            result.insert_pdf(source_pdf, from_page=page_no, to_page=page_no)

    source_pdf.close()

    # Save only as temp file (no extra "result_sorted" file)
    output_filename = os.path.join("temp", "result_temp.pdf")
    result.save(output_filename, garbage=4, deflate=True, clean=True)
    result.close()
    return output_filename


def create_count_excel(df):
    # ---------- SKU Report ----------
    sku_df = df[["qty", "soldBy", "color", "sku"]].value_counts().reset_index()
    sku_df.columns = ["Qty", "SoldBy", "Color", "SKU", "Count"]
    sku_df["SKU_lower"] = sku_df["SKU"].str.lower()
    sku_df = sku_df.sort_values(by=["Count", "SKU_lower", "Qty"], ascending=[False, True, True])
    sku_df = sku_df.drop(columns=["SKU_lower"]).reset_index(drop=True)

    # ---------- Courier+SoldBy Report ----------
    courierSold_df = df[["courier", "soldBy"]].value_counts().reset_index()
    courierSold_df.columns = ["Courier", "SoldBy", "Packages"]
    courierSold_df = courierSold_df.sort_values(by=["Packages", "Courier"], ascending=[False, True]).reset_index(drop=True)
    
    #Courier Report Only
    # ---------- SoldBy Only Report ----------
    courier_df = df[["courier"]].value_counts().reset_index()
    courier_df.columns = ["courier", "Packages"]
    courier_df = courier_df.sort_values(by=["Packages", "courier"], ascending=[False, True]).reset_index(drop=True)

    # ---------- SoldBy Only Report ----------
    soldby_df = df[["soldBy"]].value_counts().reset_index()
    soldby_df.columns = ["SoldBy", "Packages"]
    soldby_df = soldby_df.sort_values(by=["Packages", "SoldBy"], ascending=[False, True]).reset_index(drop=True)

    summary_path = os.path.join("output", "summary_report.xlsx")
    with pd.ExcelWriter(summary_path, engine="xlsxwriter") as writer:
        workbook = writer.book
        worksheet = workbook.add_worksheet("Summary")
        writer.sheets["Summary"] = worksheet

        # Formats
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

            # Adjust column widths based on max text length
            for i, col in enumerate(df_block.columns):
                max_len = max(
                    [len(str(col))] + [len(str(val)) for val in df_block[col]]
                )
                worksheet.set_column(i, i, min(max_len + 2, 30))  # Cap width at 30 chars
            row += 2

        # Write all three blocks
        write_block("SKU REPORT", sku_df)
        write_block("COURIER + SOLD BY REPORT", courierSold_df)
        write_block("COURIER", courier_df)
        write_block("SOLD BY REPORT", soldby_df)

    print("✅ summary_report.xlsx with wrapped text and adjusted widths generated.")
    return summary_path
