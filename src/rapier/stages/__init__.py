"""Built-in stages. Importing this package registers them with the runtime."""
from . import echo  # noqa: F401  (registers the "echo" stage)

__all__ = ["echo"]
