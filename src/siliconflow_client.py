"""
siliconflow_client.py — 硅基流动视觉模型接入层
说明：
- 不在代码里硬编码任何API Key
- 通过环境变量 SILICONFLOW_API_KEY / SILICONFLOW_VISION_MODEL 启用
- 支持单页审计、批量审计、OCR提取
- 结果结构化返回，可嵌入GUI和报告
"""

from __future__ import annotations

import base64
import json
import os
import urllib.request
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PageAuditItem:
    """单页审计结果。"""
    page_num: int = 0
    page_type: str = ""        # 整页位图 / 矢量排版 / 混合
    text_risk: str = ""        # 文字清晰度风险
    color_issue: str = ""      # 色彩问题
    layout_note: str = ""      # 版面备注
    raw_content: str = ""      # 原始模型返回
    ocr_text: str = ""         # OCR 识别的文字


@dataclass
class BatchAuditResult:
    """批量审计结果。"""
    model: str
    total_pages: int
    items: list[PageAuditItem] = field(default_factory=list)
    summary: str = ""

    def to_markdown(self) -> str:
        lines = [
            "## AI 版面审计报告",
            f"使用模型: `{self.model}`",
            f"审计页数: {self.total_pages}",
            "",
        ]
        if self.summary:
            lines.append(f"### 总体评估\n{self.summary}\n")

        for item in self.items:
            lines.append(f"### 第 {item.page_num} 页")
            if item.page_type:
                lines.append(f"- **页面类型**: {item.page_type}")
            if item.text_risk:
                lines.append(f"- **文字风险**: {item.text_risk}")
            if item.color_issue:
                lines.append(f"- **色彩问题**: {item.color_issue}")
            if item.layout_note:
                lines.append(f"- **版面备注**: {item.layout_note}")
            if item.ocr_text:
                lines.append(f"- **OCR 文字** (前200字):\n  > {item.ocr_text[:200]}")
            # 若结构化字段都为空，显示原始内容
            if not any([item.page_type, item.text_risk, item.color_issue]):
                lines.append(f"\n{item.raw_content}")
            lines.append("")

        return "\n".join(lines)


class SiliconFlowVisionClient:
    """OpenAI兼容格式的硅基流动视觉客户端。"""

    # 支持的视觉模型
    KNOWN_MODELS = {
        "ocr": "PaddlePaddle/PaddleOCR-VL",
        "vision": "Qwen/Qwen2.5-VL-32B-Instruct",
        "vision_fast": "Qwen/Qwen3-VL-8B-Instruct",
        "vision_large": "Qwen/Qwen3-VL-32B-Instruct",
    }

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://api.siliconflow.cn/v1",
        model: Optional[str] = None,
        timeout: int = 90,
    ):
        self.api_key = api_key or os.getenv("SILICONFLOW_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.model = model or os.getenv("SILICONFLOW_VISION_MODEL", self.KNOWN_MODELS["vision"])
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _chat(self, image_bytes: bytes, prompt: str, temperature: float = 0.1) -> str:
        """发送视觉请求并返回文本内容。"""
        if not self.enabled:
            raise RuntimeError("未设置 API Key，无法启用视觉审计")

        data_url = self._image_to_data_url(image_bytes)
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            "stream": False,
            "temperature": temperature,
            "max_tokens": 1024,
        }
        request = urllib.request.Request(
            url=f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = json.loads(response.read().decode("utf-8"))

        return body["choices"][0]["message"]["content"]

    def audit_page(self, image_bytes: bytes, prompt: str = "") -> PageAuditItem:
        """审计单页，返回结构化结果（兼容自然语言返回）。"""
        if not prompt:
            prompt = (
                "请分析这张印刷样册页面，用中文简洁回答以下三点，每点用【】标注标题：\n"
                "【页面类型】整页图片/矢量排版/混合\n"
                "【文字风险】文字清晰度风险描述\n"
                "【色彩问题】色彩问题描述"
            )

        raw = self._chat(image_bytes, prompt)
        item = PageAuditItem(raw_content=raw)

        # 先尝试解析 JSON（如果模型返回了 JSON 格式）
        try:
            cleaned = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            data = json.loads(cleaned)
            item.page_type = data.get("page_type", "")
            item.text_risk = data.get("text_risk", "")
            item.color_issue = data.get("color_issue", "")
            item.layout_note = data.get("layout_note", "")
        except (json.JSONDecodeError, KeyError):
            # 回退：从自然语言中提取【】标签内容
            import re
            def _extract(tag: str) -> str:
                m = re.search(rf"【{tag}】\s*(.+?)(?=【|\Z)", raw, re.DOTALL)
                return m.group(1).strip() if m else ""
            item.page_type = _extract("页面类型")
            item.text_risk = _extract("文字风险")
            item.color_issue = _extract("色彩问题")
            item.layout_note = _extract("版面备注")

        return item

    def ocr_page(self, image_bytes: bytes) -> str:
        """OCR识别页面文字。"""
        prompt = "请识别图片中的所有文字内容，按原文顺序输出，不要添加任何解释。"
        return self._chat(image_bytes, prompt)

    def batch_audit(
        self,
        image_list: list[tuple[int, bytes]],
        do_ocr: bool = False,
    ) -> BatchAuditResult:
        """批量审计多页。image_list: [(page_num, image_bytes), ...]"""
        items: list[PageAuditItem] = []
        for page_num, img_bytes in image_list:
            print(f"  [AI审计] 第 {page_num} 页...")
            try:
                item = self.audit_page(img_bytes)
                item.page_num = page_num

                if do_ocr:
                    try:
                        item.ocr_text = self.ocr_page(img_bytes)
                    except Exception as exc:
                        item.ocr_text = f"OCR失败: {exc}"

                items.append(item)
            except Exception as exc:
                items.append(PageAuditItem(page_num=page_num, raw_content=f"审计失败: {exc}"))

        # 生成摘要
        summary = self._generate_summary(items)

        return BatchAuditResult(
            model=self.model,
            total_pages=len(image_list),
            items=items,
            summary=summary,
        )

    def _generate_summary(self, items: list[PageAuditItem]) -> str:
        """基于审计结果生成摘要。"""
        if not items:
            return "无审计结果"

        types: dict[str, int] = {}
        risks = 0
        color_issues = 0

        for item in items:
            t = item.page_type or "未知"
            types[t] = types.get(t, 0) + 1
            if item.text_risk and "低" not in item.text_risk and "无" not in item.text_risk:
                risks += 1
            if item.color_issue and "无" not in item.color_issue:
                color_issues += 1

        lines = [f"共审计 {len(items)} 页。"]
        for t, count in types.items():
            lines.append(f"- {t}: {count} 页")
        if risks > 0:
            lines.append(f"- 存在文字清晰度风险: {risks} 页")
        if color_issues > 0:
            lines.append(f"- 存在色彩问题: {color_issues} 页")

        return "\n".join(lines)

    def _image_to_data_url(self, image_bytes: bytes, max_short_side: int = 800) -> str:
        """将图像压缩到合理大小后转为 data URL。"""
        from PIL import Image
        import io as _io

        img = Image.open(_io.BytesIO(image_bytes)).convert("RGB")
        # 限制最短边不超过 max_short_side，避免 base64 超过 API 限制
        w, h = img.size
        short_side = min(w, h)
        if short_side > max_short_side:
            scale = max_short_side / short_side
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

        buf = _io.BytesIO()
        img.save(buf, format="JPEG", quality=72, optimize=True)
        encoded = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{encoded}"
