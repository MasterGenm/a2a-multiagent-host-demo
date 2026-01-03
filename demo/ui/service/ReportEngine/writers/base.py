# -*- coding: utf-8 -*-
from __future__ import annotations
from pathlib import Path
from typing import Literal
from ..model import DocumentModel

class BaseWriter:
    def write(self, doc: DocumentModel, output_path: str) -> str:
        raise NotImplementedError

def ensure_parent(p: str):
    Path(p).parent.mkdir(parents=True, exist_ok=True)

def pick_ext(fmt: str) -> str:
    return ".docx" if fmt == "docx" else ".pdf" if fmt == "pdf" else ".html"

def pick_writer(fmt: Literal["html","docx","pdf"]):
    if fmt == "docx":
        from .docx_writer import DocxWriter
        return DocxWriter()
    if fmt == "pdf":
        from .pdf_writer import PdfWriter
        return PdfWriter()
    # 兼容现有 HTML 产线（复用现在的 html_generation_node）
    from .html_passthru import HtmlPassthruWriter
    return HtmlPassthruWriter()
