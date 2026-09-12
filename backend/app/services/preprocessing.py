"""
Document Preprocessing Service
Comprehensive OCR image enhancement & quality assessment pipeline:
- Image Quality Assessment (blur, low-res, contrast, lighting, tilt, noise)
- Multi-variant generation (enhanced, upscaled, sharpened, thresholded, deskewed)
- Automatic deskewing and adaptive contrast
- Graceful fallback to original image or PIL
"""
import cv2
import numpy as np
from PIL import Image, ImageEnhance, ImageFilter
import logging, os
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

logger = logging.getLogger(__name__)


def assess_image_quality(input_path: str) -> Dict[str, Any]:
    """
    Analyze image quality before OCR.
    Detects blur, low resolution, poor contrast, uneven lighting, shadows, noise, and skew.
    Returns quality score, issues list, and non-blocking warning if quality is substandard.
    """
    try:
        img = cv2.imread(input_path)
        if img is None:
            return {
                "quality_score": 50,
                "issues": ["unreadable_file"],
                "warning": "Image quality is low. OCR may be inaccurate. Consider uploading a clearer image.",
                "details": {}
            }

        h, w = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img

        # 1. Blur detection via Laplacian variance
        lap_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        is_very_blurry = lap_var < 35.0
        is_blurry = lap_var < 80.0

        # 2. Resolution check
        total_pixels = h * w
        is_very_low_res = (w < 480 or h < 320 or total_pixels < 160000)
        is_low_res = (w < 720 or h < 480 or total_pixels < 350000)

        # 3. Contrast & Dynamic range
        contrast_std = float(gray.std())
        dynamic_range = float(gray.max() - gray.min())
        is_low_contrast = (contrast_std < 32.0 or dynamic_range < 70.0)

        # 4. Lighting & Exposure
        mean_brightness = float(gray.mean())
        is_dark = mean_brightness < 60.0
        is_overexposed = mean_brightness > 215.0

        # Uneven lighting across 3x3 grid blocks
        grid_rows = np.array_split(gray, 3, axis=0)
        block_means = []
        for r in grid_rows:
            for b in np.array_split(r, 3, axis=1):
                block_means.append(float(b.mean()))
        lighting_variance = float(np.std(block_means))
        is_uneven_lighting = lighting_variance > 40.0

        # 5. Skew Angle
        skew_angle = _estimate_skew_angle(gray)
        is_tilted = abs(skew_angle) > 3.5

        # 6. High-frequency noise
        diff = cv2.absdiff(gray, cv2.medianBlur(gray, 3))
        noise_level = float(diff.mean())
        is_noisy = noise_level > 18.0

        issues: List[str] = []
        score = 100

        if is_very_blurry:
            issues.append("excessive_blur")
            score -= 35
        elif is_blurry:
            issues.append("slight_blur")
            score -= 18

        if is_very_low_res:
            issues.append("very_low_resolution")
            score -= 30
        elif is_low_res:
            issues.append("low_resolution")
            score -= 15

        if is_low_contrast:
            issues.append("poor_contrast")
            score -= 15

        if is_dark:
            issues.append("underexposed_dark")
            score -= 15
        elif is_overexposed:
            issues.append("overexposed_bright")
            score -= 15

        if is_uneven_lighting:
            issues.append("uneven_lighting")
            score -= 12

        if is_noisy:
            issues.append("high_noise")
            score -= 10

        if is_tilted:
            issues.append("document_tilt")
            score -= 10

        score = max(5, min(100, score))

        warning = None
        has_severe = any(i in issues for i in ["excessive_blur", "very_low_resolution", "poor_contrast", "high_noise"])
        if score < 70 or has_severe or len(issues) >= 2:
            warning = "Image quality is low. OCR may be inaccurate. Consider uploading a clearer image."

        logger.info(f"Image quality for {Path(input_path).name}: score={score}, issues={issues}")
        return {
            "quality_score": score,
            "issues": issues,
            "warning": warning,
            "details": {
                "blur_variance": round(lap_var, 1),
                "resolution": f"{w}x{h}",
                "contrast_std": round(contrast_std, 1),
                "mean_brightness": round(mean_brightness, 1),
                "lighting_variance": round(lighting_variance, 1),
                "skew_angle": round(skew_angle, 2),
                "noise_level": round(noise_level, 2),
            }
        }
    except Exception as e:
        logger.warning(f"Image quality assessment failed for {input_path}: {e}")
        return {
            "quality_score": 75,
            "issues": [],
            "warning": None,
            "details": {}
        }


def _estimate_skew_angle(gray: np.ndarray) -> float:
    """Estimate skew angle of document in degrees using binary threshold moments."""
    try:
        thresh = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 15, 4
        )
        coords = np.column_stack(np.where(thresh > 0))
        if len(coords) < 50:
            return 0.0
        angle = cv2.minAreaRect(coords)[-1]
        if angle < -45:
            angle = -(90 + angle)
        else:
            angle = -angle
        if abs(angle) > 45:
            return 0.0
        return float(angle)
    except Exception:
        return 0.0


