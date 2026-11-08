"""Confirmed local ASR aliases; preserves original script spelling and word positions."""
import re,unicodedata
from pathlib import Path
from update_core import atomic_json,read_json

def tokens(text):return re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)?",unicodedata.normalize('NFKC',str(text)).casefold().replace('’',"'"))
def dictionary_word(text):return unicodedata.normalize('NFKC',str(text)).casefold().replace('’',"'").replace('‘',"'")
def load():
    try:return validate(read_json(Path.home()/'.dynamic_teleprompter_pronunciations.json'))
    except (OSError,ValueError,TypeError):return []
def save(entries):
    entries=validate(entries);atomic_json(Path.home()/'.dynamic_teleprompter_pronunciations.json',entries);return entries
def validate(entries):
    if not isinstance(entries,list) or len(entries)>1000:raise ValueError('Invalid pronunciation list.')
    result=[];owners={}
    for entry in entries:
        name=str(entry.get('name','')).strip();variants=entry.get('alternatives',[])
        if not tokens(name) or len(tokens(name))>8 or not isinstance(variants,list):raise ValueError('Enter a name and recognized examples.')
        clean=[]
        for variant in variants:
            key=tuple(tokens(variant))
            if not key or len(key)>12:raise ValueError('Use the name alone, rather than a whole sentence.')
            if key in owners and owners[key]!=tuple(tokens(name)):raise ValueError('The same recognized example is assigned to two names: '+str(variant))
            owners[key]=tuple(tokens(name));value=' '.join(key)
            if value not in clean:clean.append(value)
        if clean:result.append(dict(name=name,alternatives=clean,confirmed=True))
    return result

def mappings(entries,script):
    script_tokens=tokens(script) if isinstance(script,str) else list(script)
    def contains(phrase):return any(script_tokens[i:i+len(phrase)]==phrase for i in range(len(script_tokens)-len(phrase)+1))
    result=[]
    for entry in validate(entries or []):
        canonical=tokens(entry['name'])
        if not contains(canonical):continue
        for variant in entry['alternatives']:
            alias=tokens(variant)
            # A genuine script phrase or conflicting name must never be overwritten.
            if alias!=canonical and not contains(alias):result.append((alias,canonical,entry['name']))
    return sorted(result,key=lambda item:len(item[0]),reverse=True)

def rewrite_tokens(words,entries,script):
    rules=mappings(entries,script);result=[];i=0
    while i<len(words):
        match=next((item for item in rules if words[i:i+len(item[0])]==item[0]),None)
        if match:result+=match[1];i+=len(match[0])
        else:result.append(words[i]);i+=1
    return result

def rewrite_words(words,entries,script):
    rules=mappings(entries,script);result=[];i=0
    normalized=[' '.join(tokens(item.get('word',''))) for item in words]
    while i<len(words):
        match=next((item for item in rules if normalized[i:i+len(item[0])]==item[0]),None)
        if not match:result.append(dict(words[i]));i+=1;continue
        alias,canonical,name=match;source=words[i:i+len(alias)];start=float(source[0]['start']);end=float(source[-1]['end'])
        for n,word in enumerate(canonical):
            result.append(dict(word=word,start=start+(end-start)*n/len(canonical),end=start+(end-start)*(n+1)/len(canonical),
                conf=min(float(part.get('conf',1)) for part in source),pronunciation_alias=True,recognized_as=' '.join(normalized[i:i+len(alias)]),pronunciation_estimate=len(canonical)>1))
        i+=len(alias)
    return result
