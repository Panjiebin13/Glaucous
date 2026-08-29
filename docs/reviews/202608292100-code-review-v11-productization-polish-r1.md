# 代码评审报告：v1.1 前置产品化打磨（第 r1 轮）

> 评审日期：2026-08-29 21:00
> 评审对象：spec docs/designs/202608291800-plan-v11-productization-polish.md（评审通过版）；代码为提交 9142392（相对父提交 5cca288 全部改动：新增资产 2、源码文件 7、新增测试 6、打包配置）
> 模式：聚焦复审（改动范围：R1-R7 + 附加项 A/B/C/D 实现）
> 结论：**不通过**（阻塞 1 项，建议 3 项）

## 评审方式说明

除静态审读外，执行如下只读式运行验证（PowerShell，PYTHONPATH=src，未改任何源码）：

- python -m pytest tests/ -q → **112 passed, 1 skipped**（基线 67 passed 1 skipped 无回退）；
- 管道端到端冒烟（spec §10 验收）：/help、/model、/skills、/expand、/view pyproject.toml、/exit 均退出码 0；Banner 第三行呈「当前模型 deepseek-v4-flash · 模式 plan」；/model 列出 deepseek-v4-flash/deepseek-v4-pro 两档案；/skills 列 create-skill [内置|· 未加载]；/expand 空缓冲提示「暂无可展开的思考过程」；
- 核已装 rich 13.x Live.start/Live.stop 源码：均有 _started 幂等守卫（pause/resume 重复调用、恢复未激活均安全的判定依据）；
- 核 permission/approval.py gate：DANGEROUS + approve_type → 本次放行但不记录豁免（R6 安全兜底判定依据）。

## 一、阻塞问题

### B1. create-skill/SKILL.md 正文超出 spec §1.2「必须包含且仅包含」的要点范围
- **维度**：Spec 符合性（提请作者确认）
- **代码位置**：
  1. src/glaucous/assets/skills/create-skill/SKILL.md:24-26 —— 在「文件模板」要点的「要求」下新增「正文用中文书写，结构清晰、可直接执行」；
  2. src/glaucous/assets/skills/create-skill/SKILL.md:30-34 —— 整段新增「## 约束」小节（一次只创建一个技能 / 不覆盖已存在同名技能目录 / 正文不含密钥令牌）；
  3. SKILL.md:25 —— description 要点下追加「（不要写成『这是一个技能』这类无信息描述）」。
- **spec 位置**：§1.2「正文为给模型的执行指令，**必须包含且仅包含以下要点**：1~7」。
- **冲突说明**：七要点本身全部覆盖（逐条核实：ask_user 确认用途与触发场景 / 小写连字符命名 / 固定路径 .glaucous/skills/<name>/SKILL.md 且不写工作区之外 / 含 name 与 description 的 frontmatter 模板 / 写文件仅 Build 模式、Plan 须先声明 / 复读自校验 / /clear 或重启后生效），但上述新增内容超出「仅包含」边界：「一次只创建一个技能」实质改变模型行为（spec 未限制批量创建），其余条目亦为 spec 未列的规范性要求。「且仅包含」为显式内容契约，现状与之不符。
- **修复方向**（二选一，提请作者确认）：① 删除附加条目，正文对齐 spec 七要点；② 若附加约束为预期行为，在 spec §1.2 登记为要点或范围说明后维持现状。代码层不受影响，仅资产内容修订。

## 二、建议问题

### S1. budget 事件计入思考步数却在 /expand 重放中不可见，思考区显示原始事件名
- **维度**：逻辑正确性（呈现完整性）
- **代码位置**：cli.py:506-545 render_event 无 budget 分支（重放时静默无输出）；cli.py:580 _thinking_line 兜底 return event（思考区向用户显示字面 budget）。
- **spec 位置**：§3.1「/expand：打印当前缓冲（最近一轮）的全部条目（复用 render_event 逐条渲染）」；§3.4「摘要行的 N 与缓冲条目数一致」。
- **说明**：budget 属非 text 事件，被计入思考步数 N 并写入 turn_events（cli.py:903-905）；但 render_event 无 budget 分支，/expand 重放该条时无任何输出——用户看到「N 步」却见不到对应行；折叠开启时 _thinking_line 兜底 return event，动态区直接显示字面「budget」一行。spec 指定的复用机制本身满足，但呈现信息缺失/露出内部事件名。建议为 budget 增一句可读摘要（如「上下文占用 X%」）或在 _thinking_line//expand 中统一映射。

