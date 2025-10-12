# 🧠 a2a-multiagent-host-demo

本项目基于 https://github.com/a2aproject/a2a-samples  构建，集成式多智能体演示工程。

> > ✅ **当前版本已成功实现从用户请求 → 深度研究（QE）→ 套模板生成报告（RE）→ 返回 UI/接口 的闭环通路。**

---

## 📌 项目亮点

- ✅ 支持多 Agent 注册与任务协作
- ✅ 实现完整对话流：发送消息 → 等待回复 → 展示响应
- ✅ 支持异步任务后台调度，任务状态自动刷新（**Pending → Running → Success**）✅
- ✅ 使用 FastAPI + Uvicorn 构建轻量后端服务
- ✅ 前端使用现代 UI 架构（支持状态管理与消息流程可视化）
- ✅ 提供运行演示截图与状态跳动 demo（见下方）
---

## 🧪 A2A Multi-Agent Host Demo（Naga + QueryEngine + ReportEngine + Mesop）

一个集成式多智能体演示工程：

* 前端用 **Mesop** 渲染对话与任务进度
* 后端用 **FastAPI** 提供统一接口
* **Intent Parser** 解析意图 → 路由到 **QueryEngine（研究）** 与 **ReportEngine（报告）**
* 内置 **Ollama** 作为本地模型（可选）


---

## 目录

