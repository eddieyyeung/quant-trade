"""Platform entry point: ``python -m quant_trade``.

This starts a server. It is deliberately not a command dispatcher — research
operations are invoked from the web UI or from ``quant_trade.services``, never
from an argv-parsed subcommand.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field

DEFAULT_HOST = "127.0.0.1"
"""Loopback only. The platform has no authentication, so it must not be
reachable from the network unless the operator opts in with ``--host``."""

DEFAULT_PORT = 9555

_APP_FACTORY = "quant_trade.runtime.app:create_platform_app"


@dataclass
class Options:
    """Parsed command-line options."""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    reload: bool = False
    unknown: list[str] = field(default_factory=list)


def parse_args(args: list[str]) -> Options:
    """Parse ``--host`` / ``--port`` / ``--reload``; collect anything else."""
    opts = Options()
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--reload":
            opts.reload = True
            i += 1
        elif arg == "--port" and i + 1 < len(args):
            opts.port = _parse_port(args[i + 1], opts)
            i += 2
        elif arg.startswith("--port="):
            opts.port = _parse_port(arg.split("=", 1)[1], opts)
            i += 1
        elif arg == "--host" and i + 1 < len(args):
            opts.host = args[i + 1]
            i += 2
        elif arg.startswith("--host="):
            opts.host = arg.split("=", 1)[1]
            i += 1
        else:
            opts.unknown.append(arg)
            i += 1
    return opts


def _parse_port(value: str, opts: Options) -> int:
    try:
        return int(value)
    except ValueError:
        opts.unknown.append(f"--port={value}")
        return opts.port


def main(argv: list[str] | None = None) -> int:
    """Start the platform. Returns a process exit code."""
    opts = parse_args(list(sys.argv[1:] if argv is None else argv))

    if opts.unknown:
        print(
            "本平台不接受子命令或未知参数: " + " ".join(opts.unknown) + "\n"
            "研究操作请通过 Web 界面发起；此处只负责启动服务。\n"
            "用法: python -m quant_trade [--host HOST] [--port PORT] [--reload]",
            file=sys.stderr,
        )
        return 2

    try:
        import uvicorn
    except ImportError:
        print("缺少 web 依赖，请先执行: uv sync --extra web", file=sys.stderr)
        return 1

    print(f"量化研究平台: http://{opts.host}:{opts.port}")
    if opts.reload:
        uvicorn.run(_APP_FACTORY, factory=True, host=opts.host, port=opts.port, reload=True, log_level="info")
    else:
        from quant_trade.runtime.app import create_platform_app

        uvicorn.run(create_platform_app(), host=opts.host, port=opts.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