### S2. models.toml.example 较 spec §4.1 字面模板多一行注释
- **维度**：Spec 符合性（轻微）
- **代码位置**：src/glaucous/assets/models.toml.example:3「# 默认档案为文件中的第一个段；可用环境变量 GLAUCOUS_DEFAULT_MODEL 指定其他段名。」
- **spec 位置**：§4.1 模板代码块（仅含两行注释）。
- **说明**：多出的注释是对 load_registry 既有默认档案语义（取首段、可经 GLAUCOUS_DEFAULT_MODEL 覆盖，registry.py:115）的真实描述，无害且不违密钥零存储；如追求与 spec 逐字一致可删除，或将该注释吸收进 spec §4.1。

### S3. 提问卡仅 1 个选项时仍走箭头选择（提请作者确认口径）
- **维度**：Spec 符合性（轻微）
- **代码位置**：cli.py:254 if options and _arrow_mode():（options 仅 1 项也触发箭头选择）。
- **spec 位置**：§6.2 通用触发条件「选项数 ≥2；否则走现有数字输入卡」；同节提问卡条款为「options 非空（≤6）→ 箭头选择」。
- **说明**：spec 两处口径不完全一致（通用条件 ≥2 / 提问卡条款 非空），代码从提问卡条款。单选项箭头选择功能上无碍（Enter 即返回），若以通用条件为准改为 len(options) >= 2 即可；请作者确认口径。

## 三、已核实正确项

### R3 思考折叠时序契约（§3.1-§3.4，含 B3/B4/r2-B1/r2-B2/r3-B1/r4-B1/r4-B2/r5-S1 修复结论）
| 检查要点 | 证据 | 结果 |
|---|---|---|
| 思考区只收纳非 text 事件；text 增量不进区、不入缓冲 | cli.py:897-918 on_event：text 直出 render_event；非 text 才入 turn_events/思考区 | ✓ |
| text 增量在折叠开/关两模式逐字一致 | render_event text 分支 markup=False emoji=False end=空，两模式同一路径 | ✓ |
| 轮末 finally 收缩顺序：摘要行 → md 卡片 → 用量行 | cli.py:1275-1291 thinking.close → render_answer_card → _usage_line | ✓ |
| 缓冲轮末保留、轮首重置；/clear、/resume 显式重置 | finally 无清空动作；cli.py:1256 run() 前 reset_turn_buffers；commands.py:227,244 | ✓ |
| 异常路径（LLMError/通用异常/中断）收缩仍执行、卡片跳过、用量行按已收集数据打印 | turn_ok 门控只管卡片；KeyboardInterrupt/CancelledError/Exception 均经 finally（cli.py:1264-1291） | ✓ |
| GLAUCOUS_COLLAPSE=off 与管道降级：不开 Live，逐条实时打印，turn_events 仍缓冲 | _collapse_enabled 判 stdout TTY + env（cli.py:1127-1132）；thinking=None 走原路径；单测三分支覆盖 | ✓ |
| Live 启动失败降级实时打印、本轮不再尝试 | ThinkingView.start 捕获 Exception 置 _degraded（cli.py:603-613）；实现为全会话不再尝试，较 spec 下限更严，终端能力静态下安全 | ✓ |
| 四阻塞点（ask/decision/plan_decision/retry）pause/resume try/finally | cli.py:244-269、291-352、854-883、208-214；prompt_plan_decision 经 confirm 包裹 | ✓ |
| 无死锁：pause 重复调用安全、resume 未激活安全 | rich Live.start/stop 源码均有 _started 幂等守卫；ThinkingView.pause 判 active、resume 判 _live 非 None（cli.py:634-646） | ✓ |
| N 口径 = 非 text 事件 + 交互伪事件，与缓冲同口径 | add 计数 + note_step 经 live_hooks[step] 接线；伪事件同时写 turn_events（三回调内）；测试断言计数一致 | ✓ |

