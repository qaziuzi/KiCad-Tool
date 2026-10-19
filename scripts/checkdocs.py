"""
checkdocs.py - fail when the README and the code disagree.

Documentation written alongside a change is usually right. The dangerous kind
is documentation invalidated by a *later* change, because nothing flags it:
the MCP section once claimed conventions never reached chat, which was true
when written and became wrong two commits later when get_conventions was added.

Run before publishing:

    python scripts/checkdocs.py

Deliberately not part of mcp_server.py --selftest: that answers "is my install
working", and a stale README is not a user's problem.
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
from typing import List, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
README = os.path.join(ROOT, "README.md")

NUMBER_WORDS = {
    1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
    7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven",
    12: "twelve", 13: "thirteen", 14: "fourteen", 15: "fifteen",
    16: "sixteen", 17: "seventeen", 18: "eighteen", 19: "nineteen",
    20: "twenty",
}

problems: List[str] = []
notes: List[str] = []


def fail(msg: str) -> None:
    problems.append(msg)


def ok(msg: str) -> None:
    notes.append(msg)


def live_server() -> Tuple[str, List[str]]:
    """Ask the real server what it is and what it offers."""
    msgs = [
        {"jsonrpc": "2.0", "id": 0, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    ]
    proc = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "mcp_server.py")],
        input="\n".join(json.dumps(m) for m in msgs) + "\n",
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    version, tools = "", []
    for line in proc.stdout.splitlines():
        try:
            result = json.loads(line).get("result", {})
        except json.JSONDecodeError:
            continue
        if "serverInfo" in result:
            version = result["serverInfo"]["version"]
        if "tools" in result:
            tools = [t["name"] for t in result["tools"]]
    if not tools:
        fail("could not get a tool list from mcp_server.py: "
             + (proc.stderr.strip().splitlines() or ["no output"])[-1][:160])
    return version, tools


def check_tools(readme: str, version: str, tools: List[str]) -> None:
    for name in tools:
        if name not in readme:
            fail(f"tool {name!r} is not mentioned in README.md")
    if all(t in readme for t in tools):
        ok(f"all {len(tools)} tool names documented")

    # A tool named in the README that the server no longer has.
    for match in re.findall(r"`([a-z][a-z0-9_]{3,})`", readme):
        if match.endswith("_py") or "." in match:
            continue
        if match in ("get_conventions",) or match in tools:
            continue
        if match.startswith(("find_", "generate_", "promote_", "list_",
                             "make_", "read_", "footprint_", "symbol_",
                             "lcsc_", "kicad_")) and match not in tools:
            fail(f"README mentions {match!r}, which the server does not provide")

    count = len(tools)
    word = NUMBER_WORDS.get(count, str(count))
    if word not in readme.lower():
        fail(f"README never says the tool count in words ({word!r}); "
             "it is easy to leave stale")
    else:
        ok(f"tool count stated as {word!r}")

    for other, other_word in NUMBER_WORDS.items():
        if other == count:
            continue
        if re.search(rf"\b{other_word}\s+tools\b", readme, re.I):
            fail(f"README still says {other_word!r} tools; there are {count}")
        if re.search(rf"\b{other}\s+tools\b", readme):
            fail(f"README still says '{other} tools'; there are {count}")

    if f"{count} tools advertised" not in readme:
        fail(f"the --selftest sample in README should show "
             f"'{count} tools advertised'")
    else:
        ok("--selftest sample matches the real tool count")

    if version and version not in readme:
        fail(f"README does not show the current server version {version}")
    elif version:
        ok(f"server version {version} shown")


def check_files(readme: str) -> None:
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT,
                             capture_output=True, text=True).stdout.split()
    missing = []
    for path in tracked:
        if not path.endswith((".py", ".md")):
            continue
        if os.path.basename(path) in ("README.md",):
            continue
        if os.path.basename(path) not in readme:
            missing.append(path)
    if missing:
        fail("not listed in the README Files table: " + ", ".join(missing))
    else:
        ok("every tracked script and doc appears in the README")


def check_flags(readme: str) -> None:
    install = io.open(os.path.join(ROOT, "install.py"), encoding="utf-8").read()
    flags = set(re.findall(r'ap\.add_argument\("(--[a-z-]+)"', install))
    undocumented = sorted(f for f in flags if f not in readme and f not in install.split('"""')[1])
    if undocumented:
        fail("install.py flags documented nowhere: " + ", ".join(undocumented))
    else:
        ok(f"all {len(flags)} install.py flags documented")


def main() -> int:
    if not os.path.isfile(README):
        print(f"no README at {README}")
        return 1
    readme = io.open(README, encoding="utf-8").read()

    print("Checking the README against the code\n")
    version, tools = live_server()
    if tools:
        check_tools(readme, version, tools)
    check_files(readme)
    check_flags(readme)

    for note in notes:
        print(f"  ok    {note}")
    for problem in problems:
        print(f"  STALE {problem}")

    print()
    if problems:
        print(f"{len(problems)} mismatch(es). The README is wrong, not the code —")
        print("fix the docs, or say why the code should change instead.")
        return 1
    print("README and code agree.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
