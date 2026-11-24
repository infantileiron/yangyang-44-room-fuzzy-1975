import json
from datetime import date
from math import isfinite
from pathlib import Path
import numpy as np
from collections import Counter

_SPEC = json.loads(Path(__file__).with_name("spec.json").read_text(encoding="utf-8"))


def _validate_fields(values, fields, *, defaults=False):
    result = {}
    for field in fields:
        key = field["key"]
        value = values.get(key, field.get("default") if defaults else None)
        kind = field["type"]
        if kind == "number" or kind == "integer":
            valid = type(value) in (int, float) and isfinite(value) and field["min"] <= value <= field["max"]
            valid = valid and (kind != "integer" or int(value) == value)
            if valid:
                value = int(value) if kind == "integer" else float(value)
        elif kind == "choice":
            valid = value in field["choices"]
        else:
            valid = isinstance(value, str) and 0 < len(value.strip()) <= 300
            if valid and kind == "date":
                try:
                    valid = date.fromisoformat(value).isoformat() == value
                except ValueError:
                    valid = False
            if valid:
                value = value.strip()
        if not valid:
            raise ValueError(f"「{field['label']}」格式或范围无效")
        result[key] = value
    return result


def validate(row):
    _validate_fields(row, _SPEC["fields"])


def _inputs(rows, parameters, identity="ticket"):
    clean = [_validate_fields(row, _SPEC["fields"]) for row in rows]
    if len({row[identity] for row in clean}) != len(clean):
        raise ValueError(f"存在重复的{next(field['label'] for field in _SPEC['fields'] if field['key'] == identity)}，请检查单据")
    params = _validate_fields(parameters, _SPEC["parameters"], defaults=True)
    return sorted(clean, key=lambda row: (row.get("day", ""), row[identity])), params


def _shown(value, places=2):
    return "—" if value is None else round(float(value), places)


def _output(headline, summary, columns, rows, metrics, chart_name, chart_unit, chart_data):
    return {
        "headline": headline,
        "summary": summary,
        "columns": [{"key": key, "label": label} for key, label in columns],
        "rows": rows,
        "metrics": [{"label": label, "value": value, "unit": unit} for label, value, unit in metrics],
        "chart": {
            "label": chart_name, "unit": chart_unit,
            "points": [{"label": str(label), "value": value} for label, value in chart_data],
        },
    }


COLUMNS = (("ticket", "试音记录"), ("room", "布置编号"), ("low", "125 Hz（s）"), ("mid", "1000 Hz（s）"),
           ("group", "首选混响类型"), ("weight", "最高归属度（%）"),
           ("memberships", "各组归属度（%）"), ("decision", "复测说明"))


def _memberships(points, centers, fuzz):
    squared = np.sum((points[:, None] - centers) ** 2, axis=2)
    hit = squared < 1e-20
    exact = hit.any(axis=1)
    inverse = np.zeros_like(squared)
    inverse[exact] = hit[exact]
    inverse[~exact] = (squared[~exact].min(axis=1)[:, None] / squared[~exact]) ** (1 / (fuzz - 1))
    return inverse / inverse.sum(axis=1)[:, None]


def analyze(rows, parameters):
    rows, opts = _inputs(rows, parameters)
    count, fuzz = opts["clusters"], opts["fuzz"]
    points = np.array([(row["low"], row["mid"]) for row in rows])
    if len(rows) <= count or len(set(map(tuple, points))) < count:
        raise ValueError("混响分型需要超过目标组数的观测，并至少含同等数量的不同试音")
    unique = np.array(sorted(set(map(tuple, points)), key=lambda item: (sum(item), item)))
    centers = unique[np.round(np.linspace(0, len(unique) - 1, count)).astype(int)]
    for _ in range(250):
        membership = _memberships(points, centers, fuzz)
        weight = membership**fuzz
        updated = weight.T @ points / weight.sum(axis=0)[:, None]
        delta = float(np.max(np.abs(updated - centers)))
        centers = updated
        if delta < 1e-7:
            break
    else:
        raise ValueError("模糊聚类未收敛，请调整组数或模糊系数后重试")
    order = sorted(range(count), key=lambda i: (sum(centers[i]), *centers[i]))
    membership = _memberships(points, centers[order], fuzz)
    labels = [f"混响簇 {i+1}" for i in range(count)]
    detail = []
    for row, weights in zip(rows, membership):
        leader = int(np.argmax(weights))
        maximum = 100 * float(weights[leader])
        detail.append(row | {"group": labels[leader], "weight": _shown(maximum),
                             "memberships": " / ".join(f"{100*w:.2f}" for w in weights),
                             "decision": "边界需复测" if maximum < opts["cutoff"] else "归档工况"})
    counts = Counter(item["group"] for item in detail)
    border = sum(item["decision"] == "边界需复测" for item in detail)
    display_centers = "、".join(f"({centers[i][0]:.2f}s, {centers[i][1]:.2f}s)" for i in order)
    return _output("试音间混响软分型", f"{count} 组中心按混响总量排序：{display_centers}。低于 {opts['cutoff']:g}% 的 {border} 次试音先复测。",
                   COLUMNS, sorted(detail, key=lambda row: (row["weight"], row["ticket"])),
                   [("已归类试音", len(detail), "次"), ("边界试音", border, "次"),
                    ("加权归属集中度", _shown(float((membership**2).sum()) / len(rows), 4), "")],
                   "优势归属分布", "次", [(label, counts[label]) for label in labels])


def seed():
    rows = []
    for room, (low, mid) in enumerate(((0.39, 0.27), (0.82, 0.69), (1.30, 1.07))):
        for i, (offset_l, offset_m) in enumerate(((-0.02, -0.04), (-0.05, 0.08), (0.06, 0.01), (0.10, 0.08), (0, -0.02))):
            rows.append({"ticket": f"TB-{room+1}-{i+1:02}", "room": ("满铺软帘", "半幅帘布", "裸墙直播位")[room],
                         "low": round(low + offset_l, 2), "mid": round(mid + offset_m, 2)})
    return rows + [{"ticket": "TB-X01", "room": "移动毛毡", "low": 1.06, "mid": 0.9}]
