import requests
import json
import sys
import shutil
import os
from pdfrw import PdfReader, PdfWriter
from tqdm import tqdm
import fitz
from datetime import datetime
import re
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---------------------- Check Server Status ----------------------
def check_status():
    url = "https://raw.githubusercontent.com/sagar9995/meesho_file/main/lockv2.json"
    r = requests.get(url=url)
    if r.status_code == 200 and r.json().get("Status", False):
        return None
    else:
        sys.exit()

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
        print(f"No valid PDF files found in {filepath}")
        return []
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

# ---------------------- Convert PDF to String (Parallel) ----------------------
def convert_pdf_to_string(file_path):
    doc = fitz.open(file_path)
    all_page = [None] * len(doc)

    def process_page(i):
        return doc[i].get_text("text")

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(process_page, i): i for i in range(len(doc))}
        for future in as_completed(futures):
            idx = futures[future]
            all_page[idx] = future.result()

    doc.close()
    return all_page

# ---------------------- Precompiled regex ----------------------
CLEAN_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\xff]")

# ---------------------- Extraction Helpers ----------------------
def quantity_extract(page):
    page_clean = CLEAN_PATTERN.sub("", page)
    lines = page_clean.split("\n")
    try:
        qty_start = next(i for i, l in enumerate(lines) if "QTY" in l.upper())
        qtys = []
        for l in lines[qty_start + 1:]:
            l_stripped = l.strip()
            if any(keyword in l_stripped.upper() for keyword in ["SKU", "SOLD BY", "COLOR", "SIZE"]):
                break
            if l_stripped.isdigit():
                qtys.append(int(l_stripped))
        total_qty = sum(qtys) if qtys else 1
        return total_qty, len(qtys) > 1
    except StopIteration:
        return 1, False

def courier_extract(page):
    page = CLEAN_PATTERN.sub("", page)
    page = page.split("\n")
    try:
        return page[2].strip()
    except:
        return ""

def sku_extract(page):
    page = CLEAN_PATTERN.sub("", page)
    page = page.split("\n")
    all_pipe = [x for x in page if "|" in x]
    try:
        skus = [x for x in all_pipe if x[0].isnumeric()]
    except:
        return "", False
    if not skus:
        return "", False
    sku = skus[0].split(" ", 1)
    return sku[1].split("|", 1)[0], len(skus) > 1

def soldBy_extract(page):
    page = CLEAN_PATTERN.sub("", page)
    page = page.split("\n")
    try:
        soldby_idx = [x for x in range(len(page)) if "Sold By:" in page[x]][0]
        return page[soldby_idx].replace("Sold By:", "").strip().split(",", 1)[0]
    except:
        return ""

# ---------------------- Extract Data (Parallel) ----------------------
def extract_data(text, merged_pdf_path, output_path):
    df_list = []
    error_pages = []

    def process_page(idx, page):
        try:
            sku, multi_sku = sku_extract(page)
            qty, mqty = quantity_extract(page)
            courier = courier_extract(page)
            soldBy = soldBy_extract(page)
            multi = (multi_sku or mqty or qty > 1)
            return {"page": idx, "sku": sku, "qty": qty, "multi": multi, "courier": courier, "soldBy": soldBy}, idx if sku == "" else None
        except:
            return None, idx

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(process_page, i, page) for i, page in enumerate(text)]
        for future in as_completed(futures):
            result, error_idx = future.result()
            if result:
                df_list.append(result)
            if error_idx is not None:
                error_pages.append(error_idx)

    df = pd.DataFrame(df_list)

    # Handle error pages
    if error_pages:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        error_filename = f"error_pages_{timestamp}.pdf"
        reader_input = PdfReader(merged_pdf_path)
        writer = PdfWriter()
        for page in error_pages:
            writer.addpage(reader_input.pages[page])
        writer.write(os.path.join(output_path, error_filename))

    return df

# ---------------------- PDF Whitespace ----------------------
def pdf_whitespace(pdf_path, temp_path):
    """Remove whitespace and save to temp directory only"""
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

