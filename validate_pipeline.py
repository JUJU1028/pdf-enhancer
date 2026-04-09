"""
validate_pipeline.py — 端到端验证脚本
用途：
1. 跑真实PDF样册的完整增强流程
2. 输出诊断、处理结果与可复查的Markdown报告
3. 可选接入硅基流动视觉模型做AI版面审计（需环境变量）
"""

from __future__ import annotations

import io
import os
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    os.environ["PYTHONIOENCODING"] = "utf-8"

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR / "src"))

from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from color_converter import ColorConverter
from image_enhancer import ImageEnhancer
from pdf_parser import PDFParser
from pipeline import PipelineConfig, PrintPipeline
from siliconflow_client import SiliconFlowVisionClient

console = Console(force_terminal=True, highlight=False, legacy_windows=False)


def create_test_pdf(output_path: Path) -> Path:
    """生成一个模拟真实低清样册的整页位图PDF。"""
    console.print("[yellow]生成测试PDF（模拟整页低清样册）...[/yellow]")

    import fitz
    from PIL import Image, ImageDraw

    doc = fitz.open()
    page_width = 842
    page_height = 595

    for index in range(3):
        img = Image.new("RGB", (1208, 825), color=(240, 238, 235))
        draw = ImageDraw.Draw(img)
        draw.rectangle((40, 40, 1168, 785), outline=(190, 120, 80), width=6)
        draw.text((70, 60), f"ZI FENG BROCHURE PAGE {index + 1}", fill=(60, 60, 60))
        draw.text((70, 130), "LOW RESOLUTION SAMPLE FOR PRINT PIPELINE", fill=(90, 90, 90))
        draw.rectangle((70, 200, 560, 720), fill=(210, 170, 130))
        draw.rectangle((610, 200, 1120, 720), fill=(150, 180, 205))
        draw.text((90, 740), "户型图 / 价格 / 联系方式 / 品牌信息", fill=(55, 55, 55))

        img_buf = io.BytesIO()
        img.save(img_buf, format="JPEG", quality=38)

        page = doc.new_page(width=page_width, height=page_height)
        page.insert_image(fitz.Rect(0, 0, page_width, page_height), stream=img_buf.getvalue())

    doc.save(str(output_path))
    doc.close()
    console.print(f"[green]测试PDF生成: {output_path}[/green]")
    return output_path


def check_dependencies() -> dict[str, bool]:
    checks: dict[str, bool] = {}
    libs = {
        "fitz (PyMuPDF)": "fitz",
        "Pillow": "PIL",
        "OpenCV": "cv2",
        "numpy": "numpy",
        "rich": "rich",
        "pikepdf": "pikepdf",
        "Real-ESRGAN (可选)": "realesrgan",
        "basicsr (可选)": "basicsr",
        "torch (可选)": "torch",
    }
    for name, module_name in libs.items():
        try:
            __import__(module_name)
            checks[name] = True
        except ImportError:
            checks[name] = False
    return checks


def maybe_run_ai_audit(input_pdf: Path, report: PDFReport | None = None) -> str | None:
    client = SiliconFlowVisionClient()
    if not client.enabled:
        return None

    parser = PDFParser()
    total = report.page_count if report else 1
    audit_pages = min(total, 5)  # 最多审计5页

    if not report:
        image_bytes = parser.render_page_as_image(input_pdf, page_index=0, dpi=150)
        prompt = (
            "请分析这张房地产样册页面，重点回答：1) 这是整页位图还是矢量排版；"
            "2) 文字是否可能因低分辨率而影响印刷；3) 对高质量彩印最关键的三项风险。"
        )
        result = client.audit_page(image_bytes, prompt)
        return result.raw_content

    # 批量审计
    image_list = []
    for idx in range(audit_pages):
        img_bytes = parser.render_page_as_image(input_pdf, page_index=idx, dpi=150)
        image_list.append((idx + 1, img_bytes))

    batch_result = client.batch_audit(image_list)
    return batch_result.to_markdown()


