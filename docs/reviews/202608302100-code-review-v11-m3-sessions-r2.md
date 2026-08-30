# 代码评审报告：V1.1-M3 会话管理 Spec（第 2 轮）

> 评审日期：2026-08-30 22:30
> 评审对象：spec docs/designs/202608302000-plan-v11-m3-sessions.md（含 §十一实现对齐注记）；代码聚焦本轮改动
> 模式：聚焦复审（改动范围：src/glaucous/sessions/index.py（on_error/_notify/_write）、src/glaucous/cli.py（1480~1493 装配与迁移计数）、src/glaucous/commands.py（_entry_file/_switch_to_session/_cmd_fork）、tests/test_sessions_commands.py、TODO.md、spec §十一）
> 结论：**不通过**（阻塞 1 项，建议 2 项）

## 〇、复审核对结论速览

| 上轮问题 | 处置声明 | 核对结果 |
|---|---|---|
| r1-B1 索引写入失败静默 | on_error 回调 + _notify + repl 装配 | 已消除（覆盖面核对 ✓，测试 ✓） |
| r1-B2 fork IO/空文件/加载失败击穿 | _cmd_fork 五处兜底 | 声明五处已消除（测试 ✓）；同函数入口第六处 IO 面残留，见本轮 B1 |
| r1-S3 迁移计数虚报 | 仅计「已迁移」前缀行 | 已消除，干净 |
| r1-S5 切换 load 无兜底 / degraded 不可切回 | try/except + _entry_file 回退 | 已消除，干净 |
| r1-S8 state 口径分叉 | 作者确认，三处统一重置 | 已统一（含失败路径语义正确），spec §4.3 修订 + §十一 4 |

## 一、阻塞问题

### B1. _cmd_fork 会话文件创建入口（project_dir/create_session_file）的 OSError 未兜底，degraded 环境下 /fork 必现击穿——r1-B2 同条款残留
- **维度**：Spec 符合性（错误处理策略）/ 逻辑正确性
- **代码位置**：src/glaucous/commands.py:729~731——

```
    from .sessions.paths import project_dir
    new_file = History.create_session_file(ctx.workspace, session_dir=project_dir(ctx.workspace))
```

  该行位于 _cmd_fork 全部 try 之外：实参 project_dir(ctx.workspace) 先求值（paths.py:47 mkdir(parents=True, exist_ok=True)），History.create_session_file 内部同面 mkdir（history.py:187），两处 OSError 均裸抛。放大链与 r1-B2 一致：cli.py:1601 await handle_command(task, ctx) 无 try/except（repl 顶层 except Exception（cli.py:1629）仅覆盖任务轮 ctx.loop.run，命令路径不在其内），异常穿透 repl → asyncio.run → 进程带 traceback 退出。
- **spec 位置**：§八「/fork 当前会话无 JSONL（未落盘）｜不会发生（create 即落盘）；**IO 失败 → 报错保持原会话**」；§八末行「~/.glaucous 无法创建（极端环境）……迁移/索引静默降级，**对话功能可用**」。
- **冲突/缺陷说明**：本轮五处兜底（读源 commands.py:733~737、空文件 738~741、meta 742~748、写新 749~753、load 770~775）均落地且测试覆盖（test_fork_io_error_keeps_session 断言通过），但 fork 流程第一步「建新会话文件」的 IO 失败未纳入同一兜底。触发面：①degraded 环境（~/.glaucous 不可写，启动已降级到 workspace 旧路径）下每次 /fork 必现，与 spec §八「该环境对话功能可用」的承诺直接矛盾；②正常运行中目录被外力移除后重建失败/磁盘满/权限收窄。只读模拟实证（patch sessions_root 指向不可创建路径后执行 /fork，未改动任何源码）：

  `CRASH-OUT-OF-REPL: NotADirectoryError -> [Errno 20] Not a directory: .../blocker/no-such/sessions/998614b00ba8`

  异常未被转成 renderer.error，进程直接退出，而非 spec 要求的「报错保持当前会话」。
