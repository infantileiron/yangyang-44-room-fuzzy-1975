"""换装前后两份固化结果的混响簇承接与试音归属对照。

承接规则：新旧混响簇按中心距升序配对，中心距不超过阈值且双方均未配对时
一对一承接，新簇沿用旧号。共同试音逐条判定：新簇承接了其旧簇为「沿用」；
新旧中心接近（不超过阈值）但归属对不上为「待核」，不计入归属变化；其余
为「变更」。仅在一侧出现的试音记为「新增」或「移除」，同样不算归属变化。
"""
from collections import Counter
from math import hypot

KEPT, CHANGED, PENDING, ADDED, REMOVED = '沿用', '变更', '待核', '新增', '移除'
_STATUS_ORDER = {CHANGED: 0, PENDING: 1, ADDED: 2, REMOVED: 3, KEPT: 4}


def _label_key(label):
    *_, tail = str(label).split(' ')
    return (0, int(tail)) if tail.isdigit() else (1, str(label))


def _centers(clusters):
    return {item['label']: (float(item['low']), float(item['mid'])) for item in clusters}


def _distance(first, second):
    return hypot(first[0] - second[0], first[1] - second[1])


def inherit_pairs(baseline_centers, candidate_centers, tolerance):
    """新旧簇一对一承接：中心距不超过阈值的候选配对按距离升序先到先得。"""
    candidates = [(_distance(b_center, c_center), b_label, c_label)
                  for b_label, b_center in baseline_centers.items()
                  for c_label, c_center in candidate_centers.items()]
    candidates.sort(key=lambda item: (item[0], _label_key(item[1]), _label_key(item[2])))
    matched_b, matched_c, inheritance = set(), set(), {}
    for distance, b_label, c_label in candidates:
        if distance > tolerance:
            break
        if b_label in matched_b or c_label in matched_c:
            continue
        inheritance[c_label] = (b_label, round(distance, 4))
        matched_b.add(b_label)
        matched_c.add(c_label)
    return inheritance


def compare(baseline, candidate, tolerance):
    """对照两份固化结果。两侧均需提供 clusters（簇中心）与 rows（逐行明细）。"""
    b_centers = _centers(baseline['clusters'])
    c_centers = _centers(candidate['clusters'])
    inheritance = inherit_pairs(b_centers, c_centers, tolerance)
    b_rows = {row['ticket']: row for row in baseline['rows']}
    c_rows = {row['ticket']: row for row in candidate['rows']}
    auditions = []
    for ticket in sorted(set(b_rows) | set(c_rows)):
        before, after = b_rows.get(ticket), c_rows.get(ticket)
        if before and after:
            b_label, c_label = before['group'], after['group']
            distance = _distance(b_centers[b_label], c_centers[c_label])
            if inheritance.get(c_label, (None,))[0] == b_label:
                status = KEPT
            elif distance <= tolerance:
                status = PENDING
            else:
                status = CHANGED
            room, shown = after['room'], round(distance, 4)
        elif after:
            b_label, c_label, status, room, shown = None, after['group'], ADDED, after['room'], None
        else:
            b_label, c_label, status, room, shown = before['group'], None, REMOVED, before['room'], None
        auditions.append({'ticket': ticket, 'room': room, 'baseline': b_label,
                          'candidate': c_label, 'status': status, 'distance': shown})
    auditions.sort(key=lambda item: (_STATUS_ORDER[item['status']], item['ticket']))
    pairs = []
    for c_label, (b_label, distance) in sorted(inheritance.items(), key=lambda item: _label_key(item[0])):
        common = [ticket for ticket, row in b_rows.items() if row['group'] == b_label and ticket in c_rows]
        kept = sum(c_rows[ticket]['group'] == c_label for ticket in common)
        pairs.append({'baseline': b_label, 'candidate': c_label, 'distance': distance,
                      'common': len(common), 'kept': int(kept)})
    inherited_b = {b_label for b_label, _ in inheritance.values()}
    counts = Counter(item['status'] for item in auditions)
    return {
        'tolerance': tolerance,
        'pairs': pairs,
        'new_clusters': [{'label': label, 'low': c_centers[label][0], 'mid': c_centers[label][1]}
                         for label in sorted(set(c_centers) - set(inheritance), key=_label_key)],
        'retired_clusters': [{'label': label, 'low': b_centers[label][0], 'mid': b_centers[label][1]}
                             for label in sorted(set(b_centers) - inherited_b, key=_label_key)],
        'summary': {'changed': counts[CHANGED], 'pending': counts[PENDING], 'kept': counts[KEPT],
                    'added': counts[ADDED], 'removed': counts[REMOVED],
                    'changed_rooms': sorted({item['room'] for item in auditions if item['status'] == CHANGED})},
        'auditions': auditions,
    }
