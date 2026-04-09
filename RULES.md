# 开发规范 RULES.md

## 代码规范
- Python 3.10+，使用类型注解
- 每个模块独立，通过 `src/pipeline.py` 串联
- 所有函数必须有 docstring
- 异常要捕获并记录，不能让整个流程崩溃

## 文件命名
- 源码：`src/module_name.py`（snake_case）
- 测试：`tests/test_module_name.py`
- 输出文件：`output/原文件名_print_ready_时间戳.pdf`

## 印刷技术规范
- 目标输出分辨率：300 DPI（最低可接受250 DPI）
- 色彩：输入RGB → 输出CMYK，使用 ISO Coated v2 ICC Profile
- 黑色文字：叠印（overprint）
- 出血位：默认3mm，可配置
- 压缩：图像用JPEG质量95，其他元素用ZIP

## 更新记录
- 2026-04-08：初始化规范
