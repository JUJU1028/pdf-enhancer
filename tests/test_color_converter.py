"""tests/test_color_converter.py — ColorConverter ICC 色彩转换测试。"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR / "src"))

from color_converter import ColorConverter, _auto_find_icc


class TestAutoFindICC:
    """ICC 自动发现逻辑测试。"""

    def test_auto_find_returns_path_or_none(self):
        """_auto_find_icc 应返回 Path 或 None。"""
        result = _auto_find_icc()
        if result is not None:
            assert isinstance(result, Path)
            assert result.name == "ISOcoated_v2_eci.icc"
            assert result.exists(), "返回的 ICC 路径必须存在"

    def test_auto_find_in_models_dir(self, project_root):
        """找到的 ICC 应在 models/ 目录下。"""
        result = _auto_find_icc()
        if result is not None:
            assert "models" in str(result), (
                f"ICC 路径应包含 models/，实际: {result}"
            )


class TestColorConverterInit:
    """ColorConverter 初始化测试。"""

    def test_init_with_explicit_icc(self, project_root):
        """传入显式 cmyk_icc_path 时应正常初始化。"""
        icc_path = project_root / "models" / "ISOcoated_v2_eci.icc"
        if icc_path.exists():
            cc = ColorConverter(cmyk_icc_path=str(icc_path))
            assert cc.cmyk_icc_path == icc_path

    def test_init_autofind(self):
        """不传 cmyk_icc_path 时应自动发现。"""
        cc = ColorConverter()  # 不抛异常即可
        assert cc is not None

    def test_invalid_icc_path_graceful_degradation(self):
        """无效 ICC 路径应触发优雅降级（回退到 Pillow 内置转换），不抛异常。"""
        from PIL import Image
        cc = ColorConverter(cmyk_icc_path="/nonexistent/path/to/file.icc")
        # 创建真实 RGB JPEG
        img = Image.new("RGB", (50, 50), color=(100, 150, 200))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        rgb_bytes = buf.getvalue()
        # 不应抛异常，应优雅降级
        result = cc.convert_bytes(rgb_bytes, quality=85)
        assert isinstance(result, tuple)
        assert len(result[0]) > 0  # 仍产生有效输出

    def test_init_with_rendering_intent(self):
        """rendering_intent 参数应被正确解析。"""
        for intent in ("perceptual", "relative", "saturation", "absolute"):
            cc = ColorConverter(rendering_intent=intent)
            assert cc.rendering_intent is not None


class TestConvertBytes:
    """字节级色彩转换测试（使用真实 API）。"""

    def _make_rgb_jpeg(self, size: int = 100, quality: int = 85) -> bytes:
        from PIL import Image
        img = Image.new("RGB", (size, size), color=(200, 100, 50))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        return buf.getvalue()

    def test_convert_bytes_returns_tuple(self):
        """convert_bytes 应返回 (bytes, str) 元组。"""
        cc = ColorConverter()
        rgb_bytes = self._make_rgb_jpeg()
        result = cc.convert_bytes(rgb_bytes, quality=85)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], bytes)
        assert result[1] in ("RGB", "CMYK", "YCbCr")

    def test_convert_bytes_increases_or_preserves(self):
        """CMYK 转换后字节流应有效。"""
        cc = ColorConverter()
        rgb_bytes = self._make_rgb_jpeg(size=500)
        cmyk_bytes, mode = cc.convert_bytes(rgb_bytes, quality=85)
        assert isinstance(cmyk_bytes, bytes)
        assert len(cmyk_bytes) > 0
        # mode 应为 CMYK（如果 ICC 有效）或 RGB（fallback）
        assert mode in ("RGB", "CMYK")

    def test_convert_bytes_idempotent(self):
        """相同输入两次调用应产生相同输出。"""
        cc = ColorConverter()
        rgb_bytes = self._make_rgb_jpeg()
        r1 = cc.convert_bytes(rgb_bytes, quality=85)
        r2 = cc.convert_bytes(rgb_bytes, quality=85)
        assert r1 == r2, "CMYK 转换应对相同输入产生相同输出"

    def test_convert_bytes_with_different_qualities(self):
        """不同 quality 参数应产生不同输出。"""
        cc = ColorConverter()
        rgb_bytes = self._make_rgb_jpeg(size=500)
        r_q80 = cc.convert_bytes(rgb_bytes, quality=80)
        r_q95 = cc.convert_bytes(rgb_bytes, quality=95)
        # 不同质量应产生不同字节
        assert r_q80[0] != r_q95[0], "不同 quality 应产生不同输出"

    def test_convert_bytes_invalid_input(self):
        """无效字节应抛出 ValueError。"""
        cc = ColorConverter()
        with pytest.raises(ValueError):
            cc.convert_bytes(b"not an image", quality=85)


class TestRenderingIntent:
    """渲染意图参数测试。"""

    def test_supported_intents(self):
        """四种渲染意图均应正常执行（不抛异常）。"""
        from PIL import Image
        cc = ColorConverter()
        img = Image.new("RGB", (50, 50), color=(100, 150, 200))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        rgb_bytes = buf.getvalue()

        for intent in ("perceptual", "relative", "saturation", "absolute"):
            cc = ColorConverter(rendering_intent=intent)
            result = cc.convert_bytes(rgb_bytes, quality=85)
            assert isinstance(result, tuple)
            assert len(result[0]) > 0
