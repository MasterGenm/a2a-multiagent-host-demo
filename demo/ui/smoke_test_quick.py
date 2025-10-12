# -*- coding: utf-8 -*-
import glob, os
from pathlib import Path

# === 让 python 找到你的包（根据你的项目层级调整）===
import sys
UI_DIR = Path(__file__).resolve().parent
SRV_DIR = UI_DIR / "service"
sys.path.insert(0, str(UI_DIR.parent))   # 项目根
sys.path.insert(0, str(UI_DIR))          # ui
sys.path.insert(0, str(SRV_DIR))         # ui/service

# === 导入已存在的 Agent ===
from service.QueryEngine.agent import create_agent as create_qe
from service.ReportEngine.agent import ReportAgent
import importlib, inspect

def newest(pattern: str):
    files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    return files[0] if files else None

def main():
    # 0) 诊断：确认导入的 agent.py 是哪个
    mod = importlib.import_module("service.ReportEngine.agent")
    print("[DIAG] agent module file =>", mod.__file__)
    print("[DIAG] has generate_report_from_files =>", hasattr(ReportAgent, "generate_report_from_files"))

    # 1) 跑 QueryEngine（快模式已用环境变量控制）
    qe = create_qe()
    query = "【意图解析器指令】\n- task: research\n- primary_query: 金融科技技术应用发展\n"
    _ = qe.research(query, save_report=True)

    # 2) 找到刚刚落盘的 draft/state
    out_dir = Path(qe.config.output_dir)  # eg. ui/reports/query_engine_streamlit_reports
    draft = newest(str(out_dir / "draft_*.md"))
    state = newest(str(out_dir / "state_*.json"))
    print("draft =>", draft)
    print("state =>", state)

    # 3) 调 ReportEngine 直出 HTML
    ra = ReportAgent()
    html_path = ra.generate_report_from_files(
        # 使用“标准参数名”；函数内部也兼容 query_engine_draft/query_engine_state/save_html
        draft_path=draft,
        state_path=state,
        save_report=True
    )

    # 4) 打印结果（含兜底）
    final_path = html_path or getattr(ra, "get_last_saved_html_path", lambda: "")()
    print("\n✅ 最终 HTML：", final_path or "<EMPTY>")

if __name__ == "__main__":
    main()
