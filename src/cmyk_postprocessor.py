"""
cmyk_postprocessor.py — PDF 真 CMYK 后处理模块
职责：
  通过 pikepdf 将已增强 PDF 里的 ICCBased-RGB 图像流替换为原生 CMYK JPEG 流，
  同时嵌入 CMYK ICC Profile（如 ISOcoated_v2_eci.icc）。

为什么需要这个模块：
  PyMuPDF 的 insert_image() 不支持高效存储 CMYK JPEG，会导致体积膨胀 6x。
  pikepdf 可以直接操作 PDF 内部流，实现零损耗替换。

使用方式：
  from cmyk_postprocessor import CMYKPostProcessor
  proc = CMYKPostProcessor(icc_path="models/ISOcoated_v2_eci.icc")
  proc.process("output/enhanced.pdf", "output/enhanced_cmyk.pdf")
"""

from __future__ import annotations

import io
import struct
from pathlib import Path
from typing import Optional

import pikepdf
from PIL import Image, ImageCms


def _build_icc_stream(pdf: pikepdf.Pdf, icc_path: Path) -> pikepdf.Object:
    """将 ICC Profile 文件打包为 pikepdf Stream，返回 [/ICCBased <stream>] 数组。"""
    icc_bytes = icc_path.read_bytes()

    # 从 ICC 头部读通道数（offset 16 是 colorspace sig，N 可以从 PCS 推）
    # 更简单：根据文件名/常识判断，或解析 offset 20 的 colorspace tag
    n_channels = _guess_icc_channels(icc_bytes)

    icc_stream = pikepdf.Stream(pdf, icc_bytes)
    icc_stream.stream_dict["/N"] = pikepdf.Integer(n_channels)
    icc_stream.stream_dict["/Alternate"] = (
        pikepdf.Name("/DeviceCMYK") if n_channels == 4 else pikepdf.Name("/DeviceRGB")
    )

    return pikepdf.Array([pikepdf.Name("/ICCBased"), icc_stream])


def _guess_icc_channels(icc_bytes: bytes) -> int:
    """从 ICC Profile 头部推测色彩通道数。"""
    if len(icc_bytes) < 20:
        return 3
    # ICC header offset 16: colorspace signature (4 bytes)
    cs_sig = icc_bytes[16:20]
    if cs_sig == b"CMYK":
        return 4
    if cs_sig in (b"RGB ", b"sRGB"):
        return 3
    if cs_sig in (b"GRAY", b"gray"):
        return 1
    return 3


def _rgb_jpeg_to_cmyk_jpeg(
    rgb_jpeg_bytes: bytes,
    icc_path: Optional[Path],
    quality: int = 80,
    jpeg_subsampling: int = 2,
) -> bytes:
    """将 RGB JPEG 字节流转换为 CMYK JPEG 字节流，可选 ICC 色彩管理。"""
    img = Image.open(io.BytesIO(rgb_jpeg_bytes)).convert("RGB")

    if icc_path and icc_path.exists():
        try:
            # 使用 LittleCMS 通过 ICC Profile 转换
            src_profile = ImageCms.createProfile("sRGB")
            dst_profile = ImageCms.getOpenProfile(str(icc_path))
            transform = ImageCms.buildTransformFromOpenProfiles(
                src_profile, dst_profile,
                inMode="RGB", outMode="CMYK",
                renderingIntent=ImageCms.Intent.PERCEPTUAL,
            )
            img_cmyk = ImageCms.applyTransform(img, transform)
        except Exception:
            # 降级：Pillow 内置转换
            img_cmyk = img.convert("CMYK")
    else:
        img_cmyk = img.convert("CMYK")

    out_buf = io.BytesIO()
    img_cmyk.save(
        out_buf,
        format="JPEG",
        quality=quality,
        subsampling=jpeg_subsampling,
        optimize=True,
    )
    return out_buf.getvalue()


