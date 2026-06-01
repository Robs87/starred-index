#!/usr/bin/env python3
"""
Sync GitHub starred repos → repos.json → README.md

流程：
1. 抓取用户所有 starred repos
2. 与 repos.json 对比，找出新增
3. 关键词规则自动归类新增 repo
4. 写回 repos.json + 重新生成 README.md
"""

import json
import os
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

USERNAME = "Robs87"
REPOS_JSON = Path(__file__).parent / "repos.json"
README_MD = Path(__file__).parent / "README.md"

# ── 分类定义（key = 分类 ID，用于 repos.json） ─────────────────────
CATEGORIES = {
    "ai-agent":          ("🤖 AI Agent 框架 & 核心平台", 1),
    "api-proxy":         ("🔌 API 中转 & LLM 工具链", 2),
    "agent-skills":      ("📚 Agent Skills 合集 & 教程", 3),
    "design":            ("🎨 设计 & 创意 & 内容创作", 4),
    "obsidian":          ("📝 Obsidian & 笔记生态", 5),
    "finance":           ("💰 个人财务 & 记账", 6),
    "rss":               ("📰 RSS & 信息聚合", 7),
    "notion":            ("🔗 Notion 生态", 8),
    "selfhost":          ("🏠 自托管 & NAS & 运维", 9),
    "app-ref":           ("📱 应用 & 产品参考", 10),
    "learn":             ("📖 学习 & 教育", 11),
    "uncategorized":     ("📦 未分类", 99),
}

# ── 关键词自动分类规则 ─────────────────────────────────────────────
# 按优先级排列，第一个匹配生效。越具体的规则越靠前。
AUTO_RULES = [
    # ── 最高优先级：领域特定 ──
    # 学习教育
    ("learn", [
        "textbook", "教材", "cs50", "blockchain", "solidity",
        "english-level", "英语", "llm-course",
    ]),
    # 个人财务
    ("finance", [
        "beecount", "记账", "bookkeeping", "cashflow", "现金流",
        "openbb", "finance-app", "finance platform",
        "llms-in-finance", "finance",
    ]),
    # Notion 生态
    ("notion", [
        "notion", "weread2notion", "douban2notion", "toggl2notion",
        "podcast2notion", "weread_to_notion",
    ]),
    # RSS & 信息聚合
    ("rss", [
        "rss-reader", "rss feed", "rss aggregator", "notion-rss",
        "firecrawl", "web-crawler", "scraper", "follow-builder",
        "clawfeed", "news digest",
    ]),
    # Obsidian & 笔记
    ("obsidian", [
        "obsidian", "note-sync", "fast-note-sync", "fastnodesync",
        "claudesidian", "onenav", "bookmark",
    ]),
    # 设计 & 创意 & 内容创作（在 agent-skills 之前！）
    ("design", [
        "excalidraw", "whiteboard", "ppt-skill", "slide-deck",
        "html-ppt", "illustration", "xiaohei", "markitdown",
        "html-anything", "html editor", "open-design",
        "md2wechat", "wechat article", "公众号", "排版",
        "wewrite", "paywall", "app-store",
        "headless browser", "browser automation",
    ]),
    # 自托管 & NAS & 运维
    ("selfhost", [
        "immich", "photo-management", "nas",
        "wireguard", "vpn", "wg-easy", "shadowrocket", "adblock",
        "moviepilot", "media library", "pansou", "网盘",
        "docker.io", "cloudflare workers", "lucky", "ddns",
        "unraid", "ghostty", "calibre", "mole", "mac 终端",
        "docker 镜像代理", "反向代理",
    ]),
    # API 中转
    ("api-proxy", [
        "cli-proxy-api", "sub2api", "2api", "claude2api", "codex2api",
        "openai-compatible", "ai gateway", "llm gateway", "api gateway",
        "codex-proxy", "proxy api", "usage tracker", "ai bijia",
        "token 信息", "中转", "中转服务", "抹平信息差", "便宜",
    ]),
    # ── 较低优先级：通用 Agent 相关 ──
    # Agent Skills（具体 skill 名称）
    ("agent-skills", [
        "agent-skill", "agent skill", "darwin-skill", "nuwa-skill",
        "graphify", "knowledge-graph", "autoresearch", "autocli",
        "agency-agent", "expert role", "use-case", "usecase",
        "security-practice", "orange-book", "obsidian-skills",
        "visual-skill", "chrome-cdp-skill", "opencrew", "multi-agent",
        "zread", "sciwrite", "narrator", "larksuite/cli",
        "public-agent-skills", "karpathy", "claude-howto", "howto",
        "skills framework", "agentic skills", "khazix", "minimax",
        "academic-research", "academic writing",
        "awesome-openclaw-usecases", "use cases",
    ]),
    # AI Agent 框架（最宽泛，放最后）
    ("ai-agent", [
        "openclaw", "hermes-agent", "hermes", "claude-code", "claude code",
        "codex", "clawdbot", "moltbot", "agent-harness",
        "superpowers", "agent framework", "ai-agent", "ai agent",
        "memory system", "ai memory", "mem9", "memos", "mempalace",
        "openviking", "context-database", "cc-switch",
        "coding agent", "deepseek", "agent infrastructure",
        "personal ai", "ai-worker", "all-in-one assistant",
        "memory os", "self-evolving memory",
    ]),
]


