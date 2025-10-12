# -*- coding: utf-8 -*-
from pathlib import Path
import os
from typing import Optional

# ui 根目录： .../demo/ui
UI_ROOT = Path(__file__).resolve().parents[2]

# 顶层 reports 目录（不直接落文件）
REPORTS_ROOT = (UI_ROOT / "reports").resolve()

# 子目录（可用环境变量覆盖）
QUERY_OUT_DIR = Path(os.getenv(
    "A2A_QUERY_DIR",
    str(REPORTS_ROOT / "query_engine_streamlit_reports")
)).resolve()

FINAL_OUT_DIR = Path(os.getenv(
    "A2A_FINAL_DIR",
    str(REPORTS_ROOT / "final_reports")
)).resolve()

MEDIA_OUT_DIR = Path(os.getenv(
    "A2A_MEDIA_DIR",
    str(REPORTS_ROOT / "media_engine_streamlit_reports")
)).resolve()

def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p

def set_cwd_to_ui_root():
    # 固定工作目录，避免默认跑到 C:\Users\Lenovo
    os.chdir(str(UI_ROOT))

def get_query_dir() -> Path:
    return ensure_dir(QUERY_OUT_DIR)

def get_final_dir() -> Path:
    return ensure_dir(FINAL_OUT_DIR)

def get_media_dir() -> Path:
    return ensure_dir(MEDIA_OUT_DIR)

def query_path(filename: str) -> Path:
    return get_query_dir() / filename

def final_path(filename: str) -> Path:
    return get_final_dir() / filename
