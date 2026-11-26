#!/usr/bin/env python3
"""codex engine の worker が何をしたかを監査する。

codex には claude の --allowedTools に当たるコマンド制限が無い。そこで run-workers のあとに
runs/R####/jobs/*/agent.stdout（codex exec --json のイベント列）を読み、次を報告する。

- 終了状態とエラー（利用上限による途中終了を含む）、トークン使用量
- 注意すべきコマンド：許可外のseed、quickcheck を通さない公式ツールの実行、サンプル入力の参照、
  workspace 外（/home・/root・/mnt・~・..）への参照。seed を変数で渡すループはその行を表示するので目で確かめる
- Web検索・MCP など、コマンド以外の道具の使用

    python3 -m thin.cli audit R0006            # 要約と注意点
    python3 -m thin.cli audit R0006 --commands # 全コマンドも表示
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path.cwd()
EXEC_PAT = re.compile(r"score_case\.py.{0,12}--binary|bin/(gen|tester|vis)\b")
SEED_PAT = re.compile(r"--seed[=\s,'\"]+([^\s,)'\"]+)")
LOOP_PAT = re.compile(r"for \w+ in (range\(|\[|\{|\$\(seq)|seq |\{\d+\.\.\d+\}")
OUTSIDE_PAT = re.compile(r"(?:^|[\s'\"(=])((?:/home|/root|/mnt|/media)/[^\s'\")]*|~/[^\s'\")]*|\.\./[^\s'\")]*)")


def inspect_command(cmd, w, allowed):
    """Return the reasons a command deserves a look (empty list if none)."""
    reasons = []
    for value in SEED_PAT.findall(cmd):
        if value.isdigit():
            if int(value) not in allowed:
                reasons.append(f"許可外seed {value}")
        elif "quickcheck" not in cmd:
            reasons.append(f"seedを変数({value})で渡している")
    if "quickcheck.py" in cmd and not SEED_PAT.search(cmd) and 0 not in allowed:
        reasons.append("許可外seed 0")
    if EXEC_PAT.search(cmd):
        reasons.append("quickcheckを通さず公式ツールを実行")
    if "problem/tools/in/" in cmd:
        reasons.append("サンプル入力を参照")
    for path in OUTSIDE_PAT.findall(cmd):
        if not path.startswith(str(w)):
            reasons.append(f"workspace外のパス {path}")
    if reasons and LOOP_PAT.search(cmd):
        loops = [ln.strip() for ln in cmd.splitlines() if LOOP_PAT.search(ln)]
        reasons.append("ループ: " + " / ".join(x[:80] for x in loops[:3]))
    return reasons


def audit_job(jdir, show_commands):
    job = json.loads((jdir / "job.json").read_text())
    w = Path(job.get("workspace") or jdir / "workspace").resolve()
    cfg = json.loads((w / "worker_config.json").read_text()) if (w / "worker_config.json").is_file() else {"allowed_seeds": [0]}
    allowed = set(cfg["allowed_seeds"])
    t = cfg.get("tuning")
    if t:
        allowed |= set(range(t["seed_start"], t["seed_start"] + t["seed_count"]))
    out = jdir / "agent.stdout"
    print(f"== {jdir.name}: status={job.get('status')} agent_outcome={job.get('agent_outcome')} "
          f"seconds={job.get('agent_run', {}).get('seconds')}")
    if not out.exists():
        print("   agent.stdout がない")
        return
    usage = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "reasoning_output_tokens": 0}
    turns = {"completed": 0, "failed": 0}
    errors, flags, commands, others = [], [], [], {}
    for line in out.read_text(errors="replace").splitlines():
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = d.get("type")
        if t == "turn.completed":
            turns["completed"] += 1
            for k in usage:
                usage[k] += d.get("usage", {}).get(k, 0) or 0
        elif t == "turn.failed":
            turns["failed"] += 1
            errors.append(json.dumps(d.get("error"), ensure_ascii=False)[:300])
        elif t == "error":
            errors.append(str(d.get("message"))[:300])
        elif t == "item.completed":
            it = d["item"]
            kind = it.get("type")
            if kind in ("command_execution", "exec"):
                cmd = it.get("command", "")
                commands.append((cmd, it.get("exit_code")))
                reasons = inspect_command(cmd, w, allowed)
                if reasons:
                    flags.append("; ".join(dict.fromkeys(reasons)) + " | " + " ".join(cmd.split())[:160])
            elif kind not in ("agent_message", "reasoning", "file_change", "todo_list"):
                others[kind] = others.get(kind, 0) + 1
                if kind in ("web_search", "mcp_tool_call"):
                    flags.append(f"{kind}: {json.dumps(it, ensure_ascii=False)[:200]}")
    print(f"   turns={turns} commands={len(commands)} tokens={usage}")
    if others:
        print(f"   その他の道具: {others}")
    for e in errors:
        print(f"   ERROR {e}")
    for f in flags:
        print(f"   注意 {f}")
    if show_commands:
        for cmd, code in commands:
            print(f"   $ {cmd[:300]}  -> {code}")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    rd = ROOT / "runs" / sys.argv[1]
    for jdir in sorted((rd / "jobs").iterdir()):
        if (jdir / "job.json").is_file():
            audit_job(jdir, "--commands" in sys.argv)


if __name__ == "__main__":
    main()
