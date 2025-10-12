# -*- coding: utf-8 -*-
"""
ReportEngine 默认配置（适配你的目录结构）
- 优先读同目录 config.json；其次读环境变量；最后走内置默认
- 自动创建输出/模板/日志/输入目录
- 默认目录规划：
    最终报告输出:    <ui>/reports/final_reports
    QueryEngine输入: <ui>/reports/query_engine_streamlit_reports
    Insight输入:     <ui>/reports/insight_engine_streamlit_reports
    Media输入:       <ui>/reports/media_engine_streamlit_reports
"""

from __future__ import annotations
import os, json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# 目录推断：.../ui/service/ReportEngine/utils/config.py
UTILS_DIR = Path(__file__).resolve().parent
RE_ROOT   = UTILS_DIR.parent           # .../ReportEngine
SRV_ROOT  = RE_ROOT.parent             # .../service
UI_ROOT   = SRV_ROOT.parent            # .../ui

def _norm(p: str) -> str:
    """展开 ~ 和 环境变量，并返回绝对规范化路径字符串"""
    return str(Path(os.path.expandvars(os.path.expanduser(p))).resolve())

@dataclass
class Config:
    # 输出/模板/日志
    output_dir: str
    log_file: str
    template_dir: str

    # 三个输入引擎的产出目录（ReportEngine 读取这些作为原始材料）
    insight_dir: str
    media_dir: str
    query_dir: str

    # LLM（仅供 ReportEngine 内部节点使用；与你的主链路互不影响）
    default_llm_provider: str = "zhipu"  # 统一默认走智谱，可按需改
    zhipu_api_key: Optional[str] = os.getenv("ZHIPU_API_KEY") or os.getenv("NAGA_API_KEY")
    zhipu_model: str = os.getenv("REPORT_ZHIPU_MODEL", "glm-4.5-flash")

    # 超时/重试（给长报告用）
    api_timeout: float = float(os.getenv("REPORT_API_TIMEOUT", "900"))
    max_retries: int = int(os.getenv("REPORT_MAX_RETRIES", "6"))

def _ensure_dirs(cfg: Config) -> None:
    Path(cfg.output_dir).mkdir(parents=True, exist_ok=True)
    Path(cfg.template_dir).mkdir(parents=True, exist_ok=True)
    Path(cfg.insight_dir).mkdir(parents=True, exist_ok=True)
    Path(cfg.media_dir).mkdir(parents=True, exist_ok=True)
    Path(cfg.query_dir).mkdir(parents=True, exist_ok=True)
    Path(Path(cfg.log_file).parent).mkdir(parents=True, exist_ok=True)

def _load_config_json_if_any() -> Optional[dict]:
    """支持 ReportEngine/ 与 ReportEngine/utils/ 放 config.json"""
    for p in [RE_ROOT / "config.json", UTILS_DIR / "config.json"]:
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
    return None

def load_config(_: Optional[str] = None) -> Config:
    # ——默认路径（与你的目录方案一致）——
    default_output_dir   = UI_ROOT / "reports" / "final_reports"
    default_log_file     = UI_ROOT / "logs" / "report.log"
    default_template_dir = RE_ROOT / "templates"
    default_insight_dir  = UI_ROOT / "reports" / "insight_engine_streamlit_reports"
    default_media_dir    = UI_ROOT / "reports" / "media_engine_streamlit_reports"
    default_query_dir    = UI_ROOT / "reports" / "query_engine_streamlit_reports"

    data = _load_config_json_if_any() or {}

    # 环境变量优先，其次 config.json，最后默认
    output_dir   = os.getenv("REPORT_OUTPUT_DIR")   or data.get("output_dir",   str(default_output_dir))
    log_file     = os.getenv("REPORT_LOG_FILE")     or data.get("log_file",     str(default_log_file))
    template_dir = os.getenv("REPORT_TEMPLATE_DIR") or data.get("template_dir", str(default_template_dir))
    insight_dir  = os.getenv("REPORT_INSIGHT_DIR")  or data.get("insight_dir",  str(default_insight_dir))
    media_dir    = os.getenv("REPORT_MEDIA_DIR")    or data.get("media_dir",    str(default_media_dir))
    query_dir    = os.getenv("REPORT_QUERY_DIR")    or data.get("query_dir",    str(default_query_dir))

    default_llm_provider = data.get("default_llm_provider", os.getenv("REPORT_LLM_PROVIDER", "zhipu"))
    zhipu_api_key = data.get("zhipu_api_key") or os.getenv("ZHIPU_API_KEY") or os.getenv("NAGA_API_KEY")
    zhipu_model   = data.get("zhipu_model", os.getenv("REPORT_ZHIPU_MODEL", "glm-4.5-flash"))
    api_timeout   = float(data.get("api_timeout", os.getenv("REPORT_API_TIMEOUT", "900")))
    max_retries   = int(data.get("max_retries", os.getenv("REPORT_MAX_RETRIES", "6")))

    cfg = Config(
        output_dir   = _norm(output_dir),
        log_file     = _norm(log_file),
        template_dir = _norm(template_dir),
        insight_dir  = _norm(insight_dir),
        media_dir    = _norm(media_dir),
        query_dir    = _norm(query_dir),
        default_llm_provider = default_llm_provider,
        zhipu_api_key = zhipu_api_key,
        zhipu_model   = zhipu_model,
        api_timeout   = api_timeout,
        max_retries   = max_retries,
    )
    _ensure_dirs(cfg)
    return cfg
