<h1 align="center">🎛️ VibeRunOut</h1>
<p align="center"><em>vibe 见底警告 — 看看你还能 vibe 多久</em></p>

<p align="center">
  <a href="#快速开始">快速开始</a> ·
  <a href="#支持的-provider">Providers</a> ·
  <a href="#主题--强调色">主题</a> ·
  <a href="#widget--仪表盘">Widget</a> ·
  <a href="#自定义-provider">自定义</a> ·
  <a href="#安全">安全</a>
</p>

---

<p align="center">纯 Python stdlib 写的本地 dashboard, 统一监控各家 AI Coding Plan (zai / MiniMax / Kimi / 自定义) 的剩余配额。<b>环表示剩余</b>, 用一点少一点, 见底变红。</p>

<p align="center">
  <img src="dashboard-1.png" alt="VibeRunOut dashboard screenshot" width="780">
</p>

<sub>👆 实际运行截图: 圆环倒计时 + 剩余 vibe % + 重置倒计时 + 趋势卡</sub>

---

## 快速开始

```powershell
# 1. 克隆
git clone https://github.com/zwq871482439/VibeRunOut.git
cd VibeRunOut

# 2. 启动 (零依赖, Python 3.8+ 即可)
python server.py
```

首次启动自动生成空配置, 打开 http://localhost:5000 看到引导卡片。

```powershell
# 3. 点右上角 ⚙ Settings → 启用模板 → 填 key → Save
# 或者直接编辑 config.json
```

## 支持的 Provider

| Provider | 认证 | 显示维度 | Endpoint |
|---|---|---|---|
| **Z.ai / 智谱 GLM** | API Key (raw) | 5 小时 · 周 · 月 | `api.z.ai/api/monitor/usage/quota/limit` |
| **MiniMax Coding Plan** | Bearer (国内) | 5 小时 · 周 | `www.minimaxi.com/v1/token_plan/remains` |
| **Kimi Code** | Bearer | 5 小时 · 月 | `api.kimi.com/coding/v1/usages` |
| **GitHub Copilot** | Bearer (stub) | 待接入 | 个人余额需 org API |

**为什么 Kimi 没有周?** Kimi API 只暴露 5h + 月度窗口, 没有独立周维度; 官网 "7 天用量" 是从月配额折算的, 不重复计算。

## 主题 & 强调色

**4 套主题** (Settings 里实时切换):

- 🪟 **Glass** — Apple 液态玻璃, 渐变光晕, 磨砂面板 (默认)
- 🧘 **Minimal** — Linear 极简, 细线克制, 留白多
- 📊 **Data** — Grafana 仪表盘风, 等宽字体, 数据优先
- 🚀 **Brand** — Vercel 渐变 + 立体色块, 设计感

每套主题都自带明暗双模式 (跟随系统 / 手动切换)。

**5 种强调色** (叠加在主题之上, 影响按钮 / 焦点环 / 玻璃背景光晕):

| | ID | 色调 | 适合场景 |
|---|---|---|---|
| ▢ | `glass` | 冷灰 #94a3b8 | 液态玻璃中性 (默认) |
| ✦ | `aurora` | 紫绿 #8b5cf6→#34d399 | 神秘魔幻 |
| 🍇 | `berry` | 紫粉 #7c3aed→#ec4899 | 时尚年轻 |
| 🌊 | `ocean` | 靛青 #6366f1→#06b6d4 | 冷静专业 |
| 🌅 | `sunset` | 暖橙 #f59e0b→#ec4899 | 温暖个性 |

## Widget & 仪表盘

仪表盘完全由 **widgets** 数组驱动, 在 Settings 里可视化编辑:

- **拖拽排序**: 抓卡片头拖动, 顺序立即落盘
- **启用 / 停用**: 右上角 ⊘ 一键隐藏
- **每行独立配置**: summary / provider / trend 各自的设置 (环样式、默认维度、默认 provider) 内联编辑

内置 3 种 widget 类型:
- `summary` — 全局状态: N 家同步正常 / 最危险那条
- `provider` — 单 provider 的所有维度 (环 / 条形)
- `trend` — 独立趋势卡: 多选 provider × 维度, 每个组合一条线

## 自定义 Provider

打开 **⚙ Settings** → **Add custom provider (JSON)**, 粘贴 (走 generic JSONPath 提取器):

```json
{
  "id": "my-api",
  "label": "My API",
  "color": "#10b981",
  "url": "https://api.example.com/quota",
  "auth": "bearer",
  "key": "sk-xxx",
  "enabled": true,
  "template": "custom",
  "extract": {
    "rings": [
      {
        "title": "5 小时",
        "jsonPath": "$.data.hour5.used",
        "totalJsonPath": "$.data.hour5.limit",
        "resetJsonPath": "$.data.hour5.reset_at",
        "resetUnit": "ms"
      }
    ],
    "extras": [
      { "name": "套餐", "jsonPath": "$.data.tier" }
    ]
  }
}
```

JSONPath 支持 `$.a.b.c` 点路径; `resetUnit` 可以是 `ms` / `s` / 留空 (自动判断)。

## 功能

- 🎨 **圆环倒计时**: 剩余越多环越满, `<20%` 见底变红
- 🎭 **4 主题 × 5 强调色 × 明暗**: 60 种视觉组合
- 📊 **趋势卡**: provider × 维度自由组合, Chart.js 渐变面积图
- 🔔 **桌面通知 + 中心**: 剩 <20% 弹通知; 中心看历史 vibe 事件流
- 🤚 **拖拽 widget**: 抓卡片头自由排序, 启停独立
- ⚙ **设置面板**: 4 内置模板 + 自定义 JSON, 改完热生效
- ⚡ **零依赖**: 纯 Python stdlib, 不需要 `pip install` (除 Chart.js CDN)
- 🔒 **本地优先**: 只绑 `127.0.0.1`, key 不出本机

## 安全

- `config.json` 已 gitignore, **永远不会进 git**
- dashboard 只绑 `127.0.0.1`, 局域网他人访问不到
- `GET /api/config` 时 key 自动 mask 成 `***xxxx`
- `POST /api/config` 时检测到 mask 值就 **从磁盘回填真 key** (避免误覆盖)
- 日志 `logs/` 也 gitignore, 不上传 quota 历史

## 项目结构

```
VibeRunOut/
├── server.py              # 单文件, 全部代码 (后端 + 嵌入 HTML/CSS/JS)
├── config.json            # 你的 key (gitignore)
├── CHANGELOG.md           # 版本变更
├── .gitignore             # 保护 config.json + logs/
├── logs/                  # 运行时日志 (gitignore)
│   ├── last_quota.json    # 最近一次 raw response (调试)
│   └── history.jsonl      # 趋势数据, 保留 7 天
├── dashboard-1.png        # README 截图
└── README.md              # 本文件
```

## 已知限制

- **MiniMax 实验性**: 部分账号 endpoint 可能要 cookie 而非 API key
- **趋势图要联网**: Chart.js 走 CDN, 断网时图区空白但卡片正常
- **桌面通知只在浏览器开着时有效**: 关闭标签页就不弹
- **多 provider 串行**: 3-5 家无感, 多了可考虑改并发

## 不在范围

- OAuth / Playwright 浏览器登录 (Claude Code / Cursor / Codex 那种只能爬)
- 真正的后端定时任务 + Web Push (关了浏览器也弹通知)
- Provider OAuth token 自动续期

## License

MIT — 随便用, 出问题不负责。

---

<p align="center"><sub>built with 🐍 Python stdlib, 💙 & panic about vibe quota</sub></p>