### R5 用量口径（§5，含 S1/S2/S6/r2-S2 修复结论）
| 检查要点 | 证据 | 结果 |
|---|---|---|
| on_usage 构造注入、发射主体为 LLMClient、switch_profile 保留 | client.py:66-74、159-164、81-92（切换不触碰 _on_usage） | ✓ |
| stream_options 携带 + 网关降级去参重试一次 | client.py:139、144-154；单测断言首次带参、重试去参、其余原样 | ✓ |
| 降级重试不破坏 429/5xx 退避链 | 仅 not _is_retryable 才走降级分支，可重试错误原样抛出回到 chat() 退避（client.py:150-154） | ✓ |
| usage 归一化：DeepSeek 字段 / OpenAI details / 缺失即 None | _normalize_usage（client.py:193-212）；usage chunk 在 choices 检查前处理；单测四场景 | ✓ |
| turn_usage 本轮累计；缓存字段 None→0 基线后累加 | cli.py:1170-1181 _accumulate_usage；单测复刻口径断言 | ✓ |
| /compact 轮间用量门控且必恢复（含压缩抛异常） | commands.py:190-196 counting_usage try/finally | ✓ |
| 无 usage → 整轮无用量行、摘要行无 token 段（§5.3 不变量） | _usage_line/_usage_token_brief 双 falsy 返回 None/空串（cli.py:126-149）；单测覆盖 | ✓ |
| 格式与命中率：<1000 原样/≥1000 一位小数 k；命中率四舍五入；缓存段按 None 省略；管道同样打印 | _fmt_tokens/_usage_line；单测逐字断言 ⏱ ↑12.3k ↓456 tokens · 缓存命中 82% | ✓ |

### R6 箭头选择契约（§6，含 B1/B2/r2-S3/r2-S4 修复结论）
| 检查要点 | 证据 | 结果 |
|---|---|---|
| 不用 prompt_toolkit Application，原始按键读取 | select_with_arrows + _default_read_key（msvcrt/termios），与事件循环无关（cli.py:389-503） | ✓ |
| 三选项对齐 ApprovalDecision.choice | 同意/同意同类型/拒绝 → approve/approve_type/reject（cli.py:318-326） | ✓ |
| 方案三选项对齐 planning.CHOICE_*，第三项文案对齐 FR-08 | 执行（逐次审批）/执行（自动批准）/继续讨论一下（cli.py:858-869） | ✓ |
| 审批取消=拒绝、理由「用户取消」 | cli.py:319-320 idx is None → reject reason=用户取消 | ✓ |
| 方案取消=选三、用户取消意图落 feedback | cli.py:865-869 idx is None → KEEP_PLANNING feedback=用户取消（PlanDecision 无 reason 字段） | ✓ |
| DANGEROUS 统一三选项，安全语义由 gate 兜底 | 箭头路径不分列；核 approval.py:114-123：DANGEROUS+approve_type 本次放行但不记录豁免，与现状一致 | ✓ |
| 非 TTY/pipe/plain 一律数字回退 | _arrow_mode 判 stdout.isatty + GLAUCOUS_INPUT!=plain（cli.py:229-232）；三卡保留原数字/文本回退，越界回喂/空回答行为不变 | ✓ |
| read_key 可注入；任何异常（含 KeyboardInterrupt）→ None | cli.py:441-448 显式 (Exception, KeyboardInterrupt)；单测伪按键序列覆盖移动/循环/取消/异常 | ✓ |
| POSIX termios raw 切换 try/finally 还原 | cli.py:485-503 finally tcsetattr(TCSADRAIN) | ✓ |
| 选项渲染经主题与 escape | question/选项均 escape（cli.py:413-419）；❯ 高亮 + 提示行齐全 | ✓ |

