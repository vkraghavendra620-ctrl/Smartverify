"""
Document Preprocessing Service
Enhanced OCR image enhancement pipeline:
- Resizing small/mobile photos
- Grayscale conversion
- Contrast enhancement (CLAHE)
- Denoising & sharpening
- Deskewing / Rotation correction
- Graceful fallback to original image or PIL
"""
import cv2
import numpy as np
from PIL import Image, ImageEnhance, ImageFilter
import logging, os
from pathlib import Path

logger = logging.getLogger(__name__)


def preprocess_image(input_path: str) -> str:
    """Preprocess an image for optimal OCR performance. Returns path to preprocessed file."""
    try:
        img = cv2.imread(input_path)
        if img is None:
            logger.warning(f"cv2 could not read {input_path}, falling back to PIL")
            return _pil_preprocess(input_path)

        # 1. Resize if too small (upscale to min 1500px width for OCR clarity)
        h, w = img.shape[:2]
        if w < 1500:
            scale = 1500.0 / w
            img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

        # 2. Convert to grayscale
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        # 3. CLAHE Contrast Enhancement
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray)

        # 4. Noise reduction
        denoised = cv2.fastNlMeansDenoising(enhanced, h=10)

        # 5. Sharpening filter
        kernel = np.array([[0, -1, 0],
                           [-1, 5, -1],
                           [0, -1, 0]], dtype=np.float32)
        sharpened = cv2.filter2D(denoised, -1, kernel)

        # 6. Adaptive Thresholding
        thresh = cv2.adaptiveThreshold(
            sharpened, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, 11, 2
        )

        # 7. Deskew
        deskewed = _deskew(thresh)

        # Save preprocessed image
        out_path = _output_path(input_path, "_prep.png")
        cv2.imwrite(out_path, deskewed)
        logger.info(f"Preprocessed image saved to {out_path}")
        return out_path

    except Exception as e:
        logger.error(f"Preprocessing failed for {input_path}: {e}")
        return input_path  # Fall back to original image on failure


def _pil_preprocess(input_path: str) -> str:
    """Fallback preprocessing using Pillow."""
    try:
        img = Image.open(input_path).convert("L")
        img = ImageEnhance.Contrast(img).enhance(2.0)
        img = ImageEnhance.Sharpness(img).enhance(2.0)
        out_path = _output_path(input_path, "_prep.png")
        img.save(out_path)
        return out_path
    except Exception as e:
        logger.error(f"PIL preprocessing failed: {e}")
        return input_path


def _deskew(image: np.ndarray) -> np.ndarray:
    """Correct skew in binary image."""
    try:
        coords = np.column_stack(np.where(image > 0))
        if len(coords) < 10:
            return image
        angle = cv2.minAreaRect(coords)[-1]
        if angle < -45:
            angle = -(90 + angle)
        else:
            angle = -angle
        if abs(angle) < 0.5 or abs(angle) > 45:
            return image
        h, w = image.shape
        center = (w // 2, h // 2)
        M = cv2.getRotationMatrix2D(center, angle, 1.0)
        rotated = cv2.warpAffine(image, M, (w, h), flags=cv2.INTER_CUBIC,
                                  borderMode=cv2.BORDER_REPLICATE)
        return rotated
    except Exception:
        return image


def _output_path(input_path: str, suffix: str) -> str:
    p = Path(input_path)
    return str(p.parent / (p.stem + suffix))
