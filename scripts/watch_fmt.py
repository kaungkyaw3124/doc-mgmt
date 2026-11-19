import sys, json

def short(s, n=110):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[:n - 1] + "…"

for line in sys.stdin:
    try:
        e = json.loads(line)
    except Exception:
        if line.strip():
            print(line.rstrip())
        continue
    t = e.get("type")
    if t == "assistant":
        for c in e["message"].get("content", []):
            if c.get("type") == "text" and c["text"].strip():
                print("\n💬 " + short(c["text"], 300))
            elif c.get("type") == "tool_use":
                i, n = c.get("input", {}), c["name"]
                if n in ("Edit", "Write", "Read", "NotebookEdit"):
                    print("  ✏️  %s: %s" % (n, i.get("file_path")))
                elif n == "Bash":
                    print("  ▶  " + short(i.get("command")))
                elif n in ("Grep", "Glob"):
                    print("  🔎 %s: %s" % (n, short(i.get("pattern"))))
                else:
                    print("  🔧 " + n)
    elif t == "user":
        content = e["message"].get("content")
        for c in content if isinstance(content, list) else []:
            if c.get("type") == "tool_result" and c.get("is_error"):
                print("  ❌ " + short(c.get("content"), 160))
    elif t == "result":
        print("\n✅ session finished: " + short(e.get("result", ""), 300))
