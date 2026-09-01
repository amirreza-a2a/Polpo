# ============================================================
#  interfaces/api/__main__.py
#  CLI Entry Point for Running FastAPI REST Server
# ============================================================

import os
import uvicorn

if __name__ == "__main__":
    host = os.environ.get("API_HOST", "0.0.0.0")
    port = int(os.environ.get("API_PORT", "8000"))
    uvicorn.run("interfaces.api.app:app", host=host, port=port, reload=False)
