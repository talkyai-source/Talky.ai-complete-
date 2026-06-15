"""AI Options endpoints — provider catalog, voice preview, tenant config.

Public surface mirrors the previous single-file `ai_options.py`. The
following symbols are re-exported because they are imported directly
from this package by other modules (notably `campaigns.py`):

  - router
  - _english_google_voices
  - _fetch_tenant_config
  - _get_deepgram_voices_for_current_key
  - get_elevenlabs_voices_for_current_key  (passthrough from elevenlabs_catalog)
"""
from __future__ import annotations

from fastapi import APIRouter

# Load environment variables once when the package imports — preserves the
# behaviour of the previous module-level `load_dotenv()` call.
from app.core.dotenv_compat import load_dotenv

load_dotenv()

# Sub-modules
from . import benchmark as _benchmark_mod
from . import clone as _clone_mod
from . import config as _config_mod
from . import preview as _preview_mod
from . import providers as _providers_mod
from . import testing as _testing_mod

# Re-exports for external callers (campaigns.py)
from ._catalog import (
    _english_google_voices,
    _get_deepgram_voices_for_current_key,
)
from ._shared import _fetch_tenant_config
from app.infrastructure.tts.elevenlabs_catalog import (
    get_elevenlabs_voices_for_current_key,
)

router = APIRouter(prefix="/ai-options", tags=["AI Options"])
router.include_router(_providers_mod.router)
router.include_router(_preview_mod.router)
router.include_router(_testing_mod.router)
router.include_router(_config_mod.router)
router.include_router(_benchmark_mod.router)
router.include_router(_clone_mod.router)


__all__ = [
    "router",
    # Used by campaigns.py
    "_english_google_voices",
    "_fetch_tenant_config",
    "_get_deepgram_voices_for_current_key",
    "get_elevenlabs_voices_for_current_key",
]
