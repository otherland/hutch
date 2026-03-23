"""Agent name generation: random AdjectiveNoun combinations."""
from __future__ import annotations
import random, re

ADJECTIVES = (
    "Red", "Blue", "Green", "Gold", "Silver", "Amber", "Coral", "Cyan",
    "Jade", "Sage", "Teal", "Violet", "Cobalt", "Copper", "Bronze",
    "Emerald", "Misty", "Foggy", "Frosty", "Swift", "Quiet", "Bold",
    "Calm", "Bright", "Dark", "Wild", "Silent", "Gentle", "Dusty", "Rustic",
)
NOUNS = (
    "Stone", "Lake", "Creek", "Pond", "Bear", "Mountain", "Castle",
    "River", "Forest", "Valley", "Canyon", "Meadow", "Island", "Cliff",
    "Cave", "Ridge", "Peak", "Brook", "Glen", "Grove", "Fox", "Wolf",
    "Hawk", "Eagle", "Owl", "Falcon", "Raven", "Otter", "Tower", "Bridge",
)

_VALID: frozenset[str] = frozenset(
    f"{a}{n}".lower() for a in ADJECTIVES for n in NOUNS
)
_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_CTX_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def generate() -> str:
    return random.choice(ADJECTIVES) + random.choice(NOUNS)


def is_valid_name(name: str) -> bool:
    return name.lower() in _VALID


def is_valid_thread_id(tid: str) -> bool:
    return bool(tid and _KEY_RE.fullmatch(tid.strip()))


def is_valid_context_key(key: str) -> bool:
    return bool(key and _CTX_KEY_RE.fullmatch(key.strip()))


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-") or "project"
