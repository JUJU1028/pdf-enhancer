"""tests/test_pdf_parser.py — PDFParser 评分与解析逻辑测试。"""
from __future__ import annotations

import pytest
from pdf_parser import PDFParser


class TestPDFParserBasic:
    """基础解析功能测试。"""

    def test_parse_text_pdf(self, parser: PDFParser, tiny_text_pdf):
        report = parser.parse(str(tiny_text_pdf))
        assert report.page_count == 1
        assert len(report.pages) == 1

    def test_parse_image_pdf(self, parser: PDFParser, tiny_image_pdf):
        report = parser.parse(str(tiny_image_pdf))
        assert report.page_count == 1
        assert len(report.pages) == 1

    def test_parse_cmyk_pdf(self, parser: PDFParser, tiny_cmyk_pdf):
        report = parser.parse(str(tiny_cmyk_pdf))
        assert report.page_count == 1
        assert report.has_cmyk_images is True

    def test_real_sample(self, parser: PDFParser, sample_pdf_path):
        report = parser.parse(str(sample_pdf_path))
        assert report.page_count == 18, "真实样册应有 18 页"
        assert report.overall_min_dpi is not None, "应有 DPI 估算"
        assert report.print_ready_score > 0, "应有评分"


class TestPrintReadyScore:
    """评分逻辑边界测试。"""

    def test_cmyk_image_score_correctness(self, parser: PDFParser, tiny_cmyk_pdf):
        """CMYK 图像应触发 color_managed=True，评分应反映 DPI 与覆盖率。"""
        report = parser.parse(str(tiny_cmyk_pdf))
        assert report.has_cmyk_images is True
        assert report.color_managed is True, "CMYK 图像应标记为 color_managed"
        # 合成 CMYK PDF（800×600 px，97 DPI，覆盖率 37%）
        # 评分 = 基准分(30) + CMYK(30) + ICC(20) + min(50, DPI_ratio*50) - 低覆盖率
        # 实际值取决于解析器的评分算法，只需 > 0 证明逻辑正常
        assert report.print_ready_score > 0, (
            f"CMYK PDF 评分应 > 0，实际: {report.print_ready_score}"
        )
        # CMYK + ICC 时不应出现极低分
        assert report.print_ready_score >= 50, (
            f"CMYK+ICC 页面评分不应低于50，实际: {report.print_ready_score}"
        )

    def test_text_only_score(self, parser: PDFParser, tiny_text_pdf):
        """纯文字页面：字体嵌入=100；其他维度扣分。"""
        report = parser.parse(str(tiny_text_pdf))
        assert report.print_ready_score > 0

    def test_high_dpi_image_full_score(self, parser: PDFParser, tiny_image_pdf):
        """整页高分辨率图像（308 DPI）应获得满分或高分。"""
        report = parser.parse(str(tiny_image_pdf))
        assert report.overall_min_dpi is not None
        assert report.overall_min_dpi > 150, (
            f"合成图像 DPI={report.overall_min_dpi:.1f} 应 > 150"
        )
        # 高 DPI + 全页覆盖 + 无需转换 → 应满分
        assert report.print_ready_score == 100, (
            f"高DPI整页图像应满分，实际: {report.print_ready_score}"
        )

    def test_real_sample_score_diagnostic(self, parser: PDFParser, sample_pdf_path):
        """真实样册评分应在合理范围（0-100）。"""
        report = parser.parse(str(sample_pdf_path))
        assert 0 <= report.print_ready_score <= 100, (
            f"评分越界: {report.print_ready_score}"
        )
        # 至少应该有低 DPI 问题（因为是低清样册）
        assert report.overall_min_dpi is not None


class TestPageStrategy:
    """页面策略判断测试。"""

    def test_image_page_is_raster(self, parser: PDFParser, tiny_image_pdf):
        """整页图像页应被标记为 is_raster_page。"""
        report = parser.parse(str(tiny_image_pdf))
        page = report.pages[0]
        assert page.is_raster_page is True, "整页图像应标记为 raster_page"
        assert page.recommended_strategy == "rebuild_page"

    def test_text_page_preserve(self, parser: PDFParser, tiny_text_pdf):
        """纯文字页推荐 preserve 策略。"""
        report = parser.parse(str(tiny_text_pdf))
        page = report.pages[0]
        assert page.recommended_strategy == "preserve"

    def test_real_sample_strategies(self, parser: PDFParser, sample_pdf_path):
        """真实样册应有重建页和混合页。"""
        report = parser.parse(str(sample_pdf_path))
        strategies = {p.recommended_strategy for p in report.pages}
        # 低清位图样册，应大量 rebuild_page
        rebuild_count = sum(1 for p in report.pages if p.is_raster_page)
        assert rebuild_count > 0, "真实低清样册应有整页重建页"
        assert "rebuild_page" in strategies


class TestColorManaged:
    """color_managed 属性测试（ICCBased + CMYK）。"""

    def test_cmyk_is_color_managed(self, parser: PDFParser, tiny_cmyk_pdf):
        report = parser.parse(str(tiny_cmyk_pdf))
        assert report.color_managed is True

    def test_text_is_not_color_managed(self, parser: PDFParser, tiny_text_pdf):
        report = parser.parse(str(tiny_text_pdf))
        # 纯文字无图像，不触发 CMYK 或 ICC
        assert report.color_managed is False


class TestRenderPage:
    """页面渲染功能测试。"""

    def test_render_text_page(self, parser: PDFParser, tiny_text_pdf):
        img_bytes = parser.render_page_as_image(str(tiny_text_pdf), page_index=0, dpi=72)
        assert isinstance(img_bytes, bytes)
        assert len(img_bytes) > 0

    def test_render_image_page(self, parser: PDFParser, tiny_image_pdf):
        img_bytes = parser.render_page_as_image(str(tiny_image_pdf), page_index=0, dpi=72)
        assert isinstance(img_bytes, bytes)
        assert len(img_bytes) > 0

    def test_render_invalid_page_index(self, parser: PDFParser, tiny_text_pdf):
        with pytest.raises((IndexError, ValueError)):
            parser.render_page_as_image(str(tiny_text_pdf), page_index=99, dpi=72)
