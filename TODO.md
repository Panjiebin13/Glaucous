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
