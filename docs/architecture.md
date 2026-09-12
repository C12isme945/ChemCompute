# 架构与边界

```mermaid
flowchart LR
  User[原生桌面控制台] -->|管理员认证与 ZIP| API[FastAPI Controller]
  User -->|元数据与资源摘要| AI[DeepSeek 建议节点]
  AI -->|经本地验证的节点 ID| User
  API --> DB[(SQLite 节点/邀请码/任务队列)]
  API --> ZIP[受限输入与结果文件库]
  Agent[节点代理] -->|独立心跳| API
  Worker[单任务工作线程] -->|原子领取/下载/状态/结果| API
  Agent --> Worker
  Worker --> GMX[GROMACS 独立任务目录]
  Installer[Inno Setup + 冻结 Python/Tk] --> User
```

跨机通信使用加密 Tailscale 链路或外置 HTTPS。控制端使用 SQLite，适合单进程、小规模私有部署；任务领取使用 BEGIN IMMEDIATE 事务，单节点最多一个新版运行任务。任务具有租约和持久恢复记录，失联保留所有权，原节点恢复后补传结果；无高可用和永久故障的跨机接管。新旧队列不应混用于同一节点。

Controller 与 Agent 由同一冻结可执行程序提供，当前用户后台运行，不是系统服务。新版本添加 tasks_v2/packages_v2 表，保留旧数据。运行配置与数据库在用户运行目录，安装目录仅程序。DeepSeek 与远程管理凭据用当前用户 DPAPI 加密；节点令牌与主控 secret 仍为受 ACL 保护的本地文件。科学输入、结果不加密落盘。
