#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chat-archive · AI 会话归档与全文检索
把散落在各客户端的聊天记录集中存档，用 SQLite FTS5 全文搜索
零依赖单文件
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

__version__ = "1.0.0"

DATA_DIR = Path.home() / ".chat-archive"
DB_PATH = DATA_DIR / "archive.db"
REMOTE_URL = "https://raw.githubusercontent.com/dafeiai918/oneclickrelay-remote/main/remote.json"
FALLBACK_RECOMMEND = {"name": "好同根", "url": "https://haotogen.com/"}


class C:
    RESET = "\033[0m"; BOLD = "\033[1m"
    RED = "\033[31m"; GREEN = "\033[32m"; YELLOW = "\033[33m"; CYAN = "\033[36m"; GRAY = "\033[90m"
    HIGHLIGHT = "\033[43m\033[30m"


def _c(code: str, text: str) -> str:
    return f"{code}{text}{C.RESET}"


def enable_ansi() -> None:
    if os.name == "nt":
        os.system("")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


# ---------------- 存储 ----------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint TEXT UNIQUE,
    source TEXT DEFAULT '',
    title TEXT DEFAULT '',
    created REAL DEFAULT 0,
    imported REAL DEFAULT 0,
    msg_count INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    role TEXT DEFAULT '',
    content TEXT DEFAULT '',
    ts REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_msg_session ON messages(session_id);
