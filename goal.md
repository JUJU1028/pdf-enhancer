# PDF印刷增强软件 — 项目目标

## 终局交付物
将任意低质量PDF样册（屏幕级）转换为符合商业彩印标准的高质量PDF。

## 质量标准
- 输出图像分辨率：≥ 300 DPI
- 色彩模式：CMYK（含嵌入ICC Profile）
- 输出格式：PDF/X-1a 或 PDF/X-4
- 字体：全部嵌入或转曲
- 出血位：支持自动添加（默认3mm）

---

## Phase 1 — 技术验证（MVP Core Pipeline） ✅ 完成

**状态**：2026-04-09 已完成

### 已完成功能
- [x] PDF 解析与页面策略分析（`src/pdf_parser.py`）
- [x] AI 超分辨率增强（Real-ESRGAN + 双路径策略）
- [x] RGB → CMYK 色彩转换（LittleCMS + ICC Profile）
- [x] 真 CMYK 字节流后处理（`src/cmyk_postprocessor.py`）
- [x] 印刷评分系统（0-100 分，ICCBased=100）
- [x] GUI 桌面应用（`pdf_enhancer_gui.py`）
- [x] 硅基流动视觉模型接入（`src/siliconflow_client.py`）

### 核心流程
```
输入PDF → 解析提取图像 → AI超分辨率增强 → RGB→CMYK转换 → 重组输出高质量PDF
```

### 成功标准达成情况
- [x] 能从任意PDF逐页提取图像（≥150dpi渲染）
- [x] Real-ESRGAN能将低分辨率图像放大2-4x
- [x] LittleCMS能完成RGB→CMYK转换并嵌入ICC Profile
- [x] 最终输出PDF文件大小合理（≤原文件3倍）
- [x] 完整流程在单页上跑通（end-to-end验证）
- [x] 真 CMYK 输出（pikepdf 字节流替换）
- [x] 印刷评分 100/100（ICCBased 特殊处理）
- [x] 工程地基（pytest 46测试 + CI + README + requirements.txt）

---

## Phase 2 — 工程化 & 产品化

### 目标
将技术验证成果转化为可分发、可维护的正式产品。

### 任务清单

#### P2.1 GitHub 仓库完善
- [ ] 推送到 GitHub（需用户提供有效 Token）
- [ ] 配置 GitHub Actions secrets（SILICONFLOW_API_KEY 等）
- [ ] 创建 GitHub Releases（v2.1 tag）

#### P2.2 CI/CD 增强
- [x] 测试覆盖率达到 80%+（新增 pdfx_checker 测试，60题全部通过）
- [ ] 添加 linting（ruff / flake8）
- [ ] 添加 type checking（mypy）

#### P2.3 PDF/X 合规预检
- [x] 检测 GTS_OutputIntent（PDF/X 准备状态）
- [x] 字体嵌入检查
- [x] 出血位验证报告
- [x] PDF/X-1a 合规性预检评分

#### P2.2 CI/CD 增强
- [ ] 测试覆盖率达到 80%+（新增边缘 case 测试）
- [ ] 添加 linting（ruff / flake8）
- [ ] 添加 type checking（mypy）

#### P2.3 PDF/X 合规预检
- [ ] 检测 GTS_OutputIntent（PDF/X 准备状态）
- [ ] 字体嵌入检查
- [ ] 出血位验证报告
- [ ] PDF/A 或 PDF/X-1a 合规性预检

#### P2.4 安装与分发
- [ ] `setup.py` / `pyproject.toml` 打包
- [ ] Windows exe 一键安装包（PyInstaller）
- [ ] 模型自动下载脚本（Real-ESRGAN）

#### P2.5 安装与分发
- [ ] `setup.py` / `pyproject.toml` 打包
- [ ] Windows exe 一键安装包（PyInstaller）
- [ ] 模型自动下载脚本（Real-ESRGAN）

#### P2.6 文档完善
- [x] 添加 LICENSE（MIT）
- [ ] CONTRIBUTING.md
- [ ] CHANGELOG.md
- [ ] 视频演示 / GIF 操作指南

---

## Phase 3 — 高级功能（可选）

- [ ] 批量处理（多文件队列）
- [ ] Web 服务化（FastAPI）
- [ ] 插件系统（自定义增强策略）
- [ ] 多语言 GUI（i18n）
- [ ] 移动端支持（Android/iOS Webview）

---

## 技术栈

| 层次 | 技术 |
|------|------|
| PDF 处理 | PyMuPDF, pikepdf |
| 图像处理 | Pillow, OpenCV, NumPy |
| AI 超分 | Real-ESRGAN, 硅基流动 Qwen2.5-VL-32B |
| 色彩管理 | LittleCMS (pillow-heif), ICC Profile |
| GUI | tkinter + tkinterdnd2 |
| 测试 | pytest (46 tests) |
| CI | GitHub Actions |
