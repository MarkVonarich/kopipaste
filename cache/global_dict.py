# cache/global_dict.py — v2025.08.18-01 
__version__ = "2025.08.18-01"

from typing import Dict, List, Tuple
import logging
from db.queries import load_global_alias_rows, bump_global_alias
from utils.text import norm_text

log = logging.getLogger("finbot.cache")

# GLOBAL_CACHE: norm -> [(category, type, popularity)]
GLOBAL_CACHE: Dict[str, List[Tuple[str,str,int]]] = {}

def load_global_cache():
    global GLOBAL_CACHE
    rows = load_global_alias_rows()
    cache: Dict[str, List[Tuple[str,str,int]]] = {}
    for n, c, t, p in rows:
        cache.setdefault(n, []).append((c, t, int(p or 0)))
    for k in list(cache.keys()):
        cache[k] = sorted(cache[k], key=lambda x: x[2], reverse=True)
    GLOBAL_CACHE = cache
    log.info("GLOBAL_CACHE loaded: %d keys", len(GLOBAL_CACHE))

def bump_global_popularity(merch: str, typ: str, category: str, inc: int = 1):
    nm = norm_text(merch)
    bump_global_alias(nm, typ, category, inc)
    GLOBAL_CACHE.setdefault(nm, [])
    found = False
    for i,(c,t,p) in enumerate(GLOBAL_CACHE[nm]):
        if c==category and t==typ:
            GLOBAL_CACHE[nm][i] = (c,t,p+inc)
            found = True
            break
    if not found:
        GLOBAL_CACHE[nm].append((category, typ, inc))
    GLOBAL_CACHE[nm] = sorted(GLOBAL_CACHE[nm], key=lambda x: x[2], reverse=True)

def global_suggestions(merch: str) -> List[Tuple[str,str]]:
    nm = norm_text(merch)
    if nm in GLOBAL_CACHE:
        return [(c,t) for (c,t,_) in GLOBAL_CACHE[nm]][:4]
    if not GLOBAL_CACHE:
        load_global_cache()
    # простая эвристика без RapidFuzz: точного попадания может не быть
    return [] 