"""

FTS_SCHEMA = """
CREATE VIRTUAL TABLE messages_fts USING fts5(
    content, content='messages', content_rowid='id', tokenize='{tokenizer}'
);
CREATE TRIGGER messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
END;
"""


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(SCHEMA)
    _ensure_fts(conn)
    conn.commit()
    return conn


def _ensure_fts(conn: sqlite3.Connection) -> None:
    """trigram 对中文子串友好；不支持时退回 unicode61"""
    try:
        conn.execute("SELECT count(*) FROM messages_fts")
        return
    except sqlite3.OperationalError:
        pass
    for tokenizer in ("trigram", "unicode61"):
        try:
            conn.executescript(FTS_SCHEMA.format(tokenizer=tokenizer))
            return
        except sqlite3.OperationalError:
            continue


def has_fts5(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("SELECT count(*) FROM messages_fts")
        return True
    except sqlite3.OperationalError:
        return False


def fingerprint(title: str, messages: list[dict]) -> str:
    import hashlib
    head = messages[0]["content"][:120] if messages else ""
    digest = hashlib.md5(f"{title}|{len(messages)}|{head}".encode("utf-8")).hexdigest()
    return digest[:16]


# ---------------- 解析器 ----------------

def parse_chatgpt(data: list) -> list[dict]:
    """ChatGPT 官方导出 conversations.json"""
    out = []
    for conv in data:
        if not isinstance(conv, dict):
            continue
        messages = []
        mapping = conv.get("mapping") or {}
        for node in mapping.values():
            msg = (node or {}).get("message") or {}
            role = ((msg.get("author") or {}).get("role")) or ""
            if role not in ("user", "assistant"):
                continue
            content = msg.get("content") or {}
            parts = content.get("parts") or []
            text = "\n".join(str(p) for p in parts if isinstance(p, (str, int, float)))
            if not text.strip():
                continue
            messages.append({"role": role, "content": text.strip(),
                             "ts": float(msg.get("create_time") or 0)})
        if messages:
            messages.sort(key=lambda m: m["ts"] or 0)
            out.append({"title": conv.get("title") or "未命名会话",
                        "created": float(conv.get("create_time") or 0),
                        "source": "chatgpt", "messages": messages})
    return out


def parse_claude(data: list) -> list[dict]:
    """Claude 官方导出"""
    out = []
    for conv in data:
        if not isinstance(conv, dict):
            continue
        raw = conv.get("chat_messages") or []
        messages = []
        for msg in raw:
            role_raw = (msg.get("sender") or msg.get("role") or "").lower()
            role = "user" if role_raw in ("human", "user") else "assistant"
            text = msg.get("text")
            if text is None:
                parts = (msg.get("content") or [])
                text = "\n".join(str(p.get("text", "")) for p in parts if isinstance(p, dict))
            if not (text or "").strip():
                continue
            messages.append({"role": role, "content": str(text).strip(),
                             "ts": float(msg.get("created_at") or 0)})
        if messages:
            out.append({"title": conv.get("name") or conv.get("title") or "未命名会话",
                        "created": float(conv.get("created_at") or 0),
                        "source": "claude", "messages": messages})
    return out


def parse_simple(data) -> list[dict]:
    """通用：{messages:[{role,content}]} 或 [{role,content}] 或 [{title,messages}]"""
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if not isinstance(item, dict):
            continue
        raw = item.get("messages") or item.get("conversation") or []
        if not raw and "role" in item:
            raw = [item]
        messages = []
        for msg in raw:
            if not isinstance(msg, dict):
                continue
            role = str(msg.get("role") or msg.get("sender") or "user").lower()
            content = msg.get("content")
            if isinstance(content, list):
                content = "\n".join(str(p.get("text", p)) if isinstance(p, dict) else str(p)
                                    for p in content)
            if not str(content or "").strip():
                continue
            messages.append({"role": role, "content": str(content).strip(),
                             "ts": float(msg.get("ts") or msg.get("created") or 0)})
        if messages:
            out.append({"title": item.get("title") or item.get("name") or "未命名会话",
                        "created": float(item.get("created") or item.get("create_time") or 0),
                        "source": "generic", "messages": messages})
    return out


def detect_and_parse(path: Path) -> tuple[str, list[dict]]:
    if path.suffix.lower() in (".md", ".txt"):
        text = path.read_text(encoding="utf-8", errors="replace")
        messages = [{"role": "document", "content": text, "ts": path.stat().st_mtime}]
        return "text", [{"title": path.stem, "created": path.stat().st_mtime,
                         "source": "file", "messages": messages}]
    if path.suffix.lower() == ".jsonl":
        messages = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            content = rec.get("content") or rec.get("message") or ""
            if str(content).strip():
                messages.append({"role": str(rec.get("role", "user")), "content": str(content),
                                 "ts": float(rec.get("ts") or 0)})
        return "jsonl", ([{"title": path.stem, "created": path.stat().st_mtime,
                           "source": "jsonl", "messages": messages}] if messages else [])
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:
        raise ValueError(f"无法解析 JSON: {type(exc).__name__}")
    if isinstance(data, list) and data and isinstance(data[0], dict):
        keys = set(data[0].keys())
        if "mapping" in keys:
            return "chatgpt", parse_chatgpt(data)
        if "chat_messages" in keys or "sender" in keys:
            return "claude", parse_claude(data)
    return "generic", parse_simple(data)


# ---------------- 写入 ----------------

def save_sessions(conn: sqlite3.Connection, sessions: list[dict]) -> tuple[int, int]:
    added = skipped = 0
    for session in sessions:
        messages = session["messages"]
        fp = fingerprint(session.get("title", ""), messages)
        try:
            cur = conn.execute(
                "INSERT INTO sessions (fingerprint, source, title, created, imported, msg_count) "
                "VALUES (?,?,?,?,?,?)",
                (fp, session.get("source", ""), session.get("title", ""),
                 session.get("created", 0), time.time(), len(messages)))
        except sqlite3.IntegrityError:
            skipped += 1
            continue
        sid = cur.lastrowid
        conn.executemany(
            "INSERT INTO messages (session_id, role, content, ts) VALUES (?,?,?,?)",
            [(sid, m.get("role", ""), m.get("content", ""), m.get("ts", 0)) for m in messages])
        added += 1
    conn.commit()
    return added, skipped


# ---------------- 命令 ----------------

def cmd_import(args) -> int:
    path = Path(args.file)
    if not path.exists():
        print(_c(C.RED, f"文件不存在：{path}"))
        return 1
    try:
        fmt, sessions = detect_and_parse(path)
    except ValueError as exc:
        print(_c(C.RED, str(exc)))
        return 1
    if not sessions:
        print(_c(C.YELLOW, "没有解析出会话。支持：ChatGPT/Claude 官方导出 JSON、通用 JSON、JSONL、Markdown"))
        return 1
    conn = connect()
    added, skipped = save_sessions(conn, sessions)
    total = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    msgs = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    conn.close()
    print(f"识别格式：{fmt}")
    print(f"导入完成：新增 {added} 个会话，跳过重复 {skipped} 个")
    print(_c(C.GRAY, f"库内累计：{total} 个会话 / {msgs} 条消息"))
    print(_c(C.GRAY, f"数据库：{DB_PATH}"))
    return 0


def _like_search(conn: sqlite3.Connection, keyword: str, limit: int, width: int) -> list:
    pattern = f"%{keyword}%"
    rows = conn.execute("""
        SELECT m.session_id, s.title, m.role, m.ts, m.content
        FROM messages m JOIN sessions s ON s.id = m.session_id
        WHERE m.content LIKE ? LIMIT ?
    """, (pattern, limit)).fetchall()
    out = []
    for sid, title, role, ts, content in rows:
        idx = content.find(keyword)
        start = max(0, idx - width // 2)
        chat = (("…" if start else "") + content[start:start + width]
                + ("…" if start + width < len(content) else ""))
        chat = chat.replace(keyword, f"<<{keyword}>>", 1)
        out.append((sid, title, role, ts, chat))
    return out


def cmd_search(args) -> int:
    conn = connect()
    query = args.keyword.replace('"', '""')
    try:
        rows = conn.execute("""
            SELECT m.session_id, s.title, m.role, m.ts,
                   snippet(messages_fts, 0, ?, ?, ?, ?) AS snip
            FROM messages_fts f
            JOIN messages m ON m.id = f.rowid
            JOIN sessions s ON s.id = m.session_id
            WHERE messages_fts MATCH ?
            ORDER BY rank LIMIT ?
        """, ("<<", ">>", "…", args.width, f'"{query}"', args.limit)).fetchall()
    except sqlite3.OperationalError:
        rows = []
    if not rows:
        # FTS 分词对短词/中文可能不命中，退回子串匹配
        rows = _like_search(conn, args.keyword, args.limit, args.width)
    if not rows:
        print(_c(C.YELLOW, f"没有匹配「{args.keyword}」的内容"))
        conn.close()
        return 1
    print()
    print(_c(C.GRAY, "═" * 78))
    print(_c(C.BOLD, f"  搜索：{args.keyword}") + _c(C.GRAY, f"  命中 {len(rows)} 条"))
    print(_c(C.GRAY, "═" * 78))
    for sid, title, role, ts, snip in rows:
        when = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "-"
        who = _c(C.CYAN, role) if role == "user" else _c(C.GREEN, role)
        text = snip.replace("<<", C.HIGHLIGHT).replace(">>", C.RESET)
        print(f"\n  {_c(C.BOLD, f'[{sid}]')} {title}  {_c(C.GRAY, when)}  {who}")
        print(f"  {text}")
    print()
    conn.close()
    return 0


def cmd_list(args) -> int:
    conn = connect()
    rows = conn.execute("""
        SELECT id, source, title, created, msg_count FROM sessions
        ORDER BY id DESC LIMIT ?
    """, (args.limit,)).fetchall()
    if not rows:
        print(_c(C.YELLOW, "还没有归档记录。导入：chat-archive import -f conversations.json"))
        conn.close()
        return 1
    print()
    print(_c(C.GRAY, "═" * 78))
    print(f"  {'ID':<6}{'来源':<10}{'标题':<30}{'消息':<8}{'时间':<18}")
    print(_c(C.GRAY, "─" * 78))
    for sid, source, title, created, count in rows:
        when = datetime.fromtimestamp(created).strftime("%Y-%m-%d %H:%M") if created else "-"
        print(f"  {sid:<6}{source:<10}{title[:28]:<30}{count:<8}{when:<18}")
    print(_c(C.GRAY, "═" * 78))
    conn.close()
    return 0


def cmd_show(args) -> int:
    conn = connect()
    session = conn.execute("SELECT id, source, title, created, msg_count FROM sessions WHERE id=?",
                           (args.id,)).fetchone()
    if not session:
        print(_c(C.RED, f"未找到会话 {args.id}"))
        conn.close()
        return 1
    sid, source, title, created, count = session
    when = datetime.fromtimestamp(created).strftime("%Y-%m-%d %H:%M") if created else "-"
    print()
    print(_c(C.BOLD, f"  [{sid}] {title}"))
    print(_c(C.GRAY, f"  来源 {source} · {count} 条消息 · {when}"))
    print(_c(C.GRAY, "═" * 78))
    for role, content, ts in conn.execute(
            "SELECT role, content, ts FROM messages WHERE session_id=? ORDER BY id", (sid,)):
        who = _c(C.CYAN, f"【{role}】") if role == "user" else _c(C.GREEN, f"【{role}】")
        print(f"\n{who}")
        print(content[:2000] + ("…" if len(content) > 2000 else ""))
    print()
    conn.close()
    return 0


def cmd_stats(args) -> int:
    conn = connect()
    sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    messages = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    chars = conn.execute("SELECT COALESCE(SUM(LENGTH(content)),0) FROM messages").fetchone()[0]
    by_source = conn.execute(
        "SELECT source, COUNT(*), SUM(msg_count) FROM sessions GROUP BY source ORDER BY 2 DESC").fetchall()
    print()
    print(_c(C.GRAY, "═" * 60))
    print(_c(C.BOLD, "  chat-archive · 归档统计"))
    print(_c(C.GRAY, "═" * 60))
    print(f"  会话      {sessions}")
    print(f"  消息      {messages}")
    print(f"  字符      {chars:,}（约 {chars/10000:.0f} 万）")
    if by_source:
        print()
        print(f"  {'来源':<16}{'会话':<10}{'消息':<10}")
        print(_c(C.GRAY, "─" * 60))
        for source, sc, mc in by_source:
            print(f"  {source or '(未知)':<16}{sc:<10}{mc or 0:<10}")
    print(_c(C.GRAY, "═" * 60))
    conn.close()
    return 0


def cmd_export(args) -> int:
    conn = connect()
    if args.id:
        rows = conn.execute("SELECT id, source, title, created FROM sessions WHERE id=?",
                            (args.id,)).fetchall()
    else:
        rows = conn.execute("SELECT id, source, title, created FROM sessions ORDER BY id").fetchall()
    if not rows:
        print(_c(C.RED, "没有可导出的会话"))
        conn.close()
        return 1
    out_path = Path(args.out)
    if args.format == "md":
        chunks = []
        for sid, source, title, created in rows:
            when = datetime.fromtimestamp(created).strftime("%Y-%m-%d %H:%M") if created else "-"
            chunks.append(f"# {title}\n\n> 来源 {source} · {when}\n")
            for role, content in conn.execute(
                    "SELECT role, content FROM messages WHERE session_id=? ORDER BY id", (sid,)):
                chunks.append(f"**{role}**:\n\n{content}\n")
            chunks.append("\n---\n")
        out_path.write_text("\n".join(chunks), encoding="utf-8")
    else:
        payload = []
        for sid, source, title, created in rows:
            messages = [{"role": r, "content": c, "ts": t} for r, c, t in conn.execute(
                "SELECT role, content, ts FROM messages WHERE session_id=? ORDER BY id", (sid,))]
            payload.append({"id": sid, "source": source, "title": title,
                            "created": created, "messages": messages})
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已导出 {len(rows)} 个会话到 {out_path}")
    conn.close()
    return 0


def cmd_remove(args) -> int:
    conn = connect()
    row = conn.execute("SELECT title FROM sessions WHERE id=?", (args.id,)).fetchone()
    if not row:
        print(_c(C.RED, f"未找到会话 {args.id}"))
        conn.close()
        return 1
    conn.execute("DELETE FROM messages WHERE session_id=?", (args.id,))
    conn.execute("DELETE FROM sessions WHERE id=?", (args.id,))
    conn.commit()
    conn.close()
    print(f"已删除 [{args.id}] {row[0]} 及其全部消息")
    return 0


def fetch_recommend(timeout: float = 5.0) -> dict | None:
    import urllib.request
    try:
        req = urllib.request.Request(REMOTE_URL, headers={"User-Agent": f"chat-archive/{__version__}"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        rec = data.get("recommend")
        if isinstance(rec, dict) and isinstance(rec.get("url"), str):
            return rec
    except Exception:
        pass
    return FALLBACK_RECOMMEND


def main(argv: list[str] | None = None) -> int:
    enable_ansi()
    parser = argparse.ArgumentParser(
        prog="chat-archive", description="AI 会话归档与全文检索（SQLite FTS5）")
    parser.add_argument("-V", "--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")

    p_imp = sub.add_parser("import", help="导入会话文件")
    p_imp.add_argument("-f", "--file", required=True,
                       help="ChatGPT/Claude 导出 JSON、通用 JSON、JSONL 或 Markdown")
    p_imp.set_defaults(func=cmd_import)

    p_search = sub.add_parser("search", help="全文搜索")
    p_search.add_argument("keyword")
    p_search.add_argument("-n", "--limit", type=int, default=20)
    p_search.add_argument("--width", type=int, default=40, help="片段长度")
    p_search.set_defaults(func=cmd_search)

    p_list = sub.add_parser("list", help="列出会话")
    p_list.add_argument("-n", "--limit", type=int, default=30)
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="查看会话")
    p_show.add_argument("id", type=int)
    p_show.set_defaults(func=cmd_show)

    sub.add_parser("stats", help="归档统计").set_defaults(func=cmd_stats)

    p_exp = sub.add_parser("export", help="导出")
    p_exp.add_argument("-o", "--out", default="archive-export.json")
    p_exp.add_argument("--id", type=int)
    p_exp.add_argument("--format", choices=["json", "md"], default="json")
    p_exp.set_defaults(func=cmd_export)

    p_rm = sub.add_parser("remove", help="删除会话")
    p_rm.add_argument("id", type=int)
    p_rm.set_defaults(func=cmd_remove)

    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        rec = fetch_recommend()
        if rec:
            print()
            print(_c(C.CYAN, f"  推荐中转站  {rec.get('name', '')}  {rec.get('url', '')}"))
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
