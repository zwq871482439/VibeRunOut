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
        ]
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
<style>
  :root {
    --bg: #f7f7f8;
    --card: #ffffff;
    --card-alt: #fcfcfd;
    --border: #e6e6ea;
    --text: #1f1f1f;
    --muted: #6b7280;
    --bar: #e5e7eb;
    --err-bg: #fff1f2;
    --err-fg: #b91c1c;
    --err-border: #fecdd3;
    --focus: #2563eb;
    --focus-ring: rgba(37,99,235,0.15);
    --toggle-off: #d1d5db;
    --toggle-on: #22c55e;
  }
  html.dark {
    --bg: #0f1115;
    --card: #1a1d23;
    --card-alt: #20242c;
    --border: #2a2f38;
    --text: #e5e7eb;
    --muted: #8b95a5;
    --bar: #2a2f38;
    --err-bg: #2a1a1d;
    --err-fg: #fca5a5;
    --err-border: #5b2630;
    --focus: #3b82f6;
    --focus-ring: rgba(59,130,246,0.25);
    --toggle-off: #3a3f48;
    --toggle-on: #22c55e;
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0; padding: 0;
    background: var(--bg);
    color: var(--text);
    font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
          "Microsoft YaHei", sans-serif;
  }
  header {
    padding: 24px 32px 12px;
    display: flex; align-items: baseline; justify-content: space-between;
  }
  header h1 { margin: 0; font-size: 20px; font-weight: 600; }
  header .meta { color: var(--muted); font-size: 12px; }
  header .actions { display: flex; gap: 8px; align-items: center; }
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
    border-radius: 14px;
    padding: 20px;
    box-shadow: 0 1px 2px rgba(0,0,0,0.03);
  }
  .card-header {
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 16px;
  }
  .card-header h2 {
    margin: 0; font-size: 16px; font-weight: 600;
    display: flex; align-items: center; gap: 8px;
  }
  .card-header .dot {
    width: 10px; height: 10px; border-radius: 50%;
    background: var(--accent, #2B7FFF);
  }
  .card-header .status {
    font-size: 12px; color: var(--muted);
    display: inline-flex; align-items: center; gap: 4px;
    font-variant-numeric: tabular-nums;
  }
  .card-header .status .led {
    width: 8px; height: 8px; border-radius: 50%;
    background: #22c55e;  /* 默认绿 (通畅) */
    box-shadow: 0 0 4px rgba(34,197,94,0.5);
  }
  .card-header .status.err .led {
    background: var(--err-fg);
    box-shadow: 0 0 4px rgba(239,68,68,0.5);
  }
  .card-header .status.err { color: var(--err-fg); }
  .section-title {
    display: flex; justify-content: space-between; align-items: center;
    font-weight: 500; margin-bottom: 8px;
  }
  .section-title .label { color: var(--text); }
  .section-title .pct { font-variant-numeric: tabular-nums; color: var(--text); }
  .bar {
    height: 8px; background: var(--bar); border-radius: 999px;
    overflow: hidden;
  }
  .bar > div {
    height: 100%; border-radius: 999px;
    background: var(--accent, #2B7FFF);
    transition: width .3s ease;
  }
  .bar.tall { height: 20px; }
  .section { margin-bottom: 18px; }
  .section:last-child { margin-bottom: 0; }

  .muted { color: var(--muted); }

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
  .ring-text .pct { font-size: 18px; font-weight: 600; font-variant-numeric: tabular-nums; }
  .ring-text .label { font-size: 10px; color: var(--muted); margin-top: 2px; }
  .ring-wrapper { position: relative; }
  .ring-meta { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
  .ring-meta .title { font-size: 14px; font-weight: 600; color: var(--text); }
  .ring-meta .reset { font-size: 12px; color: var(--muted); }
  .rings-row { display: flex; gap: 12px; align-items: stretch; margin-bottom: 14px; }
  .rings-row .ring-block { flex: 1; }

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

  .err {
    background: var(--err-bg); color: var(--err-fg);
    border: 1px solid var(--err-border); border-radius: 10px;
    padding: 12px; font-size: 13px;
  }
  .err a { color: var(--err-fg); }
  button {
    border: 1px solid var(--border); background: var(--card);
    padding: 6px 12px; border-radius: 8px; cursor: pointer;
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
    border-radius: 14px;
    max-width: 720px; width: 90vw;
    max-height: 85vh; overflow-y: auto;
    padding: 24px;
    box-shadow: 0 20px 40px rgba(0,0,0,0.15);
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
    border-radius: 10px;
    padding: 12px;
    display: flex; flex-direction: column; gap: 4px;
  }
  .template-card.enabled { border-color: var(--focus); background: var(--card-alt); }
  .template-card .name { font-weight: 600; font-size: 14px; }
  .template-card .desc { color: var(--muted); font-size: 12px; flex: 1; }
  .template-card .actions { margin-top: 8px; display: flex; gap: 6px; }

  .provider-card {
    border: 1px solid var(--border);
    border-radius: 10px;
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
    border: 1px solid var(--border); border-radius: 6px;
    padding: 0; cursor: pointer; background: none;
    flex-shrink: 0;
  }
  .provider-card .pc-color::-webkit-color-swatch-wrapper { padding: 2px; }
  .provider-card .pc-color::-webkit-color-swatch { border: none; border-radius: 4px; }
  .provider-card .pc-label {
    flex: 1;
    border: 1px solid transparent; border-radius: 6px;
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
    border-radius: 6px;
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
    box-shadow: 0 1px 2px rgba(0,0,0,0.2);
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
    border: 1px solid var(--border); border-radius: 6px;
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
    border: 1px solid var(--border); border-radius: 8px;
    padding: 8px; font-family: ui-monospace, monospace; font-size: 12px;
    resize: vertical;
  }
  .custom-box {
    border: 1px dashed var(--border);
    border-radius: 10px;
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
    padding: 10px 16px; border-radius: 8px;
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
</style>
</head>
<body>
<header>
  <div>
      <h1>VibeRunOut <span class="subtitle">vibe 见底警告</span></h1>
    </div>
  <div class="actions">
    <span class="meta" id="updated">--</span>
    <button class="hdr-btn icon-only" onclick="toggleTheme()" id="theme-btn" title="切换主题">🌙</button>
    <button class="hdr-btn icon-only" onclick="enableNotifs()" id="notif-btn" title="开启告警通知">🔔</button>
    <button class="hdr-btn icon-only" onclick="openSettings()" title="设置">⚙</button>
    <button class="hdr-btn" id="refresh-btn" onclick="load()">🔄 Refresh</button>
  </div>
</header>
<main id="main"></main>
<footer>auto refresh every 60s · keys never leave the server</footer>

<!-- Settings Modal -->
<div class="modal-backdrop" id="modal" onclick="if(event.target===this)closeSettings()">
  <div class="modal">
    <span class="close" onclick="closeSettings()">&times;</span>
    <h2>Settings</h2>

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

    <div class="footer-actions">
      <button onclick="closeSettings()">Cancel</button>
      <button class="primary" onclick="saveSettings()">Save</button>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
// ---------- 主题 (优先于其他脚本, 避免 FOUC) ----------
(function initTheme() {
  const saved = localStorage.getItem("vibeout-theme");
  const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const dark = saved ? saved === "dark" : prefersDark;
  if (dark) document.documentElement.classList.add("dark");
  document.addEventListener("DOMContentLoaded", () => {
    const btn = document.getElementById("theme-btn");
    if (btn) btn.textContent = dark ? "☀️" : "🌙";
  });
})();
function toggleTheme() {
  const html = document.documentElement;
  const isDark = html.classList.toggle("dark");
  localStorage.setItem("vibeout-theme", isDark ? "dark" : "light");
  const btn = document.getElementById("theme-btn");
  if (btn) btn.textContent = isDark ? "☀️" : "🌙";
  // 已渲染的图表要重绘 (轴线颜色等)
  if (typeof refreshCharts === "function") refreshCharts();
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
  function fmtReset(iso) {
    if (!iso) return "";
    return fmtRelative(new Date(iso).getTime() - Date.now());
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
        rings.push({ title, percent: pct, resetText: fmtReset(d.resetTime) });
      }
    }
  if (payload.usage && payload.usage.limit) {
    const limit = Number(payload.usage.limit);
    const used = Number(payload.usage.used || 0);
    const pct = limit > 0 ? Math.round(used / limit * 100) : 0;
    rings.push({ title: "月", percent: pct, resetText: fmtReset(payload.usage.resetTime) });
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
  const r = 42;
  const C = 2 * Math.PI * r;
  const dash = (pct / 100) * C;
  return `
    <svg class="ring-svg" width="100" height="100" viewBox="0 0 100 100">
      <circle class="ring-track" cx="50" cy="50" r="${r}" stroke-width="8"></circle>
      <circle class="ring-fill" cx="50" cy="50" r="${r}" stroke-width="8"
              stroke="${color}"
              stroke-dasharray="${C}"
              stroke-dashoffset="${C - dash}"></circle>
    </svg>
  `;
}

function ringBlock(r, accent) {
  // r.percent 是"已用%"; 这里翻转为"剩余%", 体现 "还能 vibe 多久"
  const used = Math.max(0, Math.min(100, r.percent));
  const remaining = 100 - used;
  // 剩余越少越警告
  const color = remaining < 20 ? "#ef4444" : remaining < 50 ? "#f59e0b" : accent;
  return `
    <div class="ring-block">
      <div class="ring-wrapper" style="width:100px;height:100px;flex-shrink:0">
        ${ringSvg(remaining, color)}
        <div class="ring-text">
          <span class="pct">剩 ${remaining}%</span>
          <span class="label">可 vibe</span>
        </div>
      </div>
      <div class="ring-meta">
        <div class="title">${escapeHtml(r.title)}</div>
        ${r.resetText ? `<div class="reset">⏱ ${escapeHtml(r.resetText)}</div>` : ""}
      </div>
    </div>
  `;
}

function cardHtml(p) {
  const accent = p.color || PROVIDER_ACCENT[p.id] || "#2B7FFF";
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
      const ringHtml = rings.length
        ? `<div class="rings-row">${rings.map(r => ringBlock(r, accent)).join("")}</div>` : "";
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
      const chartTitles = rings.map(r => r.title);
      const chartHtml = `<details class="more-folder" data-chart-pid="${escapeHtml(p.id)}" data-chart-titles="${escapeHtml(JSON.stringify(chartTitles))}" data-chart-accent="${escapeHtml(accent)}">
            <summary>📈 趋势</summary>
            <div class="more-content">
              <div class="chart-wrap" id="chart-${escapeHtml(p.id)}">
                <div class="chart-wrap empty">展开后加载...</div>
              </div>
            </div>
          </details>`;
      body = ringHtml + extrasHtml + chartHtml;
    }
  }
  return `
    <div class="card" style="--accent:${accent}">
      <div class="card-header">
        <h2><span class="dot"></span>${escapeHtml(p.label)}</h2>
        ${renderStatus(p)}
      </div>
      ${body}
    </div>`;
}

function renderStatus(p) {
  if (p.ok) {
    return `<span class="status"><span class="led"></span>通畅</span>`;
  }
  if (p.error === "disabled") {
    return `<span class="status"><span class="led" style="background:var(--muted);box-shadow:none"></span>已禁用</span>`;
  }
  // 错误: 红灯 + 错误码
  const code = escapeHtml(String(p.error || "error"));
  return `<span class="status err"><span class="led"></span>${code}</span>`;
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
    const cards = data.providers.map(cardHtml).join("");
    const hasAny = data.providers.some(p => p.ok || (p.error && p.error !== "disabled" && p.error !== "no key configured"));
    if (!hasAny) {
      // 空配置 / 全 disabled / 全无 key: 显示引导
      main.innerHTML = `
        <div class="card" style="grid-column:1/-1;text-align:center;padding:48px 24px">
          <div style="font-size:48px;margin-bottom:16px">🎛️</div>
          <h2 style="margin:0 0 8px">还没有配置任何 provider</h2>
          <p style="color:var(--muted);margin:0 0 24px">点右上角 ⚙ Settings, 启用内置模板并填入 API key, 然后保存。</p>
          <button class="hdr-btn primary" style="font-size:14px;padding:10px 20px" onclick="openSettings()">⚙ 打开 Settings</button>
        </div>`;
    } else {
      main.innerHTML = cards;
    }
    document.getElementById("updated").textContent =
      "updated " + new Date().toLocaleTimeString("zh-CN", {hour12: false});
    checkAndNotify(data.providers);
    // 每次刷新页面都让 history 重新拉一次 (不缓存)
    historyCache = null;
    // 绑定趋势 details 的展开事件 (lazy 加载, 每次渲染都要重新绑)
    document.querySelectorAll("details.more-folder[data-chart-pid]").forEach(d => {
      d.addEventListener("toggle", () => {
        if (d.open) {
          const pid = d.dataset.chartPid;
          const titles = JSON.parse(d.dataset.chartTitles || "[]");
          const accent = d.dataset.chartAccent || "#2B7FFF";
          loadChart(pid, titles, accent);
        }
      });
      // 如果本来就是展开状态 (不太可能, 但保险), 直接加载
      if (d.open) {
        const pid = d.dataset.chartPid;
        const titles = JSON.parse(d.dataset.chartTitles || "[]");
        const accent = d.dataset.chartAccent || "#2B7FFF";
        loadChart(pid, titles, accent);
      }
    });
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

async function loadChart(pid, ringTitles, accent) {
  const container = document.getElementById("chart-" + pid);
  if (!container) return;
  const history = await fetchHistory();
  // 过滤出该 provider 的记录
  const points = [];
  for (const rec of history) {
    const p = (rec.providers || []).find(x => x.id === pid);
    if (!p || !p.rings || !p.rings.length) continue;
    const ringByName = {};
    for (const r of p.rings) ringByName[r.title] = r.percent;
    points.push({ ts: rec.ts, rings: ringByName });
  }
  // 渲染 canvas
  container.classList.remove("empty");
  container.innerHTML = '<canvas></canvas>';
  const ctx = container.querySelector("canvas").getContext("2d");

  const labels = points.map(pt => {
    const d = new Date(pt.ts);
    return d.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
  });
  const isDark = document.documentElement.classList.contains("dark");
  const gridColor = isDark ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.06)";
  const tickColor = isDark ? "#8b95a5" : "#6b7280";

  const datasets = ringTitles.map((title, idx) => {
    // 给每条线一点色相偏移
    const colors = [accent, "#f59e0b", "#10b981", "#ef4444"];
    const c = colors[idx % colors.length];
    return {
      label: title,
      data: points.map(pt => pt.rings[title] ?? null),
      borderColor: c,
      backgroundColor: c + "22",
      tension: 0.3,
      pointRadius: 1.5,
      pointHoverRadius: 4,
      spanGaps: true,
    };
  });

  // 销毁老实例
  if (chartInstances[pid]) chartInstances[pid].destroy();
  chartInstances[pid] = new Chart(ctx, {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { labels: { color: tickColor, boxWidth: 10, font: { size: 10 } } },
        tooltip: { intersect: false },
      },
      scales: {
        x: { ticks: { color: tickColor, maxTicksLimit: 6, font: { size: 10 } }, grid: { color: gridColor } },
        y: { min: 0, max: 100, ticks: { color: tickColor, font: { size: 10 }, callback: v => v + "%" }, grid: { color: gridColor } },
      },
    },
  });
}

