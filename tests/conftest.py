"""tests/conftest.py — pytest 全局 fixtures。"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import fitz
import pytest
from PIL import Image

BASE_DIR = Path(__file__).parent.parent

# ── 全局 PIL DecompressionBombWarning 抑制 ──────────────
# 合成测试 PDF 使用的较大图像会触发此警告，不影响测试正确性
Image.MAX_IMAGE_PIXELS = None  # 禁用解压炸弹检查
sys.path.insert(0, str(BASE_DIR / "src"))

# ── 路径 fixtures ──────────────────────────────────────
@pytest.fixture
def project_root() -> Path:
    return BASE_DIR


@pytest.fixture
def sample_pdf_path(project_root: Path) -> Path:
    """真实低清样册 PDF（有 18 页）。"""
    p = project_root / "sample_input" / "zifeng-brochure.pdf"
    if not p.exists():
        pytest.skip(f"样本文件不存在: {p}")
    return p


@pytest.fixture
def output_dir(project_root: Path, tmp_path: Path) -> Path:
    """每个测试独占的临时输出目录。"""
    out = tmp_path / "output"
    out.mkdir()
    return out


# ── 合成 PDF fixtures ─────────────────────────────────
@pytest.fixture
def tiny_text_pdf(tmp_path: Path) -> Path:
    """仅含文本的 1 页 PDF（用于字体嵌入测试）。"""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)  # A4
    page.insert_text((100, 200), "Hello, World!", fontsize=24)
    path = tmp_path / "tiny_text.pdf"
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def tiny_image_pdf(tmp_path: Path) -> Path:
    """含 72 DPI 图像的 1 页 PDF（用于 DPI 估算测试）。"""
    from PIL import Image
    import io

    # 创建 2550×3501 px @ 72dpi ≈ A4 尺寸（用于模拟低清位图样册）
    img = Image.new("RGB", (2550, 3501), color=(200, 220, 240))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    img_bytes = buf.getvalue()

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    img_rect = fitz.Rect(0, 0, 595, 842)  # 整页图像
    page.insert_image(img_rect, stream=img_bytes)
    path = tmp_path / "tiny_image.pdf"
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def tiny_cmyk_pdf(tmp_path: Path) -> Path:
    """含 CMYK 图像的 1 页 PDF（用于色彩空间检测测试）。"""
    from PIL import Image
    import io

    # CMYK 图像
    img = Image.new("CMYK", (800, 600), color=(0, 0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    img_bytes = buf.getvalue()

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(fitz.Rect(50, 50, 545, 550), stream=img_bytes)
    path = tmp_path / "tiny_cmyk.pdf"
    doc.save(str(path))
    doc.close()
    return path


# ── 模块级 parser fixture（可复用）────────────────────
@pytest.fixture
def parser():
    """共享的 PDFParser 实例。"""
    from pdf_parser import PDFParser
    return PDFParser()
