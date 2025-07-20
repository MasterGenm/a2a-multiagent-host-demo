# 🧠 a2a-multiagent-host-demo

本项目基于 https://github.com/a2aproject/a2a-samples  构建，实现了一个具备基本任务创建与对话交互功能的 demo。

> > ✅ **当前版本已成功实现 conversation 模块的消息交互流程，以及异步任务列表自动更新能力，支持前后端完整运行！**

---

## 📌 项目亮点

- ✅ 支持多 Agent 注册与任务协作
- ✅ 实现完整对话流：发送消息 → 等待回复 → 展示响应
- ✅ 支持异步任务后台调度，任务状态自动刷新（**Pending → Running → Success**）✅
- ✅ 使用 FastAPI + Uvicorn 构建轻量后端服务
- ✅ 前端使用现代 UI 架构（支持状态管理与消息流程可视化）
- ✅ 提供运行演示截图与状态跳动 demo（见下方）
---

## 🧪 技术栈

| 模块 | 技术 |
|------|------|
| 后端 | FastAPI, Uvicorn, asyncio |
| 协议 | A2A, gRPC |
| 前端 | React + Zustand（或其他状态管理） |
| 其他 | Google ADK, OpenAPI, nest_asyncio |

---
## 📖 权限校验与动态注册（新增）
🔑 权限校验的具体表现是什么？
权限校验是项目中的重要功能，目的是根据用户身份（如访客或管理员）限制或允许访问不同的页面。具体实现如下：

校验发生时机：当用户请求 /agents 页面时，系统通过 @register_page 装饰器调用的 wrapper 函数进行权限检查。

校验机制：通过 auth_service.check_permission('agents') 来判断用户角色是否具备访问该页面的权限。

表现方式：若权限校验失败，系统会返回“您没有权限访问此页面”的提示。

🔧 动态注册的表现
动态注册的关键在于页面的行为可以根据用户角色的变化而动态调整，避免了为每个角色编写独立页面的繁琐。具体步骤如下：

静态注册：所有页面的注册过程在程序启动时通过 @register_page 装饰器完成。

动态行为：当用户访问页面时，系统会根据角色（访客或管理员）决定是否显示对应页面内容。

此设计确保了系统的灵活性，你无需为每种角色编写一套代码，而是通过一个 动态权限系统 管理页面行为。
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
python ui/Neo_main.ipynb
```
🚀 快速启动

启动步骤

在 Cell 1 中，首先设置用户角色身份：

auth_service.set_user_role("guest")

如果要切换身份为管理员，只需改成：

auth_service.set_user_role("admin")

运行服务：重启内核，然后从上到下运行所有单元格。

在浏览器中访问：http://127.0.0.1:12000。

访客身份展示：

访问“Home”和“Conversation”链接，页面会正常加载。

访问“Agents”、“Event List”或“Settings”链接时，系统会阻止你访问并展示权限提示：“您没有权限...”。

管理员身份展示：

切换为“admin”身份，重新启动内核。

访问之前被拦截的页面，权限校验通过，页面内容成功加载。



## 📈 后续优化计划

✅ 支持异步任务列表自动更新
✅ conversation 模块添加 retry 与 loading 状态优化
✅集成完整 API 权限校验与动态注册机制
* [ ] 多 Agent 切换与上下文联动支持（正在评估 Prebuilt Agent、Build Your Own 和 Q&A Agent 三种路径的适配性，未来版本将逐步引入这些功能）
* [ ] 实现高级用户角色切换与访问控制机制
---

## 🧑‍💻 作者

Zhang Yaofeng ｜ Email: [yfzhang_finance@163.com](mailto:yfzhang_finance@163.com)
欢迎联系我交流 A2A 协议开发、AI agent 流程设计与前后端集成！

---

## 📄 License

本项目遵循 MIT 协议，详见 [LICENSE](./LICENSE) 文件。
