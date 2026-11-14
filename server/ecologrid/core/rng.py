"""決定論的な乱数。Region ごとに独立した seed 付き RNG を持つ。"""
import hashlib
import random
import uuid


def make_rng(*parts) -> random.Random:
    digest = hashlib.sha256(":".join(str(p) for p in parts).encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def new_id(rng: random.Random) -> str:
    """乱数列から GUID(v4 形式)を作る。同じ seed・同じ順序なら同じ ID。"""
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))
