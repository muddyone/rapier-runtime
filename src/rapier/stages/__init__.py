"""Built-in stages. Importing this package registers them with the runtime."""
from . import echo  # noqa: F401  (registers the "echo" stage)
from . import frame  # noqa: F401  (registers the front-door "frame" stage)
from . import resolver  # noqa: F401  (registers the five Resolver stages)
from . import proposer  # noqa: F401  (registers spark / pattern_lock / cut)

__all__ = ["echo", "frame", "resolver", "proposer"]
