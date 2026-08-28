# Glaucous TODO

> 按开发计划表维护每日进度勾选（评委可见的开发过程素材）。

## M0 原型闭环（8/27–8/28）

### Day 1（8/27）

- [ ] 0.1 WSL2 环境检查（本轮跳过：按用户要求不做环境配置）
- [ ] 0.2 GitHub 新建仓库（本轮跳过：按用户要求不做环境配置）
- [x] 0.3 项目骨架：pyproject.toml、src/glaucous/ 包结构、pytest 配置
- [x] 0.4 LLM 客户端：OpenAI 兼容请求 + 重试退避 + 流式读取
- [x] 0.5 工具基座：Tool 协议、registry、JSON Schema 定义
- [x] 0.6 三个只读工具：read_file / list_dir / grep
- [x] 0.7 主循环 v0：请求 → tool_calls → 执行 → 回喂 → 终止
- [x] 0.8 简版 CLI：input 循环 + print 输出（无主题）

> 0.3~0.8 代码已按 SDD 流程（Plan Review 4 轮 + Code Review 3 轮）完成编码；
> 按用户要求本轮未做环境配置与运行验证，环境就绪后请自行验收 Day 1 验收标准。

### Day 2（8/28）

- [x] 0.9 bash 工具（含超时、UTF-8、kill；先全部放行）
- [x] 0.10 write_file / edit_file 工具（含唯一匹配校验）
- [x] 0.11 Plan 语义 v0：Plan 下不注册写工具（声明层隐藏）
- [x] 0.12 submit_plan + 三选一切换确认（简版交互）
- [x] 0.13 edit 前打印 diff、用户 y/n 确认（审批的雏形）
- [x] 0.14 会话 JSONL 落盘（--resume 可续）
- [ ] 0.15 端到端验证：真实小项目完整走一遍修 bug 流程（按用户要求本轮未执行）

> 0.9~0.14 代码已按 SDD 流程（Plan Review 2 轮 + Code Review 2 轮）完成编码；
> 按用户要求本轮未做运行验证，M0 验收（需求→探索→方案→授权→修改→汇报）待环境就绪后执行。
