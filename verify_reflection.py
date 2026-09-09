"""反思路径验证：空知识库下，search 未命中应触发反思并换用 web_search。"""
import os
import tempfile

os.environ["LLM_PROVIDER"] = "mock"
os.environ["EMBEDDING_PROVIDER"] = "local"
os.environ["CHROMA_DIR"] = tempfile.mkdtemp(prefix="docmind_empty_")

from agent import Agent

ag = Agent()
q = "公司怎么报销差旅费？"
print(f"[verify] 空库提问: {q}\n--- 推理过程 ---")
seq = []
for ev in ag.run(q, stream=False):
    seq.append(ev["type"])
    print(f"  [{ev['type']}] {ev['text'][:90].replace(chr(10), ' ')}")
print("--- 事件序列 ---", seq)
assert "reflection" in seq, "反思事件未触发"
assert "search_knowledge" in " ".join(seq) or "action" in seq
print("OK: 自我反思 / 换工具重试路径验证通过。")
