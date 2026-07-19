# Changelog

All notable changes to VibeRunOut are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-07-20

### Added
- Initial release
- Dashboard with widget system: 4 built-in provider templates (zai / MiniMax / Kimi / Copilot) + generic custom JSONPath
- 4 dashboard themes: Glass (Glassmorphism, flagship), Minimal (Linear), Data (Grafana), Brand (Vercel)
- 4 accent colors: Aurora (purple+green, default), Berry (purple+pink), Ocean (indigo+cyan), Sunset (orange+pink)
- Per-provider color picker, ring display style (Ring / Bar / Text)
- Trend chart component with multi-select chips (provider × dimension free combination)
- Standalone summary card with two-row tier display (5h / Week, sorted by 不足/还行/充足)
- Settings modal with 4 tabs: Providers, Elements (Widgets), Theme, Notification
- Drag-to-reorder for both providers (legacy) and widgets (new)
- Custom alert rules (threshold / cooldown / one-shot / channels browser+log)
- Desktop notification (>80% threshold, 10-minute cooldown)
- Notification center panel (bell icon → recent history)
- Local persistence (config.json) + immediate hot-switch (no refresh)
- Dark mode (independent from theme/accent)
- Auto-refresh every 60 seconds
- Empty state onboarding (SVG icons, "vibe 见底警告" copy)
- Status text simplified: green LED only when healthy, "略紧" / "紧张" / "断连" text on warning/danger
- Auto-record sync failures disabled (was creating noise)
- Per-widget settings: ring_display / trend_mode / trend_default_ring / trend_default_providers
- Persistence of widget toggle and drag-order (immediate POST, no Save needed)
- Reset trendSelected when trend default ring/provider changes (immediate effect)
- Fixed Kimi ring: usage field is weekly not monthly
- Improved month reset text: shows specific date (e.g., "7月24日重置") instead of hours
- Per-provider ring_display override (per-widget color)
- Drop redundant "剩" character (e.g., "95%" not "95% 剩")
- Smoke ring: 100→110px, smart 3-level color (green/yellow/red by remaining%)
- Smart reset text: <3 days "N 天后", 1-3 days "N 天 M 小时后", <1 day "Xh Ym 后"

### Security
- config.json always gitignored (never enters git)
- logs/ always gitignored
- API key field masked on GET /api/config (e.g., "********xxxx")
- Server binds 127.0.0.1 only (not exposed to LAN)

### Known Limitations
- GitHub Copilot stub (requires PAT + org API, not implemented for personal users)
- Kimi API has no 7-day window field (we treat usage field as weekly)
- Some providers may have rate-limit failures shown as "断连" (red LED + tooltip)
- Desktop notifications require browser open (not background service)
- Single-process reload safety (no multi-worker)
- No OAuth login flow (Playwright browser login not implemented)
