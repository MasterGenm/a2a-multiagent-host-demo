# 🧠 a2a-multiagent-host-demo

本项目基于 [Google A2A 协议]((https://github.com/a2aproject/a2a-samples)](https://github.com/a2aproject/a2a-samples)) 构建，实现了一个具备基本任务创建与对话交互功能的 demo。

> ✅ **当前版本已成功实现 conversation 模块的消息交互流程，支持前后端完整运行！**

---

## 📌 项目亮点

- ✅ 支持多 Agent 注册与任务协作
- ✅ 实现完整对话流：发送消息 → 等待回复 → 展示响应
- ✅ 使用 FastAPI + Uvicorn 构建轻量后端服务
- ✅ 前端使用现代 UI 架构（支持状态管理与消息流程可视化）
- ✅ 提供运行演示截图（见下方）

---

## 🧪 技术栈

| 模块 | 技术 |
|------|------|
| 后端 | FastAPI, Uvicorn, asyncio |
| 协议 | A2A, gRPC |
| 前端 | React + Zustand（或其他状态管理） |
| 其他 | Google ADK, OpenAPI 结构化通信 |

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

* [ ] 支持异步任务列表自动更新
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
