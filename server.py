"""
VibeRunOut — vibe 见底警告
监控 AI Coding Plan 余量, 看看你还能 vibe 多久
- 纯 Python stdlib, 零依赖
- 配置驱动: 内置 zai / Minimax / kimi / GitHub Copilot 4 个模板
  + 支持 generic JSONPath 提取器自定义任意 provider
- 密钥放在 config.json (左上角齿轮 → Settings 模态框可改, 热生效)

启动: python server.py
然后浏览器打开 http://localhost:5000
"""

import json
import os
import re
import threading
import urllib.request
import urllib.error
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ.get("PORT", "5000"))
CONFIG_PATH = Path(__file__).parent / "config.json"
LOG_PATH = Path(__file__).parent / "logs" / "last_quota.json"
HISTORY_PATH = Path(__file__).parent / "logs" / "history.jsonl"
ALERTS_LOG_PATH = Path(__file__).parent / "logs" / "alerts.jsonl"
HISTORY_RETENTION_DAYS = 7

# ---------- 内置模板 (4 个) ----------
# normalize 模板: zai/minimax/kimi 走前端内置的 normalize() 分支 (保持原逻辑)
# copilot 走 stub 提示; custom 走 generic JSONPath 提取器
BUILTIN_TEMPLATES = [
    {
        "id": "zai",
        "label": "Z.ai / 智谱 GLM",
        "color": "#2B7FFF",
        "url": "https://api.z.ai/api/monitor/usage/quota/limit",
        "auth": "raw",
        "template": "zai",
        "description": "智谱 GLM Coding Plan 国内版 (raw key 认证)",
    },
    {
        "id": "minimax",
        "label": "MiniMax Coding Plan",
        "color": "#7C3AED",
        "url": "https://www.minimaxi.com/v1/token_plan/remains",
        "auth": "bearer",
        "template": "minimax",
        "description": "MiniMax 国内 (Bearer Subscription Key)",
    },
    {
        "id": "kimi",
        "label": "Kimi Code",
        "color": "#1F1F1F",
        "url": "https://api.kimi.com/coding/v1/usages",
        "auth": "bearer",
        "template": "kimi",
        "description": "Kimi Code (Bearer API key)",
    },
    {
        "id": "copilot",
        "label": "GitHub Copilot",
        "color": "#6e7681",
        "url": "https://api.github.com/copilot_internal/user",
        "auth": "bearer",
        "template": "copilot",
        "description": "实验性: Copilot 用户级 premium request 余额需 PAT。",
    },
]


# ---------- 配置加载 (含旧 schema 迁移 + 热生效锁) ----------
_config_lock = threading.RLock()
_config_cache = {}


def _default_config():
    """首次运行: 4 个模板默认全 disabled, key 留空"""
    return {
        "providers": [
            {**t, "key": "", "enabled": False}
            for t in BUILTIN_TEMPLATES
        ],
        "alerts": [],
        "ring_display": "ring",       # "ring" (圆环) | "bar" (进度条) | "text" (纯文字)
        "trend_mode": "chart",        # "chart" (显示趋势卡) | "hidden" (隐藏)
        "trend_default_ring": "*",    # 默认选中的维度: "*"=全部 | "5 小时" | "周" | "月"
        "trend_default_providers": "all",  # "all"=所有 | "first"=第一个
        # Widget 列表 (dashboard 渲染顺序 = 数组顺序)
        # 拖拽调整顺序, 取消勾选隐藏
        "widgets": [
            {"id": "summary",          "type": "summary",  "enabled": True},
            {"id": "provider:zai",     "type": "provider", "provider_id": "zai",     "enabled": True, "ring_display": "ring"},
            {"id": "provider:minimax", "type": "provider", "provider_id": "minimax", "enabled": True, "ring_display": "ring"},
            {"id": "provider:kimi",    "type": "provider", "provider_id": "kimi",    "enabled": True, "ring_display": "ring"},
            {"id": "trend",            "type": "trend",    "enabled": True, "trend_mode": "chart", "trend_default_ring": "*", "trend_default_providers": "all"},
        ],
    }


def _migrate_old_schema(old):
    """从 {zai: '...', minimax: '...', kimi: '...'} 迁移到新 schema"""
    if not isinstance(old, dict) or "providers" in old:
        return None
    new_providers = []
    id_to_template = {t["id"]: t for t in BUILTIN_TEMPLATES}
    for old_id, key in old.items():
        tmpl = id_to_template.get(old_id)
        if not tmpl or not key:
            continue
        new_providers.append({
            **{k: v for k, v in tmpl.items() if k != "description"},
            "key": key,
            "enabled": True,
        })
    return {"providers": new_providers} if new_providers else None


def load_config():
    if not CONFIG_PATH.exists():
        cfg = _default_config()
        try:
            CONFIG_PATH.write_text(
                json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"✓ 首次启动, 已创建空配置: {CONFIG_PATH}")
            print(f"  打开 http://localhost:{PORT} → 点右上角 ⚙ Settings → 启用模板并填 key")
        except Exception as e:
            print(f"⚠ failed to write default config: {e}")
        return cfg
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        print(f"⚠ failed to parse {CONFIG_PATH}: {e}")
        return _default_config()
    migrated = _migrate_old_schema(raw)
    if migrated is not None:
        # 写回新 schema, 保留 user 的 key
        try:
            CONFIG_PATH.write_text(
                json.dumps(migrated, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"✓ migrated old config.json → new schema")
        except Exception as e:
            print(f"⚠ failed to write migrated config: {e}")
        return migrated
    if "providers" not in raw:
        return _default_config()
    return raw


def get_config():
    with _config_lock:
        return _config_cache


def reload_config():
    """每次 GET /api/config 或 POST 之后都重读 (热生效)"""
    global _config_cache
    with _config_lock:
        _config_cache = load_config()
        return _config_cache


# 启动时初始化
_config_cache = load_config()


# ---------- HTTP / quota 拉取 ----------
def http_get(url, headers, timeout=10):
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8")


def append_history(results):
    """每次拉取 quota 后, 追加一条记录到 logs/history.jsonl (保留近 7 天)
    记录格式: {"ts": "...", "providers": [{id, label, ok, rings: [{title, percent, resetText}]}]}
    """
    try:
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        # 用前端 normalize 的简化逻辑在后端复刻一遍 (只取 rings 的 title/percent)
        snapshot = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "providers": [],
        }
        for r in results:
            if not r.get("ok"):
                continue
            pid = r["id"]
            tmpl = r.get("template", pid)
            raw = r.get("data", {})
            rings = _extract_rings_snapshot(pid, tmpl, raw, r.get("extract"))
            snapshot["providers"].append({
                "id": pid,
                "label": r.get("label", pid),
                "ok": True,
                "rings": rings,
            })
        with HISTORY_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(snapshot, ensure_ascii=False) + "\n")
        # 清理 7 天前的记录 (lazy: 只有文件超过 5MB 才清理一次)
        try:
            if HISTORY_PATH.stat().st_size > 5 * 1024 * 1024:
                _rotate_history()
        except Exception:
            pass
    except Exception as e:
        print(f"history append failed: {e}")


def _rotate_history():
    """读出全部, 滤掉过期, 写回"""
    import time
    cutoff = time.time() - HISTORY_RETENTION_DAYS * 86400
    kept = []
    with HISTORY_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                ts = rec.get("ts", "")
                t = datetime.fromisoformat(ts).timestamp()
                if t >= cutoff:
                    kept.append(line)
            except Exception:
                continue
    with HISTORY_PATH.open("w", encoding="utf-8") as f:
        for line in kept:
            f.write(line + "\n")


def _extract_rings_snapshot(pid, tmpl, raw, extract):
    """后端复刻 normalize 的 rings 提取, 只返回 [{title, percent}]
    保持轻量, 不算 resetText (历史图不需要)"""
    rings = []
    try:
        if tmpl == "zai":
            limits = (raw.get("data") or {}).get("limits") or []
            for l in limits:
                if l.get("type") == "TOKENS_LIMIT" and l.get("unit") == 3:
                    rings.append({"title": "5 小时", "percent": l.get("percentage", 0)})
                elif l.get("type") == "TOKENS_LIMIT" and l.get("unit") == 6:
                    rings.append({"title": "周", "percent": l.get("percentage", 0)})
        elif tmpl == "kimi":
            payload = raw.get("data") or raw
            for item in (payload.get("limits") or []):
                d = item.get("detail") or item
                w = item.get("window") or {}
                limit = float(d.get("limit") or 0)
                used = float(d.get("used") or 0)
                pct = round(used / limit * 100) if limit > 0 else 0
                title = "5 小时" if w.get("duration") == 300 else "用量"
                rings.append({"title": title, "percent": pct})
            if payload.get("usage", {}).get("limit"):
                u = payload["usage"]
                limit = float(u["limit"]); used = float(u.get("used") or 0)
                rings.append({"title": "月", "percent": round(used/limit*100) if limit > 0 else 0})
        elif tmpl == "minimax":
            if raw.get("base_resp", {}).get("status_code", 0) != 0:
                return []
            arr = raw.get("model_remains") or []
            if not arr:
                return []
            main = next((m for m in arr if m.get("model_name") == "general"), arr[0])
            rings.append({"title": "5 小时", "percent": 100 - (main.get("current_interval_remaining_percent") or 100)})
            rings.append({"title": "周", "percent": 100 - (main.get("current_weekly_remaining_percent") or 100)})
        elif tmpl == "custom" and extract:
            for r in (extract.get("rings") or []):
                used = float(_jp(raw, r.get("jsonPath")) or 0)
                total = float(_jp(raw, r.get("totalJsonPath")) or 0)
                pct = round(used/total*100) if total > 0 else 0
                rings.append({"title": r.get("title", "用量"), "percent": pct})
    except Exception:
        pass
    return rings


def _jp(obj, path):
    """简版 JSONPath: 只支持 $.a.b.c"""
    if not path or path == "$":
        return obj
    if not path.startswith("$"):
        return None
    cur = obj
    for p in path[1:].split("."):
        if not p:
            continue
        if cur is None:
            return None
        cur = cur.get(p) if isinstance(cur, dict) else None
    return cur


def append_alert_log(alert_id, provider_id, provider_label, ring_title, remaining_pct, channels):
    """记录一条通知触发历史到 logs/alerts.jsonl"""
    try:
        ALERTS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "alert_id": alert_id,
            "provider_id": provider_id,
            "provider_label": provider_label,
            "ring": ring_title,
            "remaining_pct": remaining_pct,
            "channels": channels,
        }
        with ALERTS_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        # lazy rotate (>1MB)
        try:
            if ALERTS_LOG_PATH.stat().st_size > 1024 * 1024:
                _rotate_alerts()
        except Exception:
            pass
    except Exception as e:
        print(f"alert log failed: {e}")


def _rotate_alerts():
    """保留最近 500 条"""
    lines = []
    with ALERTS_LOG_PATH.open("r", encoding="utf-8") as f:
        lines = [l for l in f if l.strip()]
    with ALERTS_LOG_PATH.open("w", encoding="utf-8") as f:
        for line in lines[-500:]:
            f.write(line if line.endswith("\n") else line + "\n")


def fetch_provider(p):
    """p 是从 CONFIG["providers"] 里读出来的 provider dict (已包含 key, url, auth, color)"""
    pid = p.get("id", "?")
    label = p.get("label", pid)
    color = p.get("color", "#2B7FFF")
    if not p.get("enabled", True):
        return {"id": pid, "label": label, "color": color,
                "ok": False, "error": "disabled", "template": p.get("template", "")}
    key = (p.get("key") or "").strip()
    if not key:
        return {"id": pid, "label": label, "color": color,
                "ok": False, "error": "no key configured", "template": p.get("template", "")}
    url = p.get("url", "").strip()
    if not url:
        return {"id": pid, "label": label, "color": color,
                "ok": False, "error": "no url configured", "template": p.get("template", "")}

    auth_style = p.get("auth", "bearer")
    if auth_style == "raw":
        headers = {"Authorization": key}
    else:
        headers = {"Authorization": f"Bearer {key}"}
    headers["User-Agent"] = "QuotaDashboard/1.0"

    try:
        status, body = http_get(url, headers)
        if status >= 400:
            return {"id": pid, "label": label, "color": color,
                    "ok": False, "error": f"HTTP {status}", "template": p.get("template", "")}
        data = json.loads(body)
        return {"id": pid, "label": label, "color": color, "ok": True,
                "data": data, "template": p.get("template", ""), "extract": p.get("extract")}
    except urllib.error.HTTPError as e:
        return {"id": pid, "label": label, "color": color,
                "ok": False, "error": f"HTTP {e.code}: {e.reason}", "template": p.get("template", "")}
    except Exception as e:
        return {"id": pid, "label": label, "color": color,
                "ok": False, "error": str(e), "template": p.get("template", "")}