class CMYKPostProcessor:
    """pikepdf CMYK 后处理器：将 PDF 中的 ICCBased-RGB 图像流替换为 CMYK JPEG。"""

    def __init__(
        self,
        icc_path: Optional[str | Path] = None,
        jpeg_quality: int = 80,
        jpeg_subsampling: int = 2,
        verbose: bool = True,
    ) -> None:
        self.icc_path = Path(icc_path) if icc_path else None
        self.jpeg_quality = jpeg_quality
        self.jpeg_subsampling = jpeg_subsampling
        self.verbose = verbose

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(msg)

    def process(
        self,
        input_path: str | Path,
        output_path: str | Path,
    ) -> dict:
        """处理整个 PDF，返回统计信息字典。"""
        input_path = Path(input_path)
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        self._log(f"\n[CMYK后处理] 输入: {input_path.name}")

        pdf = pikepdf.open(input_path)

        # 预构建 CMYK ICC stream（共享复用）
        cmyk_icc_cs = None
        if self.icc_path and self.icc_path.exists():
            self._log(f"  加载 ICC: {self.icc_path.name}")
            cmyk_icc_cs = _build_icc_stream(pdf, self.icc_path)

        converted = 0
        skipped = 0
        errors = []

        for page_idx, page in enumerate(pdf.pages):
            resources = page.get("/Resources", None)
            if resources is None:
                continue
            xobjs = resources.get("/XObject", {})

            for name in list(xobjs.keys()):
                xobj = xobjs[name]
                try:
                    if not hasattr(xobj, "stream_dict"):
                        continue
                    sd = xobj.stream_dict
                    # 只处理 JPEG (DCTDecode) 图像
                    filt = sd.get("/Filter", None)
                    if filt is None or str(filt) != "/DCTDecode":
                        continue

                    cs = sd.get("/ColorSpace", None)
                    if cs is None:
                        continue

                    # 检查是否是 ICCBased-RGB（需要替换）或已是 CMYK（跳过）
                    cs_str = str(cs)
                    if "ICCBased" not in cs_str and "DeviceCMYK" not in cs_str:
                        # 裸 /DeviceRGB，也需要转换
                        if "DeviceRGB" not in cs_str:
                            skipped += 1
                            continue
                    if "DeviceCMYK" in cs_str:
                        self._log(f"  页{page_idx+1} {name}: 已是 CMYK，跳过")
                        skipped += 1
                        continue

                    # 读取原始 RGB JPEG 流
                    rgb_bytes = bytes(xobj.read_raw_bytes())
                    w = int(sd.get("/Width", 0))
                    h = int(sd.get("/Height", 0))

                    self._log(f"  页{page_idx+1} {name}: RGB→CMYK ({w}×{h})")

                    # 转换为 CMYK JPEG
                    cmyk_bytes = _rgb_jpeg_to_cmyk_jpeg(
                        rgb_bytes,
                        self.icc_path,
                        quality=self.jpeg_quality,
                        jpeg_subsampling=self.jpeg_subsampling,
                    )

                    # 替换图像流
                    xobj.write(cmyk_bytes, filter=pikepdf.Name("/DCTDecode"))

                    # 更新 ColorSpace
                    if cmyk_icc_cs is not None:
                        sd["/ColorSpace"] = cmyk_icc_cs
                    else:
                        sd["/ColorSpace"] = pikepdf.Name("/DeviceCMYK")

                    # 更新尺寸（CMYK JPEG 是 4 通道，Length 会自动更新）
                    converted += 1

                except Exception as exc:
                    msg = f"页{page_idx+1} {name} 转换失败: {exc}"
                    errors.append(msg)
                    self._log(f"  [警告] {msg}")

        self._log(f"  转换完成: {converted} 张图像，{skipped} 张跳过，{len(errors)} 个错误")
        self._log(f"  保存到: {output_path.name}")

        pdf.save(str(output_path))
        pdf.close()

        return {
            "converted": converted,
            "skipped": skipped,
            "errors": errors,
            "output_path": str(output_path),
        }


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("用法: python cmyk_postprocessor.py <input.pdf> <output.pdf> [icc_path]")
        sys.exit(1)

    icc = sys.argv[3] if len(sys.argv) > 3 else None
    proc = CMYKPostProcessor(icc_path=icc)
    stats = proc.process(sys.argv[1], sys.argv[2])
    print(f"\n完成: 转换 {stats['converted']} 张, 跳过 {stats['skipped']} 张")
    if stats["errors"]:
        print("错误:")
        for e in stats["errors"]:
            print(f"  - {e}")
