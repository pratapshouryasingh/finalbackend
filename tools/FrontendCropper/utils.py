import json
import fitz  # PyMuPDF
import os

def load_config(path: str):
    with open(path, "r") as f:
        return json.load(f)

def crop_pdf_all_pages(input_file: str, output_file: str, crop: dict):
    import fitz
    doc = fitz.open(input_file)

    for page_num, page in enumerate(doc, start=1):
        page_width, page_height = page.rect.width, page.rect.height

        x = crop["x"]
        y = crop["y"]
        w = crop["width"]
        h = crop["height"]

        # --- Scale factors (canvas → PDF page space) ---
        canvas_w = crop.get("canvasWidth")
        canvas_h = crop.get("canvasHeight")

        if canvas_w and canvas_h:
            scale_x = page_width / canvas_w
            scale_y = page_height / canvas_h
        else:
            # fallback: assume frontend already in PDF units
            scale_x = 1
            scale_y = 1

        # --- Convert to PDF coords (bottom-left origin) ---
        x0 = x * scale_x
        y0 = page_height - (y + h) * scale_y
        x1 = (x + w) * scale_x
        y1 = page_height - y * scale_y

        rect = fitz.Rect(x0, y0, x1, y1)
        page.set_cropbox(rect)

    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    doc.save(output_file)
    doc.close()
