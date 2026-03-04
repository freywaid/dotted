"""
Optional dependency helpers for CLI format support.
"""


def require_yaml():
    """
    Import and return the yaml module, or exit with a clear message.
    """
    try:
        import yaml
        return yaml
    except ImportError:
        raise SystemExit(
            "PyYAML is required for YAML support. "
            "Install it with: pip install dotted-notation[yaml]"
        )


def require_toml_reader():
    """
    Import and return a TOML reader module.
    Uses tomllib (stdlib 3.11+), falls back to tomli.
    """
    try:
        import tomllib
        return tomllib
    except ImportError:
        pass
    try:
        import tomli
        return tomli
    except ImportError:
        raise SystemExit(
            "tomli is required for TOML support on Python < 3.11. "
            "Install it with: pip install dotted-notation[toml]"
        )


def require_toml_writer():
    """
    Import and return a TOML writer module.
    Uses tomli_w if available.
    """
    try:
        import tomli_w
        return tomli_w
    except ImportError:
        raise SystemExit(
            "tomli_w is required for TOML output. "
            "Install it with: pip install dotted-notation[toml]"
        )
