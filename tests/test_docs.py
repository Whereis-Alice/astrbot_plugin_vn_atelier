"""文档离线检查：链接、工具清单及 README 内容边界。"""

from __future__ import annotations

import ast
import json
import re
import unittest
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]


def headings(text: str) -> set[str]:
    """生成文档所用普通标题的 GitHub 锚点，保留中文。"""
    anchors: set[str] = set()
    for heading in re.findall(r"^#{1,6} (.+)$", text, re.MULTILINE):
        base = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
        anchor, index = base, 0
        while anchor in anchors:
            index += 1
            anchor = f"{base}-{index}"
        anchors.add(anchor)
    return anchors


class DocumentationTests(unittest.TestCase):
    def test_local_links_images_and_anchors_exist(self):
        checked = 0
        for path in DOCS:
            text = re.sub(r"```.*?```", "", path.read_text(encoding="utf-8"), flags=re.S)
            targets = re.findall(r"\[[^\]\n]+\]\(([^)\s]+)\)", text)
            targets += re.findall(r'(?:src|href)="([^"]+)"', text)
            for target in targets:
                url = urlsplit(target)
                if url.scheme or url.netloc:
                    continue
                with self.subTest(document=path.name, link=target):
                    linked = (path.parent / unquote(url.path)).resolve() if url.path else path
                    self.assertTrue(linked.is_relative_to(ROOT), target)
                    self.assertTrue(linked.exists(), target)
                    if url.fragment and linked.suffix == ".md":
                        self.assertIn(
                            unquote(url.fragment), headings(linked.read_text(encoding="utf-8"))
                        )
                    checked += 1
        self.assertGreater(checked, 50)

    def test_readme_stays_short_and_without_release_history(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(text.splitlines()), 120)
        self.assertNotRegex(text, r"(?i)\bv\d+\.\d+(?:\.\d+)*\b|升级须知|本次更新|新增：")
        self.assertIn("[更新日志](CHANGELOG.md)", text)
        self.assertIn("(docs/llm-tools.md)", text)
        self.assertIn("(LICENSE)", text)
        self.assertIn("PyuraMazo/astrbot_plugin_galgame_box", text)

    def test_tool_documentation_matches_implementation(self):
        tree = ast.parse((ROOT / "core/service/llm_tools.py").read_text(encoding="utf-8"))
        constants = {}
        for node in tree.body:
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                constants[node.target.id] = ast.literal_eval(node.value)
            elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                constants[node.targets[0].id] = ast.literal_eval(node.value)
        text = (ROOT / "docs/llm-tools.md").read_text(encoding="utf-8")
        documented = re.findall(r"^\| `(vn_atelier_\w+)` \|", text, re.MULTILINE)
        self.assertCountEqual(documented, constants["TOOL_NAMES"])
        self.assertEqual(constants["_KEYWORD_SCHEMA"]["required"], ["keyword"])
        self.assertIn(f"最多返回 {constants['_MAX_ITEMS']} 个结果", text)
        self.assertIn(f"{constants['_MAX_CHARS']} 字符", text)
        self.assertIn("limitSetting.enableLlmTools", text)
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        setting = schema["limitSetting"]["items"]["enableLlmTools"]
        self.assertIs(setting["default"], False)
        self.assertNotIn("随机作品", setting["hint"])
        self.assertIn("docs/llm-tools.md", setting["hint"])


if __name__ == "__main__":
    unittest.main()