def build_report_markdown(
    input_pdf: Path,
    output_pdf: Path,
    mode: str,
    before_report,
    result,
    ai_audit: str | None,
) -> str:
    in_size_kb = input_pdf.stat().st_size / 1024
    out_size_kb = output_pdf.stat().st_size / 1024 if output_pdf.exists() else 0

    return f"""# PDF样册增强验证报告

## 输入与输出
- 输入文件：`{input_pdf}`
- 输出文件：`{output_pdf}`
- 增强模式：`{mode}`

## 输入文件诊断
- 总页数：{before_report.page_count}
- 最低图像DPI：{before_report.overall_min_dpi or '无图像'}
- 输入印刷就绪度：{before_report.print_ready_score}/100
- 是否已有CMYK图像：{'是' if before_report.has_cmyk_images else '否'}
- 字体全部嵌入：{'是' if before_report.all_fonts_embedded else '否'}

## 输出文件结果
- 输出印刷就绪度：{result.final_score}/100
- 增强图像数：{result.enhanced_images}
- 整页重建页数：{result.rebuilt_pages}
- 原样保留页数：{result.preserved_pages}
- 总耗时：{result.elapsed_seconds:.1f} 秒
- 文件大小变化：{in_size_kb:.1f} KB → {out_size_kb:.1f} KB

## 页面策略摘要
| 页码 | 推荐策略 | 最低DPI | 图像覆盖率 |
|---|---|---:|---:|
{chr(10).join(f"| {page.page_index + 1} | {page.recommended_strategy} | {page.min_image_dpi or 0:.1f} | {page.image_coverage_ratio:.2f} |" for page in before_report.pages)}

## 已发现问题
{chr(10).join(f"- {item}" for item in before_report.to_dict()['issues']) or '- 无'}

## 本轮修复重点
- 改为“整页重建 + 安全图像替换”双策略，避免 bad xref / update_stream 问题
- 使用 `page.get_image_rects()` 修正真实样册图像位置与DPI估算
- 针对整页72DPI位图样册，默认走整页重建，更接近真实生产需求
- 色彩转换改为“ICC优先，缺失时稳定回退CMYK”，不再伪造ICC流程

## AI版面审计（可选）
{ai_audit or '未启用。若要启用，请在环境变量中设置 `SILICONFLOW_API_KEY`。'}

## 结论
{'本轮已经跑通真实样册闭环处理，输出文件可继续进行人工目检与打样。' if result.success else '本轮处理未完全成功，需要继续排查。'}

## 警告与回退
{chr(10).join(f"- {item}" for item in result.errors) or '- 无'}
"""


