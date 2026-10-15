"""Root convenience entrypoint: start the API with ``python main.py``.

Equivalent to ``python -m src.main`` and to the documented
``uvicorn src.main:create_app --factory`` invocation. It exists so the service can
be started from the project root without remembering uvicorn flags.
"""

from __future__ import annotations

import uvicorn

from src.main import create_app
from src.utils.config import get_settings

__all__ = ["main"]


def main() -> None:
    """Build the application and serve it with uvicorn."""
    settings = get_settings()
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
