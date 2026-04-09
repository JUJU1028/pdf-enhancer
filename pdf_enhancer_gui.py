"""
pdf_enhancer_gui.py — PDF印刷增强桌面应用  v2.1
功能：
1. 拖拽/选择PDF文件导入（支持原生文件拖放）
2. 自动诊断并展示页数、DPI、评分、ICC、色彩管理、页面策略
3. 可调增强模式、目标DPI、出血位、CMYK模式、ICC
4. 实时进度条与日志
5. 处理完成可打开输出文件/文件夹
6. 可选硅基流动AI版面审计
"""

from __future__ import annotations

import io
import os
import sys
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import (
    BooleanVar,
    DoubleVar,
    Entry,
    Frame,
    Label,
    StringVar,
    Tk,
    Toplevel,
    filedialog,
    messagebox,
    ttk,
)

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR / "src"))

# ── 尝试加载拖拽支持（优雅降级） ──
_dnd_available = False
try:
    import tkinterdnd2

    _dnd_available = True
except ImportError:
    pass

from pipeline import PipelineConfig, PipelineResult, PrintPipeline
from pdf_parser import PDFParser, PDFReport
from color_converter import _auto_find_icc


# ─── 颜色主题 ───────────────────────────────────────────
BG = "#F5F5F7"
CARD_BG = "#FFFFFF"
ACCENT = "#2563EB"
ACCENT_HOVER = "#1D4ED8"
TEXT_PRIMARY = "#1D1D1F"
TEXT_SECONDARY = "#6B7280"
TEXT_MUTED = "#9CA3AF"
SUCCESS = "#10B981"
WARNING = "#F59E0B"
DANGER = "#EF4444"
BORDER = "#E5E7EB"
INPUT_BG = "#F9FAFB"


