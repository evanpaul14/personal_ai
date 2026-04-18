import os
import sys
import base64
import shutil
import subprocess
import tempfile

from config import config

RUN_PYTHON_SCHEMA = {
    "type": "function",
    "function": {
        "name": "run_python",
        "description": (
            "Execute a small Python script on the server. "
            "numpy, pandas, and matplotlib are available. "
            "No internet access. File access restricted to the sandbox directory. "
            "stdout/stderr are returned. If matplotlib is used, the plot is returned as an image."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python source code to execute"},
            },
            "required": ["code"],
        },
    },
}

# Injected at the top of every script. sandbox_dir and py_prefix are
# filled in at runtime so the path checks are concrete strings.
_PREAMBLE_TEMPLATE = """\
import sys, os, builtins as _builtins

# ── Matplotlib (pre-import with Agg so plt.show() saves to file) ────────────
try:
    import matplotlib as _mpl
    _mpl.use('Agg')
    import matplotlib.pyplot as _plt_internal
    def _capture_show(*a, **kw):
        _plt_internal.savefig('/tmp/_sandbox_plot.png', bbox_inches='tight', dpi=150)
        _plt_internal.close('all')
    _plt_internal.show = _capture_show
    import matplotlib.pyplot as plt
    plt.show = _capture_show
except ImportError:
    pass

# ── Network import block ──────────────────────────────────────────────────────
_net_blocked = frozenset({{'socket','subprocess','pty','ftplib','imaplib','smtplib',
                           'telnetlib','requests','httpx','aiohttp'}})
_real_import = _builtins.__import__
_import_depth = [0]
def _safe_import(name, *args, **kwargs):
    root = name.split('.')[0]
    if _import_depth[0] == 0 and root in _net_blocked:
        raise ImportError(f"Module '{{name}}' is not allowed in sandbox")
    _import_depth[0] += 1
    try:
        return _real_import(name, *args, **kwargs)
    finally:
        _import_depth[0] -= 1
_builtins.__import__ = _safe_import

# ── Filesystem access control ────────────────────────────────────────────────
_SANDBOX_DIR  = {sandbox_dir!r}
_PLOT_FILE    = '/tmp/_sandbox_plot.png'
# Paths that libraries (numpy, pandas, matplotlib) need to read
_PY_PREFIXES  = ({py_prefix!r}, {exec_prefix!r})

def _is_allowed_read(path):
    path = os.path.realpath(os.path.abspath(path))
    if path.startswith(_SANDBOX_DIR):
        return True
    if path == _PLOT_FILE:
        return True
    for p in _PY_PREFIXES:
        if path.startswith(p):
            return True
    # Allow /tmp for matplotlib font cache etc.
    if path.startswith('/tmp/'):
        return True
    return False

def _is_allowed_write(path):
    path = os.path.realpath(os.path.abspath(path))
    return path.startswith(_SANDBOX_DIR) or path == _PLOT_FILE or path.startswith('/tmp/')

_real_open = _builtins.open
def _safe_open(file, mode='r', *args, **kwargs):
    # Non-file-path objects (e.g. int fds) pass through
    if not isinstance(file, (str, bytes, os.PathLike)):
        return _real_open(file, mode, *args, **kwargs)
    path = os.fspath(file)
    writing = any(c in mode for c in ('w', 'a', 'x', '+'))
    if writing and not _is_allowed_write(path):
        raise PermissionError(f"Write access denied outside sandbox: {{path}}")
    if not writing and not _is_allowed_read(path):
        raise PermissionError(f"Read access denied outside sandbox: {{path}}")
    return _real_open(file, mode, *args, **kwargs)
_builtins.open = _safe_open

# Patch os.open as well
_real_os_open = os.open
def _safe_os_open(path, flags, *args, **kwargs):
    writing = bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
    if writing and not _is_allowed_write(path):
        raise PermissionError(f"Write access denied outside sandbox: {{path}}")
    if not writing and not _is_allowed_read(path):
        raise PermissionError(f"Read access denied outside sandbox: {{path}}")
    return _real_os_open(path, flags, *args, **kwargs)
os.open = _safe_os_open

# ── End preamble ─────────────────────────────────────────────────────────────
"""

def run_python(code: str) -> dict:
    tmpdir = tempfile.mkdtemp(prefix="sandbox_")
    plot_path = "/tmp/_sandbox_plot.png"

    if os.path.exists(plot_path):
        os.remove(plot_path)

    try:
        preamble = _PREAMBLE_TEMPLATE.format(
            sandbox_dir=os.path.realpath(tmpdir),
            py_prefix=os.path.realpath(sys.prefix),
            exec_prefix=os.path.realpath(sys.exec_prefix),
        )

        script_path = os.path.join(tmpdir, "script.py")
        with open(script_path, "w") as f:
            f.write(preamble + code)

        env = {
            "PATH": "/usr/bin:/usr/local/bin",
            "HOME": tmpdir,
            "TMPDIR": tmpdir,
            "PYTHONPATH": "",
            "MPLCONFIGDIR": tmpdir,   # matplotlib writes font cache here
        }

        result = subprocess.run(
            [sys.executable, script_path],
            capture_output=True,
            text=True,
            timeout=config.SANDBOX_TIMEOUT,
            env=env,
            cwd=tmpdir,
        )

        stdout = result.stdout[:8192]
        stderr = result.stderr[:2048]

        image_b64 = None
        if os.path.exists(plot_path):
            with open(plot_path, "rb") as f:
                image_b64 = base64.b64encode(f.read()).decode()
            os.remove(plot_path)

        return {
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": result.returncode,
            "image": image_b64,
        }

    except subprocess.TimeoutExpired:
        return {"error": f"Execution timed out after {config.SANDBOX_TIMEOUT}s", "exit_code": -1}
    except Exception as e:
        return {"error": str(e), "exit_code": -1}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
