"""
pdfx_checker.py — PDF/X 合规预检模块
职责：
1. 检测 GTS_OutputIntent（PDF/X 准备状态）
2. 字体嵌入详细报告
3. 出血位（BleedBox）验证
4. PDF/X-1a / PDF/X-4 合规性评分
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import fitz


# ── PDF/X 标准常量 ────────────────────────────────────────────────
PDFX_STANDARDS = {
    "PDF/X-1a:2001": "GTS_PDFXVersion",
    "PDF/X-1a:2003": "GTS_PDFXVersion",
    "PDF/X-4": "GTS_PDFXVersion",
}

INTENT_NAMES = {
    "sRGB": "sRGB IEC61966-2.1",
    "AdobeRGB": "Adobe RGB (1998)",
    "ISOcoated": "ISO Coated",
    "CoatedFOGRA39": "Coated FOGRA39",
    "PSOcoated": "PSO Coated v3",
    "ISOcoated_v2_eci": "ISO Coated v2 (ECI)",
}


@dataclass
class FontInfo:
    """单个字体详细信息。"""
    name: str
    family: str
    embedded: bool           # 是否嵌入
    subset: bool             # 是否为子集
    type_name: str           # Type1 / TrueType / CIDFont / ...
    is_cid: bool             # 是否为 CIDFont（常见于 Asian fonts）
    to_unicode_present: bool # 是否有 ToUnicode 映射


@dataclass
class BleedInfo:
    """出血位信息。"""
    has_bleed_box: bool
    bleed_left: float
    bleed_right: float
    bleed_top: float
    bleed_bottom: float
    bleed_mm: float  # 推断的出血量（mm）
    has_trim_box: bool
    trim_left: float
    trim_right: float
    has_art_box: bool


@dataclass
class OutputIntentInfo:
    """GTS_OutputIntent 信息。"""
    present: bool
    standard: Optional[str]   # PDF/X-1a / PDF/X-4 等
    output_profile: Optional[str]  # ICC 名称
    registry_name: Optional[str]   # URL 标识符
    condition: Optional[str]       # 打印条件描述


@dataclass
class PDFXReport:
    """完整 PDF/X 合规预检报告。"""
    file_path: str
    output_intent: OutputIntentInfo
    fonts: list[FontInfo]
    bleed: BleedInfo
    pdfx_compliant: bool       # 是否符合 PDF/X
    pdfx_score: int            # PDF/X 合规评分 0-100
    issues: list[str]          # 问题列表
    suggestions: list[str]      # 改进建议

    def to_dict(self) -> dict:
        return {
            "output_intent_present": self.output_intent.present,
            "output_intent_standard": self.output_intent.standard,
            "output_intent_profile": self.output_intent.output_profile,
            "font_count": len(self.fonts),
            "fonts_embedded": sum(1 for f in self.fonts if f.embedded),
            "fonts_subset": sum(1 for f in self.fonts if f.subset),
            "bleed_mm": self.bleed.bleed_mm,
            "pdfx_compliant": self.pdfx_compliant,
            "pdfx_score": self.pdfx_score,
            "issues": self.issues,
            "suggestions": self.suggestions,
        }


class PDFXChecker:
    """PDF/X 合规性预检器。"""

    def __init__(self, bleed_tolerance_mm: float = 0.5):
        """
        Args:
            bleed_tolerance_mm: 出血位检测容差（mm）。检测到的出血 > 此值才认为是有效出血。
        """
        self.bleed_tolerance_mm = bleed_tolerance_mm

    def check(self, pdf_path: str | Path) -> PDFXReport:
        """对 PDF 文件执行完整的 PDF/X 预检。"""
        pdf_path = Path(pdf_path)
        doc = fitz.open(str(pdf_path))

        output_intent = self._check_output_intent(doc)
        fonts = self._check_fonts(doc)
        bleed = self._check_bleed(doc)

        doc.close()

        issues, suggestions = self._build_issues_and_suggestions(
            output_intent, fonts, bleed, pdf_path
        )
        pdfx_score = self._calc_pdfx_score(output_intent, fonts, bleed, issues)
        pdfx_compliant = (
            output_intent.present
            and all(f.embedded for f in fonts)
            and bleed.bleed_mm > 0
            and len([i for i in issues if "严重" in i or "必须" in i]) == 0
        )

        return PDFXReport(
            file_path=str(pdf_path),
            output_intent=output_intent,
            fonts=fonts,
            bleed=bleed,
            pdfx_compliant=pdfx_compliant,
            pdfx_score=pdfx_score,
            issues=issues,
            suggestions=suggestions,
        )

    def _check_output_intent(self, doc: fitz.Document) -> OutputIntentInfo:
        """检测 GTS_OutputIntent 和 PDF/X 标准版本。"""
        intent = OutputIntentInfo(
            present=False,
            standard=None,
            output_profile=None,
            registry_name=None,
            condition=None,
        )

        try:
            import re

            # 遍历 catalog 中的 OutputIntents
            for xref in range(1, doc.xref_length()):
                try:
                    obj_str = doc.xref_object(xref, compressed=False)

                    if "/OutputIntent" not in obj_str:
                        continue

                    # 提取标准信息
                    standard = None
                    for std in PDFX_STANDARDS:
                        if std.replace("-", "").replace(":", "").lower() in obj_str.replace("-", "").replace(":", "").lower():
                            standard = std
                            break

                    # 提取 ICC profile 名称（S 键）
                    profile = None
                    s_match = re.search(r"/S\s*\(\s*([^)]+)\s*\)", obj_str)
                    if s_match:
                        profile = s_match.group(1).strip()
                        for key, label in INTENT_NAMES.items():
                            if key.lower() in profile.lower():
                                profile = label
                                break

                    # 提取 RegistryName
                    reg_name = None
                    rn_match = re.search(r"/RegistryName\s*\(\s*([^)]*)\s*\)", obj_str)
                    if rn_match:
                        reg_name = rn_match.group(1).strip()

                    # 提取 DestOutputProfile（ICC stream xref）
                    dest_match = re.search(r"/DestOutputProfile\s+(\d+)\s+0\s+R", obj_str)
                    if dest_match:
                        profile_xref = int(dest_match.group(1))
                        try:
                            profile_obj = doc.xref_object(profile_xref, compressed=False)
                            n_m = re.search(r"/N\s+(\d+)", profile_obj)
                            if n_m:
                                n_val = n_m.group(1)
                                channels = {"3": "RGB", "4": "CMYK", "1": "Gray"}.get(n_val, f"{n_val}ch")
                                if not profile:
                                    profile = f"ICCBased-{channels}"
                        except Exception:
                            pass

                    intent.standard = standard or "PDF/X（未知版本）"
                    intent.output_profile = profile or intent.output_profile
                    intent.registry_name = reg_name or intent.registry_name
                    intent.present = True

                except Exception:
                    continue

        except Exception:
            pass

        return intent

    def _check_fonts(self, doc: fitz.Document) -> list[FontInfo]:
        """获取所有字体的详细嵌入状态。"""
        fonts: list[FontInfo] = []
        seen: set[tuple[str, str]] = set()

        for page in doc:
            for font in page.get_fonts(full=True):
                # get_fonts(full=True) 返回:
                # [0]=xref [1]=ext [2]=type [3]=name [4]=basefont [5]=fonttype [6]=encoding [7]=...
                try:
                    xref    = font[0]
                    ext     = font[1] if len(font) > 1 else ""
                    ftype   = font[2] if len(font) > 2 else ""
                    name    = font[3] if len(font) > 3 else ""
                    basefont = font[4] if len(font) > 4 else ""
                    fonttype = font[5] if len(font) > 5 else 0

                    key = (name, ext)
                    if key in seen:
                        continue
                    seen.add(key)

                    is_embedded = bool(fonttype)
                    is_subset = bool(name and name.startswith("+"))
                    is_cid = ext in ("cidfont", "cidfontt", "otf") and "CID" in (ftype or "")
                    has_tounicode = False

                    if xref:
                        try:
                            font_obj = doc.xref_object(int(xref), compressed=False)
                            has_tounicode = "/ToUnicode" in font_obj
                        except Exception:
                            pass

                    fonts.append(FontInfo(
                        name=name or basefont or "Unknown",
                        family=basefont or name or "Unknown",
                        embedded=is_embedded,
                        subset=is_subset,
                        type_name=ftype or ext or "Unknown",
                        is_cid=is_cid,
                        to_unicode_present=has_tounicode,
                    ))
                except Exception:
                    continue

        return fonts

    def _check_bleed(self, doc: fitz.Document) -> BleedInfo:
        """检测出血位和裁切框。"""
        bleed = BleedInfo(
            has_bleed_box=False,
            bleed_left=0.0, bleed_right=0.0,
            bleed_top=0.0, bleed_bottom=0.0,
            bleed_mm=0.0,
            has_trim_box=False,
            trim_left=0.0, trim_right=0.0,
            has_art_box=False,
        )

        if len(doc) == 0:
            return bleed

        try:
            page = doc[0]
            mediabox = page.mediabox

            # BleedBox
            try:
                bleed_box = page.get_bbox("Bleed")
                if bleed_box and all(v > 0 for v in bleed_box):
                    bleed.has_bleed_box = True
                    bleed.bleed_left   = bleed_box.x0
                    bleed.bleed_right  = bleed_box.x1
                    bleed.bleed_top    = bleed_box.y0
                    bleed.bleed_bottom = bleed_box.y1
            except Exception:
                pass

            # TrimBox
            try:
                trim_box = page.get_bbox("Trim")
                if trim_box and all(v > 0 for v in trim_box):
                    bleed.has_trim_box = True
                    bleed.trim_left  = trim_box.x0
                    bleed.trim_right = trim_box.x1
            except Exception:
                pass

            # ArtBox
            try:
                art_box = page.get_bbox("Art")
                bleed.has_art_box = bool(art_box and all(v > 0 for v in art_box))
            except Exception:
                pass

            # 推断出血量：比较 BleedBox 与 TrimBox
            if bleed.has_bleed_box and bleed.has_trim_box:
                left_bleed  = (bleed.bleed_left - bleed.trim_left) * 25.4 / 72.0
                right_bleed = (bleed.trim_right - bleed.bleed_right) * 25.4 / 72.0
                top_bleed   = (bleed.bleed_top - trim_box.y0) * 25.4 / 72.0
                bleed.bleed_mm = max(0.0, left_bleed, right_bleed, top_bleed)
            elif bleed.has_bleed_box:
                # bleed box 超出 mediabox 边缘的部分即为出血
                inferred = (mediabox.x1 - bleed.bleed_right) * 25.4 / 72.0
                bleed.bleed_mm = max(0.0, inferred)

        except Exception:
            pass

        return bleed

    def _build_issues_and_suggestions(
        self,
        intent: OutputIntentInfo,
        fonts: list[FontInfo],
        bleed: BleedInfo,
        pdf_path: Path,
    ) -> tuple[list[str], list[str]]:
        """根据检查结果生成问题和建议。"""
        issues: list[str] = []
        suggestions: list[str] = []

        # OutputIntent
        if not intent.present:
            issues.append("严重：未找到 GTS_OutputIntent，PDF 未进行印刷色彩准备")
            suggestions.append("嵌入 ICC Profile（推荐 ISOcoated_v2_eci 或 PSO Coated v3），启用 PDF/X-1a 或 PDF/X-4 输出")
        else:
            if intent.standard and "未知" in intent.standard:
                issues.append("警告：检测到 OutputIntent 但无法识别 PDF/X 版本")
                suggestions.append("建议显式设置 PDF/X-1a:2003 或 PDF/X-4 标准")
            else:
                suggestions.append(f"OutputIntent 正常：{intent.standard}，Profile: {intent.output_profile}")

        # 字体
        unembedded = [f for f in fonts if not f.embedded]
        if unembedded:
            issues.append(f"严重：存在 {len(unembedded)} 个未嵌入字体，换机印刷将出现字体缺失")
            suggestions.append("在输出设置中启用「嵌入所有字体」选项")
        elif fonts:
            suggestions.append("字体全部嵌入，字体方面符合印刷要求")

        subset_only = [f for f in fonts if f.subset and f.type_name in ("Type1", "TrueType")]
        if subset_only:
            suggestions.append(f"注意：{len(subset_only)} 个字体为子集嵌入，建议全面嵌入以保证极端缩放质量")

        # 出血位
        if bleed.bleed_mm < 3.0 and bleed.bleed_mm > 0:
            issues.append(f"出血位不足：当前 {bleed.bleed_mm:.1f} mm，建议 >= 3mm")
            suggestions.append("在设计软件中设置 3mm 出血后重新导出，或在管线中启用 bleed_mm 参数")
        elif bleed.bleed_mm == 0:
            if not bleed.has_bleed_box and not bleed.has_trim_box:
                issues.append("未检测到出血位信息，建议添加 3mm 出血位以防止裁切露白")
                suggestions.append("使用 bleed_mm 参数启用出血位添加功能")
            else:
                suggestions.append("出血位信息已设置，符合印刷标准")

        # 综合
        if intent.present and all(f.embedded for f in fonts):
            suggestions.append("PDF/X 合规性基础达标，输出文件可送交印刷厂")

        return issues, suggestions

    def _calc_pdfx_score(
        self,
        intent: OutputIntentInfo,
        fonts: list[FontInfo],
        bleed: BleedInfo,
        issues: list[str],
    ) -> int:
        """计算 PDF/X 合规评分（0-100）。"""
        score = 0

        # OutputIntent: 40分
        score += 40 if intent.present else 0

        # 字体: 30分
        if fonts:
            embedded_ratio = sum(1 for f in fonts if f.embedded) / len(fonts)
            score += int(30 * embedded_ratio)
        else:
            score += 15  # 无字体（纯图像）给一半分

        # 出血位: 20分
        if bleed.bleed_mm >= 3.0:
            score += 20
        elif bleed.bleed_mm >= 1.0:
            score += 10
        elif bleed.bleed_mm > 0:
            score += 5

        # 无严重问题: 10分
        severe_issues = [i for i in issues if "严重" in i]
        score += 10 if not severe_issues else 0

        return min(100, score)


def check_pdfx(pdf_path: str | Path) -> PDFXReport:
    """便捷入口：对 PDF 执行 PDF/X 合规预检。"""
    checker = PDFXChecker()
    return checker.check(pdf_path)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法: python pdfx_checker.py <input.pdf>")
        sys.exit(1)

    report = check_pdfx(sys.argv[1])
    d = report.to_dict()

    print("\n" + "=" * 60)
    print("PDF/X 合规预检报告")
    print("=" * 60)
    print(f"OutputIntent:       {'是' if d['output_intent_present'] else '否'}")
    print(f"PDF/X 标准:         {d['output_intent_standard'] or '无'}")
    print(f"ICC Profile:         {d['output_intent_profile'] or '无'}")
    print(f"字体数量:           {d['font_count']}（嵌入: {d['fonts_embedded']}，子集: {d['fonts_subset']}）")
    print(f"出血位:             {d['bleed_mm']:.1f} mm")
    print(f"PDF/X 合规评分:     {d['pdfx_score']}/100")
    print(f"PDF/X 合格:         {'是' if d['pdfx_compliant'] else '否'}")
    print("\n问题：")
    for issue in d["issues"]:
        print(f"  - {issue}")
    print("建议：")
    for sug in d["suggestions"]:
        print(f"  - {sug}")
