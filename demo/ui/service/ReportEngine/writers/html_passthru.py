# -*- coding: utf-8 -*-
from __future__ import annotations
from ..model import DocumentModel
from .base import BaseWriter, ensure_parent

class HtmlPassthruWriter(BaseWriter):
    """复用现有的 HTML 生成链路，这里只做占位以便统一接口。
       真正的 HTML 生成仍在现在的节点里完成，这里只负责“返回路径”。"""
    def write(self, doc: DocumentModel, output_path: str) -> str:
        ensure_parent(output_path)
        # 直接返回 output_path（假定外部节点已写入）
        return output_path
