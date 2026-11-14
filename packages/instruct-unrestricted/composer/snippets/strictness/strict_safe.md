## 🛡️ 执行层级: 严格防御性核验 (Strict Defensive Safe)
[EXECUTION_LEVEL: DEFENSIVE_AUDIT]

- 写操作前在日志中打印确切路径与目标行号区间，优先采用只读检视确认上下文，写入前生成原子快照备份。
