"""Э0: испытания И1, И2, И4, И5, И6, И10 на PDF проекта. Только локально, без сети."""
import sys, os, time, json, hashlib, re, gc
import pypdfium2 as pdfium
import pdfplumber
import psutil
from PIL import ImageDraw

DATA, OUT = sys.argv[1], sys.argv[2]
os.makedirs(OUT, exist_ok=True)
proc = psutil.Process()
MM = 25.4 / 72

FORMATS = {"A4": (210, 297), "A3": (297, 420), "A2": (420, 594), "A1": (594, 841), "A0": (841, 1189)}

def fmt(w, h):
    a, b = sorted((w, h))
    for n, (x, y) in FORMATS.items():
        if abs(a - x) <= 3 and abs(b - y) <= 3:
            return n
    for n, (x, y) in FORMATS.items():  # кратные форматы: короткая сторона = стороне базового
        for base in (x, y):
            if abs(a - base) <= 3:
                k = b / (y if base == x else x)
                if abs(k - round(k)) < 0.05 and round(k) > 1:
                    return f"{n}x{round(k)}"
    return f"нестанд. {round(a)}x{round(b)}"

def rss():
    return proc.memory_info().rss / 2**20

report = {"files": {}}
files = sorted(f for f in os.listdir(DATA) if f.endswith(".pdf"))

# И1 + И2 (pdfium) + И6
for f in files:
    p = os.path.join(DATA, f)
    runs = []
    for r in range(3):
        gc.collect(); m0 = rss(); peak = m0
        t0 = time.perf_counter(); doc = pdfium.PdfDocument(p); t_open = time.perf_counter() - t0
        pages = []; t_text = t_render = 0
        for i in range(len(doc)):
            pg = doc[i]
            w, h = pg.get_size(); rot = pg.get_rotation()
            t1 = time.perf_counter(); tp = pg.get_textpage(); txt = tp.get_text_range(); t_text += time.perf_counter() - t1
            t1 = time.perf_counter(); img = pg.render(scale=150 / 72).to_pil(); t_render += time.perf_counter() - t1
            peak = max(peak, rss())
            pages.append({"n": i + 1, "mm": [round(w * MM), round(h * MM)], "format": fmt(w * MM, h * MM), "rotation": rot,
                          "chars": tp.count_chars(), "has_text": tp.count_chars() > 50,
                          "cid_garbage": txt.count("�") + len(re.findall(r"\(cid:\d+\)", txt)),
                          "text_sha": hashlib.sha256(txt.encode()).hexdigest()[:16],
                          "img_px": img.size, "img_sha": hashlib.sha256(img.tobytes()).hexdigest()[:16]})
            del img; tp.close(); pg.close()
        doc.close(); gc.collect()
        runs.append({"open_s": round(t_open, 3), "text_s": round(t_text, 3), "render150_s": round(t_render, 3),
                     "rss_start_mb": round(m0), "rss_peak_mb": round(peak), "rss_end_mb": round(rss()), "pages": pages})
    same = all(r["pages"] == runs[0]["pages"] for r in runs)  # И10
    report["files"][f] = {"size_kb": os.path.getsize(p) // 1024, "pages": runs[0]["pages"],
                          "timing_runs": [{k: v for k, v in r.items() if k != "pages"} for r in runs],
                          "repeat_identical": same}

