# 开发者工具链

> 状态：implemented（基础链路）；真实跨平台 provider/registry 验收仍为 pending
> 适用范围：内置工具、Desktop Runner、Attempt 快照和 CLI 开发工作流

## 变更安全契约

`file_write`、`file_edit`、`file_patch` 必须携带完整 `expected_sha256`：

- 已存在文件必须传 `file_read.metadata.sha256` 返回的 64 位摘要。
- 新文件传空字符串，表示调用方明确确认目标不存在。
- 缺失、格式不匹配或文件已被外部修改时，工具直接返回 `conflict`，不得写入。
- `file_patch` 会校验每个 hunk 的上下文、old 行数和 new 行数，禁止仅按行号盲目替换。

多文件修改使用 `apply_change_set`。它会先预检全部路径和 hash，再逐文件原子替换；中途失败时恢复已写入文件的原始字节和权限。变更集最多 20 个文件，路径不可重复。

## 回退与 Git

Side-effect Mission 产生 Attempt 快照。快照记录：

- 文件内容摘要、文件类型、权限位和符号链接目标；
- 未跟踪文件和二进制文件的字节状态；
- Git index mode/object/stage；
- WorkUnit、Artifact、文件来源和恢复审计。

`/undo preview` 先执行工作区、index 和元数据冲突预检；`/undo` 只有确认且预检通过才恢复。检测到外部修改时 fail-closed，不覆盖用户内容。

文件工具不再后台自动 Git commit。显式提交必须调用 `git_commit`，相关副作用工具包括：
`git_branch_create`、`git_commit`、`git_revert`、`git_cherry_pick`。这些调用仍需经过权限策略和 Decision。

## 当前工具分组

| 分组 | 工具 |
|---|---|
| 文件 | `file_read`、`file_write`、`file_edit`、`file_patch`、`file_write_batch`、`apply_change_set`、`file_search`、`file_glob`、`mkdir` |
| 代码质量 | `code_execute`、`lint_check`、`ast_symbols`、`test_discover`、`formatter`、`type_check` |
| 依赖 | `package_manager`（install/update 默认 dry-run） |
| Git | `git_status`、`git_diff`、`git_log`、`git_branch`、`git_branch_create`、`git_commit`、`git_revert`、`git_cherry_pick` |
| 诊断 | `log_tail`、`process_list`、`port_check`、`service_health` |
| 工作流 | `change_plan`、Artifact/Memory/Skill/Browser/Web 工具 |

使用 `/tools` 查看运行时注册表，不维护第二份手工清单。

## 仍待实现

- LSP/AST 符号级安全重命名和跨文件引用更新；当前 `ast_symbols` 只读。
- 基于 Git diff 的受影响测试选择和增量测试缓存；当前 `test_discover` 只发现，不执行。
- Formatter、Type Checker 的项目配置解析和多语言统一输出。
- 事务变更后的自动 lint/test/diff 门禁与统一审计报告持久化。
- Windows/macOS/Linux 真实安装、升级、回滚和真实 provider nightly 证据。

后续实现必须先补契约测试；工具失败必须返回稳定错误分类，不得返回 synthetic success。
