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

## 🖥️ 异步任务列表自动更新（新增）

用户点击“开始任务”按钮后，系统将任务注册为异步执行流程，并立刻返回任务编号，任务状态将在列表中自动刷新。

| 状态 | 含义         | 类比说明 |
|------|--------------|----------|
| `Pending` | 已接收任务，尚未开始处理 | 麦当劳：刚取到号 |
| `Running` | 任务执行中 | 后厨正在做汉堡 |
| `Success` | 成功完成，附带结果 | 打包完成，等你取走 |
| `Failed`  | 执行失败（附错误信息） | 厨房炸糊了，重新来过 |



https://github.com/user-attachments/assets/6aec0242-14ad-48b3-b921-257418aa9cc9

该机制基于 `asyncio + to_thread` 实现非阻塞任务调度，支持并发执行与前端状态轮询刷新。

---

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

   这次更新主要解决了页面卡顿问题，核心优化是加入异步任务调度与状态轮询。相比原同步逻辑，现在用户操作后能立刻得到反馈，显著提升了交互体验。这一思路可推广至类似阻塞问题的产品逻辑中。” 

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
python ui/main.ipynb
```



## 📈 后续优化计划

✅ 支持异步任务列表自动更新
* [ ] conversation 模块添加 retry 与 loading 状态优化
* [ ] 多 Agent 切换与上下文联动支持
* [ ] 集成完整 API 权限校验与动态注册机制

---

## 🧑‍💻 作者

Zhang Yaofeng ｜ Email: [yfzhang_finance@163.com](mailto:yfzhang_finance@163.com)
欢迎联系我交流 A2A 协议开发、AI agent 流程设计与前后端集成！

---

## 📄 License

本项目遵循 MIT 协议，详见 [LICENSE](./LICENSE) 文件。
