# -*- coding: utf-8 -*-
"""FIX80 persistent, bounded question selection. No UI / network dependencies.

The caller owns the remaining-id bag. We NEVER refill it here or put an issued
question back. Similarity/coverage preferences only rank ids still in that bag.
"""
SYMBOLS = 'ㄅㄆㄇㄈㄉㄊㄋㄌㄍㄎㄏㄐㄑㄒㄓㄔㄕㄖㄗㄘㄙㄚㄛㄜㄝㄞㄟㄠㄡㄢㄣㄤㄥㄦㄧㄨㄩ'
TONES = ('1', '2', '3', '4', '5')
RECENT_LIMIT = 12
ANSWER_WINDOW = 8
TYPE_LABELS = {'reading':'生活閱讀', 'riddle':'特徵猜字', 'word':'詞語填空',
               'sequence':'順序與數量', 'contrast':'相反詞語', 'function':'語句與輕聲'}


def tone_of(syllable):
    if syllable.startswith('˙'):
        return '5'
    return {'ˊ':'2', 'ˇ':'3', 'ˋ':'4'}.get(syllable[-1:], '1')


class QuestionSelection:
    def __init__(self, bank):
        self.features = {}
        for q in bank:
            self.features[q['id']] = {
                'answer':q['answer'], 'char':q['reference_text'][q['tokens'].index('__')],
                'symbols':tuple(c for c in SYMBOLS if c in q['answer']),
                'tone':tone_of(q['answer']), 'category':q.get('category', '日常閱讀'),
                'type':q.get('question_type', 'reading')}
        self.recent = []
        self.symbol_counts = dict.fromkeys(SYMBOLS, 0)
        self.tone_counts = dict.fromkeys(TONES, 0)
        self.solved_symbol_counts = dict.fromkeys(SYMBOLS, 0)
        self.solved_tone_counts = dict.fromkeys(TONES, 0)

    @staticmethod
    def _prefer(candidates, predicate):
        matching = [qid for qid in candidates if predicate(qid)]
        return matching or candidates

    def choose(self, deck, last_id, rng):
        """Remove exactly one id, even when every preference has to relax."""
        if not deck:
            raise ValueError('出題佇列不可為空')
        candidates = list(deck)
        candidates = self._prefer(candidates, lambda q:q != last_id)
        recent_ids = set(self.recent)
        candidates = self._prefer(candidates, lambda q:q not in recent_ids)
        recent = [self.features[q] for q in self.recent[-ANSWER_WINDOW:]]
        sounds = {f['answer'] for f in recent}
        chars = {f['char'] for f in recent}
        candidates = self._prefer(candidates, lambda q:self.features[q]['answer'] not in sounds
                                  and self.features[q]['char'] not in chars)
        if self.recent:
            previous = self.features[self.recent[-1]]
            candidates = self._prefer(candidates, lambda q:self.features[q]['category'] != previous['category'])
            candidates = self._prefer(candidates, lambda q:self.features[q]['type'] != previous['type'])

        def score(qid):
            f = self.features[qid]
            # Practice all answer symbols, not merely text/distractor symbols.
            unseen = sum(self.symbol_counts[s] == 0 for s in f['symbols'])
            missing_tone = self.tone_counts[f['tone']] == 0
            exposure = sum(self.symbol_counts[s] for s in f['symbols']) / max(1, len(f['symbols']))
            return unseen, missing_tone, -exposure, -self.tone_counts[f['tone']], rng.random()

        qid = max(candidates, key=score)
        deck.remove(qid)
        self.record_shown(qid)
        return qid

    def record_shown(self, qid):
        f = self.features[qid]
        for s in f['symbols']:
            self.symbol_counts[s] += 1
        self.tone_counts[f['tone']] += 1
        self.recent = (self.recent + [qid])[-RECENT_LIMIT:]

    def record_solved(self, qid):
        f = self.features[qid]
        for s in f['symbols']:
            self.solved_symbol_counts[s] += 1
        self.solved_tone_counts[f['tone']] += 1

    def export(self):
        return {'version':1, 'catalog_ids':list(self.features), 'recent':list(self.recent),
                'symbol_counts':dict(self.symbol_counts), 'tone_counts':dict(self.tone_counts),
                'solved_symbol_counts':dict(self.solved_symbol_counts),
                'solved_tone_counts':dict(self.solved_tone_counts)}

    @staticmethod
    def _counts(data, keys):
        data = data if isinstance(data, dict) else {}
        return {k:min(1000000000, max(0, data[k]))
                if type(data.get(k)) is int else 0 for k in keys}

    def restore(self, raw, deck, last_id):
        """Add NEW bank ids, preserving pending questions and consumed old ids.

        FIX79 has no selection metadata: its catalog is precisely zy001..zy040.
        This migration does not reinsert those already drawn in the old bag.
        Optional malformed scheduling metadata never bypasses the study gate.
        """
        raw = raw if isinstance(raw, dict) else {}
        catalog = raw.get('catalog_ids')
        if not isinstance(catalog, list):
            known = {'zy%03d' % i for i in range(1, 41)}
        else:
            known = {q for q in catalog if isinstance(q, str)}
        present = set(deck)
        deck.extend(q for q in self.features if q not in known and q not in present and q != last_id)
        recent = raw.get('recent', [])
        self.recent = [q for q in recent[-RECENT_LIMIT:] if isinstance(q,str) and q in self.features] \
            if isinstance(recent, list) else []
        self.symbol_counts = self._counts(raw.get('symbol_counts'), SYMBOLS)
        self.tone_counts = self._counts(raw.get('tone_counts'), TONES)
        self.solved_symbol_counts = self._counts(raw.get('solved_symbol_counts'), SYMBOLS)
        self.solved_tone_counts = self._counts(raw.get('solved_tone_counts'), TONES)
        if not self.recent and last_id in self.features:
            if not raw:
                self.record_shown(last_id)
            else:
                self.recent = [last_id]
