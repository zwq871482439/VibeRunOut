# Changelog

All notable changes to VibeRunOut are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-07-20

首个公开版本。本地 dashboard, 统一监控各家 AI Coding Plan (zai / MiniMax / Kimi / 自定义) 的剩余配额, 用一点少一点, 见底变红。

### Added

#### 核心
- 单文件 Python stdlib HTTP server, 零依赖 (`python server.py` 即跑)
- Widget 系统: dashboard 由 widgets 数组驱动, 在 Settings 可视化编辑
  - `summary` widget: 全局 vibe 摘要 (5h / 周 各一行, 按危险度排序的 pill)
  - `provider` widget: 单 provider 卡 (环 / 条 / 文字 三种显示样式)
  - `trend` widget: 独立趋势卡 (按维度拆多张 mini chart)
- 拖拽排序 widget, 即时落盘 (不需要点 Save)
- 启用 / 停用每个 widget

#### Provider 支持
- 4 个内置模板: Z.ai / MiniMax / Kimi / GitHub Copilot (stub)
- 自定义 provider: 通用 JSONPath 提取器 (`template: "custom"`)
- 三种认证: `raw` (zai 风格) / `bearer` (主流) / 无认证
- 每个 provider 独立调色盘选色

#### 视觉
- **4 套主题**: Glass (Apple 玻璃拟态, 旗舰) / Minimal (Linear) / Data (Grafana) / Brand (Vercel)
- **5 种强调色**: Glass (液态玻璃冷灰, 默认) / Aurora (紫绿) / Berry (紫粉) / Ocean (靛青) / Sunset (暖橙)
  - 切换强调色时玻璃背景光晕颜色跟着变
- 圆环 100→110px, 三级颜色: ≥50% 绿 / 20-50% 黄 / <20% 红
- 智能重置文案: <1 天显示 "Xh Ym 后" / 1-3 天显示 "N 天 M 小时后" / 月显示 "X 月 X 日重置"

#### 趋势卡 (重点)
- 每个 ring (5 小时 / 周 / 月) 一张独立的 mini chart
- 横轴按 ring 智能匹配时间窗:
  - 5 小时 → 1 天范围 (横轴显示 hh:mm)
  - 周 → 7 天范围 (横轴显示 MM/DD)
  - 月 → 30 天范围 (横轴显示 MM/DD)
- Y 轴统一 0-100% (剩余%)
- 每行右侧 legend 显示 provider 当前剩余%
- **断线检测**: server 离线时段 (>窗口/4 没数据) 自动断开, 不硬连成直线
- **Downsample**: 单条 ring 最多 80 个点, 配合 `tension: 0.4` 平滑
- Chart.js **本地化** (不依赖 CDN, 断网也能用)

#### 告警 & 通知
- 自定义告警规则: 阈值 / 冷却时间 / 单次触发 / 多通道 (浏览器 + 日志)
- 桌面通知 (>20% 阈值, 10 分钟冷却)
- 通知中心面板 (🔔 铃铛 → 历史 vibe 事件流)
- 全局告警条 (顶栏下方, 最危险的那条)

#### 设置面板
- 4 个 tab: Providers / Elements (Widgets) / Theme / Notification
- 4 内置模板卡片可一键启用
- 自定义 provider JSON 编辑器
- 主题 + 强调色实时切换
- dirty check (有未保存改动时关闭弹原生 confirm)

### Security
- `config.json` 永远 gitignore (不进 git)
- `logs/` 永远 gitignore
- API key 在 `GET /api/config` 时自动 mask 成 `***xxxx`
- `POST /api/config` 时检测 mask 值从磁盘回填真 key (避免误覆盖)
- Server 只绑 `127.0.0.1` (局域网访问不到)

### Known Limitations
- **无深色模式**: 各主题的深色配色需要单独打磨, 当前版本强制亮色 (后续版本恢复)
- **GitHub Copilot 是 stub**: 用户级余额需 org API, 暂未实现
- **Kimi API 无独立周窗口**: 我们把 usage 字段当作周配额处理
- **桌面通知只在浏览器开着时有效**: 关闭标签页就不弹 (无后台服务)
- **多 provider 串行拉取**: 3-5 家无感, 多了可考虑改并发
- **无 OAuth 登录流程**: Claude Code / Cursor / Codex 那种只能爬 (不在范围)