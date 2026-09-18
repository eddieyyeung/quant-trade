"""Platform runtime — the FastAPI app and its assembly."""

from quant_trade.runtime.app import create_platform_app

__all__ = ["create_platform_app"]
