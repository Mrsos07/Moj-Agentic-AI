"""اسم بديل: الخادم نُقل إلى system/mcps/image/server.py (المجلد الرئيسي لخوادم النظام)."""
import sys as _sys

from app.core.system_loader import mcp_module as _mcp_module

_sys.modules[__name__] = _mcp_module('image')
