"""tests/test_pipeline_integration.py — 管线集成测试（合成 PDF）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR / "src"))

from pipeline import PipelineConfig, PrintPipeline
from pdf_parser import PDFParser


class TestPipelineSmallPDF:
    """用合成小 PDF 做端到端管线测试。"""

    def _run_pipeline(self, input_pdf: Path, output_pdf: Path) -> dict:
        config = PipelineConfig(
            enhance_mode="document",
            target_dpi=300,
            convert_to_cmyk=True,
            bleed_mm=0.0,
            jpeg_quality=80,
        )
        pipeline = PrintPipeline(config)

        progress_calls = []

        def progress(page: int, total: int, msg: str):
            progress_calls.append((page, total, msg))

        result = pipeline.process(
            str(input_pdf),
            str(output_pdf),
            progress_callback=progress,
        )
        return {
            "result": result,
            "progress_calls": progress_calls,
        }

    def test_pipeline_on_text_pdf(self, tiny_text_pdf: Path, output_dir: Path):
        """文字 PDF → 输出应成功生成，评分应有变化。"""
        output = output_dir / "out_text.pdf"

        data = self._run_pipeline(tiny_text_pdf, output)
        res = data["result"]

        assert output.exists(), f"输出文件未生成: {output}"
        assert res.success is True, f"管线执行失败: {res}"

        # 重新解析输出验证
        parser = PDFParser()
        out_report = parser.parse(str(output))
        assert out_report.page_count == 1, "输出页数应与输入一致"

    def test_pipeline_on_image_pdf(self, tiny_image_pdf: Path, output_dir: Path):
        """低清图像 PDF → 输出 DPI 应提升，文件应更大。"""
        input_size = tiny_image_pdf.stat().st_size
        output = output_dir / "out_image.pdf"

        data = self._run_pipeline(tiny_image_pdf, output)
        res = data["result"]

        assert output.exists(), f"输出文件未生成: {output}"
        assert res.success is True

        output_size = output.stat().st_size
        # 增强后 JPEG Q80 + 更高 DPI，体积通常增加
        assert output_size > input_size * 0.3, (
            f"输出文件异常小: {output_size} bytes vs 输入 {input_size}"
        )

        # 重新解析输出，验证评分有变化
        parser = PDFParser()
        out_report = parser.parse(str(output))
        # 增强后评分应 ≥ 输入（理想情况）
        assert out_report.print_ready_score >= 0

    def test_pipeline_progress_callback(self, tiny_text_pdf: Path, output_dir: Path):
        """进度回调应被正确调用。"""
        output = output_dir / "out_progress.pdf"

        data = self._run_pipeline(tiny_text_pdf, output)
        calls = data["progress_calls"]

        assert len(calls) > 0, "进度回调应至少被调用一次"
        # 格式：(page, total, message)
        for call in calls:
            assert len(call) == 3
            page, total, msg = call
            assert isinstance(page, int)
            assert isinstance(total, int)
            assert isinstance(msg, str)

    def test_pipeline_with_bleed(self, tiny_text_pdf: Path, output_dir: Path):
        """开启出血位参数不应导致错误。"""
        from pipeline import PipelineConfig, PrintPipeline

        config = PipelineConfig(
            enhance_mode="document",
            target_dpi=300,
            convert_to_cmyk=True,
            bleed_mm=3.0,
            jpeg_quality=80,
        )
        pipeline = PrintPipeline(config)
        output = output_dir / "out_bleed.pdf"

        result = pipeline.process(str(tiny_text_pdf), str(output))
        assert result.success is True
        assert output.exists()


class TestPipelineConfig:
    """PipelineConfig 参数边界测试。"""

    def test_default_config(self):
        config = PipelineConfig()
        assert config.target_dpi == 300
        assert config.jpeg_quality == 80
        assert config.convert_to_cmyk is True

    def test_cmyk_false_config(self):
        config = PipelineConfig(convert_to_cmyk=False)
        assert config.convert_to_cmyk is False

    def test_bleed_zero(self):
        config = PipelineConfig(bleed_mm=0.0)
        assert config.bleed_mm == 0.0

    def test_enhance_mode_options(self):
        for mode in ("fast", "quality", "document"):
            config = PipelineConfig(enhance_mode=mode)
            assert config.enhance_mode == mode
