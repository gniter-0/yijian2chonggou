#!/usr/bin/env python3
"""extract_pdf.py - 第一步：PDF 提取（每页文本 + 截图），不调用大模型。

用法:
    python extract_pdf.py <file.pdf> <out_dir> [--dpi 150] [--noise extra_patterns.txt] [--no-images]

输出:
    <out_dir>/extraction.json   {"source_file","chapter","total_pages","pages":[{"page_num","text","text_source","image_path","lines"}]}
    <out_dir>/page_0001.png ...
依赖: PyMuPDF (pip install pymupdf)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:  # pragma: no cover
    import fitz  # 旧版 PyMuPDF

# 通用噪声（广告/联系方式/视频集数）。宁可少删，不可错删；学科特有的用 --noise 追加。
DEFAULT_NOISE = [
    r"联系电话[：:].*",
    r"联系地址[：:].*",
    r".*押题联系.*",
    r".*联系QQ.*",
    r".*联系微信.*",
    r"精准押题.*",
    r"唯一联系.*",
    r"名师面授.*",
    r"视频\d+[—\-~到]+\d*集?\s*$",
]


def chapter_from_filename(name: str) -> str:
    cleaned = re.sub(r"[（(].+?[)）]", "", name)
    cleaned = re.sub(r"^\s*视频?\d+[—\u2013\-~到]+\d*集?\s*", "", cleaned)
    return cleaned.replace(".pdf", "").strip() or name


def clean_text(text: str, patterns: list[re.Pattern], header: str) -> tuple[str, list[str]]:
    removed: list[str] = []
    for pat in patterns:
        for m in pat.finditer(text):
            if m.group(0).strip():
                removed.append(m.group(0).strip())
        text = pat.sub("", text)
    if header:
        kept = []
        for line in text.split("\n"):
            if line.strip() == header:
                removed.append(line.strip())
                continue
            kept.append(line)
        text = "\n".join(kept)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip(), removed


def repeated_lines(raw_pages: list[str], ratio: float = 0.6) -> set[str]:
    """出现在 >= ratio 比例页面上的短行 = 页眉/页脚/网址（至少 3 页才启用）。"""
    if len(raw_pages) < 3:
        return set()
    counts: dict[str, int] = {}
    for text in raw_pages:
        for line in {ln.strip() for ln in text.split("\n") if ln.strip()}:
            if len(line) <= 40:
                counts[line] = counts.get(line, 0) + 1
    need = max(3, int(len(raw_pages) * ratio + 0.999))
    return {ln for ln, c in counts.items() if c >= need}


def strip_page_furniture(text: str, repeated: set[str], page_num: int) -> tuple[str, list[str]]:
    """删除重复页眉页脚行，以及页面前 6 行内单独成行的页码。"""
    kept, removed = [], []
    lines = text.split("\n")
    for i, line in enumerate(lines):
        s = line.strip()
        if s in repeated or (i < 6 and s == str(page_num)):
            removed.append(s)
            continue
        kept.append(line)
    return "\n".join(kept), removed


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):  # Windows 控制台默认 GBK，统一输出 UTF-8
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="PDF 提取：每页文本 + 截图")
    ap.add_argument("pdf")
    ap.add_argument("out_dir")
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--noise", help="额外噪声正则文件，每行一个")
    ap.add_argument("--no-images", action="store_true")
    ap.add_argument("--keep-furniture", action="store_true", help="不自动删除重复页眉页脚")
    args = ap.parse_args()

    pdf = Path(args.pdf)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    raw = list(DEFAULT_NOISE)
    if args.noise:
        raw += [ln for ln in Path(args.noise).read_text(encoding="utf-8").splitlines() if ln.strip()]
    patterns = [re.compile(p, re.MULTILINE) for p in raw]
    chapter = chapter_from_filename(pdf.name)

    doc = fitz.open(str(pdf))
    raw_pages = [page.get_text("text") for page in doc]
    repeated = set() if args.keep_furniture else repeated_lines(raw_pages)
    pages = []
    for i, page in enumerate(doc):
        text, removed_furniture = strip_page_furniture(raw_pages[i], repeated, i + 1)
        text, removed = clean_text(text, patterns, chapter)
        removed = removed_furniture + removed
        item = {
            "page_num": i + 1,
            "text": text,
            "text_source": "pdf" if text else "empty",  # empty → 需要 OCR，信任度降级
            "removed_noise": removed,
        }
        if not args.no_images:
            img = out / f"page_{i + 1:04d}.png"
            page.get_pixmap(dpi=args.dpi).save(str(img))
            item["image_path"] = img.name
        pages.append(item)
    doc.close()

    result = {"source_file": pdf.name, "chapter": chapter, "total_pages": len(pages), "pages": pages}
    (out / "extraction.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    empty = [p["page_num"] for p in pages if p["text_source"] == "empty"]
    print(f"提取完成: {len(pages)} 页 -> {out}")
    if repeated:
        print(f"自动删除的重复页眉页脚: {sorted(repeated)}")
    if empty:
        print(f"[WARN] 以下页没有可提取文本（可能是扫描版，需要 OCR）: {empty}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
