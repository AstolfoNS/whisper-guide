# whisper-guide 后端

Python 3.12 + FastAPI，使用uv管理依赖。统一入口为app.main:app，保持单个Uvicorn worker。此目录是apps下的独立后端应用；前端应用位于同级frontend，部署和运维脚本位于项目根目录的deploy与scripts。

开发和模型准备见[开发指南](../../docs/development.md)，接口见[接口参考](../../docs/speech-provider-interface.md)，检查命令见[测试指南](../../docs/testing.md)。