function refreshCharts() {
  // 主题切换后, 把已渲染的图表重绘 (颜色变化)
  for (const pid in chartInstances) {
    const details = document.querySelector(`details.more-folder[data-chart-pid="${pid}"]`);
    if (!details || !details.open) continue;
    const titles = JSON.parse(details.dataset.chartTitles || "[]");
    const accent = details.dataset.chartAccent || "#2B7FFF";
    loadChart(pid, titles, accent);
  }
}

async function openSettings() {
  document.getElementById("modal").classList.add("open");
  await loadConfigAndTemplates();
  savedSnapshot = JSON.stringify(config);
  renderTemplateGrid();
  renderProviderList();
}

function isDirty() {
  return JSON.stringify(config) !== savedSnapshot;
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
          <span class="pc-drag" title="拖拽排序">⠿</span>
          <input type="color" class="pc-color" value="${escapeHtml(p.color || "#2B7FFF")}"
                 oninput="updateField(${i}, 'color', this.value)" title="卡片颜色" />
          <input type="text" class="pc-label" data-field="label" placeholder="Provider 名称"
                 oninput="updateField(${i}, 'label', this.value)" />
          <span class="pc-pid"></span>
          <label class="pc-toggle" title="启用/禁用">
            <input type="checkbox" ${p.enabled ? "checked" : ""} onchange="toggleEnabled(${i})" />
            <span class="pc-toggle-slider"></span>
          </label>
          <button class="pc-delete" onclick="removeProvider(${i})" title="删除">×</button>
        </div>
        <div class="pc-body">
          <label>URL</label>
          <input type="text" data-field="url" placeholder="https://..."
                 oninput="updateField(${i}, 'url', this.value)" />

          <label>Key</label>
          <div class="pc-key-wrap">
            <input type="password" data-field="key" placeholder="粘贴新 key 覆盖"
                   oninput="updateField(${i}, 'key', this.value)" />
            <button class="pc-key-toggle" onclick="toggleKeyVisible(${i})">👁</button>
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
  // 写回磁盘前不打印任何 key, 直接 POST
  const res = await fetch("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  });
  if (res.ok) {
    showToast("已保存");
    savedSnapshot = JSON.stringify(config);  // 更新快照, 这样 closeSettings 不会再弹确认
    closeSettings();
    load();
  } else {
    const t = await res.text();
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

load();
setInterval(load, 60_000);

// ---------- 桌面通知 (>80% 告警) ----------
let notifEnabled = (Notification.permission === "granted");
let notifLastFired = {};  // provider_id -> timestamp, 避免每分钟重复弹

function enableNotifs() {
  if (!("Notification" in window)) {
    showToast("当前浏览器不支持通知", "error");
    return;
  }
  if (Notification.permission === "granted") {
    notifEnabled = true;
    showToast("通知已开启");
  } else if (Notification.permission !== "denied") {
    Notification.requestPermission().then(p => {
      if (p === "granted") {
        notifEnabled = true;
        showToast("通知已开启");
      } else {
        showToast("已拒绝, 不会再弹", "error");
      }
    });
  } else {
    showToast("已拒绝, 请到浏览器设置里允许通知", "error");
  }
}

function checkAndNotify(providers) {
  if (!notifEnabled) return;
  const now = Date.now();
  const COOLDOWN = 10 * 60 * 1000;  // 同一 provider 10 分钟内只弹一次
  for (const p of providers) {
    if (!p.ok || !p.data) continue;
    const sections = normalize(p, p.data);
    if (!sections.length || sections[0].kind !== "card") continue;
    const rings = sections[0].rings || [];
    for (const r of rings) {
      const remaining = 100 - (r.percent || 0);
      if (remaining <= 20) {
        const key = `${p.id}:${r.title}`;
        if (notifLastFired[key] && now - notifLastFired[key] < COOLDOWN) continue;
        notifLastFired[key] = now;
        try {
          new Notification("⚠️ VibeRunOut 告警", {
            body: `${p.label} · ${r.title} 只剩 ${remaining}%${r.resetText ? "\n⏱ " + r.resetText : ""}`,
            tag: key,
          });
        } catch (e) {}
        break;  // 同一 provider 只取最高的那条
      }
    }
  }
}
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
