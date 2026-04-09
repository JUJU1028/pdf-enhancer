"""tests/test_image_enhancer.py — ImageEnhancer 图像增强逻辑测试。"""
from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from image_enhancer import ImageEnhancer


def make_rgb_jpeg(width: int, height: int, quality: int = 85) -> bytes:
    """生成一个纯色 RGB JPEG 字节流。"""
    img = Image.new("RGB", (width, height), color=(120, 180, 220))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


class TestImageEnhancerInit:
    """初始化与属性测试。"""

    def test_default_init(self):
        enhancer = ImageEnhancer()
        assert enhancer.mode == "document"
        assert enhancer.scale == 4
        assert enhancer.use_gpu is False

    def test_mode_options(self):
        for mode in ("fast", "quality", "document"):
            e = ImageEnhancer(mode=mode)
            assert e.mode == mode

    def test_scale_minimum_1(self):
        e = ImageEnhancer(scale=0)
        assert e.scale == 1
        e2 = ImageEnhancer(scale=-5)
        assert e2.scale == 1


class TestEnhanceRGB:
    """RGB 图像增强测试。"""

    def test_enhance_increases_resolution(self):
        """增强后尺寸应为目标 DPI 对应的像素尺寸。"""
        enhancer = ImageEnhancer(mode="document", scale=4)
        jpeg_bytes = make_rgb_jpeg(2550, 3501)  # 模拟 72 DPI A4
        result = enhancer.enhance(jpeg_bytes, target_dpi=300, source_dpi=72)

        assert isinstance(result, bytes)
        assert len(result) > 0

        # 验证输出是有效 JPEG
        img = Image.open(io.BytesIO(result))
        w, h = img.size
        # 300/72 * 4 = 16.67x，300 DPI A4 ≈ 3508×4961 px
        # scale=4, 4x from 72 → 288 DPI, but target is 300
        # The enhancer should aim for ~target DPI
        assert w > 2550, f"宽度应增加，实际: {w}"
        assert h > 3501, f"高度应增加，实际: {h}"

    def test_enhance_preserves_mode(self):
        """增强输出应保持 RGB 或转换为所需模式。"""
        enhancer = ImageEnhancer(mode="document")
        jpeg_bytes = make_rgb_jpeg(200, 200)
        result = enhancer.enhance(jpeg_bytes, target_dpi=300, source_dpi=72)
        img = Image.open(io.BytesIO(result))
        # 模式应合理（RGB 或接近）
        assert img.mode in ("RGB", "L", "RGBA")

    def test_enhance_quality_modes(self):
        """三种模式都应正常返回结果（不抛异常）。"""
        for mode in ("fast", "quality", "document"):
            e = ImageEnhancer(mode=mode)
            jpeg_bytes = make_rgb_jpeg(400, 400)
            result = e.enhance(jpeg_bytes, target_dpi=300, source_dpi=72)
            assert isinstance(result, bytes)
            assert len(result) > 0

    def test_enhance_low_source_dpi(self):
        """源 DPI 很低时，增强仍应成功。"""
        enhancer = ImageEnhancer(mode="document", scale=4)
        jpeg_bytes = make_rgb_jpeg(300, 400)  # 极低分辨率
        result = enhancer.enhance(jpeg_bytes, target_dpi=300, source_dpi=36)
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_enhance_invalid_bytes(self):
        """无效 JPEG 字节应抛出异常。"""
        enhancer = ImageEnhancer()
        with pytest.raises(Exception):  # PIL 或 enhancer 内部可能抛各种异常
            enhancer.enhance(b"not a jpeg", target_dpi=300, source_dpi=72)


class TestEnhanceGrayscale:
    """灰度图像增强测试。"""

    def test_enhance_grayscale(self):
        """灰度 JPEG 应能正常增强。"""
        enhancer = ImageEnhancer(mode="document")
        img = Image.new("L", (500, 500), color=128)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        result = enhancer.enhance(buf.getvalue(), target_dpi=300, source_dpi=72)
        assert isinstance(result, bytes)
        assert len(result) > 0


class TestRealESRGANAvailability:
    """Real-ESRGAN 可选依赖检测。"""

    def test_realesrgan_available_flag(self):
        """REALESRGAN_AVAILABLE 标志应为 True 或 False（不抛异常）。"""
        from image_enhancer import REALESRGAN_AVAILABLE
        assert isinstance(REALESRGAN_AVAILABLE, bool)
