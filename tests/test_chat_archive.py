# -*- coding: utf-8 -*-
"""chat-archive 测试：临时库 + 样本导出文件，不发外网请求"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chat_archive as ca  # noqa: E402


def chatgpt_sample():
    return [{
        "title": "测试会话",
        "create_time": 1700000000,
        "mapping": {
            "a": {"message": {"author": {"role": "user"},
                              "content": {"parts": ["什么是向量数据库？"]},
                              "create_time": 1700000000}},
            "b": {"message": {"author": {"role": "assistant"},
                              "content": {"parts": ["向量数据库是专门存储和检索向量的数据库。"]},
                              "create_time": 1700000001}},
            "c": {"message": {"author": {"role": "system"}, "content": {"parts": ["忽略我"]}}},
        },
    }]


def claude_sample():
    return [{
        "name": "Claude会话",
        "created_at": 1700000100,
        "chat_messages": [
            {"sender": "human", "text": "帮我写个周报", "created_at": 1700000100},
            {"sender": "assistant", "text": "好的，请提供工作记录。", "created_at": 1700000101},
        ],
    }]


class ChatArchiveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = (ca.DB_PATH, ca.DATA_DIR)
        ca.DATA_DIR = Path(self.tmp.name)
        ca.DB_PATH = Path(self.tmp.name) / "archive.db"

    def tearDown(self):
        ca.DB_PATH, ca.DATA_DIR = self._orig
        self.tmp.cleanup()

    def _write(self, name, payload):
        path = Path(self.tmp.name) / name
        if isinstance(payload, str):
            path.write_text(payload, encoding="utf-8")
        else:
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    # ---------- 解析 ----------

    def test_parse_chatgpt(self):
        sessions = ca.parse_chatgpt(chatgpt_sample())
        self.assertEqual(len(sessions), 1)
        self.assertEqual(len(sessions[0]["messages"]), 2)
        self.assertEqual(sessions[0]["messages"][0]["role"], "user")
        self.assertIn("向量数据库", sessions[0]["messages"][1]["content"])

    def test_parse_claude(self):
        sessions = ca.parse_claude(claude_sample())
        self.assertEqual(len(sessions[0]["messages"]), 2)
        self.assertEqual(sessions[0]["messages"][0]["role"], "user")

    def test_parse_simple(self):
        sessions = ca.parse_simple({"title": "T", "messages": [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好呀"},
        ]})
        self.assertEqual(len(sessions[0]["messages"]), 2)

    def test_detect_chatgpt(self):
        path = self._write("conversations.json", chatgpt_sample())
        fmt, sessions = ca.detect_and_parse(path)
        self.assertEqual(fmt, "chatgpt")
        self.assertEqual(len(sessions), 1)

    def test_detect_claude(self):
        path = self._write("c.json", claude_sample())
        fmt, _ = ca.detect_and_parse(path)
        self.assertEqual(fmt, "claude")

    def test_detect_jsonl(self):
        path = Path(self.tmp.name) / "a.jsonl"
        path.write_text('{"role":"user","content":"问题一"}\n'
                        '{"role":"assistant","content":"回答一"}\n', encoding="utf-8")
        fmt, sessions = ca.detect_and_parse(path)
        self.assertEqual(fmt, "jsonl")
        self.assertEqual(len(sessions[0]["messages"]), 2)

    def test_detect_markdown(self):
        path = self.tmp.name + "/note.md"
        Path(path).write_text("# 我的笔记\n\n一些内容", encoding="utf-8")
        fmt, sessions = ca.detect_and_parse(Path(path))
        self.assertEqual(fmt, "text")
        self.assertEqual(sessions[0]["source"], "file")

    def test_detect_bad_json(self):
        path = self._write("bad.json", "这不是 json")
        with self.assertRaises(ValueError):
            ca.detect_and_parse(path)

    # ---------- 存储与检索 ----------

    def test_import_dedupe(self):
        path = self._write("conversations.json", chatgpt_sample())
        self.assertEqual(ca.main(["import", "-f", str(path)]), 0)
        self.assertEqual(ca.main(["import", "-f", str(path)]), 0)
        conn = ca.connect()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 2)
        conn.close()

    def test_search_finds_content(self):
        path = self._write("conversations.json", chatgpt_sample())
        ca.main(["import", "-f", str(path)])
        self.assertEqual(ca.main(["search", "向量数据库"]), 0)

    def test_search_miss_returns_error(self):
        path = self._write("conversations.json", chatgpt_sample())
        ca.main(["import", "-f", str(path)])
        self.assertEqual(ca.main(["search", "绝对不存在的内容xyz"]), 1)

    def test_list_show_stats(self):
        path = self._write("conversations.json", chatgpt_sample())
        ca.main(["import", "-f", str(path)])
        self.assertEqual(ca.main(["list"]), 0)
        self.assertEqual(ca.main(["show", "1"]), 0)
        self.assertEqual(ca.main(["stats"]), 0)

    def test_show_unknown_id(self):
        ca.connect().close()
        self.assertEqual(ca.main(["show", "999"]), 1)

    def test_export_json_and_md(self):
        path = self._write("conversations.json", chatgpt_sample())
        ca.main(["import", "-f", str(path)])
        out_json = Path(self.tmp.name) / "out.json"
        out_md = Path(self.tmp.name) / "out.md"
        self.assertEqual(ca.main(["export", "-o", str(out_json)]), 0)
        self.assertEqual(ca.main(["export", "-o", str(out_md), "--format", "md"]), 0)
        self.assertIn("向量数据库", out_json.read_text(encoding="utf-8"))
        self.assertIn("**user**", out_md.read_text(encoding="utf-8"))

    def test_remove(self):
        path = self._write("conversations.json", chatgpt_sample())
        ca.main(["import", "-f", str(path)])
        self.assertEqual(ca.main(["remove", "1"]), 0)
        conn = ca.connect()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)
        conn.close()
        self.assertEqual(ca.main(["remove", "1"]), 1)

    def test_import_missing_file(self):
        self.assertEqual(ca.main(["import", "-f", str(Path(self.tmp.name) / "nope.json")]), 1)

    def test_list_empty(self):
        self.assertEqual(ca.main(["list"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