* [架构总览](#架构总览)
* [快速开始（5 分钟 Smoke Test）](#快速开始5-分钟-smoke-test)
* [环境变量与配置](#环境变量与配置)
* [如何运行](#如何运行)
* [API 说明](#api-说明)
* [输出与目录结构](#输出与目录结构)
* [模板与自动选模](#模板与自动选模)
* [常见问题与排障](#常见问题与排障)
* [最近改动（Changelog）](#最近改动changelog)
* [后续规划](#后续规划)
* [以往历史更新]

---

## 架构总览

```
Mesop UI  ──▶  FastAPI (/api/chat,/api/query,/api/report)
                │
                ├─ Intent Parser（service/utils/intent_parser.py）
                │     └─ 产出结构化意图 + QE 查询提示
                │
                ├─ QueryEngine（service/QueryEngine）
                │     └─ 网络检索/反思/草稿 & state 文件
                │
                └─ ReportEngine（service/ReportEngine）
                      └─ 读取 QE 草稿/state + 模板 → 生成 HTML 报告
```

可选：Ollama 作为本地模型兜底；论坛引导 `forum_reader` 非必须（缺失时自动降级）。

---

## 快速开始（5 分钟 Smoke Test）

> 目标：验证“研究 → 报告 → 返回可读反馈”的闭环。

1. **准备环境变量**（见下文）
2. **启动 UI/服务：**

```bash
cd demo/ui
python main.py
# 默认: http://127.0.0.1:12000
```

3. **健康检查：**

```bash
curl "http://127.0.0.1:12000/api/health"
```

4. **一键生成简短报告（强制 ReportEngine）：**

```bash
curl "http://127.0.0.1:12000/api/chat?input=报告任务：请生成一份关于金融科技技术与应用发展趋势的简短报告&profile=naga&force_report=true"
```

预期返回（示例）：

```
... "result": "……哼，别催了，按你的指示都处理好了。
报告已生成（xxxx 字节）。 报告文件：<路径> 使用模板：<模板名>
— Amadeus·牧濑红莉栖"
```

5. **联动链路（QE → RE，Combo 模式）：**

```bash
curl "http://127.0.0.1:12000/api/chat?input=先研究后报告：请总结近一年的金融科技趋势并生成报告&profile=naga&force_combo=true"
```

预期：先跑 QE 生成草稿/state，再喂给 RE 产出 HTML，返回合成的完成提示。

---

## 环境变量与配置

在 `demo/ui/.env`（或系统环境）中配置 **至少一个**可用的模型提供方与其 API Key。

**核心（Naga 总控）：**

```ini
# 任选其一或多提供方，按优先顺序自动回落
NAGA_PROVIDER=zhipu            # zhipu | dashscope | siliconflow
NAGA_BASE_URL=https://open.bigmodel.cn/api/paas/v4
NAGA_MODEL_NAME=glm-4.5
NAGA_API_KEY=你的主Key          # 或用下列各自 Provider 的 Key

# Provider 直填（任选）
ZHIPU_API_KEY=...
DASHSCOPE_API_KEY=...
SILICONFLOW_API_KEY=...
OPENAI_API_KEY=...             # 如需 OpenAI 兼容模型

TAVILY_API_KEY=...
```

# Persona（可自定义）
NAGA_PERSONA=你是《命运石之门》Amadeus·牧濑红莉栖...
```

---

## 如何运行

```bash

python main.py

```

启动日志里能看到：

* `✅ Mesop UI mounted at /`
* `Uvicorn running on http://127.0.0.1:12000`
* QueryEngine / ReportEngine 初始化成功
* （可选）Ollama 探测状态

访问 `http://127.0.0.1:12000/` 打开 Mesop UI。

---

## API 说明

### `GET /api/health`

健康状态与路径信息。

### `GET|POST /api/chat`

统一编排接口。

**入参（常用）：**

* `input`: 文本问题 / 任务描述
* `profile`: 固定 `naga`
* `force_report`: `true|false` 强制走 ReportEngine
* `force_query`: `true|false` 强制走 QueryEngine
* `force_combo`: `true|false` 先 QE 再 RE 的联动
* `persona`: 可覆盖默认 Persona（可选）

**返回字段（核心）：**

* `result`: 最终文本反馈（含“红莉栖”完成提示包装）
* `intent_plan`: 意图解析结果（结构化 JSON）
* `used_mcp`: 恒为 `false`（当前关闭 MCP）
* `error`: 出错信息（若有）

**调用示例：**

```bash
# 仅研究（QE）
curl "http://127.0.0.1:12000/api/chat?input=请列举近一周的金融科技相关新闻并给出处&force_query=true"

# 仅报告（RE）
curl "http://127.0.0.1:12000/api/chat?input=报告任务：生成关于实时支付的趋势综述&force_report=true"

# 研究 + 报告（Combo）
curl "http://127.0.0.1:12000/api/chat?input=先研究后报告：AI+金融的风险与监管趋势&force_combo=true"
```

---

## 输出与目录结构

默认输出目录（日志可见具体路径）：

```
demo/ui/reports/
  ├─ query_engine_streamlit_reports/
  │   ├─ deep_search_report_*.md      # 研究汇编
  │   ├─ draft_*.md                   # 初稿
  │   └─ state_*.json                 # 研究状态 (供 RE 消化)
  └─ final_reports/
      └─ *.html                       # ReportEngine 产出的最终报告（保存开启时）
```

---

## 模板与自动选模

ReportEngine 会读取 `service/ReportEngine/report_template/` 下的模板。
当前在主控中提供了简单的**关键词 → 模板**映射（ `_select_template_by_query` ）：

* 命中“金融科技 / fintech / 技术发展 / 趋势 / 年度 / 季度 / 研究报告”
  → `金融科技技术与应用发展.md`
* 命中“舆情” → `日常或定期舆情监测报告模板.md`
* 命中“竞争格局 / 行业动态” → `市场竞争格局舆情分析报告.md`

> **扩展方法：**
> 新增模板文件到 `service/ReportEngine/report_template/`，并在 `_select_template_by_query` 中加入关键词规则即可。

---

## 最近改动（Changelog）

* ✅ **Intent Parser**（`service/utils/intent_parser.py`）：

  * 读取 `NAGA_*` / Provider Key，OpenAI 兼容调用
  * 稳定 JSON 输出，内置时间窗口解析与兜底
  * 新增 `to_query_engine_inputs()`，与 QE 无缝对接
* ✅ **主控（test01_main.py）**：

  * 集成 IP → QE → RE 的 **Combo** 编排
  * `FASTBOOT` 后台初始化，服务即起即用
  * 报告模板 **自动选择**
  * 统一限流/退避与重试
* ✅ **Mesop UI 修复**：Text type、Border 兼容问题
* ✅ **组件瘦身**：仅注册对话页，避免多余依赖牵引

---

## 后续规划


  
* 报告导出 **PDF/Docx**

* 更细粒度的**可观测性**（链路时序、各段耗时）

*  UI 增加**报告预览/下载**入口以及优化
  
*  进一步的agent引入以及mcp搭载


-----
## 以往历史更新
-----

## 🌟 **特性：服务层重构与 Mesop 集成强化**

本次更新对项目的后端服务层进行了显著的重构和简化，并进一步深化了与 Mesop UI 框架的集成。主要变化包括：

  - **`TaskManager` 类移除**：原有的 `TaskManager` 类的逻辑已更精细地整合到 Mesop 页面的事件处理器中，实现了更紧凑和直接的状态管理。这使得任务的创建、跟踪和更新逻辑与 UI 层的交互更加流畅，减少了中间层的复杂性。

  - **Mesop UI 集成强化**：

      - **全局服务注入**：`OllamaService`、`SecurityManager` 和 `AuthService` 等核心服务现在通过 FastAPI 的 `lifespan` 钩子在应用启动时一次性初始化，并注入到 Mesop 页面的模块中。这确保了服务实例的单例性，并简化了页面组件对这些服务的访问。
      - **启动数据一次性获取**：Ollama 的连接状态和可用模型列表在应用启动时（而非每次页面加载时）一次性获取并缓存到 `STARTUP_DATA` 全局变量中。这大大减少了不必要的 API 调用，提升了应用的启动效率和用户体验。
      - **动态页面注册优化**：Mesop 页面的注册现在是完全动态的，通过遍历 `ALL_PAGES` 列表在 `lifespan` 函数中完成。这使得页面管理更加灵活，方便未来添加或修改页面。
      - **权限校验与日志记录**：页面访问权限的校验逻辑现在直接在 Mesop 页面包装器内部执行，并与 `SecurityManager` 集成，对每一次成功的或被拒绝的页面访问都进行审计日志记录，增强了系统的可追溯性和安全性。

  - **更清晰的架构分层**：通过移除 `TaskManager` 并将相关逻辑下沉到 UI 事件处理器，项目结构变得更扁平，核心服务（如 `OllamaService`）专注于其领域职责，UI 层则更直接地管理交互和状态。

  - **项目截图**：
    
   <img width="865" height="421" alt="image" src="https://github.com/user-attachments/assets/79681344-51ff-437d-bd52-c25ed81d99de" />


-----

## 💡 主要代码优化点

  - **`Cell 1: 全局导入与设置`**：`TaskManager` 已从导入列表中移除。`STARTUP_DATA` 现在用于缓存应用启动时获取的 Ollama 连接状态和模型列表，避免重复获取。

  - **`Cell 2: 核心服务定义`**：

      - **`TaskManager` 类已被完全移除**。
      - `SecurityManager` 和 `AuthService` 保持不变，继续提供集中的安全管理和权限校验。
      - `OllamaService` 继续负责与 Ollama API 的交互，提供连接检查、模型获取和流式聊天功能。

  - **`Cell 3: UI组件、页面定义与事件处理器`**：

      - `on_load_main_page` 事件处理器现在从 `STARTUP_DATA` 中获取 Ollama 连接状态和可用模型，确保数据只在应用启动时加载一次。
      - `ui_sidebar` 组件根据 `AppState` 中的 `ollama_connected` 和 `available_models` 显示连接状态和模型选择器。
      - `audit_page` 中的 `with page_scaffold("安全审计日志")` 错误已修复，确保审计日志页面能够正确渲染。
      - `ALL_PAGES` 列表定义了所有页面及其元数据，为动态注册提供了数据源。

  - **`Cell 4: 应用生命周期与启动`**：

      - **`lifespan` 函数**：这是本次更新的核心。
          - `ollama_service` 现在作为全局变量在 `lifespan` 内部初始化，并异步检查 Ollama 连接和获取模型列表，将结果存入 `STARTUP_DATA`。
          - **核心服务（`ollama_service`, `security_manager`, `auth_service`）被显式注入到 `conversation_page_module` 中**，取代了之前在其他地方的隐式引用。
          - **动态页面注册**：`ALL_PAGES` 列表中的每个页面都在 `lifespan` 中通过 `me.page` 装饰器动态注册。
          - **权限校验逻辑被集成到 `create_wrapper` 函数中**，确保权限检查在每个页面加载前执行，并记录到审计日志。
      - `start_app` 函数：负责启动 FastAPI 和 Uvicorn 服务器，并设置初始用户角色为 "admin" 以便于测试。

----------

## 📖 权限校验与动态注册

🔑 **权限校验的表现**：

权限校验是项目中的重要功能，目的是根据用户身份（如访客或管理员）限制或允许访问不同的页面。具体实现如下：

- **校验发生时机**：当用户请求 `/agents` 页面时，系统通过 `@register_page` 装饰器进行权限检查。
- **校验机制**：通过 `auth_service.check_permission('agents')` 来判断用户角色是否具备访问该页面的权限。
- **表现方式**：若权限校验失败，系统会返回“您没有权限访问此页面”的提示。

🔧  **动态注册的表现**：

动态注册确保系统根据用户角色动态调整页面显示内容，无需为每个角色编写独立页面。具体步骤如下：

- **静态注册**：页面注册通过 `@register_page` 装饰器在程序启动时完成。
- **动态行为**：根据用户角色（访客或管理员）来调整页面展示内容。

## 🖥️ 异步任务列表自动更新

用户点击“开始任务”按钮后，系统将任务注册为异步执行流程，并立刻返回任务编号，任务状态将在列表中自动刷新。

| 状态 | 含义         | 类比说明 |
|------|--------------|----------|
| `Pending` | 已接收任务，尚未开始处理 | 麦当劳：刚取到号 |
| `Running` | 任务执行中 | 后厨正在做汉堡 |
| `Success` | 成功完成，附带结果 | 打包完成，等你取走 |
| `Failed`  | 执行失败（附错误信息） | 厨房炸糊了，重新来过 |



https://github.com/user-attachments/assets/6aec0242-14ad-48b3-b921-257418aa9cc9

该机制基于 `asyncio + to_thread` 实现非阻塞任务调度，支持并发执行与前端状态轮询刷新。



## 🐢 优化前：执行任务时严重阻塞，等待时间过长

在原始版本中，用户点击按钮后，系统会**同步执行完整任务链路**，平均耗时长达 **6～10 分钟**。  
等待期间页面无响应、无反馈，用户误以为系统卡住，**严重影响体验**。


## ⚡️ 优化后：引入异步任务调度，立刻响应 + 状态自动刷新 ✅

我通过改造任务调用逻辑，使用 `asyncio` + `to_thread` 包装任务流程，并引入任务状态缓存（`TASK_STATUS` / `TASK_RESULT`），实现：

- 用户点击后**1 秒内返回 `Pending`**
- 后台任务继续执行，**不阻塞 UI**
- 状态实时轮询更新（Pending → Running → Success）
- 最终结果自动呈现，支持多任务并发展示

这项改进显著提升系统响应性和体验流畅度，**并为未来拓展 WebSocket 推送、状态订阅打下基础。**

## 🧠 本次更新复盘总结：

这次更新主要解决了页面卡顿问题，核心优化是加入异步任务调度与状态轮询。相比原同步逻辑，现在用户操作后能立刻得到反馈，显著提升了交互体验。这一思路可推广至类似阻塞问题的产品逻辑中。

---

## 📸 项目运行截图

下图展示了 conversation 模块成功响应的消息交互界面：

![图片3](https://github.com/user-attachments/assets/6ca9d4b6-de36-4bad-b18b-d7fb782427bb)

![图片4](https://github.com/user-attachments/assets/5ff6101d-c77e-4e57-81ac-48d38e9ea31a)




> 

---

## 🚀 快速启动

确保以下库已安装：

* `fastapi`
* `uvicorn`
* `httpx`
* `a2a`
* `google.adk`
* `nest_asyncio`（若在 notebook 中运行）
 
```bash
python ui/ultimate_main.ipynb
```
#### 启动步骤

1.  **部署 Ollama 模型**：
    为了体验完整的智能对话系统，您需要部署一个基于《甄嬛传》角色数据的智能对话模型。这是一个使用 LoRA 微调技术训练的甄嬛角色模型，支持多种交互方式。

    ```bash
    ollama create huanhuan -f deployment/Modelfile.huanhuan
    ```

2.  **设置用户角色身份**：在代码中，默认将用户角色设置为 "admin"。

    ```python
    auth_service.set_user_role("admin")
    ```

    如果您想切换为 "guest" 身份，只需将上述行改为：

    ```python
    auth_service.set_user_role("guest")
    ```

3.  **运行服务**：

      * **重启内核**：这是关键步骤，确保所有全局状态被重置。
      * 然后，从上到下**运行所有单元格**。

4.  **在浏览器中访问**：

    ```
    http://127.0.0.1:12000
    ```

      * **访客身份展示**：
        访问根路径 (`/`) 或 `/chat` 页面，页面会正常加载。当尝试访问 `/tasks` 或 `/audit` 链接时，系统会阻止访问并展示权限提示：“访问被拒绝。您没有权限查看 [页面名称] 页面。”

      * **管理员身份展示**：
        切换为 "admin" 身份后，**务必重新启动内核**。再次运行所有单元格。访问之前被拦截的页面（`/tasks` 和 `/audit`），权限校验将通过，页面内容成功加载。审计日志页面会显示所有的角色切换和页面访问事件。

-----



## 📈 后续优化计划

✅ 支持异步任务列表自动更新

✅ conversation 模块添加 retry 与 loading 状态优化

✅权限校验与动态注册机制

✅多 Agent 切换与上下文联动支持
      
* [ ] 思考部署qwen3模型必要性与多模态联动方向（暂定）
---

## 🧑‍💻 作者

Zhang Yaofeng ｜ Email: [yfzhang_finance@163.com](mailto:yfzhang_finance@163.com)
欢迎联系我交流 A2A 协议开发、AI agent 流程设计与前后端集成！

---

## 📄 License

本项目遵循 MIT 协议，详见 [LICENSE](./LICENSE) 文件。
