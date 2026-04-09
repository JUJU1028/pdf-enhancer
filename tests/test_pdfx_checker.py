"""tests/test_pdfx_checker.py — PDF/X 合规预检模块测试。"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import fitz
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pdfx_checker import (
    PDFXChecker,
    PDFXReport,
    FontInfo,
    BleedInfo,
    OutputIntentInfo,
    check_pdfx,
)


# ── Fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def empty_pdf(tmp_path: Path) -> Path:
    """空白 PDF（无字体、无图像）。"""
    path = tmp_path / "empty.pdf"
    doc = fitz.open()
    doc.new_page(width=595, height=842)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def font_embedded_pdf(tmp_path: Path) -> Path:
    """含嵌入字体的 PDF（文字页面）。"""
    path = tmp_path / "font_embedded.pdf"
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((100, 400), "Hello World — 印刷测试", fontsize=24)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def pdf_with_bleed(tmp_path: Path) -> Path:
    """含出血位信息的 PDF。"""
    path = tmp_path / "with_bleed.pdf"
    doc = fitz.open()
    page = doc.new_page(width=620, height=877)   # A4 + 3mm bleed each side
    page.insert_text((100, 400), "Bleed Test Page", fontsize=24)
    doc.save(str(path))
    doc.close()
    return path


# ── Tests: OutputIntent ────────────────────────────────────────────

class TestOutputIntent:
    """GTS_OutputIntent 检测。"""

    def test_empty_pdf_no_output_intent(self, empty_pdf: Path):
        checker = PDFXChecker()
        report = checker.check(empty_pdf)
        assert report.output_intent.present is False
        assert report.output_intent.standard is None
        assert report.output_intent.output_profile is None

    def test_check_returns_pdfx_report(self, empty_pdf: Path):
        report = check_pdfx(empty_pdf)
        assert isinstance(report, PDFXReport)
        assert report.file_path == str(empty_pdf)
        assert isinstance(report.fonts, list)
        assert isinstance(report.bleed, BleedInfo)
        assert isinstance(report.issues, list)
        assert isinstance(report.suggestions, list)

    def test_score_reflects_missing_intent(self, empty_pdf: Path):
        report = check_pdfx(empty_pdf)
        # 无 OutputIntent → 0分；但因为无严重问题+无字体，给部分分
        assert report.pdfx_score < 100
        assert "严重" in report.issues[0] if report.issues else True


# ── Tests: Font Detection ──────────────────────────────────────────

class TestFontDetection:
    """字体嵌入检测。"""

    def test_empty_pdf_no_fonts(self, empty_pdf: Path):
        checker = PDFXChecker()
        report = checker.check(empty_pdf)
        assert len(report.fonts) == 0

    def test_font_embedded_detected(self, font_embedded_pdf: Path):
        checker = PDFXChecker()
        report = checker.check(font_embedded_pdf)
        assert len(report.fonts) >= 1
        assert any(f.embedded for f in report.fonts)

    def test_font_info_fields(self, font_embedded_pdf: Path):
        checker = PDFXChecker()
        report = checker.check(font_embedded_pdf)
        for f in report.fonts:
            assert isinstance(f.name, str)
            assert isinstance(f.embedded, bool)
            assert isinstance(f.subset, bool)
            assert isinstance(f.type_name, str)


# ── Tests: Bleed ───────────────────────────────────────────────────

class TestBleedDetection:
    """出血位检测。"""

    def test_empty_pdf_bleed_defaults(self, empty_pdf: Path):
        checker = PDFXChecker()
        report = checker.check(empty_pdf)
        assert report.bleed.has_bleed_box is False
        assert report.bleed.bleed_mm == 0.0

    def test_bleed_info_dataclass(self):
        bleed = BleedInfo(
            has_bleed_box=False,
            bleed_left=0, bleed_right=0, bleed_top=0, bleed_bottom=0,
            bleed_mm=0.0,
            has_trim_box=False, trim_left=0, trim_right=0, has_art_box=False,
        )
        assert bleed.bleed_mm == 0.0
        assert bleed.has_bleed_box is False


# ── Tests: PDFX Score ──────────────────────────────────────────────

class TestPDFXScore:
    """PDF/X 合规评分。"""

    def test_score_bounds(self, empty_pdf: Path):
        report = check_pdfx(empty_pdf)
        assert 0 <= report.pdfx_score <= 100

    def test_score_dict(self, empty_pdf: Path):
        report = check_pdfx(empty_pdf)
        d = report.to_dict()
        assert "pdfx_score" in d
        assert "pdfx_compliant" in d
        assert "issues" in d
        assert "suggestions" in d
        assert isinstance(d["issues"], list)
        assert isinstance(d["suggestions"], list)


# ── Tests: Edge Cases ─────────────────────────────────────────────

class TestEdgeCases:
    """边缘情况。"""

    def test_nonexistent_file_raises(self):
        checker = PDFXChecker()
        with pytest.raises(Exception):
            checker.check("/nonexistent/path/file.pdf")

    def test_outputintent_info_dataclass(self):
        oi = OutputIntentInfo(
            present=True,
            standard="PDF/X-1a:2003",
            output_profile="ISOcoated_v2_eci",
            registry_name="http://www.eci.org",
            condition="Offset printing",
        )
        assert oi.present is True
        assert "PDF/X" in oi.standard
        assert "ISOcoated" in oi.output_profile

    def test_font_info_dataclass(self):
        fi = FontInfo(
            name="TestFont",
            family="Test Family",
            embedded=True,
            subset=True,
            type_name="TrueType",
            is_cid=False,
            to_unicode_present=True,
        )
        assert fi.embedded is True
        assert fi.subset is True
        assert fi.type_name == "TrueType"

    def test_checker_bleed_tolerance_param(self, empty_pdf: Path):
        """不同的 bleed_tolerance_mm 参数应不影响基础检测。"""
        checker = PDFXChecker(bleed_tolerance_mm=1.0)
        report = checker.check(empty_pdf)
        assert report.bleed.bleed_mm == 0.0  # 无出血，容差不影响
