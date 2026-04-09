"""
pdf_parser.py — PDF解析与页面策略判断模块
职责：
1. 分析PDF页面结构、图像、字体与颜色空间
2. 估算页面与图像的有效DPI
3. 判断页面更适合“整页重建”还是“嵌入图像增强”
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import fitz


COLORSPACE_MAP = {
    1: "Gray",
    3: "RGB",
    4: "CMYK",
}


@dataclass
class PageImage:
    """单张从PDF中提取的图像信息。"""

    page_index: int
    xref: int
    width: int
    height: int
    dpi: float
    colorspace: str
    ext: str
    image_bytes: bytes
    bbox: fitz.Rect
    coverage_ratio: float = 0.0


@dataclass
class PageInfo:
    """单页分析结果。"""

    page_index: int
    width_pt: float
    height_pt: float
    width_mm: float
    height_mm: float
    images: list[PageImage] = field(default_factory=list)
    has_text: bool = False
    has_vector: bool = False
    font_names: list[str] = field(default_factory=list)
    min_image_dpi: Optional[float] = None
    estimated_page_dpi: Optional[float] = None
    image_coverage_ratio: float = 0.0
    recommended_strategy: str = "preserve"
    is_raster_page: bool = False


@dataclass
class PDFReport:
    """完整PDF诊断报告。"""

    file_path: str
    page_count: int
    pages: list[PageInfo] = field(default_factory=list)
    embedded_fonts: list[str] = field(default_factory=list)
    all_fonts_embedded: bool = False
    has_cmyk_images: bool = False
    has_icc_profiles: bool = False      # 是否有 ICCBased 色彩空间（含 ICCBased RGB）
    icc_profile_names: list[str] = field(default_factory=list)  # 嵌入的 ICC 名称列表
    overall_min_dpi: Optional[float] = None
    print_ready_score: int = 0

    @property
    def color_managed(self) -> bool:
        """色彩管理就绪：原生 CMYK 或有嵌入 ICC Profile 均视为合规。"""
        return self.has_cmyk_images or self.has_icc_profiles

    def to_dict(self) -> dict:
        return {
            "file": self.file_path,
            "pages": self.page_count,
            "all_fonts_embedded": self.all_fonts_embedded,
            "has_cmyk_images": self.has_cmyk_images,
            "has_icc_profiles": self.has_icc_profiles,
            "icc_profile_names": self.icc_profile_names,
            "color_managed": self.color_managed,
            "min_dpi_found": self.overall_min_dpi,
            "print_ready_score": self.print_ready_score,
            "issues": self._list_issues(),
        }

    def _list_issues(self) -> list[str]:
        issues: list[str] = []
        if self.overall_min_dpi and self.overall_min_dpi < 250:
            issues.append(f"图像分辨率不足：最低 {self.overall_min_dpi:.0f} DPI（印刷需≥300 DPI）")
        if not self.all_fonts_embedded:
            issues.append("存在未嵌入字体，换机可能出现字体缺失")
        if not self.color_managed:
            issues.append("图像为裸 RGB 色彩模式，无 ICC Profile，需转换为 CMYK 或嵌入 ICC 才能准确印刷")
        return issues


class PDFParser:
    """PDF解析器。"""

    PT_TO_MM = 25.4 / 72.0

    def __init__(self, target_render_dpi: int = 150):
        self.target_render_dpi = target_render_dpi

    def parse(self, pdf_path: str | Path) -> PDFReport:
        pdf_path = Path(pdf_path)
        doc = fitz.open(str(pdf_path))

        report = PDFReport(
            file_path=str(pdf_path),
            page_count=len(doc),
        )
        report.embedded_fonts = self._check_fonts(doc)
        report.all_fonts_embedded = self._all_fonts_embedded(doc)

        all_dpis: list[float] = []

        for page_idx in range(len(doc)):
            page = doc[page_idx]
            page_info = self._analyze_page(page, page_idx)
            report.pages.append(page_info)

            if page_info.min_image_dpi is not None:
                all_dpis.append(page_info.min_image_dpi)

            if any(img.colorspace == "CMYK" for img in page_info.images):
                report.has_cmyk_images = True

        if all_dpis:
            report.overall_min_dpi = min(all_dpis)

        # 检测嵌入的 ICC Profile（ICCBased 色彩空间）
        report.has_icc_profiles, report.icc_profile_names = self._check_icc_profiles(doc)

        report.print_ready_score = self._calc_score(report)
        doc.close()
        return report

    def _analyze_page(self, page: fitz.Page, page_idx: int) -> PageInfo:
        rect = page.rect
        info = PageInfo(
            page_index=page_idx,
            width_pt=rect.width,
            height_pt=rect.height,
            width_mm=rect.width * self.PT_TO_MM,
            height_mm=rect.height * self.PT_TO_MM,
        )

        info.has_text = len(page.get_text("text").strip()) > 0
        info.has_vector = len(page.get_drawings()) > 0

        image_list = page.get_images(full=True)
        page_dpis: list[float] = []
        max_coverage = 0.0

        for raw in image_list:
            xref = raw[0]
            try:
                base_image = page.parent.extract_image(xref)
                width = int(base_image["width"])
                height = int(base_image["height"])
                bbox = self._get_image_rect(page, xref)
                coverage_ratio = self._calc_coverage_ratio(page.rect, bbox)
                dpi = self._estimate_dpi(page, image_list, width, height, bbox)
                colorspace = self._normalize_colorspace(base_image.get("colorspace", "unknown"))

                page_image = PageImage(
                    page_index=page_idx,
                    xref=xref,
                    width=width,
                    height=height,
                    dpi=round(dpi, 1),
                    colorspace=colorspace,
                    ext=str(base_image.get("ext", "unknown")).lower(),
                    image_bytes=base_image["image"],
                    bbox=bbox,
                    coverage_ratio=coverage_ratio,
                )
                info.images.append(page_image)
                page_dpis.append(dpi)
                max_coverage = max(max_coverage, coverage_ratio)
            except Exception as exc:
                print(f"  [警告] 提取图像 xref={xref} 失败: {exc}")

        if page_dpis:
            info.min_image_dpi = min(page_dpis)
            info.estimated_page_dpi = min(page_dpis)
        info.image_coverage_ratio = max_coverage

        for font in page.get_fonts(full=True):
            name = font[3]
            if name and name not in info.font_names:
                info.font_names.append(name)

        info.recommended_strategy = self._recommend_strategy(info)
        info.is_raster_page = info.recommended_strategy == "rebuild_page"
        return info

    def _normalize_colorspace(self, raw: object) -> str:
        if isinstance(raw, int):
            return COLORSPACE_MAP.get(raw, str(raw))
        if isinstance(raw, str):
            upper = raw.upper()
            if "CMYK" in upper:
                return "CMYK"
            if "RGB" in upper:
                return "RGB"
            if "GRAY" in upper or "GREY" in upper:
                return "Gray"
            return raw
        return "unknown"

    def _estimate_dpi(
        self,
        page: fitz.Page,
        image_list: list[tuple],
        width_px: int,
        height_px: int,
        bbox: fitz.Rect,
    ) -> float:
        if bbox.width > 0 and bbox.height > 0:
            display_w_inch = bbox.width / 72.0
            if display_w_inch > 0:
                return width_px / display_w_inch

        if len(image_list) == 1 and self._aspect_ratio_close(width_px, height_px, page.rect.width, page.rect.height):
            display_w_inch = page.rect.width / 72.0
            if display_w_inch > 0:
                return width_px / display_w_inch

        return 72.0

    def _aspect_ratio_close(
        self,
        img_w: float,
        img_h: float,
        page_w: float,
        page_h: float,
        tolerance: float = 0.03,
    ) -> bool:
        if img_h == 0 or page_h == 0:
            return False
        image_ratio = img_w / img_h
        page_ratio = page_w / page_h
        return abs(image_ratio - page_ratio) <= tolerance * page_ratio

    def _calc_coverage_ratio(self, page_rect: fitz.Rect, img_rect: fitz.Rect) -> float:
        if page_rect.width <= 0 or page_rect.height <= 0:
            return 0.0
        if img_rect.width <= 0 or img_rect.height <= 0:
            return 0.0
        return max(0.0, min(1.0, (img_rect.width * img_rect.height) / (page_rect.width * page_rect.height)))

    def _recommend_strategy(self, info: PageInfo) -> str:
        if not info.images:
            return "preserve"

        if (
            len(info.images) == 1
            and info.image_coverage_ratio >= 0.72
            and (not info.has_text)
            and (not info.has_vector)
        ):
            return "rebuild_page"

        if (
            len(info.images) == 1
            and info.image_coverage_ratio >= 0.9
            and (info.estimated_page_dpi or 999) < 180
        ):
            return "rebuild_page"

        return "enhance_embedded"

    def _get_image_rect(self, page: fitz.Page, xref: int) -> fitz.Rect:
        try:
            rects = page.get_image_rects(xref)
            if rects:
                return rects[0]
        except Exception:
            pass

        try:
            for block in page.get_text("dict").get("blocks", []):
                if block.get("type") == 1 and block.get("xref") == xref:
                    x0, y0, x1, y1 = block["bbox"]
                    return fitz.Rect(x0, y0, x1, y1)
        except Exception:
            pass

        return fitz.Rect(0, 0, 0, 0)

    def _check_fonts(self, doc: fitz.Document) -> list[str]:
        fonts: list[str] = []
        for page in doc:
            for font in page.get_fonts(full=True):
                name = font[3]
                if name and name not in fonts:
                    fonts.append(name)
        return fonts

    def _all_fonts_embedded(self, doc: fitz.Document) -> bool:
        for page in doc:
            for font in page.get_fonts(full=True):
                if font[5] == 0:
                    return False
        return True

    def _check_icc_profiles(self, doc: fitz.Document) -> tuple[bool, list[str]]:
        """扫描 PDF xref 表，检测是否有嵌入的 ICCBased 色彩空间对象。
        返回 (has_icc, profile_names_list)。
        """
        import re as _re

        found_names: list[str] = []
        icc_stream_xrefs: set[int] = set()

        try:
            for xref in range(1, doc.xref_length()):
                try:
                    obj_str = doc.xref_object(xref, compressed=False)
                    # ICCBased 色彩空间数组对象：[ /ICCBased <n> 0 R ]
                    if "/ICCBased" not in obj_str:
                        continue
                    m = _re.search(r"\[\s*/ICCBased\s+(\d+)\s+0\s+R\s*\]", obj_str)
                    if not m:
                        continue
                    stream_xref = int(m.group(1))
                    if stream_xref in icc_stream_xrefs:
                        continue
                    icc_stream_xrefs.add(stream_xref)
                    # 读 ICC stream 对象头，获取 /N 和 /Alternate
                    try:
                        s_obj = doc.xref_object(stream_xref, compressed=False)
                        n_match = _re.search(r"/N\s+(\d+)", s_obj)
                        alt_match = _re.search(r"/Alternate\s+(/\w+)", s_obj)
                        n_val = n_match.group(1) if n_match else "?"
                        alt_val = alt_match.group(1) if alt_match else ""
                        channels = {"3": "RGB", "4": "CMYK", "1": "Gray"}.get(n_val, f"{n_val}ch")
                        label = f"ICCBased-{channels}({alt_val})" if alt_val else f"ICCBased-{channels}"
                        if label not in found_names:
                            found_names.append(label)
                    except Exception:
                        if "ICCBased" not in found_names:
                            found_names.append("ICCBased")
                except Exception:
                    continue
        except Exception:
            pass
        return (len(found_names) > 0, found_names)

    def _calc_score(self, report: PDFReport) -> int:
        score = 100
        if report.overall_min_dpi is not None:
            if report.overall_min_dpi < 72:
                score -= 40
            elif report.overall_min_dpi < 150:
                score -= 30
            elif report.overall_min_dpi < 300:
                score -= 20
        else:
            score -= 10

        if not report.all_fonts_embedded:
            score -= 20
        # 色彩管理：原生 CMYK（-0分）/ ICCBased（-0分）/ 裸 RGB（-10分）
        if not report.color_managed:
            score -= 10
        return max(0, score)

    def render_page_as_image(
        self,
        pdf_path: str | Path,
        page_index: int,
        dpi: int = 150,
    ) -> bytes:
        doc = fitz.open(str(pdf_path))
        page = doc[page_index]
        matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)
        pix = page.get_pixmap(matrix=matrix, colorspace=fitz.csRGB)
        image_bytes = pix.tobytes("png")
        doc.close()
        return image_bytes


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法: python pdf_parser.py <input.pdf>")
        sys.exit(1)

    parser = PDFParser()
    report = parser.parse(sys.argv[1])
    result = report.to_dict()

    print("\n" + "=" * 60)
    print("PDF 印刷诊断报告")
    print("=" * 60)
    print(f"文件：{result['file']}")
    print(f"页数：{result['pages']}")
    print(f"字体全部嵌入：{'是' if result['all_fonts_embedded'] else '否'}")
    print(f"最低图像DPI：{result['min_dpi_found']:.0f}" if result['min_dpi_found'] else "最低图像DPI：无图像")
    print(f"印刷就绪度评分：{result['print_ready_score']}/100")
    print("\n发现问题：")
    for issue in result["issues"]:
        print(f"  - {issue}")
    if not result["issues"]:
        print("  无重大问题")
