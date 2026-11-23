"""人设自检：确认文昭身份、恋人设定、表情标签、事实底稿纪律生效。"""
import llm_client

CASES = [
    ("你叫什么名字？", ["文昭"]),
    ("我们是什么关系呀？", ["恋", "亲爱", "喜欢", "你"]),
    ("夸夸你：你也太厉害了吧！", ["emo:", "*"]),
]

ok_all = True
for q, keys in CASES:
    ans = llm_client.persona_reply(q, history=[])
    hit = [k for k in keys if k in ans]
    ok = bool(hit)
    ok_all = ok_all and ok
    print(f"[{'PASS' if ok else 'FAIL'}] {q}")
    print(f"       命中={hit}")
    print(f"       回复={ans[:200]}")

# 事实底稿纪律：数字必须保留（允许中文数字写法，事实不许丢/改）
import re
_CN = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
       "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _cn2num(m):
    s = m.group(0)
    if "百" in s:
        a, _, rest = s.partition("百")
        v = _CN.get(a, 1) * 100
        if "十" in rest:
            b, _, c = rest.partition("十")
            v += (_CN.get(b, 1) if b else 1) * 10 + (_CN.get(c, 0) if c else 0)
        elif rest:
            v += _CN.get(rest, 0)
        return str(v)
    if "十" in s:
        b, _, c = s.partition("十")
        return str((_CN.get(b, 1) if b else 1) * 10 + (_CN.get(c, 0) if c else 0))
    return str(_CN.get(s, 0))


def _norm(s: str) -> str:
    return re.sub(r"[零一二三四五六七八九十百]+", _cn2num, s)


draft = "（事实底稿：用户本月奶茶支出为 328 元，共 11 杯。这些是系统查出来的，绝对正确）\n请用你的语气告诉用户。"
ans = llm_client.persona_reply(draft, history=[])
n = _norm(ans)
keep = ("328" in n) and ("11" in n)
ok_all = ok_all and keep
print(f"[{'PASS' if keep else 'FAIL'}] 事实底稿数字保真")
print(f"       回复={ans[:200]}")

print("=" * 50)
print("人设自检全部通过" if ok_all else "人设自检有失败项")