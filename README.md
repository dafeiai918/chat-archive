# chat-archive · AI 会话归档与全文检索

**你和 AI 聊过的东西，值得留下来并且找得到。**

把散落各处的聊天记录集中存档，本地全文搜索，随时导出。

零依赖 · SQLite FTS5 · 中文友好 · 单文件

---

## 解决什么

- 换个客户端，之前的对话就找不到了
- 想找"上次讨论那个方案"——只记得几个关键词，翻不到
- 想备份 ChatGPT / Claude 的历史记录，但导出的 JSON 根本没法读

---

## 快速开始

```bash
# 导入 ChatGPT 官方导出（设置 → 数据控制 → 导出数据）
python chat_archive.py import -f conversations.json

# 全文搜索
python chat_archive.py search "向量数据库"

# 看有哪些会话
python chat_archive.py list

# 看某个会话原文
python chat_archive.py show 12

# 导出成可读的 Markdown
python chat_archive.py export -o 备份.md --format md
```

---

## 支持导入的格式

| 格式 | 说明 |
|---|---|
| **ChatGPT 官方导出** | `conversations.json`（含 `mapping` 结构），自动识别 |
| **Claude 官方导出** | `conversations.json`（含 `chat_messages`），自动识别 |
| **通用 JSON** | `{"title": "...", "messages": [{"role","content"}]}` |
| **JSONL** | 每行一个 `{"role","content"}` |
| **Markdown / TXT** | 整个文件作为一个会话存档 |

自动识别格式，不用指定。

---

## 搜索效果

```
$ python chat_archive.py search "向量数据库"

══════════════════════════════════════════════════════════════════════════════
  搜索：向量数据库  命中 3 条
══════════════════════════════════════════════════════════════════════════════

  [2] RAG 方案讨论  2026-09-14 15:22  user
  …我想用【向量数据库】存文档切片，用哪个比较好？…

  [2] RAG 方案讨论  2026-09-14 15:22  assistant
  …【向量数据库】的选择取决于规模和运维成本，小规模推荐 Chroma……
```

中文子串匹配（FTS5 trigram + LIKE 兜底），不会因为分词问题漏掉结果。

---

## 命令一览

| 命令 | 说明 |
|---|---|
| `import -f 文件` | 导入（自动识别格式） |
| `search 关键词 [-n 20]` | 全文搜索 |
| `list [-n 30]` | 列出会话 |
| `show ID` | 查看完整对话 |
| `stats` | 归档统计 |
| `export -o 文件 [--id N] [--format json\|md]` | 导出 |
| `remove ID` | 删除会话 |

---

## 数据位置

- `~/.chat-archive/archive.db` — SQLite 数据库，全部本地
- 重复导入同一份文件不会产生重复数据（按内容指纹去重）
- 可以用任何 SQLite 工具直接查

```sql
SELECT s.title, m.role, substr(m.content,1,80)
FROM messages m JOIN sessions s ON s.id = m.session_id
WHERE m.content LIKE '%提示词%' LIMIT 20;
```

---

## 说明

- 本工具只读取你自己导出的文件，不联网、不上传
- 导出功能方便你把历史记录迁移到别的工具

---

## 相关项目

| 项目 | 用途 |
|---|---|
| [prompt-vault](https://github.com/dafeiai918/prompt-vault) | 提示词模板库 |
| [token-meter](https://github.com/dafeiai918/token-meter) | Token 用量与成本看板 |
| [OneClickRelay](https://github.com/dafeiai918/oneclickrelay) | 一键把中转站写进 60+ 客户端 |

---

## License

MIT