class App(Tk):
    """PDF印刷增强桌面应用主窗口。"""

    def __init__(self) -> None:
        super().__init__()
        self.title("PDF 印刷增强工具 v2.1")
        self.geometry("1000x760")
        self.minsize(860, 680)
        self.configure(bg=BG)

        # 状态变量
        self.input_path = StringVar(value="")
        self.output_path = StringVar(value="")
        self.enhance_mode = StringVar(value="document")
        self.target_dpi = StringVar(value="300")
        self.bleed_mm = DoubleVar(value=3.0)
        self.jpeg_quality = StringVar(value="80")
        self.enable_bleed = BooleanVar(value=True)
        self.enable_cmyk = BooleanVar(value=True)
        self.enable_true_cmyk = BooleanVar(value=True)  # 真 CMYK 输出（pikepdf 后处理）
        self.enable_audit = BooleanVar(value=False)
        self.api_key = StringVar(value="")
        self.icc_label = StringVar(value="")
        self.progress_text = StringVar(value="就绪")
        self.progress_pct = DoubleVar(value=0)
        self.log_lines: list[str] = []

        # 自动发现 ICC
        auto_icc = _auto_find_icc()
        if auto_icc:
            self.icc_label.set(f"自动检测: {auto_icc.name}")

        # 解析结果缓存
        self._report: PDFReport | None = None
        self._result: PipelineResult | None = None
        self._running = False

        self._build_ui()
        self._setup_dnd()  # 拖拽支持（可选）

    # ─── UI 构建 ─────────────────────────────────────
    def _build_ui(self) -> None:
        # 顶部标题栏
        header = Frame(self, bg=CARD_BG, height=56)
        header.pack(fill="x", padx=16, pady=(16, 0))
        header.pack_propagate(False)

        title_lbl = Label(
            header, text="PDF 印刷增强工具", font=("Microsoft YaHei UI", 16, "bold"),
            bg=CARD_BG, fg=TEXT_PRIMARY,
        )
        title_lbl.pack(side="left", padx=12)

        ver_lbl = Label(
            header, text="v2.1", font=("Microsoft YaHei UI", 10),
            bg=CARD_BG, fg=TEXT_MUTED,
        )
        ver_lbl.pack(side="left", padx=(0, 8))

        icc_info = Label(
            header, textvariable=self.icc_label, font=("Microsoft YaHei UI", 9),
            bg=CARD_BG, fg=SUCCESS,
        )
        icc_info.pack(side="right", padx=12)

        # 主内容区
        content = Frame(self, bg=BG)
        content.pack(fill="both", expand=True, padx=16, pady=12)

        # 左右分栏
        left = Frame(content, bg=BG)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))

        right = Frame(content, bg=BG, width=280)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)

        # ── 左侧面板 ──
        self._build_input_card(left)
        self._build_output_card(left)
        self._build_progress_card(left)

        # ── 右侧面板 ──
        self._build_settings_card(right)

    def _card(self, parent: Frame) -> Frame:
        card = Frame(parent, bg=CARD_BG, bd=0, highlightthickness=1,
                     highlightbackground=BORDER)
        card.pack(fill="x", pady=(0, 10))
        return card

    def _card_header(self, parent: Frame, text: str) -> Frame:
        hdr = Frame(parent, bg=CARD_BG)
        hdr.pack(fill="x", padx=16, pady=(12, 4))
        lbl = Label(hdr, text=text, font=("Microsoft YaHei UI", 11, "bold"),
                    bg=CARD_BG, fg=TEXT_PRIMARY)
        lbl.pack(anchor="w")
        return hdr

    # ── 输入文件卡片（支持拖拽） ──
    def _build_input_card(self, parent: Frame) -> None:
        card = self._card(parent)
        self._card_header(card, "输入文件")

        body = Frame(card, bg=CARD_BG)
        body.pack(fill="x", padx=16, pady=(4, 12))

        # 拖拽区域 + 输入框容器
        self.drop_frame = Frame(body, bg=INPUT_BG, bd=1, relief="solid",
                                highlightthickness=1, highlightcolor=BORDER,
                                highlightbackground=BORDER)
        self.drop_frame.pack(fill="x")

        # 拖拽提示文字
        self.drop_hint = Label(
            self.drop_frame, text="📄 拖放 PDF 文件到此处，或点击下方按钮选择",
            font=("Microsoft YaHei UI", 9), bg=INPUT_BG, fg=TEXT_MUTED,
            padx=8, pady=4,
        )
        self.drop_hint.pack(anchor="w")

        entry = Entry(
            self.drop_frame, textvariable=self.input_path, font=("Consolas", 10),
            bg=INPUT_BG, fg=TEXT_PRIMARY, bd=0, relief="flat",
        )
        entry.pack(fill="x", ipady=6)

        btn_row = Frame(body, bg=CARD_BG)
        btn_row.pack(fill="x", pady=(8, 0))

        self._btn(btn_row, "选择文件", self._on_select_file).pack(side="left", padx=(0, 8))
        self._btn(btn_row, "诊断分析", self._on_diagnose, style="secondary").pack(side="left")

        # 诊断结果区
        self.diag_frame = Frame(card, bg=CARD_BG)
        self.diag_frame.pack(fill="x", padx=16, pady=(0, 12))

    # ── 输出卡片 ──
    def _build_output_card(self, parent: Frame) -> None:
        card = self._card(parent)
        self._card_header(card, "输出文件")

        body = Frame(card, bg=CARD_BG)
        body.pack(fill="x", padx=16, pady=(4, 12))

        entry = Entry(
            body, textvariable=self.output_path, font=("Consolas", 10),
            bg=INPUT_BG, fg=TEXT_PRIMARY, bd=1, relief="solid",
            highlightthickness=1, highlightcolor=ACCENT, highlightbackground=BORDER,
        )
        entry.pack(fill="x", ipady=6)

        btn_row = Frame(body, bg=CARD_BG)
        btn_row.pack(fill="x", pady=(8, 0))

        self._btn(btn_row, "选择位置", self._on_select_output).pack(side="left", padx=(0, 8))

    # ── 进度卡片 ──
    def _build_progress_card(self, parent: Frame) -> None:
        card = self._card(parent)
        self._card_header(card, "处理进度")

        body = Frame(card, bg=CARD_BG)
        body.pack(fill="x", padx=16, pady=(4, 12))

        # 进度条
        style = ttk.Style()
        style.theme_use("default")
        style.configure("custom.Horizontal.TProgressbar",
                        troughcolor=INPUT_BG, background=ACCENT, thickness=8)

        self.progress_bar = ttk.Progressbar(
            body, variable=self.progress_pct, maximum=100,
            style="custom.Horizontal.TProgressbar",
        )
        self.progress_bar.pack(fill="x")

        self.progress_label = Label(
            body, textvariable=self.progress_text, font=("Microsoft YaHei UI", 9),
            bg=CARD_BG, fg=TEXT_SECONDARY,
        )
        self.progress_label.pack(anchor="w", pady=(4, 0))

        # 日志区
        log_frame = Frame(body, bg="#1E1E2E", bd=0)
        log_frame.pack(fill="both", expand=True, pady=(8, 0))

        self.log_text = tk_text = _create_log_widget(log_frame)
        tk_text.pack(fill="both", expand=True)

        # 操作按钮行
        btn_row = Frame(body, bg=CARD_BG)
        btn_row.pack(fill="x", pady=(8, 0))

        self.start_btn = self._btn(btn_row, "开始增强", self._on_start, style="primary")
        self.start_btn.pack(side="left", padx=(0, 8))

        self.open_btn = self._btn(btn_row, "打开输出", self._on_open_output, style="secondary")
        self.open_btn.pack(side="left", padx=(0, 8))
        self.open_btn.configure(state="disabled")

    # ── 设置卡片 ──
    def _build_settings_card(self, parent: Frame) -> None:
        card = self._card(parent)
        self._card_header(card, "增强参数")

        body = Frame(card, bg=CARD_BG)
        body.pack(fill="x", padx=16, pady=(4, 12))

        # 增强模式
        self._setting_label(body, "增强模式")
        mode_frame = Frame(body, bg=CARD_BG)
        mode_frame.pack(fill="x", pady=(0, 8))
        for label, value in [("文档增强", "document"), ("高质量", "quality"), ("快速", "fast")]:
            rb = ttk.Radiobutton(mode_frame, text=label, variable=self.enhance_mode, value=value)
            rb.pack(side="left", padx=(0, 12))

        # 目标DPI
        self._setting_label(body, "目标 DPI")
        dpi_frame = Frame(body, bg=CARD_BG)
        dpi_frame.pack(fill="x", pady=(0, 8))
        for val in ["150", "300", "600"]:
            rb = ttk.Radiobutton(dpi_frame, text=f"{val} DPI", variable=self.target_dpi, value=val)
            rb.pack(side="left", padx=(0, 12))

        # JPEG 质量
        self._setting_label(body, "JPEG 质量")
        q_frame = Frame(body, bg=CARD_BG)
        q_frame.pack(fill="x", pady=(0, 8))
        for val in ["75", "80", "85", "90"]:
            rb = ttk.Radiobutton(q_frame, text=f"Q{val}", variable=self.jpeg_quality, value=val)
            rb.pack(side="left", padx=(0, 8))

        # 分割线
        sep = Frame(body, bg=BORDER, height=1)
        sep.pack(fill="x", pady=8)

        # 开关选项
        cb_frame = Frame(body, bg=CARD_BG)
        cb_frame.pack(fill="x", pady=(0, 8))
        cb1 = ttk.Checkbutton(cb_frame, text="RGB → CMYK 色彩转换", variable=self.enable_cmyk)
        cb1.pack(anchor="w")
        cb2 = ttk.Checkbutton(cb_frame, text="真 CMYK 字节流（pikepdf）", variable=self.enable_true_cmyk)
        cb2.pack(anchor="w", pady=(4, 0))
        cb3 = ttk.Checkbutton(cb_frame, text="添加出血位 (3mm)", variable=self.enable_bleed)
        cb3.pack(anchor="w", pady=(4, 0))
        cb4 = ttk.Checkbutton(cb_frame, text="AI 版面审计", variable=self.enable_audit,
                              command=self._toggle_audit)
        cb4.pack(anchor="w", pady=(4, 0))

        # API Key 输入
        self.audit_frame = Frame(body, bg=CARD_BG)
        ttk.Label(self.audit_frame, text="硅基流动 API Key:", font=("Microsoft YaHei UI", 9)).pack(anchor="w")
        key_entry = Entry(self.audit_frame, textvariable=self.api_key, font=("Consolas", 9),
                         bg=INPUT_BG, bd=1, relief="solid", show="*")
        key_entry.pack(fill="x", ipady=4, pady=(2, 0))

        self._toggle_audit()

        # 分割线
        sep2 = Frame(body, bg=BORDER, height=1)
        sep2.pack(fill="x", pady=8)

        # 结果区
        self.result_frame = Frame(body, bg=CARD_BG)
        self.result_frame.pack(fill="x")

    def _setting_label(self, parent: Frame, text: str) -> None:
        lbl = Label(parent, text=text, font=("Microsoft YaHei UI", 9, "bold"),
                    bg=CARD_BG, fg=TEXT_SECONDARY)
        lbl.pack(anchor="w", pady=(4, 2))

    def _btn(self, parent: Frame, text: str, cmd, style: str = "secondary") -> Label:
        bg = ACCENT if style == "primary" else CARD_BG
        fg = "#FFFFFF" if style == "primary" else TEXT_PRIMARY
        relief = "flat" if style == "primary" else "solid"

        btn = Label(
            parent, text=f" {text} ", font=("Microsoft YaHei UI", 10, "bold"),
            bg=bg, fg=fg, relief=relief, bd=0 if style == "primary" else 1,
            cursor="hand2", padx=12, pady=4,
        )
        btn.bind("<Button-1>", lambda e: cmd())
        if style == "primary":
            btn.bind("<Enter>", lambda e: btn.configure(bg=ACCENT_HOVER))
            btn.bind("<Leave>", lambda e: btn.configure(bg=ACCENT))
        return btn

    # ── 拖拽支持 ──
    def _setup_dnd(self) -> None:
        """初始化拖放支持（需要 tkinterdnd2，不可用时静默跳过）。"""
        if not _dnd_available:
            self.drop_hint.configure(text="💡 安装 tkinterdnd2 可启用拖放: pip install tkinterdnd2")
            return

        try:
            # 用 TkinterDnD 替代根窗口
            self.drop_frame.drop_target_register(tkinterdnd2.DND_FILES)
            self.drop_frame.dnd_bind("<<Drop>>", self._on_drop)
            self.drop_frame.dnd_bind("<<DragEnter>>", self._on_drag_enter)
            self.drop_frame.dnd_bind("<<DragLeave>>", self._on_drag_leave)
            self.drop_hint.configure(text="📄 拖放 PDF 文件到此处，或点击下方按钮选择")
        except Exception:
            self.drop_hint.configure(
                text="💡 拖放初始化失败，请使用「选择文件」按钮"
            )

    def _on_drop(self, event) -> None:
        """处理文件拖放事件。"""
        files = event.data.split()
        pdf_files = [f for f in files if f.lower().endswith(".pdf")]
        if not pdf_files:
            messagebox.showwarning("提示", "请拖放 .pdf 文件")
            return
        dropped = pdf_files[0]
        if os.path.exists(dropped):
            self.input_path.set(dropped)
            p = Path(dropped)
            default_out = str(p.parent / f"{p.stem}_印刷增强.pdf")
            self.output_path.set(default_out)
            self._drop_highlight(False)
            # 自动触发诊断
            threading.Thread(target=self._run_diagnose, args=(dropped,), daemon=True).start()

    def _on_drag_enter(self, event) -> None:
        self._drop_highlight(True)

    def _on_drag_leave(self, event) -> None:
        self._drop_highlight(False)

    def _drop_highlight(self, active: bool) -> None:
        color = ACCENT + "18" if active else INPUT_BG
        hicolor = ACCENT if active else BORDER
        self.drop_frame.configure(bg=color)
        self.drop_hint.configure(bg=color)
        self.drop_frame.configure(highlightbackground=hicolor)
        for child in self.drop_frame.winfo_children():
            if isinstance(child, Entry):
                child.configure(bg=color)

    # ─── 事件处理 ─────────────────────────────────────
    def _on_select_file(self) -> None:
        path = filedialog.askopenfilename(
            title="选择 PDF 文件",
            filetypes=[("PDF 文件", "*.pdf"), ("所有文件", "*.*")],
        )
        if path:
            self.input_path.set(path)
            # 自动设置输出路径
            p = Path(path)
            default_out = str(p.parent / f"{p.stem}_印刷增强.pdf")
            self.output_path.set(default_out)

    def _on_select_output(self) -> None:
        path = filedialog.asksaveasfilename(
            title="保存增强 PDF",
            defaultextension=".pdf",
            filetypes=[("PDF 文件", "*.pdf")],
            initialfile="output_enhanced.pdf",
        )
        if path:
            self.output_path.set(path)

    def _toggle_audit(self) -> None:
        if self.enable_audit.get():
            self.audit_frame.pack(fill="x", pady=(4, 0))
        else:
            self.audit_frame.pack_forget()

    def _on_diagnose(self) -> None:
        path = self.input_path.get()
        if not path or not Path(path).exists():
            messagebox.showwarning("提示", "请先选择有效的 PDF 文件")
            return

        self._log("开始诊断分析...")
        threading.Thread(target=self._run_diagnose, args=(path,), daemon=True).start()

    def _run_diagnose(self, path: str) -> None:
        try:
            parser = PDFParser()
            report = parser.parse(path)
            self._report = report
            self.after(0, self._show_diagnosis, report)
        except Exception as exc:
            self._log(f"[错误] 诊断失败: {exc}")
            self.after(0, lambda: messagebox.showerror("诊断失败", str(exc)))

    def _show_diagnosis(self, report: PDFReport) -> None:
        # 清空旧诊断
        for w in self.diag_frame.winfo_children():
            w.destroy()

        self._log(f"诊断完成: {report.page_count} 页, 评分 {report.print_ready_score}/100")

        # 评分色标
        score = report.print_ready_score
        if score >= 90:
            score_color = SUCCESS
        elif score >= 60:
            score_color = WARNING
        else:
            score_color = DANGER

        # 评分大字
        score_frame = Frame(self.diag_frame, bg=CARD_BG)
        score_frame.pack(fill="x", pady=(0, 8))

        Label(score_frame, text=f"{score}", font=("Microsoft YaHei UI", 28, "bold"),
              bg=CARD_BG, fg=score_color).pack(side="left")
        Label(score_frame, text="/100\n印刷就绪度", font=("Microsoft YaHei UI", 9),
              bg=CARD_BG, fg=TEXT_SECONDARY, justify="left").pack(side="left", padx=(8, 0))

        # 详细指标（2行4列网格）
        metrics = Frame(self.diag_frame, bg=CARD_BG)
        metrics.pack(fill="x")

        info_items = [
            ("页数", f"{report.page_count} 页"),
            ("最低 DPI", f"{report.overall_min_dpi:.1f}" if report.overall_min_dpi else "无图像"),
            ("色彩模式", self._color_label(report)),
            ("字体嵌入", "✓ 全部" if report.all_fonts_embedded else "✗ 存在未嵌入"),
            ("ICC Profile", "✓ 已检测" if report.has_icc_profiles else "— 无"),
            ("色彩管理", "✓ 合规" if report.color_managed else "⚠ 需转换"),
            ("文件大小", self._file_size_label(report.file_path)),
            ("图像数量", f"{len(report.images)} 张"),
        ]

        for col, (label, value) in enumerate(info_items):
            cell = Frame(metrics, bg="#F9FAFB", padx=8, pady=6)
            cell.grid(row=col // 4, column=col % 4, padx=(0, 6), pady=(0, 6), sticky="nsew")
            for i in range(2):
                metrics.columnconfigure(i, weight=1)
            Label(cell, text=label, font=("Microsoft YaHei UI", 8),
                  bg="#F9FAFB", fg=TEXT_MUTED).pack(anchor="w")
            Label(cell, text=value, font=("Microsoft YaHei UI", 9, "bold"),
                  bg="#F9FAFB", fg=TEXT_PRIMARY).pack(anchor="w")

        # 页面策略分析（如果有）
        if hasattr(report, "pages") and report.pages:
            strat_frame = Frame(self.diag_frame, bg=CARD_BG)
            strat_frame.pack(fill="x", pady=(10, 0))
            Label(strat_frame, text="页面策略分析",
                  font=("Microsoft YaHei UI", 9, "bold"), bg=CARD_BG,
                  fg=TEXT_SECONDARY).pack(anchor="w", pady=(0, 6))

            rebuild_count = sum(1 for p in report.pages if p.is_raster_page)
            enhance_count = sum(
                1 for p in report.pages if not p.is_raster_page and p.images
            )
            preserve_count = len(report.pages) - rebuild_count - enhance_count

            strat_row = Frame(strat_frame, bg=CARD_BG)
            strat_row.pack(fill="x")
            for label, count, color in [
                ("整页重建", rebuild_count, ACCENT),
                ("增强嵌入", enhance_count, WARNING),
                ("原样保留", max(preserve_count, 0), TEXT_MUTED),
            ]:
                scell = Frame(strat_row, bg="#F9FAFB", padx=12, pady=5)
                scell.pack(side="left", padx=(0, 6), expand=True, fill="x")
                Label(scell, text=label, font=("Microsoft YaHei UI", 8),
                      bg="#F9FAFB", fg=TEXT_MUTED).pack()
                Label(scell, text=f"{count} 页", font=("Microsoft YaHei UI", 11, "bold"),
                      bg="#F9FAFB", fg=color).pack()

        # 问题列表
        issues = report.to_dict()["issues"]
        if issues:
            issues_frame = Frame(self.diag_frame, bg=CARD_BG)
            issues_frame.pack(fill="x", pady=(8, 0))
            Label(issues_frame, text="问题列表",
                  font=("Microsoft YaHei UI", 9, "bold"), bg=CARD_BG,
                  fg=DANGER).pack(anchor="w", pady=(0, 4))
            for issue in issues[:8]:  # 最多显示8条
                Label(issues_frame, text=f"  ⚠ {issue}", font=("Microsoft YaHei UI", 8),
                      bg=CARD_BG, fg=DANGER, anchor="w", wraplength=550).pack(anchor="w")

    @staticmethod
    def _color_label(report: PDFReport) -> str:
        """生成色彩模式标签。"""
        parts = []
        if report.has_cmyk_images:
            parts.append("CMYK")
        if report.has_icc_profiles:
            names = ", ".join(report.icc_profile_names[:2])
            parts.append(f"ICC({names})")
        if not parts:
            parts.append("RGB")
        return " + ".join(parts)

    @staticmethod
    def _file_size_label(path: Optional[str]) -> str:
        """生成文件大小标签。"""
        if not path or not Path(path).exists():
            return "—"
        size = Path(path).stat().st_size
        if size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size / 1024 / 1024:.1f} MB"

    def _on_start(self) -> None:
        if self._running:
            messagebox.showinfo("提示", "正在处理中，请等待完成")
            return

        input_p = self.input_path.get()
        output_p = self.output_path.get()

        if not input_p or not Path(input_p).exists():
            messagebox.showwarning("提示", "请先选择有效的输入 PDF 文件")
            return
        if not output_p:
            messagebox.showwarning("提示", "请设置输出文件路径")
            return

        self._running = True
        self.start_btn.configure(bg="#9CA3AF", text="处理中...")
        self.open_btn.configure(state="disabled")
        self.progress_pct.set(0)
        self.progress_text.set("准备中...")

        # 清空结果
        for w in self.result_frame.winfo_children():
            w.destroy()

        config = PipelineConfig(
            enhance_mode=self.enhance_mode.get(),
            target_dpi=int(self.target_dpi.get()),
            convert_to_cmyk=self.enable_cmyk.get(),
            bleed_mm=self.bleed_mm.get() if self.enable_bleed.get() else 0.0,
            jpeg_quality=int(self.jpeg_quality.get()),
        )

        # 真CMYK标志（传递给后处理）
        self._use_true_cmyk = self.enable_true_cmyk.get()

        # 环境变量传递 API Key
        api_key = self.api_key.get().strip() if self.enable_audit.get() else ""
        threading.Thread(
            target=self._run_pipeline,
            args=(input_p, output_p, config, api_key),
            daemon=True,
        ).start()

    def _run_pipeline(self, input_p: str, output_p: str, config: PipelineConfig, api_key: str) -> None:
        old_key = os.environ.get("SILICONFLOW_API_KEY")
        try:
            if api_key:
                os.environ["SILICONFLOW_API_KEY"] = api_key

            pipeline = PrintPipeline(config)
            result = pipeline.process(
                input_p, output_p,
                progress_callback=self._update_progress,
            )

            # ── 真 CMYK 后处理（pikepdf）──
            use_true_cmyk = getattr(self, "_use_true_cmyk", False)
            if use_true_cmyk and result.success and config.convert_to_cmyk:
                try:
                    from cmyk_postprocessor import CMYKPostProcessor

                    # 生成临时CMYK文件
                    cmyk_output = output_p.replace(".pdf", "_cmyk.pdf")
                    self.after(0, lambda: self._update_progress(0, 1, "真CMYK后处理..."))
                    processor = CMYKPostProcessor(
                        jpeg_quality=config.jpeg_quality,
                        verbose=False,
                    )
                    cmyk_result = processor.process(output_p, cmyk_output)
                    if cmyk_result and Path(cmyk_output).exists():
                        # 用CMYK版本替换原始输出
                        import shutil
                        shutil.move(cmyk_output, output_p)
                        result.output_path = output_p
                        self._log("[CMYK] pikepdf 后处理完成，已替换为真 CMYK 字节流")
                    else:
                        self._log("[CMYK] 后处理未生效，保留 ICCBased-RGB 输出")
                except ImportError:
                    self._log("[CMYK] 未安装 pikepdf，跳过真CMYK后处理")
                except Exception as exc:
                    self._log(f"[CMYK] 后处理异常（已跳过）: {exc}")

            self._result = result
            self.after(0, self._on_complete, result)
        except Exception as exc:
            self._log(f"[错误] 处理失败: {exc}")
            self.after(0, lambda: messagebox.showerror("处理失败", str(exc)))
        finally:
            if old_key is None:
                os.environ.pop("SILICONFLOW_API_KEY", None)
            else:
                os.environ["SILICONFLOW_API_KEY"] = old_key
            self._running = False
            self.after(0, lambda: self.start_btn.configure(bg=ACCENT, text="开始增强"))

    def _update_progress(self, current: int, total: int, msg: str) -> None:
        pct = (current / total) * 100 if total > 0 else 0
        self.progress_pct.set(pct)
        self.progress_text.set(f"{msg} ({current}/{total})")

    def _on_complete(self, result: PipelineResult) -> None:
        self.progress_text.set("处理完成!")
        self.progress_pct.set(100)
        self.open_btn.configure(state="normal")

        self._log(f"处理完成: {result.output_path}")
        self._log(f"评分: {result.original_score}/100 -> {result.final_score}/100")
        self._log(f"耗时: {result.elapsed_seconds:.1f}秒")

        # 显示结果
        for w in self.result_frame.winfo_children():
            w.destroy()

        Label(self.result_frame, text="处理结果", font=("Microsoft YaHei UI", 10, "bold"),
              bg=CARD_BG, fg=TEXT_PRIMARY).pack(anchor="w", pady=(0, 4))

        # 输出文件重新解析，获取真CMYK状态
        cmyk_status = "—"
        if Path(result.output_path).exists():
            try:
                out_parser = PDFParser()
                out_report = out_parser.parse(result.output_path)
                if out_report.has_cmyk_images:
                    cmyk_status = "✓ 真 CMYK"
                elif out_report.has_icc_profiles:
                    cmyk_status = f"✓ ICC ({', '.join(out_report.icc_profile_names[:1])})"
                else:
                    cmyk_status = "⚠ RGB"
            except Exception:
                pass

        result_items = [
            ("输出评分", f"{result.final_score}/100", SUCCESS if result.final_score >= 90 else WARNING),
            ("色彩输出", cmyk_status, SUCCESS if "✓" in cmyk_status else WARNING),
            ("增强图像", f"{result.enhanced_images} 张", TEXT_PRIMARY),
            ("整页重建", f"{result.rebuilt_pages} 页", TEXT_PRIMARY),
            ("ICC", result.icc_used or "Pillow内置", TEXT_SECONDARY),
            ("出血位", f"{result.bleed_mm:.1f}mm" if result.bleed_mm > 0 else "无", TEXT_SECONDARY),
            ("耗时", f"{result.elapsed_seconds:.1f}秒", TEXT_SECONDARY),
        ]
        for label, value, color in result_items:
            row = Frame(self.result_frame, bg=CARD_BG)
            row.pack(fill="x", pady=1)
            Label(row, text=f"{label}:", font=("Microsoft YaHei UI", 9),
                  bg=CARD_BG, fg=TEXT_MUTED, width=10, anchor="w").pack(side="left")
            Label(row, text=value, font=("Microsoft YaHei UI", 9, "bold"),
                  bg=CARD_BG, fg=color).pack(side="left")

        # AI审计结果
        if self.enable_audit.get() and self.api_key.get().strip():
            self._run_ai_audit(result.input_path)

    def _run_ai_audit(self, input_path: str) -> None:
        """后台运行AI版面审计。"""
        def _do_audit():
            try:
                from siliconflow_client import SiliconFlowVisionClient
                from pdf_parser import PDFParser as Parser

                client = SiliconFlowVisionClient(api_key=self.api_key.get().strip())
                if not client.enabled:
                    return

                parser = Parser()
                for page_idx in range(min(3, self._report.page_count if self._report else 3)):
                    img_bytes = parser.render_page_as_image(input_path, page_idx=page_idx, dpi=150)
                    prompt = (
                        "请分析这张印刷样册页面，用中文简短回答："
                        "1) 页面类型（整页位图/矢量排版/混合）；"
                        "2) 文字清晰度风险；"
                        "3) 色彩问题。"
                    )
                    audit = client.audit_page(img_bytes, prompt)
                    self.after(0, self._show_audit_result, page_idx + 1, audit.content)
            except Exception as exc:
                self._log(f"[审计] 失败: {exc}")

        threading.Thread(target=_do_audit, daemon=True).start()

    def _show_audit_result(self, page_num: int, content: str) -> None:
        self._log(f"[AI审计] 第{page_num}页: {content[:80]}...")

        # 审计结果卡片
        for w in self.result_frame.winfo_children():
            if hasattr(w, '_audit_tag'):
                w.destroy()

        sep = Frame(self.result_frame, bg=BORDER, height=1)
        sep.pack(fill="x", pady=(8, 4))
        sep._audit_tag = True

        Label(self.result_frame, text=f"AI 版面审计 - 第{page_num}页",
              font=("Microsoft YaHei UI", 9, "bold"),
              bg=CARD_BG, fg=ACCENT).pack(anchor="w")

        Label(self.result_frame, text=content, font=("Microsoft YaHei UI", 8),
              bg="#F0F4FF", fg=TEXT_PRIMARY, wraplength=260, justify="left",
              padx=8, pady=6).pack(fill="x")
        # tag it
        for w in self.result_frame.winfo_children():
            w._audit_tag = True

    def _on_open_output(self) -> None:
        path = self.output_path.get()
        if path and Path(path).exists():
            os.startfile(path)
        else:
            messagebox.showwarning("提示", "输出文件尚未生成")

    def _log(self, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{ts}] {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")


def _create_log_widget(parent: Frame):
    """创建暗色日志文本区域。"""
    import tkinter.scrolledtext as scrolledtext

    text_widget = scrolledtext.ScrolledText(
        parent,
        font=("Consolas", 9),
        bg="#1E1E2E",
        fg="#A6E3A1",
        insertbackground="#A6E3A1",
        selectbackground="#313244",
        bd=0,
        height=8,
        state="disabled",
        wrap="word",
    )
    return text_widget


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
