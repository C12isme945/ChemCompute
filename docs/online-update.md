# 在线更新（v0.4.0）

从桌面右上角或“在线更新”页检查新版本。默认包含预发布，可取消。选择“下载更新”，完成后选择“安装并重启”。下载可与计算同时进行；安装前检查本机主控全部任务及待回传结果。排队、运行、恢复或无法识别的状态均拒绝安装。纯计算节点暂需确认任务完成后手动运行安装器。

这是在线下载后重启升级。独立 PowerShell 助手等待桌面退出，确认后台进程身份后停止空闲后台，再运行安装包。配置、数据库、DeepSeek 凭据及结果不随更新覆盖。更新模式不重新注册节点、不改变角色、不重复安装系统依赖。升级后重新打开桌面并启动后台。

## 完整性与任务保护

- 固定 GitHub 来源 `C12isme945/ChemCompute`，仅精确命名的 `ChemCompute-Setup.exe`。
- 严格比较版本与预发布顺序；拒绝同版本覆盖、降级。发布新版本，不覆盖旧资产。
- 要求 GitHub API 提供 SHA-256 摘要。没有摘要的版本不提供在线安装。
- HTTPS 与受限 GitHub 下载跳转；限制 256 MiB 和十分钟下载总时长。取消、大小或摘要不符时不会替换已有文件。
- 安装前再次查询发布信息、校验文件；独立助手执行前再次校验。
- SQLite 同一写事务内检查所有任务并设置维护锁，阻止新提交或领取。锁持有期间接口返回 503，完成时凭令牌释放，异常遗留锁最多一小时自动过期。
- 核对 PID、创建时间、可执行路径和命令行；另一个桌面窗口仍打开时取消安装。

## 故障与边界

“打开更新日志目录”查看 `updates/install-*/status.json`；失败信息在更新页展示。安装失败时尝试重新打开现有程序，不宣称已回滚。若程序文件无法启动，请重新运行已知可用安装包，用户数据保留。

不提供运行中代码替换、自动回滚、差分补丁或无人登录的系统服务更新。安装包未代码签名；GitHub 摘要验证下载一致性，不能防御发布账号自身被攻破。首次从没有更新入口的版本升级，需要手动运行一次新版安装器。

## 验证

`python -m pytest tests/test_online_update.py tests/test_update_maintenance.py -q`

Windows 可运行 `python scripts/smoke_update.py --baseline <已知可用0.3安装包> --installer dist/ChemCompute-Setup.exe`，隔离验证真实安装、独立助手、重启、鉴权及配置/结果保留。测试使用独立安装标识，日志保留临时目录，不修改正式运行数据。

参考：[GitHub Release API](https://docs.github.com/en/rest/releases/releases)、[Inno Setup 安装参数](https://jrsoftware.org/ishelp/topic_setupcmdline.htm)。
