# 开源组件融合

## Tenacity 9.1.2

- 来源：https://github.com/jd/tenacity
- 许可证：Apache-2.0；原许可证见 `licenses/tenacity-LICENSE.txt`，随安装器文档分发。
- 使用方式：固定版本 Python 依赖，不复制或改写上游源码。
- 用途：官方更新信息和安装包下载遇到网络传输错误时，最多尝试三次，使用指数退避与随机等待，避免同时重试。
- 边界：校验失败、来源错误、用户取消、HTTP 权限错误不重试；不对任务提交或一次性邀请码消费进行盲目重试。
- 测试：模拟首次连接失败后恢复并校验成功，以及文件篡改不重试。

项目已有 FastAPI、HTTPX、psutil、PyInstaller、Inno Setup 继续承担接口、网络、资源监测和 Windows 打包。没有引入第二套调度器或桌面框架，以保留已部署节点和数据库兼容性。