def run_validation(input_pdf: Path, output_pdf: Path, mode: str = "document") -> Path:
    console.print(
        Panel.fit(
            "[bold cyan]PDF 印刷增强软件 — 闭环验证[/bold cyan]\n"
            f"输入: {input_pdf}\n输出: {output_pdf}\n模式: {mode}",
            title="[PDF Print Enhancer v2.0]",
        )
    )

    console.print("\n[bold]Step 1 / 6 — 依赖库检查[/bold]")
    deps = check_dependencies()
    dep_table = Table(show_header=True, header_style="bold blue")
    dep_table.add_column("依赖库", style="cyan")
    dep_table.add_column("状态")
    for name, ok in deps.items():
        dep_table.add_row(name, "已安装" if ok else "未安装")
    console.print(dep_table)

    required = ["fitz (PyMuPDF)", "Pillow", "numpy", "rich"]
    missing = [item for item in required if not deps.get(item, False)]
    if missing:
        console.print(f"[red]缺少必要依赖: {missing}[/red]")
        sys.exit(1)

    console.print("\n[bold]Step 2 / 6 — 输入PDF诊断[/bold]")
    parser = PDFParser()
    before_report = parser.parse(input_pdf)
    diag_table = Table(show_header=False, box=None)
    diag_table.add_column("项目", style="bold")
    diag_table.add_column("值")
    diag_table.add_row("总页数", str(before_report.page_count))
    diag_table.add_row("最低图像分辨率", f"{before_report.overall_min_dpi:.1f} DPI" if before_report.overall_min_dpi else "无图像")
    diag_table.add_row("印刷就绪度", f"{before_report.print_ready_score}/100")
    diag_table.add_row("CMYK图像", "是" if before_report.has_cmyk_images else "否")
    console.print(diag_table)

    console.print("\n[bold]Step 3 / 6 — 单图像增强抽检[/bold]")
    first_page_with_image = next((page for page in before_report.pages if page.images), None)
    if first_page_with_image:
        sample_image = first_page_with_image.images[0]
        enhancer = ImageEnhancer(mode=mode, scale=4)
        start = time.time()
        enhanced_bytes = enhancer.enhance(
            sample_image.image_bytes,
            target_dpi=300,
            source_dpi=sample_image.dpi or 72.0,
        )
        elapsed = time.time() - start
        from PIL import Image as PILImage

        preview = PILImage.open(io.BytesIO(enhanced_bytes))
        console.print(
            f"抽检图像：{sample_image.width}×{sample_image.height}px -> "
            f"{preview.width}×{preview.height}px ({elapsed:.2f}秒)"
        )
    else:
        console.print("未找到可抽检图像，跳过。")

    console.print("\n[bold]Step 4 / 6 — 色彩转换抽检[/bold]")
    if first_page_with_image:
        converter = ColorConverter(rendering_intent="perceptual")
        converted_bytes, new_mode = converter.convert_bytes(first_page_with_image.images[0].image_bytes)
        console.print(f"色彩模式转换结果：{first_page_with_image.images[0].colorspace} -> {new_mode}")
    else:
        console.print("无图像可测试，跳过。")

    console.print("\n[bold]Step 5 / 6 — 完整管线处理[/bold]")
    config = PipelineConfig(
        enhance_mode=mode,
        convert_to_cmyk=True,
        target_dpi=300,
        page_strategy="auto",
    )
    pipeline = PrintPipeline(config)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task_id = progress.add_task("处理页面...", total=before_report.page_count)

        def update_progress(current: int, total: int, msg: str):
            progress.update(task_id, completed=current, description=msg)

        result = pipeline.process(input_pdf, output_pdf, progress_callback=update_progress)

    console.print("\n[bold]Step 6 / 6 — 生成验证报告[/bold]")
    ai_audit = maybe_run_ai_audit(input_pdf, before_report)
    report_path = BASE_DIR / "output" / f"validation_{input_pdf.stem}.md"
    report_path.write_text(
        build_report_markdown(input_pdf, output_pdf, mode, before_report, result, ai_audit),
        encoding="utf-8",
    )

    console.print("\n" + "=" * 60)
    console.print(
        Panel.fit(
            f"[bold green]验证完成[/bold green]\n\n"
            f"输出文件: [cyan]{result.output_path}[/cyan]\n"
            f"报告文件: [cyan]{report_path}[/cyan]\n"
            f"处理页数: {result.page_count}\n"
            f"增强图像: {result.enhanced_images} 张\n"
            f"整页重建: {result.rebuilt_pages} 页\n"
            f"输出评分: {result.final_score}/100\n"
            f"耗时: {result.elapsed_seconds:.1f} 秒",
            title="[处理结果]",
        )
    )
    return report_path


if __name__ == "__main__":
    if len(sys.argv) >= 2:
        input_pdf = Path(sys.argv[1])
        if not input_pdf.exists():
            console.print(f"[red]错误：文件不存在 {input_pdf}[/red]")
            sys.exit(1)
    else:
        input_pdf = BASE_DIR / "sample_input" / "test_brochure.pdf"
        input_pdf.parent.mkdir(exist_ok=True)
        create_test_pdf(input_pdf)

    mode = sys.argv[2] if len(sys.argv) >= 3 else "document"
    output_pdf = BASE_DIR / "output" / f"enhanced_{input_pdf.stem}.pdf"
    report_path = run_validation(input_pdf, output_pdf, mode)
    console.print(f"\n验证报告已写入：{report_path}")