- **修复方向**：731 行包 try/except OSError → ctx.renderer.error("创建分叉会话文件失败：…（保持当前会话）") + return True；或 degraded 语境下回退 workspace 旧目录生成 fork 文件。可顺带为既有 fork IO 用例增补 project_dir 失败变体。
- **附注（提请作者确认）**：此点 r1 全量评审未单列（r1-B2 代码位置仅列 719/721/727），属 r1-B2 同函数同条款的残留面而非本轮新引入。若作者认定属范围裁剪，需修订 spec §八该行并登记偿还后方可放行。

## 二、建议问题

### S1. fork 半写残留文件与「upsert 先于 load」的幽灵条目影响未闭环（容忍边界未声明）
- **维度**：逻辑正确性（健壮性）
- **代码位置**：commands.py:749~753（write_text 失败 return，无清理）；759~768（upsert）先于 770~775（History.load）。
- **说明**：write 失败时半写文件留存 project_dir（r1 修复方向已预设「可容忍」）；后续索引损坏触发 rebuild 时，entry_from_file（index.py:86~105）仅跳过损坏行，meta 行完整的半写文件会被派生为索引条目，/sessions 可见并可切入残缺会话（无崩溃、无数据损失）。load 失败时索引条目已先行 upsert，文件完整场景下可正常切回，闭环成立。综合影响可容忍，但代码无容忍边界声明，后续维护易误判为缺陷。
- **修复方向**：write 失败路径 finally unlink 半写文件（或 load 失败时 remove(new_file.stem)），至少在函数注释声明「半写残留可容忍」的理由与影响。

### S2. 迁移计数与日志文案跨模块字面耦合
- **维度**：逻辑正确性（可维护性）
- **代码位置**：cli.py:1487 sum(...) 以 line.startswith("已迁移") 计数，依赖 paths.py:93 f"已迁移：{f.name}" 的文案字面。
- **说明**：当前口径正确（成功行「已迁移：」与跳过/失败/目录不可用行的「⚠」前缀可区分），但文案调整会让计数静默失准且无测试守卫（加固用例未覆盖迁移计数）。
- **修复方向**：migrate_legacy_sessions 返回结构化结果（如 (logs, moved_count)），或以共享常量做行前缀。

## 三、通过项

