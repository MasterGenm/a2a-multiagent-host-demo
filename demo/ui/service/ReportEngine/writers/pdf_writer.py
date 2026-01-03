# -*- coding: utf-8 -*-
from __future__ import annotations

import os

from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.styles import ParagraphStyle
from ..model import DocumentModel, TableData
from .base import BaseWriter, ensure_parent

_CJK_FONT_NAME = "STSong-Light"  # built-in CID font for Chinese (no external .ttf required)
_WIN_FONT_CANDIDATES = [
    ("MicrosoftYaHei", r"C:\Windows\Fonts\msyh.ttc", 0),
    ("SimSun", r"C:\Windows\Fonts\simsun.ttc", 0),
    ("SimSun-1", r"C:\Windows\Fonts\simsun.ttc", 1),
]


def _ensure_cjk_font() -> str | None:
    try:
        pdfmetrics.getFont(_CJK_FONT_NAME)
        return _CJK_FONT_NAME
    except Exception:
        try:
            pdfmetrics.registerFont(UnicodeCIDFont(_CJK_FONT_NAME))
            return _CJK_FONT_NAME
        except Exception:
            pass

    # Fallback: try Windows fonts (ttc/ttf) if available.
    for name, path, subfont_index in _WIN_FONT_CANDIDATES:
        try:
            pdfmetrics.getFont(name)
            return name
        except Exception:
            pass

        try:
            if not os.path.exists(path):
                continue
            pdfmetrics.registerFont(TTFont(name, path, subfontIndex=subfont_index))
            return name
        except Exception:
            continue

    return None


class PdfWriter(BaseWriter):
    def write(self, doc: DocumentModel, output_path: str) -> str:
        ensure_parent(output_path)
        styles = getSampleStyleSheet()

        cjk_font = _ensure_cjk_font()

        def _style(name: str, base: str) -> ParagraphStyle:
            s: ParagraphStyle = styles[base].clone(name)
            if cjk_font:
                s.fontName = cjk_font
                s.wordWrap = "CJK"
            return s

        title_style = _style("TitleCJK", "Title")
        heading1_style = _style("Heading1CJK", "Heading1")
        heading3_style = _style("Heading3CJK", "Heading3")
        normal_style = _style("NormalCJK", "Normal")
        body_style = _style("BodyTextCJK", "BodyText")

        story = []

        # 标题
        story.append(Paragraph(f"<b>{doc.meta.title}</b>", title_style))
        if doc.meta.subtitle:
            story.append(Paragraph(doc.meta.subtitle, heading3_style))
        meta_line = " ".join([x for x in [doc.meta.author, doc.meta.date] if x])
        if meta_line:
            story.append(Paragraph(meta_line, normal_style))
        story.append(Spacer(1, 12))

        # 正文
        for s in doc.sections:
            story.append(Paragraph(s.title, heading1_style))
            for p in s.paragraphs:
                story.append(Paragraph(p, body_style))
                story.append(Spacer(1, 6))
            # bullets
            for b in s.bullets:
                story.append(Paragraph(f"• {b}", body_style))
            # tables
            for t in s.tables:
                story.append(self._mk_table(t))
                story.append(Spacer(1, 10))

        # 参考文献
        if doc.references:
            story.append(PageBreak())
            story.append(Paragraph("参考文献", heading1_style))
            for i, r in enumerate(doc.references, start=1):
                story.append(Paragraph(f"[{i}] {r}", body_style))

        doc_tpl = SimpleDocTemplate(output_path, pagesize=A4, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)
        doc_tpl.build(story)
        return output_path

    @staticmethod
    def _mk_table(t: TableData) -> Table:
        data = []
        if t.headers:
            data.append([str(h) for h in t.headers])
        for row in t.rows:
            data.append([str(x) for x in row])
        tbl = Table(data)
        st = TableStyle([
            ("BOX", (0,0), (-1,-1), 0.5, colors.grey),
            ("INNERGRID", (0,0), (-1,-1), 0.25, colors.grey),
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#f0f0f0")) if t.headers else (),
            ("ALIGN", (0,0), (-1,-1), "LEFT"),
        ])
        tbl.setStyle(st)
        return tbl
