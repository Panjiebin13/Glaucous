# Glaucous TODO

> 按开发计划表维护每日进度勾选（评委可见的开发过程素材）。

## M0 原型闭环（8/27–8/28）

### Day 1（8/27）

- [x] 0.1 WSL2 环境检查（Python 3.11.15 经 Miniconda 环境就绪）
- [ ] 0.2 GitHub 新建仓库（本轮跳过：仓库尚未建立，待补）
- [x] 0.3 项目骨架：pyproject.toml、src/glaucous/ 包结构、pytest 配置
- [x] 0.4 LLM 客户端：OpenAI 兼容请求 + 重试退避 + 流式读取
- [x] 0.5 工具基座：Tool 协议、registry、JSON Schema 定义
- [x] 0.6 三个只读工具：read_file / list_dir / grep
- [x] 0.7 主循环 v0：请求 → tool_calls → 执行 → 回喂 → 终止
- [x] 0.8 简版 CLI：input 循环 + print 输出（无主题）

> 0.3~0.8 代码已按 SDD 流程（Plan Review 4 轮 + Code Review 3 轮）完成编码。
> ✅ **Day 1 验收已通过**（2026-08-27）：真实 LLM 端到端测试（deepseek-v4-flash），官方验收用例「看看这个项目的结构」正确回答（9× list_dir + 3× read_file）；另验证 grep 搜索、错误路径回喂、多轮上下文记忆、优雅退出，均符合设计契约。
