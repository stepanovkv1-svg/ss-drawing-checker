"""Э0: И3 (точность текста), И7 (устойчивость), И8 (OCR на имитации скана), И9 (штамп)."""
import sys, os, re, json, time, subprocess, random
import pypdfium2 as pdfium
from PIL import Image, ImageFilter
import numpy as np

DATA, OUT = sys.argv[1], sys.argv[2]
rep = {}

def text(doc, i):
    pg = doc[i]; tp = pg.get_textpage(); t = tp.get_text_range(); tp.close(); pg.close(); return t

# И3: признаки искажений — латиница внутри кириллических слов, нечитаемые символы, private use
def mixed(word):
    return bool(re.search(r"[А-Яа-яЁё]", word)) and bool(re.search(r"[A-Za-z]", word))
i3 = {}
for f in "ABC":
    doc = pdfium.PdfDocument(f"{DATA}/{f}.pdf")
    toks = []; bad = []
    for i in range(len(doc)):
        t = text(doc, i)
        toks += re.findall(r"\S+", t)
        bad += [c for c in t if 0xE000 <= ord(c) <= 0xF8FF or c == "�"]
    mx = sorted({w for w in toks if mixed(w)})
    # ключевые обозначения
    keys = {"ККС": len([w for w in toks if w.startswith("ККС")]),
            "шифр_НСС": len([w for w in toks if "НСС" in w]),
            "даты": sorted({m for w in toks for m in re.findall(r"\b\d{2}\.\d{2,4}\b", w)})[:15]}
    i3[f] = {"tokens": len(toks), "unreadable_chars": len(bad), "mixed_script_words": mx[:25], "mixed_count": len(mx), "keys": keys}
    doc.close()
rep["И3"] = i3

# И7: устойчивость
import shutil
os.makedirs(f"{OUT}/i7", exist_ok=True)
shutil.copy(f"{DATA}/B.pdf", f"{OUT}/i7/ok.pdf")
raw = open(f"{DATA}/B.pdf", "rb").read()
open(f"{OUT}/i7/truncated.pdf", "wb").write(raw[: len(raw) // 2])
open(f"{OUT}/i7/notpdf.pdf", "wb").write(b"this is not a pdf" * 10)
open(f"{OUT}/i7/empty.pdf", "wb").write(b"")
subprocess.run(["qpdf", "--encrypt", "1234", "1234", "256", "--", f"{DATA}/B.pdf", f"{OUT}/i7/password.pdf"], check=False)
i7 = {}
for fn in sorted(os.listdir(f"{OUT}/i7")):
    try:
        d = pdfium.PdfDocument(f"{OUT}/i7/{fn}"); n = len(d)
        pg = d[0]; pg.render(scale=0.5); pg.close(); d.close()
        i7[fn] = f"открыт, листов {n}"
    except Exception as e:
        i7[fn] = f"ошибка: {type(e).__name__}: {str(e)[:120]}"
rep["И7"] = i7

# И8: OCR на имитации скана
def tess(img, path):
    img.save(path)
    p = subprocess.run(["tesseract", path, "stdout", "-l", "rus+eng", "--psm", "11"], capture_output=True)
    return p.stdout.decode("utf-8", "replace")

def norm(w):
    return re.sub(r"[^\wА-Яа-яЁё\-.,]", "", w).strip(".,").upper()

def lat2cyr(s):
    return s.translate(str.maketrans("ABCEHKMOPTXY", "АВСЕНКМОРТХУ"))

def recall(truth, got):
    T = {norm(w) for w in re.findall(r"\S+", truth) if len(norm(w)) >= 3}
    G = {norm(w) for w in re.findall(r"\S+", got)}
    G2 = {lat2cyr(w) for w in G}
    hit = len(T & G); hit2 = len({lat2cyr(w) for w in T} & G2)
    return len(T), round(hit / max(1, len(T)), 3), round(hit2 / max(1, len(T)), 3)

i8 = []
os.makedirs(f"{OUT}/i8", exist_ok=True)
cases = [("B", 0), ("A", 6), ("B", 2)]
for f, i in cases:
    doc = pdfium.PdfDocument(f"{DATA}/{f}.pdf"); truth = text(doc, i)
    for dpi in (200, 300):
        base = doc[i].render(scale=dpi / 72, grayscale=True).to_pil()
        variants = {"чистый": base,
                    "шум+наклон1°": base.rotate(1.0, expand=True, fillcolor=255).filter(ImageFilter.GaussianBlur(0.8))}
        arr = np.array(variants["шум+наклон1°"]).astype(np.int16)
        rnd = np.random.default_rng(1).normal(0, 18, arr.shape)
        variants["шум+наклон1°"] = Image.fromarray(np.clip(arr + rnd, 0, 255).astype(np.uint8))
        for name, im in variants.items():
            t0 = time.perf_counter(); got = tess(im, f"{OUT}/i8/{f}{i}_{dpi}.png"); dt = time.perf_counter() - t0
            n, r, r2 = recall(truth, got)
            i8.append({"file": f, "page": i + 1, "dpi": dpi, "variant": name, "words_in_layer": n,
                       "recall": r, "recall_lat_cyr_normalized": r2, "seconds": round(dt, 1)})
    doc.close()
rep["И8"] = i8

# И9: штамп — текст в правом нижнем углу (185x55 мм) каждого листа
i9 = {}
for f in "ABC":
    doc = pdfium.PdfDocument(f"{DATA}/{f}.pdf"); out = []
    for i in range(len(doc)):
        pg = doc[i]; tp = pg.get_textpage(); W, H = pg.get_size(); rot = pg.get_rotation()
        mm = 72 / 25.4
        # в системе координат страницы без поворота; для поворота 270 правый нижний угол отображения — другой угол
        if rot == 0:
            box = (W - 190 * mm, 0, W, 60 * mm)
        else:
            box = None
        if box:
            t = tp.get_text_bounded(*box)
        else:
            t = "(поворот %d — нужно брать угол с учётом поворота)" % rot
        out.append({"page": i + 1, "stamp_text": re.sub(r"\s+", " ", t)[:400]})
        tp.close(); pg.close()
    i9[f] = out; doc.close()
rep["И9"] = i9

json.dump(rep, open(f"{OUT}/e0_report_b.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps({k: rep[k] for k in ("И3", "И7", "И8")}, ensure_ascii=False, indent=1))
