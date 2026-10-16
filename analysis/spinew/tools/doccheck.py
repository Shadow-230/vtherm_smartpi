"""Doc checks: same heading count/levels in en/fr/pl, every relative link and #anchor exists."""
import re, sys, unicodedata
from pathlib import Path
root = Path(sys.argv[1]); docs = root / "documentation"
def anchors(text):
    seen = {}; out = set(); fence = False
    for line in text.splitlines():
        if line.startswith("```"): fence = not fence; continue
        if fence: continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if not m: continue
        a = m.group(2).strip().lower()
        a = "".join(c for c in a if c.isalnum() or c in " -_")
        a = a.replace(" ", "-")
        n = seen.get(a, 0); seen[a] = n + 1
        out.add(a if n == 0 else f"{a}-{n}")
    return out
def heads(text):
    fence=False; r=[]
    for line in text.splitlines():
        if line.startswith("```"): fence = not fence; continue
        if not fence and re.match(r"^#{1,6}\s", line): r.append(len(line.split()[0]))
    return r
bad = 0
names = sorted({p.name for p in (docs/"en").glob("*.md")})
for n in names:
    h = {l: heads((docs/l/n).read_text()) for l in ("en","fr","pl") if (docs/l/n).exists()}
    if len({tuple(v) for v in h.values()}) != 1:
        print("HEADINGS differ", n, {k: len(v) for k,v in h.items()}); bad += 1
for l in ("en","fr","pl"):
    for p in (docs/l).glob("*.md"):
        t = p.read_text()
        for m in re.finditer(r"\]\(([^)\s]+)\)", t):
            link = m.group(1)
            if link.startswith(("http", "mailto")): continue
            path, _, frag = link.partition("#")
            target = p if not path else (p.parent / path)
            if not target.exists(): print("MISSING", p.relative_to(root), link); bad += 1; continue
            if frag and target.suffix == ".md" and frag not in anchors(target.read_text()):
                print("ANCHOR", p.relative_to(root), link); bad += 1
print("doc problems:", bad)
