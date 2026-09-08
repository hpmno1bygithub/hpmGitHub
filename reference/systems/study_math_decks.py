# -*- coding: utf-8 -*-
"""FIX86: persistent, finite, without-replacement arithmetic bags.

A cycle belongs to a teaching pool (multiply, single +/-, mixed +/-), not
one 2+2+2 intermission. Commutative reversals share an id. Only an EMPTY
pool may refill. There are no retry-until-random-is-new loops and no file,
UI, thread, or network operations in this module.
"""

DECK_VERSION = 1
RECENT_LIMIT = 24
POOL_KEYS = ('multiply', 'single_add', 'single_subtract',
             'mixed_add', 'mixed_subtract')
POOL_LABELS = {
    'multiply': '九九乘法',
    'single_add': '個位數加法',
    'single_subtract': '個位數減法',
    'mixed_add': '兩位數加個位數',
    'mixed_subtract': '兩位數減個位數',
}
POOL_INFO = {
    'multiply': ('multiply', None, '×'),
    'single_add': ('arithmetic', 'single', '+'),
    'single_subtract': ('arithmetic', 'single', '−'),
    'mixed_add': ('arithmetic', 'mixed', '+'),
    'mixed_subtract': ('arithmetic', 'mixed', '−'),
}


def _catalog():
    result = {name: {} for name in POOL_KEYS}
    for name, (kind, level, op) in POOL_INFO.items():
        for a in (range(10, 100) if level == 'mixed' else range(1, 10)):
            for b in range(1, 10):
                if (op == '−' and a < b) or (op == '+' and a + b > 99):
                    continue
                if (kind == 'multiply' or name == 'single_add') and a > b:
                    continue  # 3 x 4 / 4 x 3 and 3 + 4 / 4 + 3 are ONE fact.
                result[name]['%d:%s:%d' % (a, op, b)] = (a, b)
    return result


CATALOG = _catalog()
POOL_SIZES = {key: len(CATALOG[key]) for key in POOL_KEYS}


def identify_question(question):
    """Return (pool, canonical id), or None for an out-of-domain legacy task."""
    if not isinstance(question, dict):
        return None
    a, b, op = question.get('a'), question.get('b'), question.get('op')
    if type(a) is not int or type(b) is not int:
        return None
    if not 1 <= a <= 99 or not 1 <= b <= 9:
        return None
    if op == '×' and a <= 9:
        pool = 'multiply'
        a, b = sorted((a, b))
    elif op in ('+', '−'):
        pool = ('single' if a <= 9 else 'mixed') + ('_add' if op == '+' else '_subtract')
        if pool == 'single_add':
            a, b = sorted((a, b))
    else:
        return None
    qid = '%d:%s:%d' % (a, op, b)
    return (pool, qid) if qid in CATALOG[pool] else None


