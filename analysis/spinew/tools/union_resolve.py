"""Resolve git conflict blocks by keeping both sides (ours, then theirs).

For Markdown lists and tables where each branch adds its own entries. A blank line is put
between the sides when ours ends with a list item and theirs starts a new one (CHANGELOG).
"""
import sys
for path in sys.argv[1:]:
    out, mode, ours, theirs = [], None, [], []
    for line in open(path, encoding="utf-8").read().split("\n"):
        if line.startswith("<<<<<<< "):
            mode, ours, theirs = "ours", [], []
        elif line.startswith("=======") and mode == "ours":
            mode = "theirs"
        elif line.startswith(">>>>>>> ") and mode == "theirs":
            out.extend(ours)
            if ours and theirs and ours[-1].strip() and theirs[0].startswith("- ") and path.endswith("CHANGELOG.md"):
                out.append("")
            out.extend(theirs)
            mode = None
        elif mode == "ours":
            ours.append(line)
        elif mode == "theirs":
            theirs.append(line)
        else:
            out.append(line)
    assert mode is None, path
    open(path, "w", encoding="utf-8").write("\n".join(out))
    print("resolved", path)