def generate_preprocessing_variants(
    input_path: str,
    quality_report: Optional[Dict[str, Any]] = None
) -> Dict[str, np.ndarray]:
    """
    Generate multiple suitable image versions for multi-pass OCR:
    - 'enhanced': CLAHE contrast enhancement + denoising (ideal all-rounder)
    - 'sharpened': High-pass edge sharpening (crucial for blurry captures)
    - 'upscaled': 2x/3x bicubic interpolation (crucial for low-resolution captures)
    - 'thresholded': Adaptive Gaussian binarization (crucial for uneven lighting/shadows)
    - 'deskewed': Perspective/tilt rotated (if tilted)
    - 'original': Grayscale base
    """
    variants: Dict[str, np.ndarray] = {}
    try:
        img = cv2.imread(input_path)
        if img is None:
            return variants

        h, w = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img.copy()
        variants["original"] = gray

        # 1. Base CLAHE Enhanced
        clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
        clahe_gray = clahe.apply(gray)
        denoised = cv2.fastNlMeansDenoising(clahe_gray, h=8)
        
        # Scale base enhanced to minimum 1500px width for OCR line clarity
        if w < 1500:
            scale = 1500.0 / w
            enhanced_base = cv2.resize(denoised, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        else:
            enhanced_base = denoised.copy()
        variants["enhanced"] = enhanced_base

        # 2. Sharpened Variant (Unsharp mask for blurry images)
        gaussian = cv2.GaussianBlur(enhanced_base, (0, 0), 2.0)
        sharpened = cv2.addWeighted(enhanced_base, 1.8, gaussian, -0.8, 0)
        variants["sharpened"] = sharpened

        # 3. Upscaled Variant (Super-resolution scaling for low-res captures)
        target_scale = 2.0 if w < 1200 else 1.5
        upscaled = cv2.resize(enhanced_base, None, fx=target_scale, fy=target_scale, interpolation=cv2.INTER_CUBIC)
        # Apply gentle sharpness to upscaled
        kernel_sharp = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], dtype=np.float32)
        upscaled_sharp = cv2.filter2D(upscaled, -1, kernel_sharp)
        variants["upscaled"] = upscaled_sharp

        # 4. Adaptive Thresholded Variant (Binarized for uneven lighting & shadows)
        thresh = cv2.adaptiveThreshold(
            enhanced_base, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, 15, 3
        )
        variants["thresholded"] = thresh

        # 5. Deskewed Variant (if tilted)
        skew_angle = quality_report.get("details", {}).get("skew_angle", 0.0) if quality_report else _estimate_skew_angle(gray)
        if abs(skew_angle) > 1.2:
            variants["deskewed"] = _deskew(enhanced_base)

    except Exception as e:
        logger.error(f"Error generating preprocessing variants for {input_path}: {e}")

    return variants


def preprocess_image(input_path: str) -> str:
    """Preprocess an image for optimal OCR performance (primary single-file path)."""
    try:
        img = cv2.imread(input_path)
        if img is None:
            logger.warning(f"cv2 could not read {input_path}, falling back to PIL")
            return _pil_preprocess(input_path)

        # Upscale if too small (min 1500px width)
        h, w = img.shape[:2]
        if w < 1500:
            scale = 1500.0 / w
            img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img

        clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray)
        denoised = cv2.fastNlMeansDenoising(enhanced, h=8)

        kernel = np.array([[0, -1, 0],
                           [-1, 5, -1],
                           [0, -1, 0]], dtype=np.float32)
        sharpened = cv2.filter2D(denoised, -1, kernel)

        deskewed = _deskew(sharpened)

        out_path = _output_path(input_path, "_prep.png")
        cv2.imwrite(out_path, deskewed)
        logger.info(f"Preprocessed image saved to {out_path}")
        return out_path

    except Exception as e:
        logger.error(f"Preprocessing failed for {input_path}: {e}")
        return input_path


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


def _deskew(image: np.ndarray, thresh_img: np.ndarray = None) -> np.ndarray:
    """Correct skew in image using binary mask for angle calculation."""
    try:
        if thresh_img is None:
            thresh_img = cv2.adaptiveThreshold(
                image, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 15, 4
            )
        coords = np.column_stack(np.where(thresh_img > 0))
        if len(coords) < 15:
            return image
        angle = cv2.minAreaRect(coords)[-1]
        if angle < -45:
            angle = -(90 + angle)
        else:
            angle = -angle
        if abs(angle) < 0.5 or abs(angle) > 45:
            return image
        h, w = image.shape[:2]
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

