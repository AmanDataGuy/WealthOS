"""
# sandbox.py - run an uploaded Python strategy file, isolated

The file defines one function, `weights(close)`:
- `close`: a pandas DataFrame of daily closes, one column per symbol, indexed by date;
- returns a DataFrame of the same shape: the weight to hold in each stock from that day's close
  (0..1, each row summing to at most 1; the rest stays in cash). The trade fills at the next day's open.

    import pandas as pd

    def weights(close):
        held = (close.rolling(50).mean() > close.rolling(200).mean()).astype(float)
        return held.div(held.sum(axis=1).where(lambda s: s > 0), axis=0).fillna(0)

Three layers:
1. **Checked before it runs:** imports only pandas / numpy / math; no file, network, eval or introspection
   builtins; no `_private` attributes and no pandas / numpy file readers or writers (`read_*`, `to_csv`, `load` ...).
2. **Runs in its own Python process** (`-I`, an empty environment, a throwaway folder), killed after 30 s; on Linux
   also CPU, 1 GB memory, file-size and open-file limits.
3. **Its answer is checked:** the same dates and stocks, finite, 0..1, rows at most 1.

Off in production unless `WEALTHOS_ENABLE_PY_STRATEGIES=1`.
ponytail: layers 1-2 raise the bar but are not a security boundary (Python can't sandbox itself); before enabling it
for strangers in production, run the child in a no-network container (gVisor / Modal / E2B).
"""
import ast
import io
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from app.core import settings

TIMEOUT_S = 30
MAX_CODE = 50_000
ALLOWED_IMPORTS = {"pandas", "numpy", "math"}
BANNED_NAMES = {"open", "exec", "eval", "compile", "__import__", "globals", "locals", "vars", "getattr", "setattr",
                "delattr", "input", "breakpoint", "memoryview", "help", "exit", "quit", "dir", "type", "object"}
BANNED_ATTR = re.compile(r"^_|^read_|^to_(?!numpy$|frame$|dict$|list$)|^(eval|query|load|loads|save|savez|savetxt|"
                         r"loadtxt|fromfile|tofile|genfromtxt|memmap|ctypeslib|lib|testing|f2py|show_config|system)$")

# The child: load the file and the prices from its folder, call weights(close), print the answer as CSV.
RUNNER = """
import sys
import pandas as pd
close = pd.read_csv("close.csv", index_col=0, parse_dates=True)
namespace = {"__name__": "strategy"}
exec(compile(open("strategy.py", encoding="utf-8").read(), "strategy.py", "exec"), namespace)
out = namespace["weights"](close.copy())
if not isinstance(out, pd.DataFrame):
    sys.exit("weights(close) must return a pandas DataFrame")
out.reindex(index=close.index, columns=close.columns).to_csv(sys.stdout)
"""


def enabled() -> bool:
    return os.getenv("WEALTHOS_ENABLE_PY_STRATEGIES", "0" if settings.env() == "production" else "1") == "1"


# Refuse code that reaches outside pandas / numpy / math. Raises ValueError with the reason.
def check(code: str) -> None:
    if len(code) > MAX_CODE:
        raise ValueError(f"the strategy file is over {MAX_CODE // 1000} KB")
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise ValueError(f"the strategy file has a syntax error on line {e.lineno}: {e.msg}") from None
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods = [node.module or ""] if not node.level else ["(relative import)"]
        else:
            mods = []
        bad = [m for m in mods if m.split(".")[0] not in ALLOWED_IMPORTS]
        if bad:
            raise ValueError(f"line {node.lineno}: only pandas, numpy and math can be imported, not {bad[0]}")
        if isinstance(node, ast.Name) and node.id in BANNED_NAMES:
            raise ValueError(f"line {node.lineno}: '{node.id}' is not allowed in a strategy")
        if isinstance(node, ast.Attribute) and BANNED_ATTR.search(node.attr):
            raise ValueError(f"line {node.lineno}: '.{node.attr}' is not allowed in a strategy (no files, no internals)")
    if not any(isinstance(n, ast.FunctionDef) and n.name == "weights" for n in tree.body):
        raise ValueError("the strategy file must define a function weights(close)")


# Limits for the child process on Linux (Windows has no resource module; the timeout still applies).
def _limits():
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (TIMEOUT_S, TIMEOUT_S))
    resource.setrlimit(resource.RLIMIT_AS, (1 << 30, 1 << 30))
    resource.setrlimit(resource.RLIMIT_FSIZE, (1 << 20, 1 << 20))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))


# Run the file's weights(close) in a separate process and return the checked weights.
def run_weights(code: str, close: pd.DataFrame) -> pd.DataFrame:
    if not enabled():
        raise ValueError("Python strategies are switched off on this server")
    check(code)
    env = {k: os.environ[k] for k in ("PATH", "SYSTEMROOT") if k in os.environ} | {"PYTHONIOENCODING": "utf-8"}
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "strategy.py").write_text(code, encoding="utf-8")
        close.to_csv(Path(tmp, "close.csv"))
        try:
            proc = subprocess.run([sys.executable, "-I", "-c", RUNNER], cwd=tmp, env=env, capture_output=True,
                                  text=True, timeout=TIMEOUT_S, preexec_fn=_limits if os.name == "posix" else None)
        except subprocess.TimeoutExpired:
            raise ValueError(f"the strategy ran longer than {TIMEOUT_S} s and was stopped") from None
    if proc.returncode:
        last = (proc.stderr.strip().splitlines() or ["it stopped without saying why"])[-1]
        raise ValueError(f"the strategy failed: {last[:300]}")
    out = pd.read_csv(io.StringIO(proc.stdout), index_col=0, parse_dates=True).reindex(index=close.index,
                                                                                         columns=close.columns)
    values = out.to_numpy(dtype=float, na_value=0.0)
    if not np.isfinite(values).all():
        raise ValueError("weights must be numbers (no infinities)")
    if (values < -1e-9).any() or (values > 1 + 1e-9).any():
        raise ValueError("weights must be between 0 and 1 (long only, no leverage)")
    if (values.sum(axis=1) > 1 + 1e-6).any():
        raise ValueError("a day's weights add up to more than 1 (100% of the money)")
    return pd.DataFrame(values, index=close.index, columns=close.columns)