import fitz
from datetime import datetime
import os
from tqdm import tqdm

# ---------- helper: robustly find "Tax Invoice" Y position ----------
def _find_invoice_y(page, min_y_ratio=0.30):
    """
    Try several ways to find the top Y of the invoice heading.
    Returns a float Y (points) or None if not found.
    """
    page_h = float(page.rect.height)

    # 1) Direct text search with common variants
    variants = ["TAX INVOICE", "Tax Invoice", "Tax invoice", "Invoice"]
    rects = []
    for v in variants:
        try:
            rects += page.search_for(v)
        except Exception:
            pass

    if rects:
        rects_sorted = sorted(rects, key=lambda r: r.y0)
        # Prefer a match below min_y_ratio of the page to avoid headers
        candidates = [r for r in rects_sorted if r.y0 > page_h * min_y_ratio]
        r = candidates[0] if candidates else rects_sorted[0]
        return float(r.y0)

    # 2) Word-level search (more robust across fonts)
    try:
        words = page.get_text("words")  # (x0,y0,x1,y1,word,block,line,word_no)
        if not words:
            return None
        words.sort(key=lambda w: (w[1], w[0]))  # sort by y0, then x0

        # Look for "Tax" followed by "Invoice" on the same line
        for i in range(len(words) - 1):
            w1, w2 = words[i], words[i + 1]
            if w1[4].lower() == "tax" and w2[4].lower().startswith("invoice"):
                # same line: small vertical gap
                if abs(w1[1] - w2[1]) < 8:
                    y0 = min(w1[1], w2[1])
                    if y0 > page_h * min_y_ratio:
                        return float(y0)

        # Otherwise, any "invoice" word near bottom half
        invoice_words = [w for w in words if w[4].lower() == "invoice"]
        if invoice_words:
            invoice_words.sort(key=lambda w: w[1])
            y0 = float(invoice_words[0][1])
            if y0 > page_h * min_y_ratio:
                return y0
    except Exception:
        pass

    return None


# ---------------------- UPDATED CROPPER ----------------------
def pdf_cropper(pdf_path, config, temp_path):
    """
    Split each page into LABEL (+ optional date) and INVOICE (if keep_invoice=True).

    Config keys (all optional):
      - keep_invoice: bool (default False)
      - add_date_on_top: bool (default False)
      - label_left_margin: int (default 185)
      - label_top_margin: int (default 15)
      - invoice_top_ratio: float between 0..1 used when heading not found (default 0.58)
      - invoice_top_fixed_y: number (points). If set, forces the split Y no matter what.

    Output: temp_path/cropped_final.pdf
    """
    now = datetime.now()
    formatted_datetime = now.strftime("%d-%m-%y %I:%M %p")

    keep_invoice = bool(config.get("keep_invoice", False))
    add_date = bool(config.get("add_date_on_top", False))
    label_left = int(config.get("label_left_margin", 185))
    label_top = int(config.get("label_top_margin", 15))
    # fallback if "Tax Invoice" not found
    ratio_fallback = float(config.get("invoice_top_ratio", 0.58))
    fixed_y = config.get("invoice_top_fixed_y", None)  # e.g., 500.0

    doc = fitz.open(pdf_path)
    result = fitz.open()

    for page_no in tqdm(range(len(doc)), desc="Cropping pages"):
        try:
            page = doc[page_no]
            W, H = float(page.rect.width), float(page.rect.height)

            # 1) Determine invoice top Y
            if fixed_y is not None:
                invoice_y_top = float(fixed_y)
            else:
                invoice_y_top = _find_invoice_y(page)  # try to detect
                if invoice_y_top is None:
                    invoice_y_top = H * ratio_fallback  # hard-coded ratio fallback

            # Safety clamp
            invoice_y_top = max(30.0, min(invoice_y_top, H - 30.0))

            # 2) Build rects
            #    LABEL: crop the central label area (exclude left/right bars) up to just above invoice
            label_bottom = max(label_top + 10.0, invoice_y_top - 12.0)
            label_rect = fitz.Rect(
                label_left,               # left
                label_top,                # top
                W - label_left,           # right
                label_bottom              # bottom
            )

            # Ensure a sane label rect; if it collapses, use upper half as fallback
            if label_rect.height <= 20 or label_rect.width <= 20:
                label_rect = fitz.Rect(
                    label_left, label_top,
                    W - label_left, H * 0.5
                )

            #    INVOICE: from the heading downwards, full width
            invoice_top = max(0.0, invoice_y_top - 10.0)
            invoice_rect = fitz.Rect(
                0.0, invoice_top,
                W, H
            )

            # 3) Write pages
            if keep_invoice:
                # Add label
                result.insert_pdf(doc, from_page=page_no, to_page=page_no)
                lp = result[-1]
                lp.set_cropbox(label_rect)
                if add_date:
                    lp.insert_text(fitz.Point(12, 12), formatted_datetime, fontsize=11)

                # Add invoice
                result.insert_pdf(doc, from_page=page_no, to_page=page_no)
                ip = result[-1]
                ip.set_cropbox(invoice_rect)
            else:
                # Only label
                result.insert_pdf(doc, from_page=page_no, to_page=page_no)
                lp = result[-1]
                lp.set_cropbox(label_rect)
                if add_date:
                    lp.insert_text(fitz.Point(12, 12), formatted_datetime, fontsize=11)

        except Exception as e:
            print(f"⚠️ Error cropping page {page_no}: {e}")
            # Fallback: pass-through original page
            result.insert_pdf(doc, from_page=page_no, to_page=page_no)

    doc.close()
    output_filename = os.path.join(temp_path, "cropped_final.pdf")
    result.save(output_filename, garbage=4, deflate=True, clean=True)
    result.close()
    return output_filename