# ---------- Frontend (HTML/JS) ----------
INDEX_HTML = r"""<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>VibeRunOut — vibe 见底警告</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Inter+Tight:wght@400;500;600;700&family=Geist:wght@400;500;600;700&family=Geist+Mono:wght@400;500;600&family=Sora:wght@400;500;600;700&family=Berkeley+Mono:wght@400;500;600&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  /* ============================================================
     主题系统 - 4 套主题 × 2 明暗 × 4 强调色
     class: html.theme-{name}.dark
     accent: html.accent-{name}
     ============================================================ */

  /* 强调色 (任何主题都可叠加) */
  html.accent-aurora { --accent: #8b5cf6; --accent-2: #34d399; --accent-fg: #ffffff; }
  html.accent-berry  { --accent: #7c3aed; --accent-2: #ec4899; --accent-fg: #ffffff; }
  html.accent-ocean  { --accent: #6366f1; --accent-2: #06b6d4; --accent-fg: #ffffff; }
  html.accent-sunset { --accent: #f59e0b; --accent-2: #ec4899; --accent-fg: #ffffff; }
  /* 没 accent 时的回退 */
  :root { --accent: #6366f1; --accent-2: #06b6d4; --accent-fg: #ffffff; }

  /* 1. GLASS 主题 (旗舰) - 玻璃拟态 + 渐变光晕 */
  html.theme-glass {
    --font-sans: "Geist", "Inter", -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif;
    --font-mono: "Geist Mono", "JetBrains Mono", "Roboto Mono", ui-monospace, monospace;
    --radius-sm: 10px;
    --radius: 14px;
    --radius-lg: 22px;
    --radius-full: 999px;
  }
  html.theme-glass {
    background:
      radial-gradient(ellipse 800px 600px at 20% 0%, rgba(139,92,246,0.18), transparent 60%),
      radial-gradient(ellipse 700px 500px at 80% 100%, rgba(52,211,153,0.15), transparent 60%),
      radial-gradient(ellipse 500px 400px at 50% 50%, rgba(236,72,153,0.10), transparent 60%),
      linear-gradient(180deg, #f8f7fc 0%, #f0eef8 100%);
    color: #1d1d2c;
    min-height: 100vh;
  }
  html.theme-glass {
    --bg: transparent;
    --card: rgba(255,255,255,0.55);
    --card-alt: rgba(255,255,255,0.35);
    --border: rgba(255,255,255,0.7);
    --border-strong: rgba(255,255,255,0.9);
    --text: #1d1d2c;
    --muted: #6b6b85;
    --bar: rgba(255,255,255,0.4);
    --success: #10b981;
    --success-bg: rgba(16,185,129,0.12);
    --warning: #f59e0b;
    --warning-bg: rgba(245,158,11,0.12);
    --danger: #ef4444;
    --danger-bg: rgba(239,68,68,0.12);
    --danger-border: rgba(239,68,68,0.3);
    --focus: var(--accent);
    --focus-ring: rgba(139,92,246,0.25);
    --toggle-off: rgba(0,0,0,0.15);
    --toggle-on: #10b981;
    --err-bg: rgba(239,68,68,0.12);
    --err-fg: #ef4444;
    --err-border: rgba(239,68,68,0.3);
    --shadow-sm: 0 4px 12px rgba(99,90,150,0.08), 0 1px 2px rgba(99,90,150,0.04);
    --shadow-md: 0 8px 24px rgba(99,90,150,0.10), 0 2px 6px rgba(99,90,150,0.06), inset 0 1px 0 rgba(255,255,255,0.6);
    --shadow-lg: 0 20px 60px rgba(99,90,150,0.15), 0 4px 12px rgba(99,90,150,0.08), inset 0 1px 0 rgba(255,255,255,0.6);
  }
  html.theme-glass.dark {
    background:
      radial-gradient(ellipse 800px 600px at 20% 0%, rgba(139,92,246,0.32), transparent 60%),
      radial-gradient(ellipse 700px 500px at 80% 100%, rgba(52,211,153,0.22), transparent 60%),
      radial-gradient(ellipse 500px 400px at 50% 50%, rgba(236,72,153,0.18), transparent 60%),
      linear-gradient(180deg, #0e0a1f 0%, #0a0817 100%);
    color: #e8e8f0;
  }
  html.theme-glass.dark {
    --bg: transparent;
    --card: rgba(20,18,38,0.55);
    --card-alt: rgba(30,25,55,0.35);
    --border: rgba(255,255,255,0.10);
    --border-strong: rgba(255,255,255,0.20);
    --text: #e8e8f0;
    --muted: #8b8ba5;
    --bar: rgba(255,255,255,0.06);
    --success: #34d399;
    --success-bg: rgba(52,211,153,0.15);
    --warning: #fbbf24;
    --warning-bg: rgba(251,191,36,0.15);
    --danger: #f87171;
    --danger-bg: rgba(248,113,113,0.15);
    --danger-border: rgba(248,113,113,0.3);
    --focus-ring: rgba(139,92,246,0.35);
    --toggle-off: rgba(255,255,255,0.15);
    --toggle-on: #34d399;
    --err-bg: rgba(248,113,113,0.15);
    --err-fg: #f87171;
    --err-border: rgba(248,113,113,0.3);
    --shadow-sm: 0 4px 12px rgba(0,0,0,0.25), 0 1px 2px rgba(0,0,0,0.15);
    --shadow-md: 0 8px 24px rgba(0,0,0,0.35), 0 2px 6px rgba(0,0,0,0.20), inset 0 1px 0 rgba(255,255,255,0.08);
    --shadow-lg: 0 20px 60px rgba(0,0,0,0.5), 0 4px 12px rgba(0,0,0,0.25), inset 0 1px 0 rgba(255,255,255,0.10);
  }
  /* 玻璃特有: backdrop-filter 模糊 + 边框高光 */
  html.theme-glass .card {
    backdrop-filter: blur(20px) saturate(180%);
    -webkit-backdrop-filter: blur(20px) saturate(180%);
    border: 1px solid var(--border);
  }
  html.theme-glass .global-alert-inner {
    backdrop-filter: blur(16px) saturate(180%);
    -webkit-backdrop-filter: blur(16px) saturate(180%);
  }
  html.theme-glass .modal {
    backdrop-filter: blur(40px) saturate(180%);
    -webkit-backdrop-filter: blur(40px) saturate(180%);
    background: rgba(255,255,255,0.7);
    border: 1px solid rgba(255,255,255,0.6);
  }
  html.theme-glass.dark .modal {
    background: rgba(20,18,38,0.7);
    border: 1px solid rgba(255,255,255,0.1);
  }
  html.theme-glass .ring-fill {
    /* 圆环用 accent 渐变, 玻璃感 */
    stroke: url(#ring-gradient-default) var(--accent, #6366f1);
  }
  html.theme-glass .ring-fill.success { stroke: var(--success); }
  html.theme-glass .ring-fill.warning { stroke: var(--warning); }
  html.theme-glass .ring-fill.danger { stroke: var(--danger); }
  html.theme-glass .button.primary {
    background: linear-gradient(135deg, var(--accent), var(--accent-2, var(--accent)));
    color: var(--accent-fg, #fff);
    border: none;
    box-shadow: 0 4px 12px rgba(139,92,246,0.25);
  }
  html.theme-glass .button.primary:hover { filter: brightness(1.08); transform: translateY(-1px); }
  html.theme-glass .ring-text .pct { font-weight: 700; }
  html.theme-glass .header h1 { letter-spacing: -0.025em; }
  html.theme-glass .card h2 { letter-spacing: -0.01em; }
  html.theme-glass .chip.active {
    background: linear-gradient(135deg, var(--accent), var(--accent-2));
    color: white;
    border-color: transparent;
  }

  /* 2. MINIMAL 主题 (Linear 极简) */
  html.theme-minimal {
    --bg: #ffffff;
    --card: #ffffff;
    --card-alt: #fafafa;
    --border: #e6e8eb;
    --border-strong: #d8dde3;
    --text: #0a0a0a;
    --muted: #6b7280;
    --bar: #f0f0f3;
    --success: #00c853;
    --success-bg: #ecfdf5;
    --warning: #ff9500;
    --warning-bg: #fffbeb;
    --danger: #ff3b30;
    --danger-bg: #fef2f2;
    --danger-border: #fecaca;
    --focus: var(--accent);
    --focus-ring: rgba(0,0,0,0.05);
    --toggle-off: #d4d4d8;
    --toggle-on: #00c853;
    --err-bg: #fef2f2;
    --err-fg: #ff3b30;
    --err-border: #fecaca;
    --font-sans: "Inter Tight", "Inter", -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif;
    --font-mono: "Berkeley Mono", "JetBrains Mono", "Roboto Mono", ui-monospace, monospace;
    --radius-sm: 6px;
    --radius: 8px;
    --radius-lg: 10px;
    --radius-full: 999px;
    --shadow-sm: 0 1px 2px rgba(0,0,0,0.04);
    --shadow-md: 0 1px 3px rgba(0,0,0,0.05);
    --shadow-lg: 0 4px 12px rgba(0,0,0,0.06);
  }
  html.theme-minimal.dark {
    --bg: #0a0a0a;
    --card: #131313;
    --card-alt: #0f0f0f;
    --border: #262626;
    --border-strong: #404040;
    --text: #fafafa;
    --muted: #888888;
    --bar: #1a1a1a;
    --success: #00e676;
    --success-bg: #022c1d;
    --warning: #ffb74d;
    --warning-bg: #2a1f08;
    --danger: #ff5252;
    --danger-bg: #2a1212;
    --danger-border: #5b1f1f;
    --focus-ring: rgba(255,255,255,0.08);
    --toggle-off: #333333;
    --toggle-on: #00e676;
    --err-bg: #2a1212;
    --err-fg: #ff5252;
    --err-border: #5b1f1f;
    --shadow-sm: 0 1px 2px rgba(0,0,0,0.3);
    --shadow-md: 0 1px 3px rgba(0,0,0,0.4);
    --shadow-lg: 0 4px 12px rgba(0,0,0,0.5);
  }
  html.theme-minimal .ring-fill { stroke: var(--accent); }
  html.theme-minimal .button.primary {
    background: var(--accent);
    color: #fff;
    border: none;
  }
  html.theme-minimal .chip.active { background: var(--accent); color: #fff; border-color: var(--accent); }
  html.theme-minimal .header h1 { letter-spacing: -0.025em; }
  html.theme-minimal .ring-fill.success { stroke: var(--success); }
  html.theme-minimal .ring-fill.warning { stroke: var(--warning); }
  html.theme-minimal .ring-fill.danger { stroke: var(--danger); }

  /* 3. DATA 主题 (Grafana 仪表盘) */
  html.theme-data {
    --bg: #0b0c0e;
    --card: #181b1f;
    --card-alt: #0b0c0e;
    --border: #2a2f38;
    --border-strong: #3a4150;
    --text: #d8d8d8;
    --muted: #6b6f78;
    --bar: #2a2f38;
    --success: #00d9a3;
    --success-bg: #052e1f;
    --warning: #f0b400;
    --warning-bg: #2a1f08;
    --danger: #ff5c5c;
    --danger-bg: #2a1212;
    --danger-border: #5b1f1f;
    --focus: var(--accent);
    --focus-ring: rgba(0,217,163,0.25);
    --toggle-off: #3a4153;
    --toggle-on: #00d9a3;
    --err-bg: #2a1212;
    --err-fg: #ff5c5c;
    --err-border: #5b1f1f;
    --font-sans: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", monospace, sans-serif;
    --font-mono: "Sora", "JetBrains Mono", "Roboto Mono", ui-monospace, monospace;
    --radius-sm: 3px;
    --radius: 4px;
    --radius-lg: 6px;
    --radius-full: 999px;
    --shadow-sm: 0 1px 2px rgba(0,0,0,0.3);
    --shadow-md: 0 2px 4px rgba(0,0,0,0.4);
    --shadow-lg: 0 4px 12px rgba(0,0,0,0.5);
  }
  html.theme-data .ring-fill { stroke: var(--accent); }
  html.theme-data .ring-fill.success { stroke: var(--success); }
  html.theme-data .ring-fill.warning { stroke: var(--warning); }
  html.theme-data .ring-fill.danger { stroke: var(--danger); }
  html.theme-data .button.primary {
    background: var(--accent);
    color: #0b0c0e;
    border: none;
  }
  html.theme-data .chip.active { background: var(--accent); color: #0b0c0e; border-color: var(--accent); }
  html.theme-data .card-header { border-bottom: 1px solid var(--border); padding-bottom: 12px; }
  html.theme-data .rings-row .ring-block { padding: 8px 0; }

  /* 4. BRAND 主题 (Vercel 营销渐变) */
  html.theme-brand {
    --bg: #fafafa;
    --card: #ffffff;
    --card-alt: #fafafa;
    --border: #eaeaea;
    --border-strong: #d4d4d4;
    --text: #000;
    --muted: #525252;
    --bar: #f0f0f0;
    --success: #10b981;
    --success-bg: #ecfdf5;
    --warning: #f59e0b;
    --warning-bg: #fffbeb;
    --danger: #ef4444;
    --danger-bg: #fef2f2;
    --danger-border: #fecaca;
    --focus: var(--accent);
    --focus-ring: rgba(124,58,237,0.2);
    --toggle-off: #d4d4d8;
    --toggle-on: #10b981;
    --err-bg: #fef2f2;
    --err-fg: #ef4444;
    --err-border: #fecaca;
    --font-sans: "Geist", "Inter", -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif;
    --font-mono: "Geist Mono", "JetBrains Mono", ui-monospace, monospace;
    --radius-sm: 12px;
    --radius: 18px;
    --radius-lg: 24px;
    --radius-full: 999px;
    --shadow-sm: 0 2px 4px rgba(0,0,0,0.03);
    --shadow-md: 0 4px 12px rgba(0,0,0,0.05);
    --shadow-lg: 0 12px 32px rgba(0,0,0,0.08);
  }
  html.theme-brand.dark {
    --bg: #0a0a0a;
    --card: #171717;
    --card-alt: #0f0f0f;
    --border: #262626;
    --border-strong: #404040;
    --text: #fafafa;
    --muted: #a3a3a3;
    --bar: #1f1f1f;
    --success: #34d399;
    --success-bg: #022c1d;
    --warning: #fbbf24;
    --warning-bg: #2a1f08;
    --danger: #f87171;
    --danger-bg: #2a1212;
    --danger-border: #5b1f1f;
    --focus-ring: rgba(124,58,237,0.3);
    --toggle-off: #333333;
    --toggle-on: #34d399;
    --err-bg: #2a1212;
    --err-fg: #f87171;
    --err-border: #5b1f1f;
    --shadow-sm: 0 2px 4px rgba(0,0,0,0.3);
    --shadow-md: 0 4px 12px rgba(0,0,0,0.4);
    --shadow-lg: 0 12px 32px rgba(0,0,0,0.5);
  }
  html.theme-brand .ring-fill { stroke: var(--accent); }
  html.theme-brand .ring-fill.success { stroke: var(--success); }
  html.theme-brand .ring-fill.warning { stroke: var(--warning); }
  html.theme-brand .ring-fill.danger { stroke: var(--danger); }
  html.theme-brand .button.primary {
    background: linear-gradient(135deg, var(--accent), var(--accent-2, var(--accent)));
    color: #fff;
    border: none;
    box-shadow: 0 4px 12px rgba(124,58,237,0.25);
  }
  html.theme-brand .chip.active {
    background: linear-gradient(135deg, var(--accent), var(--accent-2));
    color: #fff;
    border-color: transparent;
  }
  html.theme-brand .header h1 {
    background: linear-gradient(90deg, var(--accent), var(--accent-2));
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    color: transparent;
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0; padding: 0;
    background: var(--bg);
    color: var(--text);
    font-family: var(--font-sans);
    font-size: 14px;
    line-height: 1.5;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
  }
  /* 所有数字用 mono, 仪表盘精确感 */
  .num, .pct, .ring-text .pct, .ring-meta .reset, .bp-meta,
  .extras .extra-row .value, .alert-row input[type="number"] {
    font-family: var(--font-mono);
    font-variant-numeric: tabular-nums;
  }
  header {
    padding: 24px 32px 12px;
    display: flex; align-items: flex-start; justify-content: space-between;
    gap: 16px;
  }
  header h1 { margin: 0; font-size: 20px; font-weight: 700; letter-spacing: -0.01em; }
  header .meta { color: var(--muted); font-size: 12px; font-family: var(--font-mono); }
  header .actions { display: flex; gap: 8px; align-items: center; }
  header .hdr-left { display: flex; flex-direction: column; gap: 4px; }
  header .hdr-meta { display: flex; align-items: center; gap: 8px; }
  main {
    max-width: 1100px;
    margin: 0 auto;
    padding: 12px 32px 64px;
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
    gap: 16px;
  }
  .card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    padding: 20px;
    box-shadow: var(--shadow-sm);
    transition: box-shadow .2s, border-color .2s;
  }
  /* 危险卡片: 整张染色 */
  .card.danger {
    border-color: var(--danger-border);
    background: linear-gradient(180deg, var(--danger-bg) 0%, var(--card) 60%);
  }
  .card-header {
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 16px;
  }
  .card-header h2 {
    margin: 0; font-size: 16px; font-weight: 600;
    display: flex; align-items: center; gap: 8px;
    letter-spacing: -0.01em;
  }
  .card-header .dot {
    width: 10px; height: 10px; border-radius: 50%;
    background: var(--accent, #2B7FFF);
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent, #2B7FFF) 15%, transparent);
  }
  .card-header .status {
    font-size: 12px; color: var(--muted);
    display: inline-flex; align-items: center; gap: 4px;
    font-variant-numeric: tabular-nums;
  }
  .card-header .status .led {
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--success);
    box-shadow: 0 0 6px color-mix(in srgb, var(--success) 60%, transparent);
  }
  .card-header .status.warn .led {
    background: var(--warning);
    box-shadow: 0 0 6px color-mix(in srgb, var(--warning) 60%, transparent);
  }
  .card-header .status.warn { color: var(--warning); }
  .card-header .status.err .led {
    background: var(--danger);
    box-shadow: 0 0 6px color-mix(in srgb, var(--danger) 60%, transparent);
  }
  .card-header .status.err { color: var(--danger); }
  .section-title {
    display: flex; justify-content: space-between; align-items: center;
    font-weight: 500; margin-bottom: 8px;
  }
  .section-title .label { color: var(--text); }
  .section-title .pct { font-variant-numeric: tabular-nums; color: var(--text); }
  .bar {
    height: 8px; background: var(--bar); border-radius: var(--radius-full);
    overflow: hidden;
  }
  .bar > div {
    height: 100%; border-radius: var(--radius-full);
    background: var(--accent, #2B7FFF);
    transition: width .3s ease;
  }
  .bar.tall { height: 20px; }
  .section { margin-bottom: 18px; }
  .section:last-child { margin-bottom: 0; }

  .muted { color: var(--muted); }

  /* 全局告警条 (顶栏下方) */
  .global-alert {
    max-width: 1100px;
    margin: 0 auto;
    padding: 0 32px;
  }
  .global-alert-inner {
    display: flex; align-items: center; gap: 10px;
    padding: 10px 16px;
    border-radius: var(--radius);
    margin-bottom: 12px;
    font-size: 13px;
    border: 1px solid;
  }
  .global-alert-inner.danger {
    background: var(--danger-bg);
    border-color: var(--danger-border);
    color: var(--danger);
  }
  .global-alert-inner.warn {
    background: var(--warning-bg);
    border-color: color-mix(in srgb, var(--warning) 40%, transparent);
    color: var(--warning);
  }
  .global-alert-inner .ga-pct {
    font-family: var(--font-mono);
    font-weight: 600;
    font-size: 16px;
    margin-left: auto;
  }

  /* ----- Ring ----- */
  .ring-block { display: flex; align-items: center; gap: 16px; padding: 12px 0; }
  .ring-svg { flex-shrink: 0; }
  .ring-track { fill: none; stroke: var(--bar); }
  .ring-fill {
    fill: none; stroke: var(--accent, #2B7FFF);
    stroke-linecap: round;
    transform: rotate(-90deg);
    transform-origin: 50% 50%;
    transition: stroke-dashoffset .4s ease;
  }
  .ring-text {
    position: absolute; inset: 0;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    line-height: 1;
  }
  .ring-text .pct { font-size: 20px; font-weight: 600; font-variant-numeric: tabular-nums; letter-spacing: -0.02em; }
  .ring-text .label { font-size: 10px; color: var(--muted); margin-top: 2px; font-weight: 500; }
  .ring-wrapper { position: relative; }
  .ring-meta { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
  .ring-meta .title { font-size: 14px; font-weight: 600; color: var(--text); }
  .ring-meta .reset { font-size: 12px; color: var(--muted); }
  .rings-row { display: flex; gap: 12px; align-items: stretch; margin-bottom: 14px; }
  .rings-row .ring-block { flex: 1; }
  /* bar 模式: 整行变垂直堆叠, 每个 ring 占一行 */
  .rings-row.rings-display-bar { flex-direction: column; gap: 10px; }
  /* text 模式: 更紧凑, 一行多个 */
  .rings-row.rings-display-text { gap: 8px; }
  .ring-block-text {
    padding: 12px 14px;
    background: var(--card-alt);
    border-radius: var(--radius);
    border: 1px solid var(--border);
  }
  .ring-block-text .text-pct {
    font-size: 24px; font-weight: 700; line-height: 1;
    font-family: var(--font-mono);
  }
  .ring-block-text .text-title {
    font-size: 13px; font-weight: 500; margin-top: 4px;
  }
  .ring-block-text .text-reset {
    font-size: 11px; color: var(--muted); margin-top: 4px;
    font-family: var(--font-mono);
  }
  .ring-block-bar .bar-meta { width: 100%; }
  .ring-block-bar .bar-title { display: flex; align-items: baseline; gap: 8px; margin-bottom: 6px; }
  .ring-block-bar .bar-pct {
    font-size: 18px; font-weight: 700;
    font-family: var(--font-mono);
  }
  .ring-block-bar .bar-label { font-size: 13px; font-weight: 500; }
  .ring-block-bar .bar-reset {
    font-size: 11px; color: var(--muted); margin-top: 4px;
    font-family: var(--font-mono);
  }

  .extras {
    border-top: 1px dashed var(--border);
    padding-top: 10px;
    display: flex; flex-direction: column; gap: 6px;
  }
  .extras .extra-row {
    display: flex; justify-content: space-between; align-items: center;
    font-size: 12px;
  }
  .extras .extra-row .name { color: var(--muted); }
  .extras .extra-row .value { color: var(--text); font-variant-numeric: tabular-nums; }

  details.more-folder {
    border-top: 1px dashed var(--border);
    margin-top: 12px;
    padding-top: 10px;
  }
  details.more-folder > summary {
    cursor: pointer; font-weight: 500; font-size: 13px;
    color: var(--muted); user-select: none;
    list-style: none;
  }
  details.more-folder > summary::-webkit-details-marker { display: none; }
  details.more-folder > summary::before { content: "▶ "; font-size: 10px; transition: transform .2s; display: inline-block; }
  details.more-folder[open] > summary::before { content: "▼ "; }
  details.more-folder[open] > summary { color: var(--text); margin-bottom: 8px; }
  .more-content { display: flex; flex-direction: column; gap: 12px; }

  .chart-wrap {
    position: relative;
    height: 140px;
    width: 100%;
  }
  .chart-wrap.empty {
    display: flex; align-items: center; justify-content: center;
    color: var(--muted); font-size: 12px;
    height: 60px;
  }

  /* 趋势组件卡片 (独立一张大卡) */
  .summary-card { padding: 16px 20px; }
  .summary-header {
    display: flex; align-items: center; justify-content: space-between;
    gap: 12px; margin-bottom: 10px; flex-wrap: wrap;
  }
  .summary-title {
    font-size: 14px; font-weight: 600;
    display: inline-flex; align-items: center; gap: 6px;
  }
  .summary-stats { font-size: 12px; color: var(--muted); }
  .summary-body {
    display: flex; gap: 24px; flex-wrap: wrap;
    font-size: 13px;
  }
  .summary-stat { display: flex; flex-direction: column; gap: 2px; }
  .summary-stat-label { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; }
  .summary-stat-pct { font-size: 22px; font-weight: 700; font-family: var(--font-mono); letter-spacing: -0.02em; }
  .summary-stat-detail { font-size: 13px; color: var(--text); }
  .summary-stat-pills { display: flex; flex-wrap: wrap; gap: 6px; }
  .summary-pill {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 3px 8px; border: 1px solid var(--border);
    border-radius: 999px; font-size: 12px; color: var(--text);
  }
  .summary-pill .summary-pill-color {
    width: 8px; height: 8px; border-radius: 50%;
  }
  .summary-pill.danger { background: var(--danger-bg); border-color: var(--danger-border); color: var(--danger); }
  .summary-pill.warn { background: var(--warning-bg); border-color: color-mix(in srgb, var(--warning) 40%, transparent); color: var(--warning); }
  .summary-pill.ok { background: var(--success-bg); border-color: color-mix(in srgb, var(--success) 40%, transparent); color: var(--success); }

  .trend-card {
    grid-column: 1 / -1;
  }
  .trend-card .tc-header {
    display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
    margin-bottom: 12px;
  }
  .trend-card .tc-title {
    font-size: 14px; font-weight: 600;
    display: inline-flex; align-items: center; gap: 6px;
    margin-right: 8px;
  }
  .trend-card .tc-chips {
    display: flex; gap: 6px; flex-wrap: wrap; align-items: center;
  }
  .trend-card .tc-group {
    display: inline-flex; align-items: center; gap: 6px;
    padding-left: 8px; border-left: 1px solid var(--border);
  }
  .trend-card .tc-group:first-of-type { border-left: 0; padding-left: 0; }
  .trend-card .tc-group-label {
    font-size: 11px; color: var(--muted); font-weight: 500;
  }
  .chip {
    display: inline-flex; align-items: center; gap: 4px;
    padding: 3px 9px;
    border: 1px solid var(--border);
    border-radius: var(--radius-full);
    background: var(--card);
    color: var(--muted);
    font-size: 12px;
    cursor: pointer;
    transition: all .15s;
    user-select: none;
  }
  .chip:hover { border-color: var(--border-strong); }
  .chip.active {
    background: var(--focus);
    color: white;
    border-color: var(--focus);
  }
  .chip .swatch {
    width: 8px; height: 8px; border-radius: 2px;
    background: currentColor;
  }
  .trend-card .tc-canvas-wrap {
    position: relative;
    height: 280px;
  }
  .trend-card .tc-empty {
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    color: var(--muted); font-size: 13px; gap: 8px;
    height: 200px;
  }

  .err {
    background: var(--err-bg); color: var(--err-fg);
    border: 1px solid var(--err-border); border-radius: var(--radius);
    padding: 12px; font-size: 13px;
  }
  .err a { color: var(--err-fg); }
  button {
    border: 1px solid var(--border); background: var(--card);
    padding: 6px 12px; border-radius: var(--radius); cursor: pointer;
    font-size: 12px; color: var(--text);
  }
  button:hover { background: var(--bg); }
  button.primary { background: var(--focus); color: white; border-color: var(--focus); }
  button.primary:hover { filter: brightness(1.1); }
  button.danger { color: var(--err-fg); border-color: var(--err-border); }
  button.danger:hover { background: var(--err-bg); }
  footer { text-align: center; color: var(--muted); padding: 24px; font-size: 12px; }

  /* ----- Modal ----- */
  .modal-backdrop {
    position: fixed; inset: 0;
    background: rgba(0,0,0,0.4);
    display: none; align-items: center; justify-content: center;
    z-index: 100;
  }
  .modal-backdrop.open { display: flex; }
  .modal {
    background: white;
    border-radius: var(--radius-lg);
    max-width: 720px; width: 90vw;
    max-height: 85vh; overflow-y: auto;
    padding: 24px;
    box-shadow: var(--shadow-lg);
  }
  .modal h2 { margin: 0 0 16px; font-size: 18px; }
  .modal h3 { margin: 16px 0 8px; font-size: 14px; color: var(--muted); font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }
  .modal .close { float: right; cursor: pointer; font-size: 22px; line-height: 1; color: var(--muted); }
  .modal .close:hover { color: var(--text); }

  .template-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 10px;
    margin-bottom: 12px;
  }
  .template-card {
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 12px;
    display: flex; flex-direction: column; gap: 4px;
  }
  .template-card.enabled { border-color: var(--focus); background: var(--card-alt); }
  .template-card .name { font-weight: 600; font-size: 14px; }
  .template-card .desc { color: var(--muted); font-size: 12px; flex: 1; }
  .template-card .actions { margin-top: 8px; display: flex; gap: 6px; }

  .provider-card {
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 12px;
    margin-bottom: 10px;
    background: var(--card-alt);
  }
  .provider-card.disabled { opacity: 0.55; background: var(--bg); }
  .provider-card .pc-header {
    display: flex; align-items: center; gap: 8px;
    margin-bottom: 10px;
  }
  .provider-card .pc-color {
    width: 28px; height: 28px;
    border: 1px solid var(--border); border-radius: var(--radius-sm);
    padding: 0; cursor: pointer; background: none;
    flex-shrink: 0;
  }
  .provider-card .pc-color::-webkit-color-swatch-wrapper { padding: 2px; }
  .provider-card .pc-color::-webkit-color-swatch { border: none; border-radius: 4px; }
  .provider-card .pc-label {
    flex: 1;
    border: 1px solid transparent; border-radius: var(--radius-sm);
    padding: 4px 8px; font-size: 14px; font-weight: 600;
    background: white;
  }
  .provider-card .pc-label:focus { border-color: var(--border); outline: none; }
  .provider-card .pc-pid {
    font-family: ui-monospace, monospace; font-size: 11px;
    color: var(--muted); background: var(--bar);
    padding: 2px 6px; border-radius: 4px;
  }
  .provider-card .pc-delete {
    border: none; background: none; cursor: pointer;
    color: var(--muted); font-size: 18px; padding: 4px 8px;
    border-radius: var(--radius-sm);
  }
  .provider-card .pc-delete:hover { color: var(--err-fg); background: var(--err-bg); }
  .provider-card .pc-drag {
    cursor: grab; color: var(--muted); font-size: 18px;
    padding: 0 4px; user-select: none;
  }
  .provider-card.dragging { opacity: 0.4; }
  .provider-card.drag-over { border-color: var(--focus); border-style: dashed; }
  .provider-card .pc-toggle {
    position: relative; display: inline-block;
    width: 36px; height: 20px; cursor: pointer;
    flex-shrink: 0;
  }
  .provider-card .pc-toggle input { opacity: 0; width: 0; height: 0; }
  .provider-card .pc-toggle-slider {
    position: absolute; inset: 0;
    background: var(--toggle-off); border-radius: 999px;
    transition: background .2s;
  }
  .provider-card .pc-toggle-slider::before {
    content: ""; position: absolute;
    width: 16px; height: 16px; left: 2px; top: 2px;
    background: white; border-radius: 50%;
    transition: transform .2s;
    box-shadow: var(--shadow-sm);
  }
  .provider-card .pc-toggle input:checked + .pc-toggle-slider { background: var(--toggle-on); }
  .provider-card .pc-toggle input:checked + .pc-toggle-slider::before { transform: translateX(16px); }
  .provider-card .pc-body {
    display: grid;
    grid-template-columns: 60px 1fr;
    gap: 8px 10px; align-items: center;
  }
  .provider-card .pc-body label {
    font-size: 12px; color: var(--muted); text-align: right;
  }
  .provider-card .pc-body input[type="text"],
  .provider-card .pc-body input[type="password"],
  .provider-card .pc-body select {
    border: 1px solid var(--border); border-radius: var(--radius-sm);
    padding: 5px 8px; font-size: 12px; width: 100%;
    font-family: inherit;
    background: var(--card);
  }
  .provider-card .pc-body input:focus,
  .provider-card .pc-body select:focus {
    outline: none; border-color: var(--focus);
    box-shadow: 0 0 0 2px var(--focus-ring);
  }
  .provider-card .pc-body .pc-key-wrap { position: relative; }
  .provider-card .pc-body .pc-key-wrap .pc-key-toggle {
    position: absolute; right: 6px; top: 50%; transform: translateY(-50%);
    border: none; background: none; cursor: pointer;
    color: var(--muted); font-size: 11px; padding: 2px 4px;
  }
  .provider-card .pc-key-hint {
    grid-column: 2;
    font-size: 11px; color: var(--muted);
    margin-top: -4px;
  }

  textarea {
    width: 100%; min-height: 200px;
    border: 1px solid var(--border); border-radius: var(--radius);
    padding: 8px; font-family: ui-monospace, monospace; font-size: 12px;
    resize: vertical;
  }
  .custom-box {
    border: 1px dashed var(--border);
    border-radius: var(--radius);
    padding: 12px;
    margin-top: 10px;
  }
  .custom-box summary { cursor: pointer; font-weight: 500; }
  .footer-actions {
    display: flex; justify-content: flex-end; gap: 8px;
    margin-top: 20px; padding-top: 16px; border-top: 1px solid var(--border);
  }
  .modal::-webkit-scrollbar { width: 8px; }
  .modal::-webkit-scrollbar-track { background: transparent; }
  .modal::-webkit-scrollbar-thumb { background: var(--bar); border-radius: 4px; }
  .modal::-webkit-scrollbar-thumb:hover { background: var(--muted); }

  .dirty-badge {
    display: inline-block; margin-left: 8px;
    background: #f59e0b; color: white;
    font-size: 10px; padding: 2px 6px; border-radius: 4px;
    vertical-align: middle;
  }
  .pc-loading {
    display: inline-block; width: 12px; height: 12px;
    border: 2px solid var(--border); border-top-color: var(--focus);
    border-radius: 50%; animation: spin 0.8s linear infinite;
    vertical-align: middle; margin-right: 4px;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .toast {
    position: fixed; bottom: 20px; right: 20px;
    background: var(--text); color: var(--card);
    padding: 10px 16px; border-radius: var(--radius);
    font-size: 13px;
    opacity: 0; transition: opacity .2s;
    z-index: 200;
  }
  .toast.show { opacity: 1; }
  .toast.error { background: var(--err-fg); }
  .subtitle { font-size: 12px; font-weight: 400; color: var(--muted); margin-left: 8px; }
  .hdr-btn {
    font-size: 13px; padding: 6px 10px; line-height: 1;
    min-width: 34px; min-height: 30px;
    display: inline-flex; align-items: center; justify-content: center; gap: 4px;
  }
  .hdr-btn.icon-only { font-size: 15px; }
  .ic { display: inline-block; vertical-align: middle; }
  .ic-wrap { display: inline-flex; align-items: center; justify-content: center; }
  .hdr-btn .ic { stroke: currentColor; }

  /* 通知铃铛 + 角标 + 下拉面板 */
  .bell-badge {
    position: absolute; top: -4px; right: -4px;
    background: var(--err-fg); color: white;
    font-size: 10px; padding: 1px 5px; border-radius: var(--radius);
    min-width: 14px; text-align: center; line-height: 1.4;
    font-weight: 600;
  }
  #bell-btn { position: relative; }
  .bell-panel {
    position: absolute; top: 56px; right: 16px;
    width: 360px; max-height: 480px; overflow-y: auto;
    background: var(--card); border: 1px solid var(--border);
    border-radius: var(--radius-lg); padding: 12px;
    box-shadow: var(--shadow-lg);
    z-index: 90; display: none;
    font-size: 13px;
  }
  .bell-panel.open { display: block; }
  .bell-panel h3 { margin: 0 0 8px; font-size: 13px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; }
  .bell-panel .bp-item {
    padding: 8px 4px; border-bottom: 1px solid var(--border);
    display: flex; flex-direction: column; gap: 2px;
  }
  .bell-panel .bp-item:last-child { border-bottom: 0; }
  .bell-panel .bp-item .bp-title { font-weight: 500; }
  .bell-panel .bp-item .bp-meta { color: var(--muted); font-size: 11px; }
  .bell-panel .bp-empty { color: var(--muted); padding: 24px 8px; text-align: center; }
  .bell-panel .bp-grant {
    margin-bottom: 10px; padding: 8px; border-radius: var(--radius);
    background: var(--bg); text-align: center;
  }

  /* Settings tabs */
  .settings-tabs {
    display: flex; gap: 4px; margin-bottom: 16px;
    border-bottom: 1px solid var(--border);
  }
  .settings-tabs button {
    border: none; background: none; padding: 10px 14px;
    font-size: 14px; color: var(--muted); cursor: pointer;
    border-bottom: 2px solid transparent; border-radius: 0;
    font-weight: 500;
  }
  .settings-tabs button.active { color: var(--focus); border-bottom-color: var(--focus); }

  /* 主题中心 (Settings) */
  .theme-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 10px;
  }
  .theme-card {
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 14px;
    cursor: pointer;
    transition: all .15s;
    background: var(--card);
    position: relative;
    overflow: hidden;
  }
  .theme-card:hover { border-color: var(--border-strong); }
  .theme-card.active {
    border-color: var(--accent, #6366f1);
    border-width: 2px;
    padding: 13px;
  }
  .theme-card .tc-preview {
    height: 60px; border-radius: 6px; margin-bottom: 8px;
    position: relative; overflow: hidden;
    background: var(--card-alt);
  }
  .theme-card .tc-preview-ring {
    position: absolute; top: 50%; left: 50%;
    transform: translate(-50%, -50%);
    width: 36px; height: 36px;
  }
  .theme-card .tc-preview-ring svg { display: block; }
  .theme-card .tc-name { font-weight: 600; font-size: 13px; }
  .theme-card .tc-desc { font-size: 11px; color: var(--muted); margin-top: 2px; }
  .theme-card .tc-font { font-size: 10px; color: var(--muted); margin-top: 4px; opacity: 0.7; }
  .theme-card.active::before {
    content: "✓"; position: absolute; top: 6px; right: 8px;
    color: var(--accent, #6366f1); font-weight: 700; font-size: 14px;
  }
  .accent-row {
    display: flex; gap: 8px; flex-wrap: wrap;
  }
  .accent-swatch {
    width: 48px; height: 48px; border-radius: 50%;
    border: 2px solid var(--border); cursor: pointer;
    position: relative; transition: transform .15s;
  }
  .accent-swatch:hover { transform: scale(1.08); }
  .accent-swatch.active {
    border-color: var(--accent, #6366f1);
    box-shadow: 0 0 0 2px var(--card), 0 0 0 4px var(--accent, #6366f1);
  }
  .accent-swatch .as-label {
    position: absolute; bottom: -18px; left: 50%;
    transform: translateX(-50%);
    font-size: 10px; color: var(--muted);
    white-space: nowrap;
  }
  .tab-panel { display: none; }
  .tab-panel.active { display: block; }

  /* 通知规则卡片 */
  .alert-row {
    border: 1px solid var(--border); border-radius: var(--radius);
    padding: 12px; margin-bottom: 10px; background: var(--card-alt);
    display: grid; grid-template-columns: 24px 1fr auto; gap: 10px; align-items: start;
  }
  .alert-row.disabled { opacity: 0.55; }
  .alert-row .ar-body { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
  .alert-row .ar-line { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; font-size: 13px; }
  .alert-row .ar-line label { color: var(--muted); font-size: 12px; min-width: 56px; }
  .alert-row select, .alert-row input[type="number"], .alert-row input[type="text"] {
    border: 1px solid var(--border); border-radius: var(--radius-sm);
    padding: 4px 8px; font-size: 13px; background: var(--card); color: var(--text);
    font-family: inherit;
  }
  .alert-row input[type="number"] { width: 70px; }
  .alert-row .ar-channels { display: flex; gap: 8px; font-size: 12px; color: var(--muted); }
  .alert-row .ar-channels label { display: inline-flex; gap: 4px; align-items: center; min-width: 0; }
  .alert-row .ar-actions { display: flex; flex-direction: column; gap: 6px; align-items: end; }
  .alert-row .ar-actions button { padding: 4px 8px; font-size: 11px; }
  .alert-add-btn {
    margin-top: 8px;
  }

  /* 组件 (Widgets) 列表 */
  .widget-row {
    display: flex; align-items: center; gap: 8px;
    padding: 8px 10px;
    border: 1px solid var(--border); border-radius: var(--radius-sm);
    background: var(--card);
    margin-bottom: 6px;
  }
  .widget-row .widget-drag { color: var(--muted); cursor: grab; display: inline-flex; }
  .widget-row .widget-drag:active { cursor: grabbing; }
  .widget-row .widget-name { flex: 1; font-size: 13px; }
  .widget-row .widget-type { font-size: 11px; color: var(--muted); font-family: var(--font-mono); }
  .widget-row.dragging { opacity: 0.4; }
  .widget-row.drag-over { border-color: var(--focus); border-style: dashed; }
  .widget-row.disabled { opacity: 0.5; }
  .widget-settings {
    display: flex; gap: 12px; flex-wrap: wrap;
    padding: 6px 8px 6px 32px;
    margin: 2px 0 8px 0;
    font-size: 12px; color: var(--muted);
  }
  .widget-settings label { display: inline-flex; align-items: center; gap: 4px; }
  .widget-settings select {
    border: 1px solid var(--border); border-radius: var(--radius-sm);
    padding: 3px 6px; background: var(--card); color: var(--text);
    font-size: 12px; font-family: inherit;
  }

  /* 显示选项 (Settings → Providers 顶部) */
  .display-row {
    display: flex; gap: 12px; margin-bottom: 18px; flex-wrap: wrap;
  }
  .display-control {
    display: flex; flex-direction: column; gap: 4px; min-width: 180px;
  }
  .display-control label {
    font-size: 11px; color: var(--muted); font-weight: 500;
    text-transform: uppercase; letter-spacing: 0.05em;
  }
  .display-control select {
    border: 1px solid var(--border); border-radius: var(--radius);
    padding: 6px 10px; background: var(--card); color: var(--text);
    font-size: 13px; font-family: inherit; cursor: pointer;
  }
</style>
</head>
<body>
<!-- 内联 SVG 图标库 (线性风格, stroke=currentColor 跟随主题) -->
<svg width="0" height="0" style="position:absolute" aria-hidden="true">
  <defs>
    <symbol id="i-moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></symbol>
    <symbol id="i-sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/></symbol>
    <symbol id="i-bell" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></symbol>
    <symbol id="i-settings" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></symbol>
    <symbol id="i-refresh" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M23 4v6h-6M1 20v-6h6"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></symbol>
    <symbol id="i-chart" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="M7 14l4-4 4 4 5-5"/></symbol>
    <symbol id="i-clock" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></symbol>
    <symbol id="i-drag" viewBox="0 0 24 24" fill="currentColor"><circle cx="9" cy="6" r="1.5"/><circle cx="15" cy="6" r="1.5"/><circle cx="9" cy="12" r="1.5"/><circle cx="15" cy="12" r="1.5"/><circle cx="9" cy="18" r="1.5"/><circle cx="15" cy="18" r="1.5"/></symbol>
    <symbol id="i-eye" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></symbol>
    <symbol id="i-plus" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14M5 12h14"/></symbol>
    <symbol id="i-trash" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></symbol>
    <symbol id="i-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18l6-6-6-6"/></symbol>
  </defs>
</svg>
<header>
  <div class="hdr-left">
    <h1>VibeRunOut <span class="subtitle">vibe 见底警告</span></h1>
    <div class="hdr-meta"><span class="meta" id="updated">--</span></div>
  </div>
  <div class="actions">
    <button class="hdr-btn icon-only" onclick="toggleTheme()" id="theme-btn" title="切换主题"></button>
    <button class="hdr-btn icon-only" onclick="toggleBellPanel()" id="bell-btn" title="通知中心"><span class="bell-badge" id="bell-badge" style="display:none">0</span></button>
    <button class="hdr-btn icon-only" onclick="openSettings()" title="设置"></button>
    <button class="hdr-btn" id="refresh-btn" onclick="load()"></button>
  </div>
</header>
<div class="global-alert" id="global-alert" style="display:none"></div>
<main id="main"></main>
<footer>auto refresh every 60s · keys never leave the server</footer>

<!-- Settings Modal -->
<div class="modal-backdrop" id="modal" onclick="if(event.target===this)closeSettings()">
  <div class="modal">
    <span class="close" onclick="closeSettings()">&times;</span>
    <h2>Settings</h2>

    <div class="settings-tabs">
      <button class="active" id="tab-providers-btn" onclick="switchTab('providers')">Providers</button>
      <button id="tab-elements-btn" onclick="switchTab('elements')"><span id="tab-elements-icon"></span> 元素</button>
      <button id="tab-theme-btn" onclick="switchTab('theme')"><span id="tab-theme-icon"></span> 主题</button>
      <button id="tab-alerts-btn" onclick="switchTab('alerts')"><span id="tab-alerts-icon"></span> 通知中心</button>
    </div>

    <div class="tab-panel active" id="tab-providers">
      <h3>内置模板</h3>
      <div class="template-grid" id="template-grid"></div>

      <h3>已启用 providers</h3>
      <div id="provider-list"></div>

      <details class="custom-box">
        <summary>+ Add custom provider (JSON)</summary>
        <p style="color:var(--muted);font-size:12px;margin:8px 0">
          粘贴一份 JSON 描述; <code>template: "custom"</code> 用 JSONPath 自动提取用量。
          支持 <code>$.a.b.c</code> 这种点路径。
        </p>
        <textarea id="custom-json" placeholder='{
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
      { "title": "5 小时", "jsonPath": "$.data.hour5.used", "totalJsonPath": "$.data.hour5.limit" },
      { "title": "周",     "jsonPath": "$.data.week.used",  "totalJsonPath": "$.data.week.limit",
        "resetJsonPath": "$.data.week.reset_at", "resetUnit": "s" }
    ],
    "extras": [
      { "name": "套餐", "jsonPath": "$.data.tier" }
    ]
  }
}'></textarea>
        <button onclick="addCustom()">Parse & add</button>
      </details>
    </div>

    <div class="tab-panel" id="tab-elements">
      <p style="color:var(--muted);font-size:12px;margin:0 0 16px">
        控制 dashboard 上每种元素的展示方式。改动立即生效 (不刷新)。
      </p>

      <h3>组件 (Widgets)</h3>
      <p style="color:var(--muted);font-size:11px;margin:0 0 8px">dashboard 上显示哪些卡 + 顺序。拖拽手柄调整顺序, 取消勾选隐藏。</p>
      <div id="widgets-list"></div>
      <p style="color:var(--muted);font-size:11px;margin:14px 0 0;padding-top:12px;border-top:1px dashed var(--border)">
        每行 widget 可单独配显示样式 (圆环/进度条/文字), 趋势 widget 还可设默认维度和 provider。
      </p>
    </div>

    <div class="tab-panel" id="tab-theme">
      <p style="color:var(--muted);font-size:12px;margin:0 0 16px">
        选一个主题风格 + 强调色。主题和字体配方绑定; 强调色 (按钮/链接/环填充) 独立。
        选完点 Save 即时生效。
      </p>

      <h3>主题风格</h3>
      <div class="theme-grid" id="theme-grid"></div>

      <h3 style="margin-top:20px">强调色</h3>
      <p style="color:var(--muted);font-size:11px;margin:0 0 8px">影响按钮、链接、卡片高亮等可强调元素。状态色 (绿/黄/红) 保持不变。</p>
      <div class="accent-row" id="accent-row"></div>
    </div>

    <div class="tab-panel" id="tab-alerts">
      <p style="color:var(--muted);font-size:12px;margin:0 0 12px">
        阈值是<b>剩余 %</b>, 跟圆环方向一致。例: <code>剩 ≤ 20%</code> 表示剩余降到 20% 及以下时触发。
        <code>*</code> 表示匹配所有 provider / 所有维度。<code>一次性</code> 触发后会自动禁用。
      </p>
      <div id="alert-list"></div>
      <button class="alert-add-btn" onclick="addAlert()" id="alert-add-btn"></button>
    </div>

    <div class="footer-actions">
      <button onclick="closeSettings()">Cancel</button>
      <button class="primary" onclick="saveSettings()">Save</button>
    </div>
  </div>
</div>

<div class="bell-panel" id="bell-panel">
  <h3>vibe 事件流</h3>
  <div id="bell-grant" class="bp-grant" style="display:none">
    <span id="bell-grant-text"></span> <button class="hdr-btn primary" onclick="enableNotifs()">开启</button>
  </div>
  <div id="bell-list"></div>
</div>

<div class="toast" id="toast"></div>

<script>
// ---------- SVG 图标 ----------
function icon(name, size = 16) {
  return `<svg class="ic" width="${size}" height="${size}" aria-hidden="true"><use href="#i-${name}"></use></svg>`;
}
function iconBtn(name, size = 16) {
  // icon 外面包一个 .ic-wrap 让 svg 垂直居中
  return `<span class="ic-wrap">${icon(name, size)}</span>`;
}

// ---------- 主题图标 (随 currentDark 变 sun/moon) ----------
function refreshHdrIcons() {
  const themeBtn = document.getElementById("theme-btn");
  if (themeBtn) themeBtn.innerHTML = icon(currentDark ? "sun" : "moon", 16);
  const bellBtn = document.getElementById("bell-btn");
  if (bellBtn) bellBtn.innerHTML = icon("bell", 16) + '<span class="bell-badge" id="bell-badge" style="display:none">0</span>';
  const setBtn = document.querySelector('button[onclick="openSettings()"]');
  if (setBtn) setBtn.innerHTML = icon("settings", 16);
  const refreshBtn = document.getElementById("refresh-btn");
  if (refreshBtn) refreshBtn.innerHTML = icon("refresh", 14) + ' Refresh';
}
function toggleTheme() {
  currentDark = !currentDark;
  applyTheme();
}

const PROVIDER_ACCENT = {
  zai: "#2B7FFF",
  minimax: "#7C3AED",
  kimi: "#1F1F1F",
  copilot: "#6e7681",
};

function fmtRelative(ms) {
  if (ms <= 0) return "即将重置";
  const sec = Math.floor(ms / 1000);
  const totalMin = Math.floor(sec / 60);
  const totalHr = Math.floor(totalMin / 60);
  const days = Math.floor(totalHr / 24);
  if (days >= 3) return `${days}天后重置`;
  if (days >= 1) return `${days}天${totalHr % 24}小时后重置`;
  if (totalHr >= 1) return `${totalHr}h${totalMin % 60}m后重置`;
  return `${totalMin}分钟后重置`;
}

// 简单的 JSONPath 解析: 只支持 "$.a.b.c" 这种点路径
function jsonpathGet(obj, path) {
  if (!path || path === "$") return obj;
  if (!path.startsWith("$")) return undefined;
  const parts = path.slice(1).split(".").filter(Boolean);
  let cur = obj;
  for (const p of parts) {
    if (cur == null) return undefined;
    cur = cur[p];
  }
  return cur;
}

function parseReset(value, unit) {
  if (value == null || value === "") return "";
  let ms;
  if (typeof value === "number") {
    if (unit === "ms") ms = value;
    else if (unit === "s") ms = value * 1000;
    else {
      // 自动判断: 大于 1e12 是 ms, 否则按 s
      ms = value > 1e12 ? value : value * 1000;
    }
  } else if (typeof value === "string") {
    const t = Date.parse(value);
    if (Number.isFinite(t)) ms = t - Date.now();
  }
  if (!Number.isFinite(ms)) return "";
  return fmtRelative(ms);
}

// ---------- Normalize (3 内置 + 1 generic) ----------
function normalize(p, raw) {
  const tmpl = p.template || p.id;
  if (tmpl === "zai") return normalizeZai(raw);
  if (tmpl === "kimi") return normalizeKimi(raw);
  if (tmpl === "minimax") return normalizeMinimax(raw);
  if (tmpl === "copilot") return normalizeCopilot(raw);
  if (tmpl === "custom") return normalizeCustom(raw, p.extract || {});
  return [];
}

function normalizeZai(raw) {
  if (!raw?.data || !Array.isArray(raw.data.limits)) return [];
  const limits = raw.data.limits;
  const h5 = limits.find(l => l.type === "TOKENS_LIMIT" && l.unit === 3);
  const d7 = limits.find(l => l.type === "TOKENS_LIMIT" && l.unit === 6);
  const mcp = limits.find(l => l.type === "TIME_LIMIT");
  function resetText(item) {
    if (!item || !item.nextResetTime) return "";
    const ms = new Date(item.nextResetTime).getTime() - Date.now();
    return fmtRelative(ms);
  }
  const rings = [];
  if (h5) rings.push({ title: "5 小时", percent: h5.percentage ?? 0, resetText: resetText(h5) });
  if (d7) rings.push({ title: "周",     percent: d7.percentage ?? 0, resetText: resetText(d7) });
  const extras = [];
  if (mcp) {
    const remainingPct = 100 - (mcp.percentage ?? 0);
    extras.push({
      name: "月",
      value: `剩 ${mcp.remaining ?? 0} / ${mcp.usage ?? "?"}  (${remainingPct}%)`,
      resetText: resetText(mcp),
    });
  }
  extras.push({ name: "套餐等级", value: raw.data.level || "-" });
  return [{ kind: "card", rings, extras }];
}

function normalizeKimi(raw) {
  const payload = raw?.data ?? raw;
  function fmtReset(iso, title) {
    if (!iso) return "";
    const diff = new Date(iso).getTime() - Date.now();
    if (diff <= 0) return "已重置";
    // 月配额: 显示具体日期 (用户更关心 "什么时候重置", 不是 "还剩多久")
    if (title === "月") {
      const d = new Date(iso);
      return `${d.getMonth() + 1}月${d.getDate()}日重置`;
    }
    return fmtRelative(diff);
  }
  const rings = [];
  if (Array.isArray(payload.limits) && payload.limits.length) {
    for (const item of payload.limits) {
      const d = item.detail || item;
      const w = item.window || {};
      const limit = Number(d.limit || 0);
      const used = Number(d.used || 0);
      const pct = limit > 0 ? Math.round(used / limit * 100) : 0;
        let title = "用量";
        if (w.duration === 300) title = "5 小时";
        else if (w.duration) title = `${Math.round(w.duration / 60)}h`;
        rings.push({ title, percent: pct, resetText: fmtReset(d.resetTime, title) });
      }
    }
  if (payload.usage && payload.usage.limit) {
    const limit = Number(payload.usage.limit);
    const used = Number(payload.usage.used || 0);
    const pct = limit > 0 ? Math.round(used / limit * 100) : 0;
    // Kimi 的 usage.resetTime 是 7 天后, 实际是周配额
    rings.push({ title: "周", percent: pct, resetText: fmtReset(payload.usage.resetTime, "周") });
  }
  const extras = [];
  if (payload.parallel?.limit) extras.push({ name: "并发限制", value: payload.parallel.limit });
  if (payload.totalQuota?.limit) extras.push({ name: "总可用", value: `${payload.totalQuota.remaining} / ${payload.totalQuota.limit}` });
  if (payload.user?.membership) extras.push({ name: "会员等级", value: payload.user.membership.level });
  return [{ kind: "card", rings, extras }];
}

function normalizeMinimax(raw) {
  if (raw?.base_resp && raw.base_resp.status_code !== 0) {
    return [{ kind: "auth_required", reason: raw.base_resp.status_msg || "unknown" }];
  }
  const arr = Array.isArray(raw?.model_remains) ? raw.model_remains : [];
  if (!arr.length) return [];
  const main = arr.find(m => m.model_name === "general") || arr[0];
  function fmtRemain(ms) {
    if (!ms || ms <= 0) return "即将重置";
    return fmtRelative(ms);
  }
  const rings = [
    { title: "5 小时", percent: 100 - (main.current_interval_remaining_percent ?? 100),
      resetText: fmtRemain(main.remains_time) },
    { title: "周",     percent: 100 - (main.current_weekly_remaining_percent ?? 100),
      resetText: fmtRemain(main.weekly_remains_time) },
  ];
  const extras = [];
  for (const m of arr) {
    if (m === main) continue;
    extras.push({
      name: `${m.model_name} (5h)`,
      value: `${100 - (m.current_interval_remaining_percent ?? 100)}%`,
    });
  }
  return [{ kind: "card", rings, extras }];
}

function normalizeCopilot(raw) {
  // GitHub Copilot 用户级 premium request 没有公开余额 API (需 org 上下文)
  return [{ kind: "card", rings: [], extras: [
    { name: "状态", value: "GitHub Copilot 用户级余额需 org API" },
    { name: "建议", value: "去 https://github.com/settings/billing 查看" },
  ]}];
}

function normalizeCustom(raw, extract) {
  const rings = [];
  for (const r of (extract.rings || [])) {
    const used = Number(jsonpathGet(raw, r.jsonPath) ?? 0);
    const total = Number(jsonpathGet(raw, r.totalJsonPath) ?? 0);
    const pct = total > 0 ? Math.round(used / total * 100) : 0;
    const resetRaw = jsonpathGet(raw, r.resetJsonPath);
    const reset = resetRaw != null ? parseReset(resetRaw, r.resetUnit) : "";
    rings.push({ title: r.title || "用量", percent: pct, resetText: reset });
  }
  const extras = [];
  for (const e of (extract.extras || [])) {
    extras.push({ name: e.name || "", value: String(jsonpathGet(raw, e.jsonPath) ?? "") });
  }
  return [{ kind: "card", rings, extras }];
}

// ---------- Render ----------
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

function ringSvg(pct, color) {
  const r = 46;
  const C = 2 * Math.PI * r;
  const dash = (pct / 100) * C;
  return `
    <svg class="ring-svg" width="110" height="110" viewBox="0 0 110 110">
      <circle class="ring-track" cx="55" cy="55" r="${r}" stroke-width="7"></circle>
      <circle class="ring-fill" cx="55" cy="55" r="${r}" stroke-width="7"
              stroke="${color}"
              stroke-dasharray="${C}"
              stroke-dashoffset="${C - dash}"></circle>
    </svg>
  `;
}

function ringBlock(r, accent, display) {
  // r.percent 是"已用%"; 这里翻转为"剩余%", 体现 "还能 vibe 多久"
  const used = Math.max(0, Math.min(100, r.percent));
  const remaining = 100 - used;
  const color = remaining < 20 ? "var(--danger)" : remaining < 50 ? "var(--warning)" : accent;
  const displayType = display || "ring";

  // 三种显示模式: ring (圆环) / bar (进度条) / text (纯文字)
  if (displayType === "text") {
    return `
      <div class="ring-block ring-block-text">
        <div class="text-meta">
          <div class="text-pct" style="color:${color}">${remaining}%</div>
          <div class="text-title">${escapeHtml(r.title)}</div>
          ${r.resetText ? `<div class="text-reset">${icon("clock", 11)} ${escapeHtml(r.resetText)}</div>` : ""}
        </div>
      </div>
    `;
  }
  if (displayType === "bar") {
    return `
      <div class="ring-block ring-block-bar">
        <div class="bar-meta">
          <div class="bar-title">
            <span class="bar-pct" style="color:${color}">${remaining}%</span>
            <span class="bar-label">${escapeHtml(r.title)}</span>
          </div>
          <div class="bar" style="height:8px;background:var(--bar);border-radius:999px;overflow:hidden">
            <div style="height:100%;width:${remaining}%;background:${color};border-radius:999px"></div>
          </div>
          ${r.resetText ? `<div class="bar-reset">${icon("clock", 11)} ${escapeHtml(r.resetText)}</div>` : ""}
        </div>
      </div>
    `;
  }
  // 默认 ring
  return `
    <div class="ring-block">
      <div class="ring-wrapper" style="width:110px;height:110px;flex-shrink:0">
        ${ringSvg(remaining, color)}
        <div class="ring-text">
          <span class="pct">${remaining}%</span>
        </div>
      </div>
      <div class="ring-meta">
        <div class="title">${escapeHtml(r.title)}</div>
        ${r.resetText ? `<div class="reset">${icon("clock", 12)} ${escapeHtml(r.resetText)}</div>` : ""}
      </div>
    </div>
  `;
}

function cardHtml(p, extraClass = "") {
  // 颜色: widget 设置优先, 再 provider 全局, 再 fallback
  const w = config.widgets.find(x => x.type === "provider" && x.provider_id === p.id);
  const accent = (w && w.color) || p.color || PROVIDER_ACCENT[p.id] || "#2B7FFF";
  let body;
  if (!p.ok) {
    if (p.error === "disabled") {
      body = `<div class="err" style="background:#f9fafb;color:var(--muted);border-color:var(--border)">已禁用 — 在 Settings 启用</div>`;
    } else {
      body = `<div class="err">⚠ ${escapeHtml(p.error || "unknown error")}</div>`;
    }
  } else {
    const sections = normalize(p, p.data);
    if (!sections.length) {
      body = `<div class="err">Unexpected payload structure.</div>`;
    } else if (sections[0].kind === "auth_required") {
      body = `<div class="err">⚠ 需要登录: ${escapeHtml(sections[0].reason)}<br><br>
        该 endpoint 只支持 cookie。考虑用 <a href="https://github.com/0xtbug/zero-limit" target="_blank">ZeroLimit</a> 做浏览器登录。
      </div>`;
    } else {
      const card = sections[0];
      const rings = (card.rings || []).slice().sort((a, b) => {
        const order = (t) => {
          if (t.includes("5 小时") || t.includes("5h")) return 0;
          if (t === "周" || t.includes("7 天") || t.includes("7d")) return 1;
          if (t === "月" || t.includes("月")) return 2;
          return 3;
        };
        return order(a.title) - order(b.title);
      });
      const extras = card.extras || [];
      // per-provider ring_display: 从 widget 配置读
      const w = config.widgets.find(x => x.type === "provider" && x.provider_id === p.id);
      const display = (w && w.ring_display) || config.ring_display || "ring";
      const ringHtml = rings.length
        ? `<div class="rings-row rings-display-${display}">${rings.map(r => ringBlock(r, accent, display)).join("")}</div>` : "";
      const extrasHtml = extras.length
        ? `<details class="more-folder">
            <summary>更多</summary>
            <div class="more-content">
              <div class="extras">${extras.map(e => `
                <div class="extra-row">
                  <span class="name">${escapeHtml(e.name)}</span>
                  <span class="value">${escapeHtml(String(e.value))}${e.resetText ? ` <span class="muted">${escapeHtml(e.resetText)}</span>` : ""}</span>
                </div>`).join("")}</div>
            </div>
          </details>` : "";
      body = ringHtml + extrasHtml;
    }
  }
  return `
    <div class="card ${extraClass}" style="--accent:${accent}">
      <div class="card-header">
        <h2><span class="dot"></span>${escapeHtml(p.label)}</h2>
        ${renderStatus(p)}
      </div>
      ${body}
    </div>`;
}

function renderStatus(p) {
  if (p.ok) {
    const sections = normalize(p, p.data);
    let minRemaining = 100;
    if (sections.length && sections[0].kind === "card") {
      for (const r of (sections[0].rings || [])) {
        const rem = 100 - (r.percent || 0);
        if (rem < minRemaining) minRemaining = rem;
      }
    }
    // 状态: 正常时只显示 LED 绿点 (无文字), 紧张/见底才显示文字
    let cls = ""; // 默认绿
    let text = ""; // 正常: 无文字
    if (minRemaining < 20) { cls = "err"; text = "紧张"; }
    else if (minRemaining < 50) { cls = "warn"; text = "略紧"; }
    return `<span class="status ${cls}"><span class="led"></span>${text}</span>`;
  }
  if (p.error === "disabled") {
    return `<span class="status"><span class="led" style="background:var(--muted);box-shadow:none"></span>已禁用</span>`;
  }
  // 断连: 红点 + 错误码
  const code = escapeHtml(String(p.error || "断连"));
  return `<span class="status err" title="${code}"><span class="led"></span>断连</span>`;
}

// 卡片上的快捷创建规则: 直接为某 provider 加一条规则
// 算 provider 最危险环的剩余% (越高表示越安全)
// 用于: 卡片排序 (升序, 危险在前) + 危险染色 + 全局告警条
function minRemainingOf(p) {
  if (!p.ok) return 100;  // 错误/禁用的排最后
  const sections = normalize(p, p.data);
  if (!sections.length || sections[0].kind !== "card") return 100;
  let minR = 100;
  for (const r of (sections[0].rings || [])) {
    const rem = 100 - (r.percent || 0);
    if (rem < minR) minR = rem;
  }
  return minR;
}

// 找出所有 provider 里最危险的那条 (provider + ring), 用于全局告警条
function findTopAlert(providers) {
  let top = null;
  for (const p of providers) {
    if (!p.ok) continue;
    const sections = normalize(p, p.data);
    if (!sections.length || sections[0].kind !== "card") continue;
    for (const r of (sections[0].rings || [])) {
      const rem = 100 - (r.percent || 0);
      if (rem < 50 && (!top || rem < top.remaining)) {
        top = { provider: p, ring: r, remaining: rem };
      }
    }
  }
  return top;
}

// 渲染顶栏下方的全局告警条 (剩余 <50% 才显示)
function renderGlobalAlert(providers) {
  const el = document.getElementById("global-alert");
  if (!el) return;
  const top = findTopAlert(providers);
  if (!top) {
    el.style.display = "none";
    el.innerHTML = "";
    return;
  }
  const cls = top.remaining < 20 ? "danger" : "warn";
  let prefix;
  if (top.remaining < 20) prefix = "见底警告";
  else if (top.remaining < 35) prefix = "紧张";
  else prefix = "略紧";
  el.style.display = "block";
  el.innerHTML = `
    <div class="global-alert-inner ${cls}">
      ${icon("bell", 16)}
      <span><b>${prefix}</b> ${escapeHtml(top.provider.label)} · ${escapeHtml(top.ring.title)} 剩 ${top.remaining}%</span>
      ${top.ring.resetText ? `<span class="muted">${escapeHtml(top.ring.resetText)}</span>` : ""}
    </div>
  `;
}

async function load() {
  const btn = document.getElementById("refresh-btn");
  const original = btn.textContent;
  btn.disabled = true;
  btn.innerHTML = '<span class="pc-loading"></span>Loading';
  try {
    const res = await fetch("/api/quota");
    const data = await res.json();
    const main = document.getElementById("main");
    currentProviders = data.providers;

    // 按 widgets 数组顺序渲染 (用户在 Settings 拖拽)
    const hasAny = data.providers.some(p => p.ok && (config.providers.find(x => x.id === p.id)?.enabled));
    if (!hasAny || !config.widgets.length) {
      main.innerHTML = `
        <div class="card" style="grid-column:1/-1;text-align:center;padding:56px 24px">
          <div style="margin-bottom:20px;opacity:0.7">${icon("bell", 56)}</div>
          <h2 style="margin:0 0 8px;font-size:22px">还没 vibe 起来</h2>
          <p style="color:var(--muted);margin:0 0 28px;font-size:13px">点下面按钮挑几个 provider, 填上 key, 几秒钟就能看到 vibe 余量</p>
          <button class="hdr-btn primary" style="font-size:14px;padding:10px 22px" onclick="openSettings()">${icon("settings", 14)} 搞起来</button>
        </div>`;
    } else {
      const html = [];
      const trendProviders = [];
      for (const w of config.widgets) {
        if (!w.enabled) continue;
        if (w.type === "summary") {
          html.push(renderSummaryWidget(data.providers));
        } else if (w.type === "provider") {
          const p = data.providers.find(x => x.id === w.provider_id);
          if (!p) continue;
          const dangerCls = (p.ok && minRemainingOf(p) < 20) ? " danger" : "";
          html.push(cardHtml(p, dangerCls));
          trendProviders.push(p);
        } else if (w.type === "trend") {
          // 趋势卡用所有 enabled provider
          trendProviders.push(...data.providers);
        }
      }
      main.innerHTML = html.join("");
      // 趋势卡单独追加 (因为它要横跨整行 + 独立 init)
      const trendEl = renderTrendCard(data.providers);
      if (trendEl) {
        main.insertAdjacentHTML("beforeend", trendEl);
      }
      initTrendCard(data.providers);
    }
    // 全局告警条 (顶部): 最危险的那条
    renderGlobalAlert(data.providers);
    document.getElementById("updated").textContent =
      "updated " + new Date().toLocaleTimeString("zh-CN", {hour12: false});
    // 每次刷新都重新拉 alerts 规则 (别处可能改了)
    await refreshAlertsConfig();
    checkAndNotify(data.providers);
    // 断连不写历史 (避免假阳性刷屏, 用户直接看卡片 LED 即可)
    // recordSyncFailures(data.providers);
    // 每次刷新页面都让 history 重新拉一次 (不缓存)
    historyCache = null;
  } catch (e) {
    document.getElementById("updated").textContent = "error: " + e.message;
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
}

// ---------- Settings Modal ----------
let config = { providers: [] };
let templates = [];
let savedSnapshot = "";  // 用来检测脏状态的 JSON 快照

// ---------- 主题中心 ----------
const THEMES = [
  { id: "glass",    name: "Glass",    desc: "磨砂面板, 渐变光晕",      font: "Vibe",    recommended: true },
  { id: "minimal",  name: "Minimal",  desc: "Linear 极简, 细线克制",     font: "Classic" },
  { id: "data",     name: "Data",     desc: "Grafana 仪表盘, mono",     font: "Tech" },
  { id: "brand",    name: "Brand",    desc: "Vercel 渐变, 立体色块",     font: "Vibe" },
];
const ACCENTS = [
  { id: "aurora", name: "Aurora 极光",  primary: "#8b5cf6", secondary: "#34d399", text: "白" },
  { id: "berry",  name: "Berry 紫粉",  primary: "#7c3aed", secondary: "#ec4899", text: "白" },
  { id: "ocean",  name: "Ocean 靛青",  primary: "#6366f1", secondary: "#06b6d4", text: "白" },
  { id: "sunset", name: "Sunset 暖阳",  primary: "#f59e0b", secondary: "#ec4899", text: "白" },
];
// 当前选择 (默认)
const savedThemeName = localStorage.getItem("vibeout-theme-name");
const savedAccent = localStorage.getItem("vibeout-accent");
const savedDarkPref = localStorage.getItem("vibeout-theme-dark"); // "light" | "dark" | null (=auto)
let currentTheme = savedThemeName || "glass";
let currentAccent = savedAccent || "aurora";
let currentDark;
if (savedDarkPref === "dark") currentDark = true;
else if (savedDarkPref === "light") currentDark = false;
else currentDark = window.matchMedia("(prefers-color-scheme: dark)").matches;

function applyTheme() {
  try {
    const html = document.documentElement;
    // 移除旧主题/accent class, 保留其他 (如 dark, custom)
    html.className = html.className
      .split(/\s+/)
      .filter(c => c && !/^theme-/.test(c) && !/^accent-/.test(c))
      .join(" ");
    html.classList.add(`theme-${currentTheme}`);
    html.classList.add(`accent-${currentAccent}`);
    if (currentDark) html.classList.add("dark");
    localStorage.setItem("vibeout-theme-name", currentTheme);
    localStorage.setItem("vibeout-accent", currentAccent);
    localStorage.setItem("vibeout-theme-dark", currentDark ? "dark" : "light");
    refreshHdrIcons();
    refreshCharts();
  } catch (e) {
    console.error("applyTheme failed:", e);
  }
}

function renderThemeCenter() {
  const grid = document.getElementById("theme-grid");
  if (grid) {
    grid.innerHTML = THEMES.map(t => `
      <div class="theme-card ${t.id === currentTheme ? "active" : ""}" data-tid="${t.id}" onclick="selectTheme('${t.id}')">
        <div class="tc-preview" id="tc-preview-${t.id}"></div>
        <div class="tc-name">${escapeHtml(t.name)}</div>
        <div class="tc-desc">${escapeHtml(t.desc)}</div>
        <div class="tc-font">font: ${t.font}</div>
      </div>
    `).join("");
    // 渲染每张卡的迷你预览 (一个圆环 + 一条线)
    THEMES.forEach(t => {
      const c = document.getElementById(`tc-preview-${t.id}`);
      if (!c) return;
      c.innerHTML = `
        <svg viewBox="0 0 60 60" style="width:36px;height:36px;position:absolute;top:50%;left:50%;transform:translate(-50%,-50%)">
          <circle cx="30" cy="30" r="22" fill="none" stroke="rgba(0,0,0,0.08)" stroke-width="4"/>
          <circle cx="30" cy="30" r="22" fill="none" stroke-width="4" stroke-dasharray="138.2" stroke-dashoffset="20" stroke-linecap="round" transform="rotate(-90 30 30)"/>
        </svg>
      `;
    });
  }
  const row = document.getElementById("accent-row");
  if (row) {
    row.innerHTML = ACCENTS.map(a => `
      <div style="position:relative">
        <div class="accent-swatch ${a.id === currentAccent ? "active" : ""}" data-aid="${a.id}"
             style="background:linear-gradient(135deg,${a.primary},${a.secondary})"
             onclick="selectAccent('${a.id}')" title="${escapeHtml(a.name)}"></div>
        <span class="as-label">${escapeHtml(a.name)}</span>
      </div>
    `).join("");
  }
}

function selectTheme(id) {
  currentTheme = id;
  applyTheme();
  // 重渲染卡片 (active 态)
  document.querySelectorAll(".theme-card").forEach(c => c.classList.toggle("active", c.dataset.tid === id));
  // 自动跟主题配的字体在 CSS 里处理
}
function selectAccent(id) {
  currentAccent = id;
  applyTheme();
  document.querySelectorAll(".accent-swatch").forEach(s => s.classList.toggle("active", s.dataset.aid === id));
}
function selectDarkMode(dark) {
  currentDark = dark;
  applyTheme();
}

// 初始化 (页面加载时立即应用)
applyTheme();

// ---------- 趋势图 ----------
let historyCache = null;
const chartInstances = {};  // pid -> Chart 实例

async function fetchHistory() {
  if (historyCache) return historyCache;
  try {
    const res = await fetch("/api/history");
    const data = await res.json();
    historyCache = data.history || [];
    return historyCache;
  } catch (e) {
    return [];
  }
}

// ---------- 趋势组件卡片 (provider × 维度 全多选) ----------
let trendChartInstance = null;
let trendSelected = { providers: new Set(), rings: new Set() };
const RING_COLOR_PALETTE = ["#2B7FFF", "#7C3AED", "#1F1F1F", "#10b981", "#f59e0b", "#ef4444", "#06b6d4", "#ec4899"];

// 摘要 widget: 顶部整体状态
function renderSummaryWidget(providers) {
  const ok = providers.filter(p => p.ok);
  const enabledOk = ok.filter(p => config.providers.find(x => x.id === p.id)?.enabled);
  if (!enabledOk.length) return "";
  // 同步状态 (只反映 API 拉取, 不反映额度)
  const syncFailed = providers.filter(p => p.config?.enabled && !p.ok && p.error !== "no key configured").length;
  let statusLine;
  if (syncFailed > 0) {
    statusLine = `<span style="color:var(--danger)">${syncFailed} 家同步失败</span>`;
  } else {
    statusLine = `<span style="color:var(--success)">同步正常</span>`;
  }
  return `
    <div class="card summary-card" style="grid-column:1/-1">
      <div class="summary-header">
        <span class="summary-title">${icon("chart", 16)} vibe 摘要</span>
        <span class="summary-stats">${statusLine}</span>
      </div>
      <div class="summary-body">
        ${ringTier(enabledOk, "5 小时")}
        ${ringTier(enabledOk, "周")}
      </div>
    </div>
  `;
}

// 给指定 ring 标题画一排: 逐家预览 5h/周 剩余%, 用 widget 颜色染色
function ringTier(enabledOk, ringTitle) {
  const rows = [];
  for (const p of enabledOk) {
    const s = normalize(p, p.data);
    if (!s.length || s[0].kind !== "card") continue;
    const r = (s[0].rings || []).find(x => x.title === ringTitle);
    if (!r) continue;
    const rem = 100 - (r.percent || 0);
    const name = escapeHtml(p.label || p.id);
    // 颜色: widget.color > provider.color > PROVIDER_ACCENT > 默认
    const w = config.widgets.find(x => x.type === "provider" && x.provider_id === p.id);
    const color = (w && w.color) || p.color || PROVIDER_ACCENT[p.id] || "#2B7FFF";
    const colorCls = rem < 20 ? "danger" : rem < 50 ? "warn" : "ok";
    rows.push({ name, pct: rem, color, colorCls });
  }
  if (!rows.length) return "";
  // 按 pct 升序, 最缺的在前
  rows.sort((a, b) => a.pct - b.pct);
  const chips = rows.map(r => `<span class="summary-pill ${r.colorCls}" style="border-color:${r.color}">
    <span class="summary-pill-color" style="background:${r.color}"></span>
    ${r.name} <b>${r.pct}%</b>
  </span>`).join("");
  return `<div class="summary-stat">
    <span class="summary-stat-label">${escapeHtml(ringTitle)}</span>
    <span class="summary-stat-pills">${chips}</span>
  </div>`;
}

function renderTrendCard(providers) {
  // 从 trend widget 读 mode (per-widget 设置优先)
  const trendW = config.widgets.find(x => x.type === "trend");
  const trendMode = (trendW && trendW.trend_mode) || config.trend_mode || "chart";
  if (trendMode === "hidden") return "";
  return `
    <div class="card trend-card">
      <div class="tc-header">
        <span class="tc-title">${icon("chart", 16)} 趋势</span>
        <div class="tc-chips">
          <div class="tc-group">
            <span class="tc-group-label">provider</span>
            <div id="trend-providers-chips" style="display:flex;gap:6px;flex-wrap:wrap"></div>
          </div>
          <div class="tc-group">
            <span class="tc-group-label">维度</span>
            <div id="trend-rings-chips" style="display:flex;gap:6px;flex-wrap:wrap"></div>
          </div>
        </div>
      </div>
      <div class="tc-canvas-wrap" id="trend-canvas-wrap">
        <div class="tc-empty">${icon("chart", 32)}<div>选择左侧 provider + 维度, 开始对比</div></div>
      </div>
    </div>
  `;
}

function initTrendCard(providers) {
  const okProviders = providers.filter(p => p.ok);
  // 默认选中: 从 trend widget 读 (per-widget 优先), 没就 fallback 全局 config
  const trendW = config.widgets.find(x => x.type === "trend");
  const defaultProv = (trendW && trendW.trend_default_providers) || config.trend_default_providers || "all";
  const defaultRing = (trendW && trendW.trend_default_ring) || config.trend_default_ring || "*";
  if (okProviders.length && !trendSelected.providers.size) {
    if (defaultProv === "first") {
      trendSelected.providers.add(okProviders[0].id);
    } else {
      // "all": 全部
      for (const p of okProviders) trendSelected.providers.add(p.id);
    }
    // 默认维度
    const allRings = new Set();
    for (const p of okProviders) {
      const sections = normalize(p, p.data);
      if (sections.length && sections[0].kind === "card") {
        for (const r of (sections[0].rings || [])) allRings.add(r.title);
      }
    }
    if (defaultRing === "*") {
      trendSelected.rings = allRings;
    } else if (allRings.has(defaultRing)) {
      trendSelected.rings = new Set([defaultRing]);
    } else {
      trendSelected.rings = allRings;
    }
  }

  // provider chips
  const pChips = document.getElementById("trend-providers-chips");
  if (pChips) {
    pChips.innerHTML = okProviders.map(p => {
      const active = trendSelected.providers.has(p.id);
      const color = p.color || PROVIDER_ACCENT[p.id] || "#2B7FFF";
      return `<span class="chip ${active ? "active" : ""}" data-pid="${escapeHtml(p.id)}"
                   style="${active ? `background:${color};border-color:${color}` : `color:${color}`}"
                   onclick="toggleTrendProvider('${escapeHtml(p.id)}')">
                   <span class="swatch"></span>${escapeHtml(p.label || p.id)}
              </span>`;
    }).join("");
  }

  // ring chips (合并所有 provider 出现过的维度)
  const allRings = new Set();
  for (const p of okProviders) {
    const sections = normalize(p, p.data);
    if (sections.length && sections[0].kind === "card") {
      for (const r of (sections[0].rings || [])) allRings.add(r.title);
    }
  }
  const rChips = document.getElementById("trend-rings-chips");
  if (rChips) {
    rChips.innerHTML = [...allRings].map(title => {
      const active = trendSelected.rings.has(title);
      return `<span class="chip ${active ? "active" : ""}" data-ring="${escapeHtml(title)}"
                   onclick="toggleTrendRing('${escapeHtml(title)}')">${escapeHtml(title)}</span>`;
    }).join("");
  }
  renderTrendChart(okProviders);
}

function toggleTrendProvider(pid) {
  if (trendSelected.providers.has(pid)) trendSelected.providers.delete(pid);
  else trendSelected.providers.add(pid);
  // 重渲染 chips (更新 active 态)
  const providers = currentProviders.filter(p => p.ok);
  initTrendCardChipStates(providers);
  renderTrendChart(providers);
}
function toggleTrendRing(title) {
  if (trendSelected.rings.has(title)) trendSelected.rings.delete(title);
  else trendSelected.rings.add(title);
  const providers = currentProviders.filter(p => p.ok);
  initTrendCardChipStates(providers);
  renderTrendChart(providers);
}
// 只更新 chips 的 active 样式, 不重建 DOM (避免闪烁)
function initTrendCardChipStates(providers) {
  document.querySelectorAll("#trend-providers-chips .chip").forEach(el => {
    const pid = el.dataset.pid;
    const active = trendSelected.providers.has(pid);
    el.classList.toggle("active", active);
    const p = providers.find(x => x.id === pid);
    const color = (p && (p.color || PROVIDER_ACCENT[pid])) || "#2B7FFF";
    el.style.background = active ? color : "";
    el.style.borderColor = active ? color : "";
    el.style.color = active ? "white" : color;
  });
  document.querySelectorAll("#trend-rings-chips .chip").forEach(el => {
    const ring = el.dataset.ring;
    el.classList.toggle("active", trendSelected.rings.has(ring));
  });
}

let currentProviders = [];
async function renderTrendChart(providers) {
  currentProviders = providers;
  const combos = [];
  for (const pid of trendSelected.providers) {
    const p = providers.find(x => x.id === pid);
    if (!p) continue;
    const accent = p.color || PROVIDER_ACCENT[pid] || "#2B7FFF";
    for (const ring of trendSelected.rings) {
      combos.push({ pid, label: p.label || pid, ring, accent });
    }
  }
  const wrap = document.getElementById("trend-canvas-wrap");
  if (!combos.length) {
    if (trendChartInstance) { trendChartInstance.destroy(); trendChartInstance = null; }
    wrap.innerHTML = `<div class="tc-empty">${icon("chart", 32)}<div>选择左侧 provider + 维度, 开始对比</div></div>`;
    return;
  }
  const history = await fetchHistory();
  if (!history.length) {
    if (trendChartInstance) { trendChartInstance.destroy(); trendChartInstance = null; }
    wrap.innerHTML = `<div class="tc-empty">${icon("clock", 32)}<div>暂无历史数据, 等几分钟积累</div></div>`;
    return;
  }
  // 收集所有时间点
  const labels = [];
  const seriesByCombo = {};  // `${pid}|${ring}` -> [percent or null]
  for (const rec of history) {
    const d = new Date(rec.ts);
    labels.push(d.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }));
    for (const combo of combos) {
      const key = `${combo.pid}|${combo.ring}`;
      if (!seriesByCombo[key]) seriesByCombo[key] = { ...combo, data: [] };
      const p = (rec.providers || []).find(x => x.id === combo.pid);
      const r = p && (p.rings || []).find(x => x.title === combo.ring);
      seriesByCombo[key].data.push(r ? r.percent : null);
    }
  }
  const isDark = document.documentElement.classList.contains("dark");
  const gridColor = isDark ? "rgba(255,255,255,0.08)" : "rgba(15,23,42,0.06)";
  const tickColor = isDark ? "#8b95a8" : "#64748b";
  const datasets = Object.values(seriesByCombo).map((s, idx) => {
    const c = s.accent;
    return {
      label: `${s.label} · ${s.ring}`,
      data: s.data,
      borderColor: c,
      backgroundColor: c + "20",
      tension: 0.3,
      pointRadius: 1.5,
      pointHoverRadius: 4,
      spanGaps: true,
    };
  });

  // 确保 canvas 存在
  if (!wrap.querySelector("canvas")) {
    wrap.innerHTML = '<canvas></canvas>';
  }
  const ctx = wrap.querySelector("canvas").getContext("2d");
  if (trendChartInstance) trendChartInstance.destroy();
  trendChartInstance = new Chart(ctx, {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: {
          labels: { color: tickColor, boxWidth: 10, boxHeight: 10, font: { size: 11 }, padding: 12 },
          position: "bottom",
        },
        tooltip: { intersect: false },
      },
      scales: {
        x: { ticks: { color: tickColor, maxTicksLimit: 8, font: { size: 10 } }, grid: { color: gridColor } },
        y: { min: 0, max: 100, ticks: { color: tickColor, font: { size: 10 }, callback: v => v + "%" }, grid: { color: gridColor } },
      },
    },
  });
}

function refreshCharts() {
  // 主题切换后, 重绘趋势卡片
  if (currentProviders.length) renderTrendChart(currentProviders);
}

async function openSettings() {
  document.getElementById("modal").classList.add("open");
  await loadConfigAndTemplates();
  await refreshAlertsConfig();
  savedSnapshot = JSON.stringify({ providers: config.providers, alerts: alertsConfig });
  renderTemplateGrid();
  renderProviderList();
  renderAlertList();
  renderThemeCenter();
  syncDisplaySelects();
  renderWidgetsList();
  // 填静态按钮的图标
  const addBtn = document.getElementById("alert-add-btn");
  if (addBtn) addBtn.innerHTML = icon("plus", 14) + " 新建规则";
  const tabAlertsIcon = document.getElementById("tab-alerts-icon");
  if (tabAlertsIcon) tabAlertsIcon.innerHTML = icon("bell", 14);
  const tabThemeIcon = document.getElementById("tab-theme-icon");
  if (tabThemeIcon) tabThemeIcon.innerHTML = icon("settings", 14);
  const tabElementsIcon = document.getElementById("tab-elements-icon");
  if (tabElementsIcon) tabElementsIcon.innerHTML = icon("settings", 14);
}

function switchTab(name) {
  document.querySelectorAll(".settings-tabs button").forEach(b => b.classList.remove("active"));
  document.getElementById("tab-" + name + "-btn").classList.add("active");
  document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
  document.getElementById("tab-" + name).classList.add("active");
}

// ---------- 显示选项 (sort_mode + ring_display) ----------
function updateDisplayPref(key, value) {
  config[key] = value;
  // 改了 trend 默认维度/provider, 重置已选 (让新默认值立即生效)
  if (key === "trend_default_ring" || key === "trend_default_providers") {
    trendSelected = { providers: new Set(), rings: new Set() };
  }
  // 即时生效: 重渲染卡片
  if (currentProviders.length) {
    const html = [];
    const trendProviders = [];
    for (const w of config.widgets) {
      if (!w.enabled) continue;
      if (w.type === "summary") {
        html.push(renderSummaryWidget(currentProviders));
      } else if (w.type === "provider") {
        const p = currentProviders.find(x => x.id === w.provider_id);
        if (!p) continue;
        const dangerCls = (p.ok && minRemainingOf(p) < 20) ? " danger" : "";
        html.push(cardHtml(p, dangerCls));
        trendProviders.push(p);
      }
    }
    document.getElementById("main").innerHTML = html.join("");
    const trendEl = renderTrendCard(currentProviders);
    if (trendEl) document.getElementById("main").insertAdjacentHTML("beforeend", trendEl);
    initTrendCard(currentProviders);
  }
}

function syncDisplaySelects() {
  // ring_display / trend_mode / trend_default_* 现在跟 widget 行走, 这里留个空
}

// ---------- 组件 (Widgets) 列表 ----------
function widgetLabel(w) {
  if (w.type === "summary") return "vibe 摘要";
  if (w.type === "trend") return "趋势";
  if (w.type === "provider") {
    const p = config.providers.find(x => x.id === w.provider_id);
    return p ? p.label || p.id : w.provider_id;
  }
  return w.id;
}

function renderWidgetsList() {
  const list = document.getElementById("widgets-list");
  if (!list) return;
  list.innerHTML = config.widgets.map((w, i) => {
    const settings = widgetSettingsHTML(w, i);
    return `
    <div class="widget-row ${w.enabled ? '' : 'disabled'}" data-idx="${i}"
         draggable="true"
         ondragstart="onWidgetDragStart(event, ${i})"
         ondragover="onWidgetDragOver(event, ${i})"
         ondrop="onWidgetDrop(event, ${i})"
         ondragend="onWidgetDragEnd(event)">
      <input type="checkbox" ${w.enabled ? "checked" : ""} onchange="toggleWidget(${i})" />
      <span class="widget-drag">${icon("drag", 14)}</span>
      <span class="widget-name">${escapeHtml(widgetLabel(w))}</span>
      <span class="widget-type">${escapeHtml(w.type)}</span>
    </div>${settings}`;
  }).join("");
}

function widgetSettingsHTML(w, i) {
  // 每个 provider widget 自带 ring_display 下拉 + 颜色
  if (w.type === "provider") {
    const cur = w.ring_display || config.ring_display || "ring";
    const color = w.color || config.providers.find(p => p.id === w.provider_id)?.color || "#2B7FFF";
    return `<div class="widget-settings">
      <label>显示 <select onchange="updateWidgetField(${i}, 'ring_display', this.value)">
        <option value="ring" ${cur === "ring" ? "selected" : ""}>圆环</option>
        <option value="bar" ${cur === "bar" ? "selected" : ""}>进度条</option>
        <option value="text" ${cur === "text" ? "selected" : ""}>文字</option>
      </select></label>
      <label>颜色 <input type="color" value="${escapeHtml(color)}" oninput="updateWidgetField(${i}, 'color', this.value)" /></label>
    </div>`;
  }
  // 趋势 widget 自带 mode / ring / provider 设置
  if (w.type === "trend") {
    const mode = w.trend_mode || config.trend_mode || "chart";
    const ring = w.trend_default_ring || config.trend_default_ring || "*";
    const prov = w.trend_default_providers || config.trend_default_providers || "all";
    return `<div class="widget-settings">
      <label>显示 <select onchange="updateWidgetField(${i}, 'trend_mode', this.value)">
        <option value="chart" ${mode === "chart" ? "selected" : ""}>折线图</option>
        <option value="hidden" ${mode === "hidden" ? "selected" : ""}>隐藏</option>
      </select></label>
      <label>默认维度 <select onchange="updateWidgetField(${i}, 'trend_default_ring', this.value)">
        <option value="*" ${ring === "*" ? "selected" : ""}>全部</option>
        <option value="5 小时" ${ring === "5 小时" ? "selected" : ""}>5 小时</option>
        <option value="周" ${ring === "周" ? "selected" : ""}>周</option>
        <option value="月" ${ring === "月" ? "selected" : ""}>月</option>
      </select></label>
      <label>默认 provider <select onchange="updateWidgetField(${i}, 'trend_default_providers', this.value)">
        <option value="all" ${prov === "all" ? "selected" : ""}>所有</option>
        <option value="first" ${prov === "first" ? "selected" : ""}>第一个</option>
      </select></label>
    </div>`;
  }
  return "";
}

function updateWidgetField(i, key, value) {
  config.widgets[i][key] = value;
  // 即时生效
  if (currentProviders.length) load();
  // 持久化
  fetch("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  });
  // 趋势默认维度/provider 改了, 重置 trendSelected
  if (key === "trend_default_ring" || key === "trend_default_providers") {
    trendSelected = { providers: new Set(), rings: new Set() };
    if (currentProviders.length) load();
  }
}

function toggleWidget(i) {
  config.widgets[i].enabled = !config.widgets[i].enabled;
  renderWidgetsList();
  if (currentProviders.length) load();  // 重新渲染
  // 立即持久化 (用户重启 dashboard 还在)
  fetch("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  });
}

let widgetDragSrc = null;
function onWidgetDragStart(event, i) {
  widgetDragSrc = i;
  event.dataTransfer.effectAllowed = "move";
  event.currentTarget.classList.add("dragging");
}
function onWidgetDragOver(event, i) {
  event.preventDefault();
  event.dataTransfer.dropEffect = "move";
  event.currentTarget.classList.add("drag-over");
}
function onWidgetDrop(event, targetIdx) {
  event.preventDefault();
  event.currentTarget.classList.remove("drag-over");
  if (widgetDragSrc === null || widgetDragSrc === targetIdx) return;
  const moved = config.widgets.splice(widgetDragSrc, 1)[0];
  config.widgets.splice(targetIdx, 0, moved);
  widgetDragSrc = null;
  renderWidgetsList();
  if (currentProviders.length) load();
  // 立即持久化
  fetch("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  });
}
function onWidgetDragEnd(event) {
  event.currentTarget.classList.remove("dragging");
  document.querySelectorAll(".widget-row.drag-over").forEach(c => c.classList.remove("drag-over"));
  widgetDragSrc = null;
}

// ---------- 通知规则 CRUD ----------
function renderAlertList() {
  const list = document.getElementById("alert-list");
  if (!list) return;
  if (!alertsConfig.length) {
    list.innerHTML = '<div class="muted" style="padding:12px 0">还没有规则。点下面 "+ 新建规则" 添加。</div>';
    return;
  }
  // 收集 provider / ring 选项 (从已配置的 provider 推断)
  const providerOpts = config.providers.map(p => `<option value="${escapeHtml(p.id)}">${escapeHtml(p.label || p.id)}</option>`).join("");
  const ringOpts = ["5 小时", "周", "月"].map(r => `<option value="${r}">${r}</option>`).join("");

  list.innerHTML = alertsConfig.map((a, i) => {
    const disabled = a.enabled === false ? "disabled" : "";
    const channels = a.channels || ["browser"];
    return `
      <div class="alert-row ${disabled}" data-idx="${i}">
        <input type="checkbox" ${a.enabled !== false ? "checked" : ""} onchange="toggleAlert(${i})" title="启用" />
        <div class="ar-body">
          <div class="ar-line">
            <input type="text" placeholder="规则名" value="${escapeHtml(a.label || "")}"
                   oninput="updateAlert(${i}, 'label', this.value)" style="flex:1;min-width:120px" />
          </div>
          <div class="ar-line">
            <label>provider</label>
            <select onchange="updateAlert(${i}, 'provider_id', this.value)">
              <option value="*" ${a.provider_id === "*" || !a.provider_id ? "selected" : ""}>所有 (*)</option>
              ${providerOpts}
            </select>
            <label>维度</label>
            <select onchange="updateAlert(${i}, 'ring', this.value)">
              <option value="*" ${a.ring === "*" || !a.ring ? "selected" : ""}>所有 (*)</option>
              ${ringOpts}
            </select>
          </div>
          <div class="ar-line">
            <label>剩 ≤</label>
            <input type="number" value="${a.threshold ?? 20}" min="1" max="99"
                   oninput="updateAlert(${i}, 'threshold', Number(this.value))" />%
            <label>冷却</label>
            <input type="number" value="${a.cooldown_min ?? 30}" min="1"
                   oninput="updateAlert(${i}, 'cooldown_min', Number(this.value))" />min
          </div>
          <div class="ar-channels">
            <label><input type="checkbox" ${channels.includes("browser") ? "checked" : ""}
                   onchange="toggleAlertChannel(${i}, 'browser', this.checked)" /> 浏览器通知</label>
            <label><input type="checkbox" ${channels.includes("log") ? "checked" : ""}
                   onchange="toggleAlertChannel(${i}, 'log', this.checked)" /> 仅记录</label>
            <label><input type="checkbox" ${a.one_shot ? "checked" : ""}
                   onchange="updateAlert(${i}, 'one_shot', this.checked)" /> 一次性</label>
          </div>
        </div>
        <div class="ar-actions">
          <button onclick="testAlert(${i})" title="测试">${icon("bell", 12)}</button>
          <button class="danger" onclick="removeAlert(${i})" title="删除">${icon("trash", 12)}</button>
        </div>
      </div>
    `;
  }).join("");

  // 修正 select 默认值 (用 textContent 不行, 改用 JS 设)
  list.querySelectorAll(".alert-row").forEach((row, i) => {
    const a = alertsConfig[i];
    const pSel = row.querySelector('select:nth-of-type(1)');
    const rSel = row.querySelector('select:nth-of-type(2)');
    if (pSel && a.provider_id && a.provider_id !== "*") pSel.value = a.provider_id;
    if (rSel && a.ring && a.ring !== "*") rSel.value = a.ring;
  });
}

function toggleAlert(i) {
  alertsConfig[i].enabled = !alertsConfig[i].enabled;
  const row = document.querySelector(`.alert-row[data-idx="${i}"]`);
  if (row) row.classList.toggle("disabled", !alertsConfig[i].enabled);
}
function updateAlert(i, key, value) {
  alertsConfig[i][key] = value;
}
function toggleAlertChannel(i, channel, checked) {
  const ch = alertsConfig[i].channels || (alertsConfig[i].channels = []);
  if (checked && !ch.includes(channel)) ch.push(channel);
  if (!checked) alertsConfig[i].channels = ch.filter(c => c !== channel);
}
function addAlert() {
  const id = "alert-" + Date.now();
  alertsConfig.push({
    id,
    enabled: true,
    label: "新规则",
    provider_id: "*",
    ring: "*",
    threshold: 20,
    channels: ["browser"],
    cooldown_min: 30,
    one_shot: false,
  });
  renderAlertList();
}
function removeAlert(i) {
  alertsConfig.splice(i, 1);
  renderAlertList();
}
function testAlert(i) {
  const a = alertsConfig[i];
  if (!a) return;
  const msg = `测试: ${a.label || "规则"} · 剩 ≤ ${a.threshold ?? 20}% 触发`;
  if (isNotifGranted()) {
    new Notification("🔔 VibeRunOut 测试", { body: msg });
  }
  showToast(msg);
  // 同时写一条日志
  fetch("/api/alerts/log", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      alert_id: a.id, provider_id: "(test)", provider_label: "测试",
      ring: a.ring || "*", remaining_pct: a.threshold ?? 20, channels: ["test"],
    }),
  }).catch(() => {});
}

function isDirty() {
  const snapshot = { providers: config.providers, alerts: alertsConfig };
  return JSON.stringify(snapshot) !== savedSnapshot;
}

function closeSettings() {
  if (isDirty()) {
    if (!confirm("有未保存的改动，确定关闭？")) return;
  }
  document.getElementById("modal").classList.remove("open");
}

async function loadConfigAndTemplates() {
  const [cfg, tpl] = await Promise.all([
    fetch("/api/config").then(r => r.json()),
    fetch("/api/templates").then(r => r.json()),
  ]);
  config = cfg;
  // 兼容老 config
  if (!config.ring_display) config.ring_display = "ring";
  if (!config.trend_mode) config.trend_mode = "chart";
  if (!config.trend_default_ring) config.trend_default_ring = "*";
  if (!config.trend_default_providers) config.trend_default_providers = "all";
  // 老 config 可能没 widgets, 按 enabled providers 生成默认数组
  if (!config.widgets || !Array.isArray(config.widgets) || !config.widgets.length) {
    config.widgets = [
      { id: "summary", type: "summary", enabled: true },
    ];
    for (const p of (config.providers || [])) {
      if (p.enabled) {
        config.widgets.push({ id: `provider:${p.id}`, type: "provider", provider_id: p.id, enabled: true });
      }
    }
    config.widgets.push({ id: "trend", type: "trend", enabled: true });
  }
  // 同步重置 trend 已选 (让 config.trend_default_* 立即生效)
  trendSelected = { providers: new Set(), rings: new Set() };
  templates = tpl;
}

function renderTemplateGrid() {
  const enabledIds = new Set(config.providers.filter(p => p.enabled).map(p => p.template || p.id));
  const grid = document.getElementById("template-grid");
  grid.innerHTML = templates.map(t => {
    const isEnabled = enabledIds.has(t.id);
    return `
      <div class="template-card ${isEnabled ? "enabled" : ""}" data-tpl="${t.id}">
        <div class="name">${escapeHtml(t.label)}</div>
        <div class="desc">${escapeHtml(t.description || "")}</div>
        <div class="actions">
          ${isEnabled
            ? `<button onclick="removeTemplate('${t.id}')">Disable</button>`
            : `<button class="primary" onclick="addTemplate('${t.id}')">Enable</button>`}
        </div>
      </div>
    `;
  }).join("");
}

function renderProviderList() {
  const list = document.getElementById("provider-list");
  if (!config.providers.length) {
    list.innerHTML = '<div class="muted" style="padding:12px 0">还没有启用任何 provider。点上面模板卡片的 "Enable" 添加。</div>';
    return;
  }
  list.innerHTML = config.providers.map((p, i) => {
    const disabledCls = p.enabled ? "" : "disabled";
    return `
      <div class="provider-card ${disabledCls}" data-idx="${i}"
           draggable="true"
           ondragstart="onDragStart(event, ${i})"
           ondragover="onDragOver(event, ${i})"
           ondrop="onDrop(event, ${i})"
           ondragend="onDragEnd(event)">
        <div class="pc-header">
          <span class="pc-drag" title="拖拽排序">${icon("drag", 16)}</span>
          <input type="text" class="pc-label" data-field="label" placeholder="Provider 名称"
                 oninput="updateField(${i}, 'label', this.value)" />
          <span class="pc-pid"></span>
          <label class="pc-toggle" title="启用/禁用">
            <input type="checkbox" ${p.enabled ? "checked" : ""} onchange="toggleEnabled(${i})" />
            <span class="pc-toggle-slider"></span>
          </label>
          <button class="pc-delete" onclick="removeProvider(${i})" title="删除">${icon("trash", 14)}</button>
        </div>
        <div class="pc-body">
          <label>URL</label>
          <input type="text" data-field="url" placeholder="https://..."
                 oninput="updateField(${i}, 'url', this.value)" />

          <label>Key</label>
          <div class="pc-key-wrap">
            <input type="password" data-field="key" placeholder="粘贴新 key 覆盖"
                   oninput="updateField(${i}, 'key', this.value)" />
            <button class="pc-key-toggle" onclick="toggleKeyVisible(${i})">${icon("eye", 12)}</button>
          </div>

          <label>Auth</label>
          <select onchange="updateField(${i}, 'auth', this.value)">
            <option value="bearer" ${p.auth === "bearer" ? "selected" : ""}>Bearer (sk-xxx)</option>
            <option value="raw" ${p.auth === "raw" ? "selected" : ""}>Raw (裸 key)</option>
          </select>
        </div>
      </div>
    `;
  }).join("");

  // 渲染后赋值, 避免 HTML attribute 转义问题
  list.querySelectorAll(".provider-card").forEach((card, i) => {
    const p = config.providers[i];
    card.querySelector(".pc-pid").textContent = p.id || "";
    const labelInput = card.querySelector('[data-field="label"]');
    const urlInput = card.querySelector('[data-field="url"]');
    const keyInput = card.querySelector('[data-field="key"]');
    if (labelInput) labelInput.value = p.label || "";
    if (urlInput) urlInput.value = p.url || "";
    // ⚠ key 字段在前端是 mask 的, 显示 ********xxxx, 不改就保留原 key (后端兜底)
    if (keyInput) {
      const masked = p.key ? "********" + p.key.slice(-4) : "";
      keyInput.value = masked;
      keyInput.dataset.original = masked;
    }
  });
}

function toggleKeyVisible(i) {
  const card = document.querySelector(`.provider-card[data-idx="${i}"]`);
  if (!card) return;
  const input = card.querySelector('[data-field="key"]');
  if (!input) return;
  input.type = input.type === "password" ? "text" : "password";
}

function toggleEnabled(i) {
  config.providers[i].enabled = !config.providers[i].enabled;
  // 更新卡片视觉 (不重渲染整列, 避免输入框失焦)
  const card = document.querySelector(`.provider-card[data-idx="${i}"]`);
  if (card) card.classList.toggle("disabled", !config.providers[i].enabled);
}

// ---------- 拖拽排序 ----------
let dragSrcIdx = null;
function onDragStart(event, i) {
  dragSrcIdx = i;
  event.dataTransfer.effectAllowed = "move";
  event.currentTarget.classList.add("dragging");
}
function onDragOver(event, i) {
  event.preventDefault();
  event.dataTransfer.dropEffect = "move";
  const card = event.currentTarget;
  card.classList.add("drag-over");
}
function onDrop(event, targetIdx) {
  event.preventDefault();
  event.currentTarget.classList.remove("drag-over");
  if (dragSrcIdx === null || dragSrcIdx === targetIdx) return;
  const moved = config.providers.splice(dragSrcIdx, 1)[0];
  config.providers.splice(targetIdx, 0, moved);
  dragSrcIdx = null;
  renderProviderList();  // 重渲染更新 idx
}
function onDragEnd(event) {
  event.currentTarget.classList.remove("dragging");
  document.querySelectorAll(".provider-card.drag-over").forEach(c => c.classList.remove("drag-over"));
  dragSrcIdx = null;
}
function updateField(i, key, value) {
  // key 字段特殊处理: 前端只存"用户改过的新值", 没改就保持 mask 占位
  // 后端 POST 时会判断 *** 开头的就回填磁盘原 key
  config.providers[i][key] = value;
}
function removeProvider(i) {
  config.providers.splice(i, 1);
  renderProviderList();
  renderTemplateGrid();
}
function addTemplate(tplId) {
  if (config.providers.some(p => (p.template || p.id) === tplId)) {
    showToast("已添加过了", "error");
    return;
  }
  const tpl = templates.find(t => t.id === tplId);
  if (!tpl) return;
  config.providers.push({
    ...tpl,
    key: "",
    enabled: true,
  });
  renderProviderList();
  renderTemplateGrid();
  // 滚动到刚加的 provider 卡片
  setTimeout(() => {
    const cards = document.querySelectorAll(".provider-card");
    const last = cards[cards.length - 1];
    if (last) {
      last.scrollIntoView({ behavior: "smooth", block: "center" });
      last.style.transition = "box-shadow .3s";
      last.style.boxShadow = "0 0 0 2px #2563eb";
      setTimeout(() => last.style.boxShadow = "", 1500);
    }
  }, 50);
  showToast("已添加, 记得填 key");
}
function removeTemplate(tplId) {
  config.providers = config.providers.filter(p => (p.template || p.id) !== tplId);
  renderProviderList();
  renderTemplateGrid();
}
function addCustom() {
  const text = document.getElementById("custom-json").value.trim();
  if (!text) { showToast("粘贴一份 JSON", "error"); return; }
  try {
    const obj = JSON.parse(text);
    if (!obj.id) { showToast("JSON 必须有 id 字段", "error"); return; }
    if (config.providers.some(p => p.id === obj.id)) {
      showToast("id 已存在", "error"); return;
    }
    config.providers.push(obj);
    document.getElementById("custom-json").value = "";
    renderProviderList();
    renderTemplateGrid();
    showToast(obj.key ? "已添加 (key 已存, 点 Save 写盘)" : "已添加, 记得填 key");
  } catch (e) {
    showToast("JSON 解析失败: " + e.message, "error");
  }
}

async function saveSettings() {
  // 并行保存 providers (POST /api/config) 和 alerts (POST /api/alerts)
  const [resCfg, resAlerts] = await Promise.all([
    fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config),
    }),
    fetch("/api/alerts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ alerts: alertsConfig }),
    }),
  ]);
  if (resCfg.ok && resAlerts.ok) {
    showToast("已保存");
    savedSnapshot = JSON.stringify({ providers: config.providers, alerts: alertsConfig });
    closeSettings();
    load();
  } else {
    const t = await (resCfg.ok ? resAlerts : resCfg).text();
    showToast("保存失败: " + t, "error");
  }
}

let toastTimer;
function showToast(msg, type) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = "toast show" + (type === "error" ? " error" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2500);
}

// 初始化: 拉 config + templates (必须在 load() 前, 否则 config.providers 是空, 误判空状态)
loadConfigAndTemplates().then(() => {
  load();
});
setInterval(load, 60_000);

// ---------- 通知中心 ----------
// alertsConfig: 从 /api/alerts 拉的规则数组
// notifLastFired: alertId -> timestamp, 实现 cooldown
let alertsConfig = [];
let notifLastFired = {};
let unreadCount = 0;
let bellPanelOpen = false;

async function refreshAlertsConfig() {
  try {
    const res = await fetch("/api/alerts");
    const data = await res.json();
    alertsConfig = data.alerts || [];
  } catch (e) {
    alertsConfig = [];
  }
}

function isNotifGranted() {
  return ("Notification" in window) && Notification.permission === "granted";
}

function enableNotifs() {
  if (!("Notification" in window)) {
    showToast("当前浏览器不支持通知", "error");
    return;
  }
  if (Notification.permission === "granted") {
    showToast("通知已开启");
    updateBellGrant();
  } else if (Notification.permission !== "denied") {
    Notification.requestPermission().then(p => {
      if (p === "granted") {
        showToast("通知已开启");
        updateBellGrant();
      } else {
        showToast("已拒绝, 请到浏览器设置里允许通知", "error");
      }
    });
  } else {
    showToast("已拒绝, 请到浏览器设置里允许通知", "error");
  }
}

// 遍历 providers, 按 alerts 规则匹配并触发
async function checkAndNotify(providers) {
  if (!alertsConfig.length) await refreshAlertsConfig();
  if (!alertsConfig.length) return;
  const now = Date.now();
  const fired = [];  // 本次触发记录
  for (const alert of alertsConfig) {
    if (!alert.enabled) continue;
    for (const p of providers) {
      if (!p.ok || !p.data) continue;
      // provider 过滤
      if (alert.provider_id && alert.provider_id !== "*" && alert.provider_id !== p.id) continue;
      const sections = normalize(p, p.data);
      if (!sections.length || sections[0].kind !== "card") continue;
      const rings = sections[0].rings || [];
      for (const r of rings) {
        // ring 过滤
        if (alert.ring && alert.ring !== "*" && alert.ring !== r.title) continue;
        const remaining = 100 - (r.percent || 0);
        if (remaining > (alert.threshold ?? 20)) continue;
        // cooldown
        const cooldownMs = (alert.cooldown_min ?? 30) * 60 * 1000;
        const key = `${alert.id}:${p.id}:${r.title}`;
        if (notifLastFired[key] && now - notifLastFired[key] < cooldownMs) continue;
        notifLastFired[key] = now;
        // 触发
        const channels = alert.channels || ["browser"];
        const body = `${p.label} · ${r.title} 只剩 ${remaining}%${r.resetText ? "\\n" + r.resetText : ""}`;
        if (channels.includes("browser") && isNotifGranted()) {
          try {
            new Notification("⚠️ VibeRunOut 告警", { body, tag: key });
          } catch (e) {}
        }
        fired.push({
          alert_id: alert.id,
          alert_label: alert.label || `${p.label} ${r.title}`,
          provider_id: p.id,
          provider_label: p.label,
          ring: r.title,
          remaining_pct: remaining,
          channels,
          ts: new Date().toISOString(),
        });
        // 一次性触发自动禁用
        if (alert.one_shot) {
          alert.enabled = false;
          // POST 回去 (不阻塞)
          fetch("/api/alerts", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ alerts: alertsConfig }),
          }).catch(() => {});
        }
        break;  // 同一 provider 只触发最高那条
      }
    }
  }
  if (fired.length) {
    // 写日志 (后端记) + 更新铃铛角标
    for (const f of fired) {
      // 后端 append_alert_log 需要触发, 这里用一个轻量 endpoint
      try {
        await fetch("/api/alerts/log", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(f),
        });
      } catch (e) {}
    }
    unreadCount += fired.length;
    updateBellBadge();
    showToast(`🔔 ${fired.length} 条告警触发`);
    if (bellPanelOpen) renderBellList();
  }
}

// ---------- 铃铛角标 + 面板 ----------
function updateBellBadge() {
  const badge = document.getElementById("bell-badge");
  if (!badge) return;
  if (unreadCount > 0) {
    badge.textContent = unreadCount > 99 ? "99+" : unreadCount;
    badge.style.display = "";
  } else {
    badge.style.display = "none";
  }
}

function updateBellGrant() {
  const grant = document.getElementById("bell-grant");
  if (!grant) return;
  grant.style.display = isNotifGranted() ? "none" : "";
  const txt = document.getElementById("bell-grant-text");
  if (txt) txt.innerHTML = icon("bell", 14) + " 系统通知未开启";
}

async function toggleBellPanel() {
  bellPanelOpen = !bellPanelOpen;
  const panel = document.getElementById("bell-panel");
  panel.classList.toggle("open", bellPanelOpen);
  if (bellPanelOpen) {
    updateBellGrant();
    await renderBellList();
    unreadCount = 0;
    updateBellBadge();
  }
}

async function renderBellList() {
  const list = document.getElementById("bell-list");
  if (!list) return;
  try {
    const res = await fetch("/api/alerts/log");
    const data = await res.json();
    const log = data.log || [];
    if (!log.length) {
      list.innerHTML = `<div class="bp-empty">${icon("bell", 28)}<div>还没有告警记录</div><div class="muted" style="font-size:11px;margin-top:4px">去 Settings → 通知中心 配置规则</div></div>`;
      return;
    }
    list.innerHTML = log.map(item => `
      <div class="bp-item">
        <div class="bp-title">⚠️ ${escapeHtml(item.provider_label || item.provider_id)} · ${escapeHtml(item.ring)} 只剩 ${item.remaining_pct}%</div>
        <div class="bp-meta">${new Date(item.ts).toLocaleString("zh-CN", {hour12: false})} · ${escapeHtml((item.channels || []).join(","))}</div>
      </div>
    `).join("");
  } catch (e) {
    list.innerHTML = '<div class="bp-empty">加载失败</div>';
  }
}

// 点击外部关闭铃铛面板
document.addEventListener("click", (e) => {
  if (!bellPanelOpen) return;
  const panel = document.getElementById("bell-panel");
  const btn = document.getElementById("bell-btn");
  if (panel && !panel.contains(e.target) && btn && !btn.contains(e.target)) {
    bellPanelOpen = false;
    panel.classList.remove("open");
  }
});
</script>
</body>
</html>
"""


