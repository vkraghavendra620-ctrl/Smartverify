"""
Document Preprocessing Service
- Noise removal, contrast adjustment, deskewing, resizing
Uses OpenCV and Pillow.
"""
import cv2
import numpy as np
from PIL import Image, ImageEnhance, ImageFilter
import logging, os, uuid
from pathlib import Path
from app.core.config import settings

logger = logging.getLogger(__name__)


def preprocess_image(input_path: str) -> str:
    """Preprocess an image for optimal OCR performance. Returns path to preprocessed file."""
    try:
        img = cv2.imread(input_path)
        if img is None:
            logger.warning(f"cv2 could not read {input_path}, falling back to PIL")
            return _pil_preprocess(input_path)

        # 1. Resize if too small
        h, w = img.shape[:2]
        if w < 1000:
            scale = 1000 / w
            img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

        # 2. Convert to grayscale
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        # 3. Denoise
        denoised = cv2.fastNlMeansDenoising(gray, h=10)

        # 4. Adaptive thresholding for better contrast
        thresh = cv2.adaptiveThreshold(
            denoised, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, 11, 2
        )

        # 5. Deskew
        deskewed = _deskew(thresh)

        # Save preprocessed image
        out_path = _output_path(input_path, "_prep.png")
        cv2.imwrite(out_path, deskewed)
        logger.info(f"Preprocessed image saved to {out_path}")
        return out_path

    except Exception as e:
        logger.error(f"Preprocessing failed for {input_path}: {e}")
        return input_path  # Fall back to original


def _pil_preprocess(input_path: str) -> str:
    """Fallback preprocessing using Pillow."""
    img = Image.open(input_path).convert("L")
    img = ImageEnhance.Contrast(img).enhance(2.0)
    img = ImageEnhance.Sharpness(img).enhance(2.0)
    out_path = _output_path(input_path, "_prep.png")
    img.save(out_path)
    return out_path


def _deskew(image: np.ndarray) -> np.ndarray:
    """Correct skew in a binary image using Hough transform."""
    coords = np.column_stack(np.where(image > 0))
    if len(coords) < 10:
        return image
    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle
    if abs(angle) < 0.5:
        return image
    h, w = image.shape
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(image, M, (w, h), flags=cv2.INTER_CUBIC,
                              borderMode=cv2.BORDER_REPLICATE)
    return rotated


def _output_path(input_path: str, suffix: str) -> str:
    p = Path(input_path)
    return str(p.parent / (p.stem + suffix))
