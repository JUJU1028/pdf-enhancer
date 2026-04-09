"""
color_converter.py — 色彩管理模块
职责：RGB图像 → CMYK转换，尽量使用用户提供的ICC Profile；
若未提供ICC，则诚实回退为Pillow CMYK转换，而不是伪造ICC流程。
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Optional

from PIL import Image, ImageCms


# 按优先级搜索的ICC文件名
DEFAULT_ICC_CANDIDATES = [
    "ISOcoated_v2_eci.icc",
    "PSOcoated_v3.icc",
    "ISOcoated_v2_fogra39.icc",
    "CoatedFOGRA39.icc",
    "JapanColor2011Coated.icc",
]

_DEFAULT_ICC_SEARCH_DIRS = [
    Path(__file__).resolve().parent.parent / "models",
    Path.home() / ".pdf-enhancer" / "models",
]


def _auto_find_icc() -> Optional[Path]:
    """在项目 models/ 和用户目录中自动搜索可用的印刷ICC。"""
    for search_dir in _DEFAULT_ICC_SEARCH_DIRS:
        if not search_dir.is_dir():
            continue
        for candidate in DEFAULT_ICC_CANDIDATES:
            found = search_dir / candidate
            if found.exists():
                return found
    return None


class ColorConverter:
    """RGB 到 CMYK 的色彩转换器。"""

    def __init__(
        self,
        rendering_intent: str = "perceptual",
        cmyk_icc_path: Optional[str | Path] = None,
    ):
        self.rendering_intent = self._parse_intent(rendering_intent)
        resolved = Path(cmyk_icc_path) if cmyk_icc_path else _auto_find_icc()
        self.cmyk_icc_path = resolved
        self._transform = None
        self._icc_profile_bytes = None

    def _parse_intent(self, intent_str: str) -> int:
        mapping = {
            "perceptual": ImageCms.Intent.PERCEPTUAL,
            "relative": ImageCms.Intent.RELATIVE_COLORIMETRIC,
            "saturation": ImageCms.Intent.SATURATION,
            "absolute": ImageCms.Intent.ABSOLUTE_COLORIMETRIC,
        }
        return mapping.get(intent_str.lower(), ImageCms.Intent.PERCEPTUAL)

    def _prepare_image(self, image: Image.Image) -> Image.Image:
        if image.mode == "CMYK":
            return image
        if image.mode == "RGBA":
            background = Image.new("RGB", image.size, (255, 255, 255))
            background.paste(image, mask=image.split()[3])
            return background
        if image.mode != "RGB":
            return image.convert("RGB")
        return image

    def _load_icc_transform(self):
        if self._transform is not None:
            return self._transform
        if not self.cmyk_icc_path or not self.cmyk_icc_path.exists():
            return None

        try:
            input_profile = ImageCms.createProfile("sRGB")
            output_profile = ImageCms.getOpenProfile(str(self.cmyk_icc_path))
            self._icc_profile_bytes = self.cmyk_icc_path.read_bytes()
            self._transform = ImageCms.buildTransform(
                input_profile,
                output_profile,
                "RGB",
                "CMYK",
                renderingIntent=self.rendering_intent,
                flags=0,
            )
            print(f"  [色彩] 使用ICC Profile: {self.cmyk_icc_path.name}")
            return self._transform
        except Exception as exc:
            print(f"  [警告] ICC变换初始化失败({exc})，回退到Pillow内置CMYK转换")
            self._transform = None
            self._icc_profile_bytes = None
            return None

    def convert_image(self, image: Image.Image) -> Image.Image:
        image = self._prepare_image(image)
        if image.mode == "CMYK":
            print("  [色彩] 图像已是CMYK，跳过转换")
            return image

        transform = self._load_icc_transform()
        if transform is not None:
            try:
                return ImageCms.applyTransform(image, transform)
            except Exception as exc:
                print(f"  [警告] ICC应用失败({exc})，回退到Pillow内置CMYK转换")

        return image.convert("CMYK")

    def convert_bytes(
        self,
        image_bytes: bytes,
        input_format: str = "JPEG",
        quality: int = 80,
        subsampling: int = 2,
    ) -> tuple[bytes, str]:
        try:
            img = Image.open(io.BytesIO(image_bytes))
        except Exception as exc:
            raise ValueError(f"无法解析图像数据: {exc}")

        cmyk_img = self.convert_image(img)
        out_buf = io.BytesIO()
        save_kwargs = {
            "format": "JPEG",
            "quality": quality,
            "subsampling": subsampling,
            "optimize": True,
        }


        if self._icc_profile_bytes:
            save_kwargs["icc_profile"] = self._icc_profile_bytes
        cmyk_img.save(out_buf, **save_kwargs)
        return out_buf.getvalue(), cmyk_img.mode

    def get_icc_profile_bytes(self) -> Optional[bytes]:
        return self._icc_profile_bytes

    @staticmethod
    def analyze_image_colorspace(image_bytes: bytes) -> dict:
        try:
            img = Image.open(io.BytesIO(image_bytes))
            icc = img.info.get("icc_profile")
            icc_desc = None
            if icc:
                try:
                    profile = ImageCms.ImageCmsProfile(io.BytesIO(icc))
                    icc_desc = ImageCms.getProfileDescription(profile)
                except Exception:
                    icc_desc = "未知Profile"

            return {
                "mode": img.mode,
                "has_icc": icc is not None,
                "icc_description": icc_desc,
                "size": img.size,
            }
        except Exception as exc:
            return {
                "mode": "unknown",
                "has_icc": False,
                "icc_description": None,
                "error": str(exc),
            }


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("用法: python color_converter.py <input.jpg> <output_cmyk.jpg>")
        sys.exit(1)

    converter = ColorConverter(rendering_intent="perceptual")

    with open(sys.argv[1], "rb") as file_obj:
        img_bytes = file_obj.read()

    info = ColorConverter.analyze_image_colorspace(img_bytes)
    print(f"原始色彩模式: {info['mode']}, ICC Profile: {info['icc_description']}")

    out_bytes, mode = converter.convert_bytes(img_bytes)

    with open(sys.argv[2], "wb") as file_obj:
        file_obj.write(out_bytes)

    print(f"转换完成 → {mode}，输出: {sys.argv[2]}")