class MathQuestionDecks:
    """Owned by StudyChallenge; export/restore participates in its transaction."""
    def __init__(self):
        self.pools = {key: {'remaining': [], 'cycle': 0, 'recent': []}
                      for key in POOL_KEYS}

    def _refill(self, key, rng):
        pool = self.pools[key]
        if pool['remaining']:
            return
        pool['remaining'] = list(CATALOG[key])
        rng.shuffle(pool['remaining'])
        pool['cycle'] += 1

    def draw(self, key, rng):
        if key not in CATALOG:
            raise ValueError('Unknown math teaching pool: %r' % (key,))
        pool = self.pools[key]
        self._refill(key, rng)
        remaining = pool['remaining']
        # At a cycle boundary avoid the last facts of the previous cycle,
        # considering ONLY unused ids in the current cycle. A bounded scan,
        # not rejection sampling, so an exhausted bank cannot hang the UI.
        recent = set(pool['recent'])
        index = len(remaining) - 1
        if remaining[index] in recent:
            for j in range(index - 1, -1, -1):
                if remaining[j] not in recent:
                    index = j
                    break
        qid = remaining.pop(index)
        pool['recent'] = (pool['recent'] + [qid])[-RECENT_LIMIT:]
        a, b = CATALOG[key][qid]
        kind, level, op = POOL_INFO[key]
        if (key == 'multiply' or key == 'single_add') and rng.randrange(2):
            a, b = b, a
        result = {'kind': kind, 'a': a, 'b': b, 'op': op}
        if level is not None:
            result['level'] = level
        return result

    def issued_count(self, key):
        p = self.pools[key]
        return ((p['cycle'] - 1) * POOL_SIZES[key]
                + POOL_SIZES[key] - len(p['remaining'])) if p['cycle'] else 0

    def choose_single_operator(self, rng):
        # Equal-size single +/- pools are scheduled evenly. Their combined
        # 90-fact cycle also completes before either operator starts repeating.
        # This avoids a run of same operators after repeatedly restarting Pyto.
        plus = self.issued_count('single_add')
        minus = self.issued_count('single_subtract')
        if plus != minus:
            return '+' if plus < minus else '−'
        return rng.choice(('+', '−'))

    def export(self):
        return {'version': DECK_VERSION,
                'pools': {key: {'remaining': list(p['remaining']),
                                'cycle': p['cycle'], 'recent': list(p['recent'])}
                          for key, p in self.pools.items()}}

    def _reserve_legacy(self, key, qid, rng):
        p = self.pools[key]
        # Initialize once, never refill if the imported history exhausted it.
        if p['cycle'] == 0:
            self._refill(key, rng)
        if qid in p['remaining']:
            p['remaining'].remove(qid)
        p['recent'] = (p['recent'] + [qid])[-RECENT_LIMIT:]

    def restore(self, raw, rng, recent_equations=(), tasks=()):
        """Keep valid remaining lists EXACTLY; migrate only missing pools.

        FIX85 retained at most 48 arithmetic equations and the last four tasks,
        NOT lifetime history. Exclude what is actually available, without
        inventing older progress or replacing a pending exercise. Empty modern
        lists mean exhausted, not missing. Unknown/duplicate ids are discarded;
        malformed optional scheduler data cannot grant answers or unlock a gate.
        """
        raw = raw if isinstance(raw, dict) and raw.get('version') == DECK_VERSION else {}
        source = raw.get('pools', {})
        source = source if isinstance(source, dict) else {}
        self.__init__()
        migrated = set()
        for key in POOL_KEYS:
            saved = source.get(key)
            if not isinstance(saved, dict) or not isinstance(saved.get('remaining'), list):
                migrated.add(key)
                continue
            p = self.pools[key]
            cycle = saved.get('cycle', 0)
            cycle = min(1000000000, max(0, cycle)) if type(cycle) is int else 0
            p['remaining'] = list(dict.fromkeys(q for q in saved['remaining'][:POOL_SIZES[key] * 2]
                if isinstance(q, str) and q in CATALOG[key]))
            history = saved.get('recent', [])
            p['recent'] = [q for q in history[-RECENT_LIMIT:]
                if isinstance(q, str) and q in CATALOG[key]] if isinstance(history, list) else []
            p['cycle'] = max(1, cycle) if p['remaining'] or p['recent'] else cycle

        legacy = []
        if isinstance(recent_equations, (list, tuple)):
            for equation in recent_equations[-48:]:
                if not isinstance(equation, str) or len(equation) > 12:
                    continue
                try:
                    a, op, b = equation.split(':')
                    legacy.append({'a': int(a), 'op': op, 'b': int(b)})
                except (TypeError, ValueError):
                    continue
        if isinstance(tasks, (list, tuple)):
            legacy.extend(tasks[:20])  # FIX97: up to 10 multiplication + 10 arithmetic
        for question in legacy:
            identity = identify_question(question)
            if identity is not None and identity[0] in migrated:
                self._reserve_legacy(identity[0], identity[1], rng)

    def progress(self, key):
        """Diagnostic progress includes all math tasks reserved for this gate."""
        p = self.pools[key]
        return {'label': POOL_LABELS[key], 'cycle': p['cycle'],
                'total': POOL_SIZES[key], 'remaining': len(p['remaining']),
                'issued': POOL_SIZES[key] - len(p['remaining']) if p['cycle'] else 0}