# ---------------------- Create Count Excel ----------------------
# ---------------------- Create Count Excel (Formatted like second script) ----------------------
def create_count_excel(df, output_path):
    df["sku"] = df["sku"].astype(str).str.strip().replace({"nan": "", "None": ""})
    df["soldBy"] = df["soldBy"].astype(str).fillna("")
    df["color"] = df.get("color", "")  # ensure column exists
    df["size"] = df.get("size", "")    # ensure column exists

    # SKU REPORT
    sku_df = df[["qty", "soldBy", "color", "sku"]].value_counts().reset_index()
    sku_df.columns = ["Qty", "SoldBy", "Color", "SKU", "Count"]
    sku_df["SKU_lower"] = sku_df["SKU"].str.lower()
    sku_df = sku_df.sort_values(by=["Count", "SKU_lower", "Qty"], ascending=[False, True, True])
    sku_df = sku_df.drop(columns=["SKU_lower"]).reset_index(drop=True)

    # COURIER + SOLD BY REPORT
    courierSold_df = df[["courier", "soldBy"]].value_counts().reset_index()
    courierSold_df.columns = ["Courier", "SoldBy", "Packages"]
    courierSold_df = courierSold_df.sort_values(by=["Packages", "Courier"], ascending=[False, True]).reset_index(drop=True)

    # COURIER REPORT
    courier_df = df[["courier"]].value_counts().reset_index()
    courier_df.columns = ["Courier", "Packages"]
    courier_df = courier_df.sort_values(by=["Packages", "Courier"], ascending=[False, True]).reset_index(drop=True)

    # SOLD BY REPORT
    soldby_df = df[["soldBy"]].value_counts().reset_index()
    soldby_df.columns = ["SoldBy", "Packages"]
    soldby_df = soldby_df.sort_values(by=["Packages", "SoldBy"], ascending=[False, True]).reset_index(drop=True)

    # Save Excel with formatting
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

        # Write all blocks
        write_block("SKU REPORT", sku_df)
        write_block("COURIER + SOLD BY REPORT", courierSold_df)
        write_block("COURIER REPORT", courier_df)
        write_block("SOLD BY REPORT", soldby_df)

    print(f"✅ Excel generated -> {summary_path}")
    return summary_path

