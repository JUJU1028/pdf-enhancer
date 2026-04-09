"""
image_enhancer.py — 图像增强模块
职责：
1. 针对低清样册页做去噪、去伪影、局部对比度恢复
2. 支持传统插值、文档增强模式，以及可选Real-ESRGAN
3. 输出更适合印刷再封装的高分辨率图像
"""

from __future__ import annotations

import io
import math
from pathlib import Path
from typing import Literal

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

try:
    from basicsr.archs.rrdbnet_arch import RRDBNet
    from realesrgan import RealESRGANer
    REALESRGAN_AVAILABLE = True
except ImportError:
    REALESRGAN_AVAILABLE = False


class ImageEnhancer:
    """图像增强器。"""

    MODEL_URLS = {
        "x4plus": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth",
        "x4plus_anime": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth",
    }

    def __init__(
        self,
        mode: Literal["fast", "quality", "document"] = "document",
        scale: int = 4,
        model_type: str = "x4plus",
        model_dir: str | Path = "models",
        use_gpu: bool = False,
    ):
        self.mode = mode
        self.scale = max(1, scale)
        self.model_type = model_type
        self.model_dir = Path(model_dir)
        self.use_gpu = use_gpu
        self._upsampler = None

    def enhance(
        self,
        image_bytes: bytes,
        target_dpi: int = 300,
        source_dpi: float = 72.0,
    ) -> bytes:
        enhanced = self.enhance_image(image_bytes, target_dpi=target_dpi, source_dpi=source_dpi)
        out_buf = io.BytesIO()
        enhanced.save(out_buf, format="PNG", compress_level=1)
        return out_buf.getvalue()

    def enhance_image(
        self,
        image_bytes: bytes,
        target_dpi: int = 300,
        source_dpi: float = 72.0,
    ) -> Image.Image:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")

        if self.mode == "document":
            img = self._document_restore(img)
        elif self.mode == "quality":
            img = self._gentle_restore(img)

        needed_scale = max(1.0, target_dpi / max(source_dpi, 1.0))
        coarse_scale = min(self.scale, max(1, int(math.floor(needed_scale))))

        print(
            f"  [增强] 原始DPI={source_dpi:.1f} → 目标{target_dpi}DPI，"
            f"需要放大{needed_scale:.2f}x，主放大{coarse_scale}x"
        )

        if needed_scale <= 1.02:
            upscaled = img
        elif self.mode == "quality" and REALESRGAN_AVAILABLE and coarse_scale >= 2:
            upscaled = self._ai_upscale(img, coarse_scale)
        else:
            upscaled = self._lanczos_upscale(img, coarse_scale)

        target_width = max(upscaled.width, int(round(img.width * needed_scale)))
        target_height = max(upscaled.height, int(round(img.height * needed_scale)))
        if upscaled.width != target_width or upscaled.height != target_height:
            upscaled = upscaled.resize((target_width, target_height), Image.LANCZOS)

        upscaled = self._apply_local_contrast(upscaled)
        upscaled = self._apply_print_sharpening(upscaled)
        return upscaled

    def _lanczos_upscale(self, img: Image.Image, scale: int) -> Image.Image:
        if scale <= 1:
            return img
        new_w = max(img.width, int(round(img.width * scale)))
        new_h = max(img.height, int(round(img.height * scale)))
        return img.resize((new_w, new_h), Image.LANCZOS)

    def _ai_upscale(self, img: Image.Image, scale: int) -> Image.Image:
        upsampler = self._get_upsampler()
        if upsampler is None:
            print("  [降级] AI模型不可用，使用Lanczos")
            return self._lanczos_upscale(img, scale)

        try:
            img_np = np.array(img.convert("RGB"))
            img_bgr = img_np[:, :, ::-1]
            output, _ = upsampler.enhance(img_bgr, outscale=scale)
            output_rgb = output[:, :, ::-1]
            return Image.fromarray(output_rgb)
        except Exception as exc:
            print(f"  [警告] AI超分失败({exc})，回退到Lanczos")
            return self._lanczos_upscale(img, scale)

    def _get_upsampler(self):
        if self._upsampler is not None:
            return self._upsampler
        if not REALESRGAN_AVAILABLE:
            return None

        model_path = self.model_dir / f"RealESRGAN_{self.model_type}.pth"
        if not model_path.exists():
            print(f"  [信息] 模型文件不存在: {model_path}")
            print(f"  [信息] 下载链接: {self.MODEL_URLS.get(self.model_type, '未知')}")
            return None

        try:
            import torch

            device = "cuda" if (self.use_gpu and torch.cuda.is_available()) else "cpu"
            print(f"  [AI] 加载Real-ESRGAN模型，设备: {device}")
            model = RRDBNet(
                num_in_ch=3,
                num_out_ch=3,
                num_feat=64,
                num_block=23,
                num_grow_ch=32,
                scale=4,
            )
            self._upsampler = RealESRGANer(
                scale=4,
                model_path=str(model_path),
                model=model,
                tile=1024,
                tile_pad=24,
                pre_pad=10,
                half=False,
                device=device,
            )
            return self._upsampler
        except Exception as exc:
            print(f"  [警告] 模型加载失败: {exc}")
            return None

    def _document_restore(self, img: Image.Image) -> Image.Image:
        restored = self._gentle_restore(img)
        if not CV2_AVAILABLE:
            return restored

        bgr = cv2.cvtColor(np.array(restored), cv2.COLOR_RGB2BGR)
        lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
        l_channel, a_channel, b_channel = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8))
        l_channel = clahe.apply(l_channel)
        merged = cv2.merge((l_channel, a_channel, b_channel))
        enhanced = cv2.cvtColor(merged, cv2.COLOR_LAB2RGB)
        return Image.fromarray(enhanced)

    def _gentle_restore(self, img: Image.Image) -> Image.Image:
        if not CV2_AVAILABLE:
            softened = img.filter(ImageFilter.GaussianBlur(radius=0.35))
            return ImageEnhance.Sharpness(softened).enhance(1.15)

        rgb = np.array(img.convert("RGB"))
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        denoised = cv2.fastNlMeansDenoisingColored(bgr, None, 3, 3, 7, 21)
        denoised = cv2.bilateralFilter(denoised, d=5, sigmaColor=30, sigmaSpace=25)
        return Image.fromarray(cv2.cvtColor(denoised, cv2.COLOR_BGR2RGB))

    def _apply_local_contrast(self, img: Image.Image) -> Image.Image:
        contrast = ImageEnhance.Contrast(img).enhance(1.04)
        color = ImageEnhance.Color(contrast).enhance(1.02)
        return color

    def _apply_print_sharpening(self, img: Image.Image) -> Image.Image:
        return img.filter(ImageFilter.UnsharpMask(radius=1.1, percent=165, threshold=3))

    def remove_jpeg_artifacts(self, image_bytes: bytes) -> bytes:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        cleaned = self._gentle_restore(img)
        out_buf = io.BytesIO()
        cleaned.save(out_buf, format="PNG", compress_level=1)
        return out_buf.getvalue()

    @staticmethod
    def estimate_output_dpi(original_dpi: float, scale: float) -> float:
        return original_dpi * scale


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("用法: python image_enhancer.py <input.jpg> <output.png> [fast|quality|document]")
        sys.exit(1)

    mode = sys.argv[3] if len(sys.argv) > 3 else "document"
    enhancer = ImageEnhancer(mode=mode, scale=4)

    with open(sys.argv[1], "rb") as file_obj:
        img_bytes = file_obj.read()

    out_bytes = enhancer.enhance(img_bytes, target_dpi=300, source_dpi=72)

    with open(sys.argv[2], "wb") as file_obj:
        file_obj.write(out_bytes)

    print(f"增强完成 → {sys.argv[2]}")
