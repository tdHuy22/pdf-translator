"""Thử xếp hạng ứng viên thuật ngữ bằng hồi quy logistic (numpy) thay cho xếp theo tần suất: học trên các bài dev của
bench/glossary_gold.json, đánh giá trên bài test.

KẾT QUẢ 21/09/2026: KHÔNG hơn cách xếp theo tần suất (AUC dev 0,849 → 0,848; test 0,863 → 0,935 nhưng độ phủ top 40
không tăng), và cắt ứng viên theo xác suất của nó làm bảng cuối kém hơn (4b: dev 83/59 so với 91/65). Vì vậy app vẫn
xếp theo tần suất — script này giữ lại để thử lại khi có nhiều bài có nhãn hơn, vd. nhãn rút từ thuật ngữ người dùng
xoá/thêm (data/user_terms.json).

    .venv/bin/python scripts/fit_term_ranker.py             # học + đánh giá, in hệ số và chỉ số
    .venv/bin/python scripts/fit_term_ranker.py --write     # ghi thêm resources/term_ranker.json (app không đọc)

Nhãn: ứng viên khớp gold hoặc ok = 1 (gold nặng gấp đôi), còn lại = 0. Ứng viên lấy theo ngưỡng nới (loose) — mọi thứ
app có thể gửi cho LLM.
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

import numpy as np
import pymupdf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import app  # noqa: E402
from bench_glossary import entries, norm  # noqa: E402

OUT = ROOT / "resources" / "term_ranker.json"


def dataset():
    gold = {k: v for k, v in json.loads((ROOT / "bench" / "glossary_gold.json").read_text()).items()
            if not k.startswith("_")}
    rows = []
    for name, spec in gold.items():
        G, O = entries(spec["gold"]), entries(spec["ok"])
        for c in app.term_candidates(pymupdf.open(ROOT / "data" / f"{name}.pdf"), 10**6, loose=True, features=True):
            k = norm(c["term"])
            g = next((i for i, e in enumerate(G) if k in e), None)
            rows.append({"paper": name, "split": spec["split"], "term": c["term"], "kind": c["kind"], "f": c["f"],
                         "old": c["score"], "gold": g, "y": int(g is not None or any(k in e for e in O)),
                         "ngold": len(G)})
    return rows


def fit(X, y, sw, lam=1.0, steps=4000, lr=0.1):
    w, b = np.zeros(X.shape[1]), 0.0
    for _ in range(steps):
        p = 1 / (1 + np.exp(-(X @ w + b)))
        g = sw * (p - y)
        w -= lr * (X.T @ g / sw.sum() + lam * w / len(y))
        b -= lr * g.sum() / sw.sum()
    return w, b


def auc(score, y):
    order = np.argsort(score)
    ranks = np.empty(len(score))
    ranks[order] = np.arange(1, len(score) + 1)
    pos = y == 1
    return (ranks[pos].sum() - pos.sum() * (pos.sum() + 1) / 2) / (pos.sum() * (~pos).sum())


def at_k(rows, key, k):
    """Trung bình theo bài: độ chính xác (gold+ok) và độ phủ gold trong k ứng viên đầu khi xếp theo `key`."""
    precs, recs = [], []
    for paper in sorted({r["paper"] for r in rows}):
        rs = sorted([r for r in rows if r["paper"] == paper], key=key, reverse=True)
        top = rs[:k]
        precs.append(sum(r["y"] for r in top) / len(top))
        recs.append(len({r["gold"] for r in top if r["gold"] is not None}) / rs[0]["ngold"])
    return 100 * np.mean(precs), 100 * np.mean(recs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="ghi hệ số ra resources/term_ranker.json")
    ap.add_argument("--lam", type=float, default=1.0)
    args = ap.parse_args()
    app._ranker = {}   # chỉ cần đặc trưng + điểm cũ; mô hình đang có không được ảnh hưởng dữ liệu học
    rows = dataset()
    names = list(rows[0]["f"])
    X = np.array([[r["f"][n] for n in names] for r in rows])
    y = np.array([r["y"] for r in rows])
    sw = np.array([2.0 if r["gold"] is not None else 1.0 for r in rows])
    dev = np.array([r["split"] == "dev" for r in rows])
    mean, std = X[dev].mean(0), X[dev].std(0) + 1e-9
    Z = (X - mean) / std
    w, b = fit(Z[dev], y[dev], sw[dev], args.lam)
    prob = 1 / (1 + np.exp(-(Z @ w + b)))
    for r, p in zip(rows, prob):
        r["new"] = float(p)

    print(f"{len(rows)} ứng viên ({dev.sum()} dev / {(~dev).sum()} test), {y.mean():.0%} là thuật ngữ (gold+ok)\n")
    print("hệ số (đặc trưng đã chuẩn hoá):")
    for n, wi in sorted(zip(names, w), key=lambda t: -abs(t[1])):
        print(f"  {n:<9}{wi:+.2f}")
    for split, mask in (("dev", dev), ("test", ~dev)):
        rs = [r for r, m in zip(rows, mask) if m]
        print(f"\n[{split}] AUC cũ {auc(np.array([r['old'] for r in rs]), y[mask]):.3f} → mới "
              f"{auc(prob[mask], y[mask]):.3f}")
        for k in (40, 60, 80):
            po, ro = at_k(rs, lambda r: (r["kind"] == "abbr", r["old"]), k)
            pn, rn = at_k(rs, lambda r: (r["kind"] == "abbr", r["new"]), k)
            print(f"  top {k:>3}: chính xác {po:5.1f} → {pn:5.1f}   độ phủ gold {ro:5.1f} → {rn:5.1f}")
    if args.write:
        OUT.write_text(json.dumps({
            "_doc": "Hồi quy logistic xếp hạng ứng viên thuật ngữ. Tạo bằng scripts/fit_term_ranker.py; app không đọc file này.",
            "trained": datetime.date.today().isoformat(), "papers": sorted({r["paper"] for r in rows if r["split"] == "dev"}),
            "features": names, "mean": mean.round(4).tolist(), "std": std.round(4).tolist(),
            "w": w.round(4).tolist(), "b": round(float(b), 4)}, ensure_ascii=False, indent=1))
        print(f"\nghi {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
