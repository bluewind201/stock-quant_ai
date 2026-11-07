"""Deterministic relevance and near-duplicate filtering; no sentiment prediction."""
import re


def terms(text):
    return set(re.findall(r'[a-z가-힣]{2,}', text.lower()))


def relevant(article, name, symbol):
    title = article.get('title', '')
    return bool((name and name.casefold() in title.casefold()) or symbol in title)


def duplicate(article, previous):
    words = terms(article.get('title', ''))
    if not words:
        return True
    for old in previous:
        # A re-used headline with a new adverse development must reach the AI.
        adverse = ('취소', '해지', '중단', '철회', '실패', '적자', '횡령', '상장폐지', '유상증자', '리콜')
        if any(token in article.get('title', '') and token not in old for token in adverse):
            continue
        other = terms(old)
        if other and len(words & other) / min(len(words), len(other)) >= .65:
            return True
    return False
