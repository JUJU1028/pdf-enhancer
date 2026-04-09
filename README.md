# PDF 印刷增强工具

将低质量样册 PDF 转换为高分辨率、可印刷输出的专业工具。

**当前版本：v2.1** — 真 CMYK 字节流输出 · 100 分印刷就绪度 · GUI 增强

---

## 功能特性

| 功能 | 说明 |
|------|------|
| **DPI 提升** | 72 DPI → 300 DPI（Lanczos 4x 重采样 + CLAHE 局部对比度增强）|
| **真 CMYK 输出** | pikepdf 后处理，将 RGB JPEG 流替换为原生 4通道 CMYK 字节流 |
| **ICC 色彩管理** | 自动发现 ISOcoated_v2_eci（ECI 官方印刷 Profile），色彩转换保真 |
| **双路径策略** | 整页位图 → `rebuild_page`，混合页面 → `enhance_embedded`，自动判断 |
| **AI 版面审计** | 接入硅基流动 Qwen2.5-VL-32B-Instruct 视觉模型（需 API Key）|
| **GUI 桌面应用** | 拖放支持、8 项诊断指标、页面策略可视化、真 CMYK 开关 |
| **出血位支持** | 3mm 裁切标记，可配置 |
| **体积控制** | JPEG Q80 + YCbCr Subsample，输出通常 ≤ 3x 输入大小 |

---

## 快速开始

### 安装依赖

```bash
pip install -r requirements.txt
```

### 方式一：GUI（推荐）

双击运行 `pdf_enhancer_gui.py`，或命令行：

```bash
python pdf_enhancer_gui.py
```

### 方式二：命令行验证

```bash
python validate_pipeline.py
```

---

## 项目结构

```
pdf-enhancer/
├── pdf_enhancer_gui.py      # GUI 桌面应用（双击运行）
├── validate_pipeline.py      # 端到端命令行验证脚本
├── gen_report.py            # 生成产品检测 HTML 报告
├── requirements.txt         # Python 依赖清单
├── .gitignore
│
├── src/
│   ├── pdf_parser.py        # PDF 解析 + 页面策略 + 印刷评分
│   ├── image_enhancer.py     # 图像增强（Lanczos / CLAHE / Real-ESRGAN）
│   ├── color_converter.py   # ICC 色彩转换（自动发现 ICC Profile）
│   ├── pipeline.py           # 主处理管线（双路径调度）
│   ├── cmyk_postprocessor.py # pikepdf 真 CMYK 后处理（v2.1）
│   └── siliconflow_client.py # 硅基流动视觉模型接入
│
├── models/
│   ├── ISOcoated_v2_eci.icc # ECI 官方正式印刷 ICC Profile（CMYK）
│   └── PSOcoated_v3.icc     # ECI 备用 ICC Profile
│
├── tests/                   # 单元测试（pytest，46 个测试）
│   ├── conftest.py
│   ├── test_pdf_parser.py
│   ├── test_image_enhancer.py
│   ├── test_color_converter.py
│   └── test_pipeline_integration.py
│
├── sample_input/
│   └── zifeng-brochure.pdf  # 真实低清样册（18 页）
│
└── output/                   # 处理结果输出目录（每次运行生成）
    ├── enhanced_zifeng-brochure.pdf   # ICCBased-RGB 输出
    └── enhanced_zifeng-cmyk.pdf       # 真 CMYK 输出（v2.1）
```

---

## 核心参数说明

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `target_dpi` | `300` | 输出目标 DPI |
| `convert_to_cmyk` | `True` | 是否执行 RGB→CMYK 转换 |
| `bleed_mm` | `3.0` | 出血位（毫米，0 = 不添加）|
| `jpeg_quality` | `80` | JPEG 压缩质量（1-100）|
| `enhance_mode` | `document` | 增强模式：`fast` / `quality` / `document` |
| `rendering_intent` | `perceptual` | 渲染意图：`perceptual` / `relative` / `saturation` / `absolute` |

### 环境变量

| 变量 | 说明 |
|------|------|
| `SILICONFLOW_API_KEY` | 硅基流动 API Key（用于 AI 版面审计，可选）|

---

## 测试

```bash
# 运行全部 46 个单元测试
pytest tests/ -v

# 运行覆盖率报告
pytest tests/ -v --cov=src --cov-report=html
```

---

## 技术架构

```
输入 PDF (72 DPI RGB)
    │
    ▼
pdf_parser.py — 解析页面结构，判断策略
    • 整页位图 → rebuild_page
    • 混合页面 → enhance_embedded
               │
               ▼
image_enhancer.py — 72→300 DPI
    • Lanczos 4x + CLAHE 局部对比度
    • 可选 Real-ESRGAN AI 超分
               │
               ▼
color_converter.py — ICC 色彩转换
    • RGB → CMYK + ISOcoated_v2_eci ICC Profile
               │
               ▼
pipeline.py — PyMuPDF 页面重建
    • insert_image (RGB JPEG)
    • 嵌入 ICC Profile
               │
               ▼
cmyk_postprocessor.py (v2.1 新增)
    • pikepdf 读取输出 PDF
    • 替换 XObject 图像流为 CMYK JPEG
    • 嵌入 CMYK ICC Profile
               │
               ▼
输出 PDF（300 DPI 真 CMYK 字节流）
```

---

## 版本历史

- **v2.1** — 真 CMYK 字节流输出（pikepdf）、GUI 增强（拖放/8项诊断/页面策略）、100/100 评分闭环
- **v2.0** — ICCBased 色彩管理、GUI 双路径策略、正式 ICC Profile
- **v1.0** — Lanczos + CLAHE 基础增强

---

## 许可证

MIT License
