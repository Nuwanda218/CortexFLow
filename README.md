# CortexFlow

面向个人学习、开发与研究的多 Agent 协作工作台。

当前仓库包含完整 C-06 Core 基础设施与四级工作空间 UI Demo。UI 使用本地 Mock，尚未接入 Core；本地自动化通过不代表真实 ChatGPT 页面或真实 Agent 的完整链路已经验收。

## 开发入口

| 分支 | 用途 | 本机目录 |
| --- | --- | --- |
| `main` | 已验收的共同基线 | `D:\CortexFlow-main` |
| `dev/ui-design` | 前端页面、交互、Mock 与前端测试 | `D:\CortexFlow-ui` |
| `dev/infrastructure` | Core、Bridge、Runtime、存储与后端测试 | `D:\CortexFlow-stage-c` |

三个分支均包含完整仓库。阶段成果验证通过后合回 `main`，另一开发分支再同步 `main`；不强推共享分支。

- [当前状态](docs/NOW.md)
- [分支约定与基线来源](docs/development-baseline.md)
- [基线验证记录](docs/baseline-verification.md)
- [UI 启动与交互说明](cortexflow-ui-demo/README.md)
- [Core 阶段状态与验证边界](docs/04-architecture/implementation-status.md)

## 常用检查

在 `cortexflow-ui-demo` 运行 `npm ci`、`npm run build`、`npm test`。浏览器测试默认端口为 5176，请确认该端口没有复用其他工作树的开发服务。

在 `contextflow-research` 运行 `python -B -m pytest tests -q -p no:cacheprovider`。真实 Agent 测试默认跳过，需要显式设置 `CF_C03_REAL_AGENT_TESTS=1` 才会调用真实服务。