| 维度 | 检查要点 | 结果 |
|------|---------|------|
| Spec 符合性 | **r1-B1 消除确认**：SessionIndex.on_error 字段（index.py:131）+ _notify（index.py:137~142，回调自身异常吞掉且不误捕 KeyboardInterrupt）+ _write OSError → 告警（index.py:326~327，文案「会话索引写入失败：…」）；repl 装配（cli.py:1483 SessionIndex(on_error=lambda msg: theme.note(f"⚠ {msg}"))）满足 §3.2/§八「renderer 告警一条，不阻断」；加固用例 test_index_write_failure_notifies（path 指向目录触发 OSError）✓ | ✓ |
| Spec 符合性 | r1-B1 告警通道覆盖面 = 单一实例全链路：cli.py:1550 session_index=session_index 挂入 ReplContext → 轮末 touch（cli.py:1638~1645）、/rename（commands.py:713）、/fork upsert（commands.py:759~768）、迁移 upsert（cli.py:1484 传同一实例 → paths.py:96）全部经同一通道，无第二实例旁路 | ✓ |
| Spec 符合性 | **r1-B2 五处兜底确认消除**：读源 OSError（commands.py:733~737）、空文件（738~741）、meta JSONDecodeError/IndexError（742~748）、写新 OSError（749~753）、History.load ValueError/OSError（770~775）——全部 renderer.error + return True，history/state/session_usage 均未触碰，「保持当前会话」语义完整；test_fork_io_error_keeps_session（session_file 指向目录触发读失败）✓；残留第六面见 B1 | ✓ |
| Spec 符合性 | **r1-S3 干净**：paths.py:93 成功行「已迁移：{name}」，跳过（90）/失败（98）/目录不可用（81）行均「⚠」起始不误匹配；cli.py:1487 仅计成功行；moved_count=0 时不打汇总行（1488），消除虚报 | ✓ |
| Spec 符合性 | **r1-S5 干净**：_switch_to_session 包 try/except (ValueError, OSError)（commands.py:683~687）→ error + 保持当前会话（state 同步不动，语义正确）；_entry_file（583~590）用户级 exists 优先 → 回退 workspace 旧路径，degraded 会话可切回；test_switch_load_failure_keeps_session（索引陈旧 + 两处路径均不存在）✓ | ✓ |
| Spec 符合性 | r1-S8 三处口径统一：_switch_to_session（commands.py:693）与 _cmd_fork（commands.py:779）均 ctx.state = SessionState()，且均在 load 成功之后——失败路径保持当前 state，与「保持当前会话」一致；/resume 四个 return 全部 SessionState()（cli.py:1200/1211/1221/1235）；spec §4.3 已同步修订 + §十一 4 注记 | ✓ |
| Spec 符合性 | §十一 四项注记与实现一致：touch auto_name 参数（index.py:202；仅 name 为空生效 222~223）、migrate 增 index 参（paths.py:65）、自动命名轮末 finally 触发（cli.py:1642 auto_name=derive_name(task)）、切换类命令 state 重置 | ✓ |
| 逻辑正确性 | 新问题检查：on_error 回调异常安全（_notify 吞 Exception，KeyboardInterrupt 等 BaseException 不误捕）；SessionEntry/project_dir 函数内延迟导入（commands.py:585/728~729）与既有 from .cli import rebuild_loop（681）同模式，无循环导入（sessions 包不反向依赖 commands/cli）；fork 各失败路径下索引与文件系统状态组合无崩溃 | ✓ |
| 逻辑正确性 | 轮末 touch 接线未受波及：ctx.session_index（带 on_error 实例）+ auto_name + token 单调不减（cli.py:1634~1645），turn_active 复位 finally 不变（1619~1620/1634） | ✓ |
| 一致性 | 债务登记：TODO.md:159~164「B1/B2 已修复、S3/S5 顺带关闭，待 r2 聚焦复审」+ S1/S2/S6/S7 四条登记（S4/S8 经 spec §十一闭环，无需登记） | ✓ |
| 一致性 | 无范围蔓延：git status 改动面（cli.py/commands.py/TODO.md 修改 + sessions 包 + 两个新测试文件）与声明一致；未实现 spec 之外功能 | ✓ |
| 逻辑正确性 | 运行验证：WSL 环境全量 pytest（tests 目录，-q）= **235 passed**（232 + 加固 3，基线 212 守恒）4.42s；加固三用例点名执行 PASSED；degraded /fork 击穿为只读模拟实证（未改动任何源码/测试） | ✓ |

## 四、复审要求

1. **B1**（必须，二选一）：
   - 修复：_cmd_fork 会话文件创建入口（project_dir / create_session_file，commands.py:731）包 OSError 兜底，报错保持当前会话（或 degraded 语境回退 workspace 旧目录）；为既有 fork IO 用例增补该入口失败变体后，进行第 3 轮快速聚焦确认（范围仅该兜底及其回归）；
   - 或：作者确认该场景属范围裁剪 → 修订 spec §八「IO 失败 → 报错保持原会话」的适用边界 + TODO.md 登记偿还，随后可放行（提请作者确认）。
2. 建议 S1/S2 不作为放行前置，可与 M3 收尾一并处理。

---

> 评审工具链备注：本轮运行验证全部为只读操作（全量 pytest、加固用例点名执行、degraded /fork 模拟脚本经 heredoc 内联执行），未修改任何源代码、测试或文档；本报告为本轮唯一落盘文件。