### R4 模型注册表模板（§4）
| 检查要点 | 证据 | 结果 |
|---|---|---|
| ensure_models_toml 在文件缺失分支之前调用 | registry.py:80-84（load_registry 缺失→先 ensure→二次检查） | ✓ |
| 已存在绝不覆盖、绝不修改 | registry.py:60-61 path.exists() 提前 return；单测逐字节比对 | ✓ |
| 任何失败静默回退既有 env 兜底，不阻断启动 | registry.py:69-70 except Exception return；二次 exists 仍缺失走 _env_fallback；单测（模板损坏→env） | ✓ |
| 密钥零存储：模板仅环境变量名、无明文 | models.toml.example 仅 api_key_env；单测断言无 api_key | ✓ |
| 生成后 load_registry 明文校验仍在、解析两档案、默认取首段 | registry.py:97-101 api_key 硬拒绝未动；单测两档案 + 默认 deepseek-v4-flash | ✓ |
| package-data 追加模板 | pyproject.toml assets/*.toml.example | ✓ |

### R2 补全与单一数据源（§2，含 S10 修复结论）
| 检查要点 | 证据 | 结果 |
|---|---|---|
| COMMAND_META 单一数据源，HELP_LINES 由其拼装 | commands.py:45-82 _build_help_lines；/view、/expand 入表 | ✓ |
| 补全器 meta 复用 commands.COMMAND_META（无循环导入） | cli.py:1065；cli→commands 既有方向 | ✓ |
| 命令段：/ 开头无空格即补全、带 meta、complete_while_typing | cli.py:1070-1074、1116 | ✓ |
| 路径段：仅 /view、目录尾缀 /、排除目录、200 上限、遍历异常静默 | cli.py:1010-1050、PATH_ARG_COMMANDS；单测覆盖过滤/嵌套/异常 | ✓ |
| 其他段不弹补全；管道/纯文本无补全器 | cli.py:1076-1077；make_prompt_session 三降级返回 None | ✓ |
| SLASH_COMMANDS/HELP_LINES/handle_command 三处一致 | /view、/expand 齐备；/view 走 cli 内联、/expand 入分派表，与既有 /exit 同模式 | ✓ |

### R7 回答卡片（§7，含 S4/S5/r3-S1 修复结论）与附加项、不回退
| 检查要点 | 证据 | 结果 |
|---|---|---|
| 卡片以 run() 返回值为源、轮末 finally 追加；空/纯空白不渲染；管道不渲染 | cli.py:1280-1287 session 门 + answer.strip()；theme.py:191-201；单测（含 None） | ✓ |
| 顺序固定：流式原文 → md 卡片 → 用量行 | cli.py:1275-1291 | ✓ |
| 附加项 A：Banner 第三行 当前模型·模式，源为 ctx.current_model，escape | cli.py:93、1218；冒烟实测 | ✓ |
| 附加项 B：拒绝理由 EOF/Ctrl+C → 理由「用户取消」继续拒绝 | cli.py:282-288 _reject_reason | ✓ |
| 附加项 C：/exit、/quit 分派分支与 _cmd_exit 已删，仅剩 repl 内联拦截；HELP 条目保留 | commands.py 分派无 /exit；cli.py:1243-1245；COMMAND_META/_COMMAND_USAGE 保留；全仓无 _cmd_exit | ✓ |
| 附加项 D：rich 上限恢复 | pyproject.toml 与 requirements.txt 均 rich>=13.7,<14 | ✓ |
| 既有不回退：管道全路径降级、sanitize_input、escape 防注入 | 冒烟管道输出完整；cli.py:152-171 原逻辑；Banner/思考区/选择器/审批卡动态值均 escape | ✓ |
| 测试声明 §十：六文件覆盖声明场景；基线不回退 | 112 passed 1 skipped；/help /model /skills /expand /view /exit 管道冒烟退出码 0 | ✓ |

## 四、复验方式

1. B1（必须）：二选一处置后复核 —— 删除 SKILL.md 附加条目（「正文用中文书写…」要求项与「## 约束」小节）使正文含且仅含 §1.2 要点 1~7；或在 spec §1.2 登记附加约束后维持现状。
2. 复跑 python -m pytest tests/ -q 保持全绿（≥112 passed，1 skipped）。
3. S1~S3 为建议级，可随批处理或登记债务，不阻塞复审。
