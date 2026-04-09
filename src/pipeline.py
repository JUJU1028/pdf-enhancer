"""
pipeline.py — 主处理管线
职责：串联PDF解析、图像增强、色彩转换与页面重建。
重点修复：
1. 真实样册整页位图无法有效增强的问题
2. update_stream / bad xref 导致的图像替换失败
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import fitz

from color_converter import ColorConverter, _auto_find_icc
from image_enhancer import ImageEnhancer
from pdf_parser import PDFParser, PDFReport, PageImage, PageInfo


@dataclass
class PipelineConfig:
    """处理管线配置。"""

    enhance_mode: str = "document"
    enhance_scale: int = 4
    target_dpi: int = 300
    remove_artifacts: bool = True

    convert_to_cmyk: bool = True
    rendering_intent: str = "perceptual"
    cmyk_icc_path: Optional[str] = None  # None = 自动查找

    page_strategy: str = "auto"  # auto | rebuild_page | enhance_embedded | preserve
    full_page_threshold: float = 0.72
    preserve_vector_pages: bool = True

    jpeg_quality: int = 80
    jpeg_subsampling: int = 2
    output_format: str = "pdf"
    use_gpu: bool = False

    # 印前高级选项
    bleed_mm: float = 0.0  # 出血位，0 = 不添加；GUI勾选后传 3.0
    embed_icc_in_pdf: bool = True  # 在输出PDF中嵌入ICC



@dataclass
class PipelineResult:
    """管线处理结果。"""

    success: bool
    input_path: str
    output_path: str
    page_count: int
    elapsed_seconds: float
    original_score: int
    final_score: int
    enhanced_images: int
    rebuilt_pages: int
    preserved_pages: int
    errors: list[str]
    icc_used: Optional[str] = None
    bleed_mm: float = 0.0

    def summary(self) -> str:
        lines = [
            f"{'成功' if self.success else '失败'}",
            f"输入：{self.input_path}",
            f"输出：{self.output_path}",
            f"页数：{self.page_count}",
            f"耗时：{self.elapsed_seconds:.1f}秒",
            f"原始印刷就绪度：{self.original_score}/100",
            f"输出印刷就绪度：{self.final_score}/100",
            f"增强图像数：{self.enhanced_images}",
            f"整页重建数：{self.rebuilt_pages}",
            f"原样保留页数：{self.preserved_pages}",
            f"ICC Profile：{self.icc_used or '无（Pillow内置）'}",
            f"出血位：{self.bleed_mm:.1f}mm" if self.bleed_mm > 0 else "出血位：无",
        ]
        if self.errors:
            lines.append("警告：")
            for item in self.errors:
                lines.append(f"  - {item}")
        return "\n".join(lines)


class PrintPipeline:
    """PDF印刷增强主管线。"""

    def __init__(self, config: Optional[PipelineConfig] = None):
        self.config = config or PipelineConfig()
        # 自动发现 ICC
        if not self.config.cmyk_icc_path:
            auto_icc = _auto_find_icc()
            if auto_icc:
                self.config.cmyk_icc_path = str(auto_icc)
        self.parser = PDFParser(target_render_dpi=self.config.target_dpi)
        self.enhancer = ImageEnhancer(
            mode=self.config.enhance_mode,
            scale=self.config.enhance_scale,
            use_gpu=self.config.use_gpu,
        )
        self.converter = ColorConverter(
            rendering_intent=self.config.rendering_intent,
            cmyk_icc_path=self.config.cmyk_icc_path,
        )

    def process(
        self,
        input_path: str | Path,
        output_path: str | Path,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> PipelineResult:
        input_path = Path(input_path)
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        start_time = time.time()
        errors: list[str] = []
        enhanced_count = 0
        rebuilt_pages = 0
        preserved_pages = 0

        print("\n[1/4] 分析PDF文件...")
        report: PDFReport = self.parser.parse(input_path)
        print(f"  页数：{report.page_count}，印刷就绪度：{report.print_ready_score}/100")
        for issue in report.to_dict()["issues"]:
            print(f"  [问题] {issue}")

        print("\n[2/4] 处理页面...")
        src_doc = fitz.open(str(input_path))
        out_doc = fitz.open()

        try:
            for page_idx, page_info in enumerate(report.pages):
                strategy = self._decide_strategy(page_info)
                if progress_callback:
                    progress_callback(page_idx + 1, report.page_count, f"第 {page_idx + 1}/{report.page_count} 页 - {strategy}")

                print(
                    f"\n  第 {page_idx + 1}/{report.page_count} 页 "
                    f"({page_info.width_mm:.0f}×{page_info.height_mm:.0f}mm) -> {strategy}"
                )

                try:
                    if strategy == "rebuild_page":
                        changed = self._rebuild_page(out_doc, src_doc, page_idx, page_info)
                        rebuilt_pages += 1
                        enhanced_count += changed
                    elif strategy == "enhance_embedded":
                        changed = self._copy_page_and_replace_images(out_doc, src_doc, page_idx, page_info)
                        enhanced_count += changed
                    else:
                        out_doc.insert_pdf(src_doc, from_page=page_idx, to_page=page_idx)
                        preserved_pages += 1
                        print("    保留原页面")
                except Exception as exc:
                    errors.append(f"页{page_idx + 1} 处理失败: {exc}")
                    out_doc.insert_pdf(src_doc, from_page=page_idx, to_page=page_idx)
                    preserved_pages += 1
                    print(f"    [警告] 已回退为保留原页：{exc}")

            print("\n[3/4] 设置元数据...")
            out_doc.set_metadata(
                {
                    "creator": "PDF Print Enhancer v2.0",
                    "producer": "PDF Print Enhancer — PyMuPDF",
                    "title": input_path.stem,
                }
            )

            print(f"\n[4/4] 保存输出文件: {output_path}")
            out_doc.save(
                str(output_path),
                garbage=4,
                deflate=True,
                clean=True,
            )
        finally:
            out_doc.close()
            src_doc.close()

        elapsed = time.time() - start_time
        final_report = self.parser.parse(output_path)
        print(f"\n处理完成，耗时 {elapsed:.1f}秒")
        print(f"输出印刷就绪度：{final_report.print_ready_score}/100")

        icc_name = None
        if self.converter.cmyk_icc_path and self.converter.cmyk_icc_path.exists():
            icc_name = self.converter.cmyk_icc_path.name

        return PipelineResult(
            success=True,
            input_path=str(input_path),
            output_path=str(output_path),
            page_count=report.page_count,
            elapsed_seconds=elapsed,
            original_score=report.print_ready_score,
            final_score=final_report.print_ready_score,
            enhanced_images=enhanced_count,
            rebuilt_pages=rebuilt_pages,
            preserved_pages=preserved_pages,
            errors=errors,
            icc_used=icc_name,
            bleed_mm=self.config.bleed_mm,
        )

    def _decide_strategy(self, page_info: PageInfo) -> str:
        if self.config.page_strategy != "auto":
            return self.config.page_strategy

        if page_info.recommended_strategy == "rebuild_page":
            return "rebuild_page"
        if page_info.images:
            return "enhance_embedded"
        return "preserve"

    def _rebuild_page(
        self,
        out_doc: fitz.Document,
        src_doc: fitz.Document,
        page_idx: int,
        page_info: PageInfo,
    ) -> int:
        src_page = src_doc[page_idx]
        source_image, source_dpi = self._get_page_source_image(src_page, page_info)
        # 整页重建：增强后直接以 JPEG 插入（PyMuPDF 只接受 RGB JPEG；CMYK 转换在增强流里完成色彩管理但保留 RGB 存储）
        processed = self._process_image_bytes(source_image, source_dpi=source_dpi, for_rebuild=True)

        bleed_pt = self.config.bleed_mm * (72.0 / 25.4)
        new_width = src_page.rect.width + 2 * bleed_pt
        new_height = src_page.rect.height + 2 * bleed_pt

        new_page = out_doc.new_page(width=new_width, height=new_height)
        target_rect = fitz.Rect(bleed_pt, bleed_pt, new_width - bleed_pt, new_height - bleed_pt)
        new_page.insert_image(target_rect, stream=processed, keep_proportion=False)

        # 出血位裁切标记
        if bleed_pt > 0:
            mark_color = (0.6, 0.6, 0.6)
            m = 6
            shape = new_page.new_shape()
            corners = [
                (bleed_pt, bleed_pt, 1, 1),
                (new_width - bleed_pt, bleed_pt, -1, 1),
                (bleed_pt, new_height - bleed_pt, 1, -1),
                (new_width - bleed_pt, new_height - bleed_pt, -1, -1),
            ]
            for cx, cy, dx, dy in corners:
                shape.draw_line(fitz.Point(cx, cy), fitz.Point(cx + dx * m, cy))
                shape.draw_line(fitz.Point(cx, cy), fitz.Point(cx, cy + dy * m))
            shape.finish(color=mark_color, width=0.3)
            shape.commit()

        print(f"    整页重建完成，源DPI≈{source_dpi:.1f}" + (f"，含{self.config.bleed_mm:.1f}mm出血" if bleed_pt > 0 else ""))
        return 1

    def _get_page_source_image(self, src_page: fitz.Page, page_info: PageInfo) -> tuple[bytes, float]:
        if page_info.images:
            primary = max(page_info.images, key=lambda item: (item.coverage_ratio, item.width * item.height))
            source_dpi = page_info.estimated_page_dpi or primary.dpi or 72.0
            return primary.image_bytes, source_dpi

        rendered = src_page.get_pixmap(matrix=fitz.Matrix(1, 1), colorspace=fitz.csRGB)
        return rendered.tobytes("png"), 72.0

    def _copy_page_and_replace_images(
        self,
        out_doc: fitz.Document,
        src_doc: fitz.Document,
        page_idx: int,
        page_info: PageInfo,
    ) -> int:
        out_doc.insert_pdf(src_doc, from_page=page_idx, to_page=page_idx)
        out_page = out_doc[-1]
        out_images = out_page.get_images(full=True)
        if not out_images or not page_info.images:
            print("    页面无可替换图像，保留原样")
            return 0

        replaced = 0
        for original, copied in zip(page_info.images, out_images):
            new_xref = copied[0]
            source_dpi = original.dpi if original.dpi > 0 else 72.0
            processed = self._process_image_bytes(original.image_bytes, source_dpi=source_dpi, image_hint=original)
            out_page.replace_image(new_xref, stream=processed)
            replaced += 1
            print(f"    已替换图像 xref={new_xref}，源DPI≈{source_dpi:.1f}")
        return replaced

    def _process_image_bytes(
        self,
        image_bytes: bytes,
        source_dpi: float,
        image_hint: Optional[PageImage] = None,
        for_rebuild: bool = False,
    ) -> bytes:
        """
        增强单张图像。
        for_rebuild=True：整页重建路径，输出 RGB JPEG（PyMuPDF 兼容），跳过 CMYK 字节流转换。
        for_rebuild=False：嵌入替换路径，输出 CMYK JPEG（嵌入 ICC）。
        """
        from PIL import Image
        import io as _io

        processed = image_bytes
        ext = (image_hint.ext if image_hint else "").lower()
        if self.config.remove_artifacts and ext in {"jpg", "jpeg"}:
            print("    去除JPEG伪影...")
            processed = self.enhancer.remove_jpeg_artifacts(processed)

        print(f"    分辨率增强（{source_dpi:.1f} → {self.config.target_dpi} DPI）...")
        processed = self.enhancer.enhance(
            processed,
            target_dpi=self.config.target_dpi,
            source_dpi=source_dpi,
        )

        if for_rebuild:
            # 整页重建：保持 RGB，输出高质量 JPEG，让 PyMuPDF 正常存储
            # ICC 色彩管理通过 Pillow 做感知调整（亮度/饱和），不做字节级 CMYK 转换
            img = Image.open(_io.BytesIO(processed)).convert("RGB")
            # 轻度印刷色彩调整（模拟 CMYK 感知）
            if self.config.convert_to_cmyk and self.converter.cmyk_icc_path:
                from PIL import ImageEnhance
                img = ImageEnhance.Color(img).enhance(0.92)
                img = ImageEnhance.Contrast(img).enhance(1.03)
            out_buf = _io.BytesIO()
            img.save(out_buf, format="JPEG",
                     quality=self.config.jpeg_quality,
                     subsampling=self.config.jpeg_subsampling,
                     optimize=True)
            return out_buf.getvalue()

        # 嵌入替换路径：做完整 CMYK 转换
        if self.config.convert_to_cmyk:
            print("    RGB → CMYK 转换...")
            processed, _ = self.converter.convert_bytes(
                processed,
                quality=self.config.jpeg_quality,
                subsampling=self.config.jpeg_subsampling,
            )
        return processed


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("用法: python pipeline.py <input.pdf> <output.pdf> [fast|quality|document]")
        sys.exit(1)

    mode = sys.argv[3] if len(sys.argv) > 3 else "document"
    config = PipelineConfig(
        enhance_mode=mode,
        convert_to_cmyk=True,
        target_dpi=300,
    )
    pipeline = PrintPipeline(config)
    result = pipeline.process(sys.argv[1], sys.argv[2])
    print("\n" + "=" * 60)
    print(result.summary())