# И2 (pdfplumber) + И4 сравнение координат движков
for f in files:
    p = os.path.join(DATA, f)
    t0 = time.perf_counter(); words_all = {}
    with pdfplumber.open(p) as pl:
        for i, page in enumerate(pl.pages):
            words_all[i] = page.extract_words(keep_blank_chars=False, use_text_flow=False)
    report["files"][f]["pdfplumber_words_s"] = round(time.perf_counter() - t0, 2)
    report["files"][f]["pdfplumber_words"] = sum(len(v) for v in words_all.values())

    # координаты: для каждого слова pdfplumber ищем те же символы в pdfium по индексу через search
    doc = pdfium.PdfDocument(p); diffs = []; checked = 0
    for i in range(len(doc)):
        pg = doc[i]; tp = pg.get_textpage(); H = pg.get_size()[1]
        rot = pg.get_rotation()
        sample = [w for w in words_all[i] if len(w["text"]) >= 5 and re.search(r"[А-Яа-я0-9]", w["text"])][:60]
        for w in sample:
            s = tp.search(w["text"], match_case=True, match_whole_word=False)
            best = None
            while True:
                res = s.get_next()
                if not res: break
                idx, cnt = res
                boxes = [tp.get_charbox(k) for k in range(idx, idx + cnt)]
                l = min(b[0] for b in boxes); r_ = max(b[2] for b in boxes)
                bo = min(b[1] for b in boxes); t = max(b[3] for b in boxes)
                # pdfplumber: top от верха страницы (в неповёрнутой системе страницы), pdfium: снизу
                x0, top = w["x0"], w["top"]; y_top_pdfium = H - t
                d = max(abs(l - x0), abs(y_top_pdfium - top))
                if best is None or d < best[0]: best = (d, (t - bo))
            s.close()
            if best: diffs.append(best); checked += 1
        tp.close(); pg.close()
    doc.close()
    if diffs:
        rel = sorted(d / max(hgt, 0.5) for d, hgt in diffs)
        report["files"][f]["coord_check"] = {"words": checked, "median_pt": round(sorted(d for d, _ in diffs)[len(diffs) // 2], 2),
                                            "max_pt": round(max(d for d, _ in diffs), 2),
                                            "share_within_char_height": round(sum(1 for x in rel if x <= 1) / len(rel), 3)}

# И4 визуально + И5: рамки на отрисовке для выбранных надписей, кропы на разных разрешениях
targets = {"A.pdf": ["ККС", "ВВНГ"], "B.pdf": ["ДОЛ-П", "кросс"], "C.pdf": ["Площадка", "Подпорная"]}
for f, needles in targets.items():
    doc = pdfium.PdfDocument(os.path.join(DATA, f)); pg = doc[0]; tp = pg.get_textpage(); W, H = pg.get_size()
    for dpi in (100, 150, 200, 300):
        sc = dpi / 72
        img = pg.render(scale=sc).to_pil().convert("RGB"); dr = ImageDraw.Draw(img); first = None
        for nd in needles:
            s = tp.search(nd); n = 0
            while n < 5:
                res = s.get_next()
                if not res: break
                idx, cnt = res; bx = [tp.get_charbox(k) for k in range(idx, idx + cnt)]
                l, b_, r_, t = min(b[0] for b in bx), min(b[1] for b in bx), max(b[2] for b in bx), max(b[3] for b in bx)
                box = (l * sc - 6, (H - t) * sc - 6, r_ * sc + 6, (H - b_) * sc + 6)
                dr.rectangle(box, outline=(220, 0, 0), width=max(2, int(sc)))
                first = first or box; n += 1
            s.close()
        if first:
            cx, cy = (first[0] + first[2]) / 2, (first[1] + first[3]) / 2; half = 5 * sc * 72 / 2.54 / 2  # ~5 см окно
            crop = img.crop((max(0, cx - half), max(0, cy - half * 0.6), cx + half, cy + half * 0.6))
            crop.save(os.path.join(OUT, f"I5_{f[0]}_{dpi}dpi.png"))
        del img
    tp.close(); pg.close(); doc.close()

json.dump(report, open(os.path.join(OUT, "e0_report.json"), "w"), ensure_ascii=False, indent=1)
print(json.dumps({f: {k: v for k, v in r.items() if k != "pages"} for f, r in report["files"].items()}, ensure_ascii=False, indent=1))
for f, r in report["files"].items():
    print(f, [(p["n"], p["format"], p["rotation"], p["chars"], p["cid_garbage"]) for p in r["pages"]])