# ---------- HTTP Server ----------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _send_json(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, code, text, ctype="text/plain; charset=utf-8"):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        return self.rfile.read(length) if length else b""

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/?"):
            body = INDEX_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/api/quota":
            cfg = get_config()
            providers_in = cfg.get("providers", [])
            # 合并: 已经在 config 里的 provider 优先
            # 兼容老 PROVIDERS 硬编码, 但新代码用 config 驱动的列表
            if not providers_in:
                # 完全没有配置, 提示用户
                self._send_json(200, {"providers": []})
                return
            results = [fetch_provider(p) for p in providers_in]
            # 调试日志 (key 已 mask, 见 fetch_provider 返回结构里不含 key)
            try:
                LOG_PATH.parent.mkdir(exist_ok=True)
                debug = {
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "providers": results,
                }
                with LOG_PATH.open("w", encoding="utf-8") as f:
                    json.dump(debug, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f"log write failed: {e}")
            # 追加历史趋势
            append_history(results)
            self._send_json(200, {"providers": results})
            return

        if self.path == "/api/history":
            # 读 history.jsonl, 返回 [{ts, providers:[{id, rings:[...]]}]
            out = []
            try:
                if HISTORY_PATH.exists():
                    with HISTORY_PATH.open("r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                out.append(json.loads(line))
                            except Exception:
                                continue
            except Exception as e:
                self._send_json(500, {"error": str(e)})
                return
            self._send_json(200, {"history": out})
            return

        if self.path == "/api/config":
            cfg = reload_config()  # 每次 GET 都重读盘, 实现外部编辑也热生效
            # mask key 字段
            masked = {
                "providers": [
                    {**p, "key": ("***" + p["key"][-4:]) if p.get("key") else ""}
                    for p in cfg.get("providers", [])
                ]
            }
            self._send_json(200, masked)
            return

        if self.path == "/api/templates":
            # 4 个内置模板, 删掉 description 之外的所有字段
            ts = [
                {k: v for k, v in t.items() if k != "description"} | {"description": t["description"]}
                for t in BUILTIN_TEMPLATES
            ]
            # 上面写法等价于: 全部字段, 但显式表达
            self._send_json(200, BUILTIN_TEMPLATES)
            return

        if self.path == "/api/alerts":
            cfg = get_config()
            self._send_json(200, {"alerts": cfg.get("alerts", [])})
            return

        if self.path == "/api/alerts/log":
            out = []
            try:
                if ALERTS_LOG_PATH.exists():
                    with ALERTS_LOG_PATH.open("r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                out.append(json.loads(line))
                            except Exception:
                                continue
            except Exception as e:
                self._send_json(500, {"error": str(e)})
                return
            # 倒序 (最新在前), 最多 100 条
            out.reverse()
            self._send_json(200, {"log": out[:100]})
            return

        self.send_error(404)

    def do_POST(self):
        if self.path == "/api/config":
            try:
                payload = json.loads(self._read_body() or b"{}")
            except Exception as e:
                self._send_json(400, {"error": f"invalid json: {e}"})
                return
            if not isinstance(payload, dict) or "providers" not in payload:
                self._send_json(400, {"error": "missing providers"})
                return

            with _config_lock:
                # 关键: 前端拿到的 key 是 mask 后的 (***xxxx), 不能直接覆盖磁盘上的真 key
                # 如果某个 provider 的 key 是 mask 值或为空, 用磁盘上现有的真 key 回填
                disk_cfg = load_config()
                disk_by_id = {p["id"]: p for p in disk_cfg.get("providers", [])}
                for p in payload["providers"]:
                    pid = p.get("id", "")
                    key_in = p.get("key", "") or ""
                    # 判断前端发回来的是否是 mask 值: 以 "***" 开头, 或者完全为空
                    is_masked = (key_in.startswith("***") or key_in == "")
                    if is_masked and pid in disk_by_id:
                        # 用磁盘上的真 key 回填
                        p["key"] = disk_by_id[pid].get("key", "")
                    # 否则保留前端发的新 key (用户主动改了)

                CONFIG_PATH.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                reload_config()
            self._send_json(200, {"status": "ok"})
            return

        if self.path == "/api/alerts":
            try:
                payload = json.loads(self._read_body() or b"{}")
            except Exception as e:
                self._send_json(400, {"error": f"invalid json: {e}"})
                return
            if not isinstance(payload, dict) or "alerts" not in payload:
                self._send_json(400, {"error": "missing alerts"})
                return
            with _config_lock:
                # 只更新 alerts 字段, 保留 providers/key 不动
                cfg = get_config()
                cfg["alerts"] = payload["alerts"]
                CONFIG_PATH.write_text(
                    json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                reload_config()
            self._send_json(200, {"status": "ok"})
            return

        if self.path == "/api/alerts/log":
            # 前端触发告警时, POST 一条日志, 后端 append 到 alerts.jsonl
            try:
                payload = json.loads(self._read_body() or b"{}")
            except Exception as e:
                self._send_json(400, {"error": f"invalid json: {e}"})
                return
            append_alert_log(
                alert_id=payload.get("alert_id", ""),
                provider_id=payload.get("provider_id", ""),
                provider_label=payload.get("provider_label", ""),
                ring_title=payload.get("ring", ""),
                remaining_pct=payload.get("remaining_pct"),
                channels=payload.get("channels", []),
            )
            self._send_json(200, {"status": "ok"})
            return

        self.send_error(404)

def main():
    print(f"Quota Dashboard → http://localhost:{PORT}")
    print(f"Config: {CONFIG_PATH}")
    cfg = get_config()
    enabled = [p["id"] for p in cfg.get("providers", []) if p.get("enabled") and p.get("key")]
    disabled = [p["id"] for p in cfg.get("providers", []) if not (p.get("enabled") and p.get("key"))]
    print(f"Enabled with key:  {', '.join(enabled) if enabled else '(none)'}")
    if disabled:
        print(f"Disabled / no key: {', '.join(disabled)}  ← Settings 启用")
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
