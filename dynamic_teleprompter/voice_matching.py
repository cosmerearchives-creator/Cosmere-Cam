"""Conservative script-guided speech matching, independent of UI and recognizer."""
from difflib import SequenceMatcher

CONTRACTIONS = {"don't": ['do','not'], "dont": ['do','not'], "can't": ['can','not'],
                "cannot": ['can','not'], "won't": ['will','not'], "i'm": ['i','am'],
                "it's": ['it','is'], "that's": ['that','is'], "we're": ['we','are'],
                "you're": ['you','are'], "i've": ['i','have'], "i'll": ['i','will']}


def expand(words):
    result, positions = [], []
    for i, word in enumerate(words):
        tokens = CONTRACTIONS.get(word, [word.replace("'", '')])
        result.extend(tokens)
        positions.extend([i+1] * len(tokens))
    return result, positions


def match_progress(script, recent, cursor):
    if not recent or not script:
        return cursor
    start = max(0, cursor-8)
    segment, positions = expand(script[start:min(len(script), cursor+32)])
    heard, _ = expand(recent[-14:])
    # Correct only long, unambiguous near-spellings from the local script.
    vocabulary = set(segment)
    fixed = []
    for word in heard:
        candidates = [candidate for candidate in vocabulary if len(word)>=5 and len(candidate)>=5
                      and SequenceMatcher(None,word,candidate).ratio() >= .82]
        fixed.append(candidates[0] if word not in vocabulary and len(candidates)==1 else word)
    if cursor < len(script):
        next_tokens, _ = expand([script[cursor]])
        if fixed[-len(next_tokens):] == next_tokens:
            # A single word can advance only the immediate next script word.
            if len(fixed) == len(next_tokens):
                return cursor+1
    best = cursor
    # Recent suffixes let a correction/repeated sentence recover from stale history.
    for length in range(2, min(12,len(fixed))+1):
        tail = fixed[-length:]
        blocks = [b for b in SequenceMatcher(None,tail,segment,autojunk=False).get_matching_blocks() if b.size]
        if not blocks or blocks[-1].a+blocks[-1].size != len(tail):
            continue
        count = sum(b.size for b in blocks)
        endpoint = start+positions[blocks[-1].b+blocks[-1].size-1]
        distance = endpoint-cursor
        required = 3 if distance>6 else 2
        if count>=required and count/length>=.65 and 0<distance<=18:
            # Do not join unrelated matches across an arbitrarily large gap.
            span = blocks[-1].b+blocks[-1].size-blocks[0].b
            if span <= length+3:
                best=max(best,endpoint)
    return best
