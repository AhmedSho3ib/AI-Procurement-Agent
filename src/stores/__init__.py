"""Stores layer: adapters for every replaceable external system.

Each subpackage follows the same Interface -> Factory -> Provider pattern:

* ``llm``       - language model providers (Gemini today, anything tomorrow)
* ``search``    - search providers (Tavily today)
* ``scraping``  - product page scrapers (ScrapeGraphAI today)
* ``reporting`` - report renderers (Jinja2 today)

Business logic depends on the interfaces only. Subpackages are imported
explicitly (``from src.stores.search import SearchFactory``) so the import graph
stays acyclic and obvious.
"""

__all__: list[str] = []
