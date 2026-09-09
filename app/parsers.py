"""多模态解析器插件契约。

默认实现只负责确定性文本解析和非文本任务登记；生产部署可通过同一接口接入
Docling、PaddleOCR/PP-Structure 等 Worker，而无需修改数据模型和 API。
"""
from dataclasses import dataclass, field
from pathlib import Path
import re


@dataclass
class ParseResult:
    """解析器统一返回的标准化结果。"""

    parser_id: str
    parser_version: str
    confidence: float
    status: str
    paragraphs: list[dict] = field(default_factory=list)
    provenance: dict = field(default_factory=dict)


class ParserPlugin:
    """所有文档解析插件必须实现的最小接口。"""

    parser_id = "base"
    parser_version = "0.0"

    def supports(self, modality: str, suffix: str) -> bool:
        raise NotImplementedError

    def parse(self, path: Path, modality: str) -> ParseResult:
        raise NotImplementedError


class LocalTextParser(ParserPlugin):
    """本地纯文本解析器，生成段落级源位置。"""

    parser_id = "local-text-parser"
    parser_version = "1.0"

    def supports(self, modality: str, suffix: str) -> bool:
        return modality == "text" and suffix in {".txt", ".md", ".csv", ".json", ".html"}

    def parse(self, path: Path, modality: str) -> ParseResult:
        raw = path.read_text(errors="ignore")
        paragraphs = []
        for index, match in enumerate(re.finditer(r"[^\n]+(?:\n(?!\s*\n)[^\n]+)*", raw)):
            text = match.group(0).strip()
            if len(text) < 2:
                continue
            paragraphs.append({
                "paragraph": index,
                "char_start": match.start(),
                "char_end": match.end(),
                "text": text,
                "confidence": 0.95,
            })
        return ParseResult(
            parser_id=self.parser_id,
            parser_version=self.parser_version,
            confidence=0.95,
            status="PARSED" if paragraphs else "REVIEW_PENDING",
            paragraphs=paragraphs,
            provenance={"source_file": path.name, "page_count": 1, "layout_blocks": 0, "tables": 0, "ocr_confidence": None},
        )


class DoclingParserPlugin(ParserPlugin):
    """Docling 插件占位实现，等待异步 Worker 安装重量依赖。"""

    parser_id = "docling"
    parser_version = "pending"

    def supports(self, modality: str, suffix: str) -> bool:
        return suffix in {".pdf", ".docx", ".pptx", ".xlsx"} or modality == "document"

    def parse(self, path: Path, modality: str) -> ParseResult:
        return ParseResult(self.parser_id, self.parser_version, 0.0, "REVIEW_PENDING", provenance={
            "source_file": path.name, "page_count": None, "layout_blocks": None,
            "tables": None, "ocr_confidence": None, "pending_reason": "等待 Docling Worker",
        })


class PaddleOCRParserPlugin(ParserPlugin):
    """PaddleOCR/PP-Structure 插件占位实现，记录 OCR 任务和结构化 provenance。"""

    parser_id = "paddleocr-ppstructure"
    parser_version = "pending"

    def supports(self, modality: str, suffix: str) -> bool:
        return modality == "image" or suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}

    def parse(self, path: Path, modality: str) -> ParseResult:
        return ParseResult(self.parser_id, self.parser_version, 0.0, "REVIEW_PENDING", provenance={
            "source_file": path.name, "page_count": 1, "layout_blocks": None,
            "tables": None, "ocr_confidence": None, "pending_reason": "等待 PaddleOCR Worker",
        })


PARSER_PLUGINS: tuple[ParserPlugin, ...] = (LocalTextParser(), DoclingParserPlugin(), PaddleOCRParserPlugin())


def select_parser(path: Path, modality: str) -> ParserPlugin | None:
    """按内容模态和文件后缀选择第一个兼容插件。"""
    suffix = path.suffix.lower()
    return next((plugin for plugin in PARSER_PLUGINS if plugin.supports(modality, suffix)), None)
