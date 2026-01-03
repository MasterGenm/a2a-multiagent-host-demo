"""
五元组图存储模块（兼容 GRAGMemoryManager）

- 默认使用本地 JSON 文件 logs/knowledge_graph/quintuples.json 持久化
- 如果安装并配置了 Neo4j + py2neo，则会同步写入图数据库
"""

import json as _json
import logging
import sys
import os
from typing import Iterable, List, Sequence, Tuple

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# 可选依赖：Neo4j / py2neo
# ----------------------------------------------------------------------
try:
    from py2neo import Graph, Node, Relationship

    _HAVE_PY2NEO = True
except Exception as e:  # 允许在无 Neo4j 环境下运行
    Graph = None          # type: ignore
    Node = None           # type: ignore
    Relationship = None   # type: ignore
    _HAVE_PY2NEO = False
    logger.warning(
        "[GRAG] 未安装 py2neo 或导入失败，将禁用 Neo4j 图存储，仅使用本地 JSON 图: %s",
        e,
    )

# ----------------------------------------------------------------------
# 读取 Neo4j 配置（优先 system.config，其次 config.json）
# ----------------------------------------------------------------------

# 把项目根目录加入 sys.path，方便在 demo/ui 下运行 main.py 时也能导入
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

try:
    # 新版推荐：从 system.config 读取
    from system.config import config  # type: ignore

    GRAG_ENABLED = config.grag.enabled
    NEO4J_URI = config.grag.neo4j_uri
    NEO4J_USER = config.grag.neo4j_user
    NEO4J_PASSWORD = config.grag.neo4j_password
    NEO4J_DATABASE = config.grag.neo4j_database

    if _HAVE_PY2NEO and GRAG_ENABLED:
        try:
            graph = Graph(
                NEO4J_URI,
                auth=(NEO4J_USER, NEO4J_PASSWORD),
                name=NEO4J_DATABASE,
            )
        except Exception as e:
            print(f"[GRAG] Neo4j 连接失败: {e}", file=sys.stderr)
            graph = None
            GRAG_ENABLED = False
    else:
        graph = None
        if not _HAVE_PY2NEO:
            GRAG_ENABLED = False

except Exception as e:
    # 兼容旧版本：从 config.json 读取
    print(f"[GRAG] 无法从 system.config 读取 Neo4j 配置: {e}", file=sys.stderr)
    try:
        CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            _cfg = _json.load(f)
        grag_cfg = _cfg.get("grag", {})
        NEO4J_URI = grag_cfg["neo4j_uri"]
        NEO4J_USER = grag_cfg["neo4j_user"]
        NEO4J_PASSWORD = grag_cfg["neo4j_password"]
        NEO4J_DATABASE = grag_cfg["neo4j_database"]
        GRAG_ENABLED = grag_cfg.get("enabled", True)

        if _HAVE_PY2NEO and GRAG_ENABLED:
            try:
                graph = Graph(
                    NEO4J_URI,
                    auth=(NEO4J_USER, NEO4J_PASSWORD),
                    name=NEO4J_DATABASE,
                )
            except Exception as e:
                print(f"[GRAG] Neo4j 连接失败: {e}", file=sys.stderr)
                graph = None
                GRAG_ENABLED = False
        else:
            graph = None
            if not _HAVE_PY2NEO:
                GRAG_ENABLED = False
    except Exception as e2:
        print(f"[GRAG] 无法从 config.json 读取 Neo4j 配置: {e2}", file=sys.stderr)
        graph = None
        GRAG_ENABLED = False

# ----------------------------------------------------------------------
# 本地 JSON 存储
# ----------------------------------------------------------------------

# BASE_DIR = demo/ui
BASE_DIR = os.path.dirname(os.path.dirname(__file__))
QUINTUPLES_FILE = os.path.join(BASE_DIR, "logs", "knowledge_graph", "quintuples.json")


def _ensure_dir() -> None:
    """确保五元组 JSON 目录存在"""
    os.makedirs(os.path.dirname(QUINTUPLES_FILE), exist_ok=True)


def load_quintuples() -> List[Tuple[str, str, str, str, str]]:
    """从本地 JSON 读取全部五元组"""
    try:
        with open(QUINTUPLES_FILE, "r", encoding="utf-8") as f:
            data = _json.load(f)
        return [tuple(item) for item in data]
    except FileNotFoundError:
        return []
    except Exception as e:
        logger.error("[GRAG] 读取五元组文件失败: %s", e)
        return []


