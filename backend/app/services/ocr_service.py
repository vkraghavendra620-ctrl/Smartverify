"""
OCR Service
Extracts text from images and PDFs using EasyOCR (line-by-line mode) with Tesseract fallback.
"""
import logging, os
from pathlib import Path
from PIL import Image

logger = logging.getLogger(__name__)

# Lazy-load EasyOCR reader to avoid startup delay
_easyocr_reader = None

def _get_easyocr():
    global _easyocr_reader
    if _easyocr_reader is None:
        try:
            import easyocr
            _easyocr_reader = easyocr.Reader(["en"], gpu=False)
            logger.info("EasyOCR reader initialised")
        except Exception as e:
            logger.warning(f"EasyOCR unavailable: {e}")
    return _easyocr_reader


def extract_text_from_image(image_path: str) -> str:
    """Extract text line-by-line using EasyOCR with Tesseract fallback."""
    text = ""

    # Primary: EasyOCR in line-by-line mode (paragraph=False preserves layout lines)
    try:
        reader = _get_easyocr()
        if reader:
            results = reader.readtext(image_path, detail=0, paragraph=False)
            text = "\n".join(results)
            if text.strip():
                logger.info(f"EasyOCR extracted {len(results)} lines ({len(text)} chars) from {image_path}")
                return text.strip()
    except Exception as e:
        logger.warning(f"EasyOCR failed: {e}")

    # Fallback: Tesseract
    try:
        import pytesseract
        img = Image.open(image_path)
        text = pytesseract.image_to_string(img, lang="eng")
        logger.info(f"Tesseract extracted {len(text)} chars from {image_path}")
    except Exception as e:
        logger.error(f"Tesseract unavailable/failed: {e}")

    return text.strip()


def extract_text_from_pdf(pdf_path: str) -> str:
    """Extract text from PDF pages (converts each page to image first)."""
    try:
        import pdf2image
        pages = pdf2image.convert_from_path(pdf_path, dpi=200)
        texts = []
        for i, page in enumerate(pages):
            tmp_path = pdf_path + f"_page_{i}.png"
            page.save(tmp_path, "PNG")
            texts.append(extract_text_from_image(tmp_path))
            os.remove(tmp_path)
        return "\n".join(texts)
    except Exception as e:
        logger.error(f"PDF text extraction failed: {e}")
        try:
            import pdfplumber
            with pdfplumber.open(pdf_path) as pdf:
                return "\n".join(p.extract_text() or "" for p in pdf.pages)
        except Exception as e2:
            logger.error(f"pdfplumber also failed: {e2}")
            return ""


def extract_text(file_path: str) -> str:
    """Route file to the appropriate extractor."""
    ext = Path(file_path).suffix.lower()
    if ext == ".pdf":
        return extract_text_from_pdf(file_path)
    else:
        return extract_text_from_image(file_path)
