"""Map mode (SPARRING spec v1.4 §3.7): the Proposer's field as the terminal artifact.

map_frame -> map_spark -> map_pattern_lock -> map_attack -> map_cite
[-> map_support] -> map_gate -> compose_map. The Cut and the Resolver do not run.
"""
from . import attack, cite, compose, field, frame, gate  # noqa: F401  (register the stages)

MAP_STAGES = ("map_frame", "map_spark", "map_pattern_lock", "map_attack", "map_cite",
              "map_support", "map_gate", "compose_map")
