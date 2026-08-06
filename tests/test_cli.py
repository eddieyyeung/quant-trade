"""Basic smoke tests for the CLI module."""

from quant_trade.cli import main


def test_main_runs() -> None:
    """CLI entry point should not crash."""
    main()