def github_api(url: str):
    """带 Token 的 GitHub API 请求，自动翻页。"""
    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"token {token}"

    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def fetch_all_starred(username: str):
    """抓取全部 starred repos（自动翻页）。"""
    repos = []
    page = 1
    while True:
        data = github_api(
            f"https://api.github.com/users/{username}/starred"
            f"?per_page=100&page={page}"
        )
        if not data:
            break
        repos.extend(data)
        page += 1
    return repos


def classify(repo: dict) -> str:
    """用关键词规则给 repo 分类。"""
    name = (repo.get("full_name") or "").lower()
    desc = (repo.get("description") or "").lower()
    topics = " ".join(repo.get("topics", [])).lower()
    text = f"{name} {desc} {topics}"

    for cat_id, keywords in AUTO_RULES:
        for kw in keywords:
            if kw.lower() in text:
                return cat_id
    return "uncategorized"


def load_existing() -> dict:
    """加载 repos.json，格式：{full_name: {category, stars, ...}}"""
    if REPOS_JSON.exists():
        return json.loads(REPOS_JSON.read_text())
    return {}


def save_repos(repos: dict):
    REPOS_JSON.write_text(json.dumps(repos, ensure_ascii=False, indent=2) + "\n")


def generate_readme(repos: dict):
    """从 repos.json 生成 README.md。"""
    lines = [
        "# ⭐ 我的 GitHub Starred Repos 分类索引\n",
        "",
        f"> 自动同步 · 共 {len(repos)} 个仓库 · 最后更新：{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n",
        "",
        "---\n",
    ]

    # 按分类顺序输出
    sorted_cats = sorted(CATEGORIES.items(), key=lambda x: x[1][1])

    for cat_id, (cat_name, _order) in sorted_cats:
        cat_repos = [
            (name, info) for name, info in repos.items()
            if info.get("category") == cat_id
        ]
        if not cat_repos:
            continue

        # 按 star 数降序
        cat_repos.sort(key=lambda x: x[1].get("stars", 0), reverse=True)

        lines.append(f"## {cat_name}\n")
        lines.append("| Repo | ⭐ | 语言 | 说明 |")
        lines.append("|---|---|---|---|")

        for name, info in cat_repos:
            stars = info.get("stars", 0)
            lang = info.get("lang", "-")
            desc = info.get("desc", "-")
            # 截断过长描述
            if len(desc) > 80:
                desc = desc[:77] + "..."
            url = info.get("url", f"https://github.com/{name}")
            lines.append(f"| [{name}]({url}) | {stars:,} | {lang} | {desc} |")

        lines.append("")

    lines.append("---\n")
    lines.append("*自动同步 by [sync-stars.yml](.github/workflows/sync-stars.yml) · [Robs87](https://github.com/Robs87)*\n")

    README_MD.write_text("\n".join(lines))


def main():
    print(f"🔍 Fetching starred repos for {USERNAME}...")
    starred = fetch_all_starred(USERNAME)
    print(f"   Found {len(starred)} starred repos")

    existing = load_existing()
    new_count = 0
    updated_count = 0

    for r in starred:
        full_name = r["full_name"]
        lic = r.get("license") or {}
        info = {
            "stars": r["stargazers_count"],
            "lang": r.get("language") or "-",
            "license": lic.get("spdx_id", "-"),
            "desc": r.get("description") or "-",
            "url": r["html_url"],
            "topics": r.get("topics", []),
        }

        if full_name in existing:
            # 更新 stars/desc 等动态字段，保留用户手动设的 category
            old = existing[full_name]
            info["category"] = old.get("category", classify(r))
            if old.get("stars") != info["stars"] or old.get("desc") != info["desc"]:
                updated_count += 1
        else:
            # 新 repo，自动分类
            info["category"] = classify(r)
            new_count += 1
            cat_name = CATEGORIES.get(info["category"], ("?",))[0]
            print(f"   ✨ NEW: {full_name} → {cat_name}")

        existing[full_name] = info

    # 检查被 unstar 的
    current_names = {r["full_name"] for r in starred}
    removed = [n for n in existing if n not in current_names]
    for n in removed:
        print(f"   ❌ REMOVED: {n}")
        del existing[n]

    save_repos(existing)
    generate_readme(existing)

    print(f"\n✅ Done: {new_count} new, {updated_count} updated, {len(removed)} removed")
    print(f"   Total: {len(existing)} repos in index")


if __name__ == "__main__":
    main()
