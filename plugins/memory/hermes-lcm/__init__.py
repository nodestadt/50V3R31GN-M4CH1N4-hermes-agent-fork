"""
Hermes-LCM: Lossless Context Management MemoryProvider.

Prevents context window degradation via SQLite-based DAG summaries.
Replaces "forgetful" session buffers with persistent, queryable summaries.
"""

from .provider import HermesLCMMemoryProvider


def _load_plugin_config() -> dict:
    """Load Hermes-LCM config from ~/.hermes/config.yaml."""
    from hermes_constants import get_hermes_home
    config_path = get_hermes_home() / "config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml
        with open(config_path, encoding="utf-8-sig") as f:
            all_config = yaml.safe_load(f) or {}
        # Config can be under memory.lcm_* or plugins.hermes-lcm
        memory_config = all_config.get("memory", {})
        lcm_config = {
            "db_path": memory_config.get("lcm_db_path"),
            "max_context_tokens": memory_config.get("max_context_tokens", 128000),
            "summary_interval": memory_config.get("summary_interval", 32000),
        }
        # Filter None values
        return {k: v for k, v in lcm_config.items() if v is not None}
    except Exception:
        return {}


def register(ctx):
    """Register the Hermes-LCM memory provider."""
    config = _load_plugin_config()
    provider = HermesLCMMemoryProvider(config=config)
    ctx.register_memory_provider(provider)