def save_quintuples(quintuples: Sequence[Tuple[str, str, str, str, str]]) -> None:
    """写入全部五元组到 JSON 文件"""
    _ensure_dir()
    try:
        with open(QUINTUPLES_FILE, "w", encoding="utf-8") as f:
            _json.dump(list(quintuples), f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("[GRAG] 保存五元组文件失败: %s", e)


# ----------------------------------------------------------------------
# 对外主接口：写入 & 查询
# ----------------------------------------------------------------------

def store_quintuples(new_quintuples: Iterable[Tuple[str, str, str, str, str]]) -> bool:
    """
    存储五元组到本地 JSON 和 Neo4j（如果启用），返回是否成功。

    new_quintuples: Iterable[(head, head_type, rel, tail, tail_type)]
    """
    try:
        # 先合并到本地集合，自动去重
        existing = set(load_quintuples())
        new_set = set(tuple(t) for t in new_quintuples)
        all_quintuples = existing | new_set

        # 持久化到本地 JSON
        save_quintuples(all_quintuples)

        # 如果没开 Neo4j 或连接不可用，只写文件即可
        if not (GRAG_ENABLED and graph is not None):
            logger.info(
                "[GRAG] Neo4j 未启用或连接失败，仅写入 JSON：新增 %d 条，现有总数 %d 条",
                len(new_set),
                len(all_quintuples),
            )
            return True

        # 同步写入 Neo4j
        success = True
        success_count = 0

        for head, head_type, rel, tail, tail_type in new_set:
            if not head or not tail:
                logger.warning(
                    "[GRAG] 跳过无效五元组（head 或 tail 为空）: %r",
                    (head, head_type, rel, tail, tail_type),
                )
                continue

            try:
                # 创建节点（这里用 name 作为唯一键）
                h_node = Node("Entity", name=head, entity_type=head_type)
                t_node = Node("Entity", name=tail, entity_type=tail_type)

                graph.merge(h_node, "Entity", "name")
                graph.merge(t_node, "Entity", "name")

                # 关系上也挂主体/客体类型信息
                r = Relationship(
                    h_node,
                    rel,
                    t_node,
                    head_type=head_type,
                    tail_type=tail_type,
                )
                graph.merge(r)
                success_count += 1
            except Exception as e:
                logger.error(
                    "[GRAG] 存储五元组到 Neo4j 失败: %r，错误: %s",
                    (head, head_type, rel, tail, tail_type),
                    e,
                )
                success = False

        logger.info(
            "[GRAG] 成功写入 Neo4j 五元组: %d/%d",
            success_count,
            len(new_set),
        )
        # 只要写入了一部分就算整体成功
        return success_count > 0 and success

    except Exception as e:
        logger.error("[GRAG] 存储五元组失败: %s", e)
        return False


def add_quintuples_to_graph(new_quintuples: Iterable[Tuple[str, str, str, str, str]]) -> bool:
    """
    兼容旧命名：有些旧代码可能还在调用 add_quintuples_to_graph，
    这里直接转调用 store_quintuples。
    """
    return store_quintuples(new_quintuples)


def get_all_quintuples() -> List[Tuple[str, str, str, str, str]]:
    """对外导出：获取所有已存储的五元组"""
    return load_quintuples()


# ----------------------------------------------------------------------
# 关键词查询（支持 Neo4j + 本地 JSON 回退）
# ----------------------------------------------------------------------

def _score_quintuple_by_keywords(
    quintuple: Tuple[str, str, str, str, str],
    keywords: Sequence[str],
) -> int:
    """简单打分：五元组中命中了多少个关键词"""
    text = " ".join(quintuple)
    score = 0
    for kw in keywords:
        if kw and kw in text:
            score += 1
    return score


def query_graph_by_keywords(keywords: Sequence[str]) -> List[Tuple[str, str, str, str, str]]:
    """
    使用关键词在 Neo4j 中做一个简单查询，返回匹配到的一些五元组。
    如果 Neo4j 未启用或查不到结果，则回退到本地 JSON 中进行关键词搜索。

    keywords: e.g. ["组会", "张三"]
    """
    keywords = [kw for kw in keywords if kw]
    if not keywords:
        return []

    results: List[Tuple[str, str, str, str, str]] = []

    # 1）优先用 Neo4j（如果可用）
    if graph is not None and GRAG_ENABLED:
        try:
            for kw in keywords:
                query = """
                MATCH (e1:Entity)-[r]->(e2:Entity)
                WHERE e1.name CONTAINS $kw
                   OR e2.name CONTAINS $kw
                   OR type(r) CONTAINS $kw
                   OR e1.entity_type CONTAINS $kw
                   OR e2.entity_type CONTAINS $kw
                RETURN e1.name AS head,
                       e1.entity_type AS head_type,
                       type(r) AS rel,
                       e2.name AS tail,
                       e2.entity_type AS tail_type
                LIMIT 5
                """
                data = graph.run(query, kw=kw).data()
                for record in data:
                    results.append(
                        (
                            record["head"],
                            record["head_type"],
                            record["rel"],
                            record["tail"],
                            record["tail_type"],
                        )
                    )
        except Exception as e:
            logger.error("[GRAG] 关键词查询 Neo4j 出错: %s", e)

        if results:
            # 去重
            seen = set()
            uniq: List[Tuple[str, str, str, str, str]] = []
            for q in results:
                if q not in seen:
                    seen.add(q)
                    uniq.append(q)
            return uniq

    # 2）Neo4j 不可用 / 没查到结果：回退到本地 JSON
    all_quintuples = load_quintuples()
    scored: List[Tuple[int, Tuple[str, str, str, str, str]]] = []

    for q in all_quintuples:
        score = _score_quintuple_by_keywords(q, keywords)
        if score > 0:
            scored.append((score, q))

    if not scored:
        logger.info("[GRAG] 本地 JSON 中也未命中任何关键词: %s", keywords)
        return []

    # 按命中关键词数量排序
    scored.sort(key=lambda x: x[0], reverse=True)

    TOP_K = 50
    top_quintuples = [q for _, q in scored[:TOP_K]]
    logger.info(
        "[GRAG] 从本地 JSON 中命中五元组: %d 条（总数=%d, keywords=%s）",
        len(top_quintuples),
        len(all_quintuples),
        keywords,
    )
    return top_quintuples
