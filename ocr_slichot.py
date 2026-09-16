#!/usr/bin/env python3
"""
ocr_slichot.py
==============

Kétlépcsős OCR-pipeline héber (nikudos) PDF-ekhez, pl. szlichot/machzor oldalakhoz.

1. lépés: a PDF minden oldalát nagy felbontású képpé alakítja (pdftoppm),
          majd Tesseract OCR-t futtat rajtuk héber nyelvi csomaggal.
2. lépés (opcionális, de erősen ajánlott nikudos szövegnél):
          minden oldal KÉPÉT és a nyers OCR-szöveget elküldi a Claude API-nak,
          ami a kép alapján kijavítja az OCR hibáit (főleg a nikud pontatlanságait).

Az eredmény: oldalankénti .txt fájlok + egy összefűzött végleges fájl.

------------------------------------------------------------------
TELEPÍTÉS (Ubuntu/Debian alapú rendszeren, pl. WSL vagy Linux gép):

    sudo apt-get update
    sudo apt-get install -y tesseract-ocr tesseract-ocr-heb poppler-utils
    pip install anthropic python-dotenv  # csak akkor kell, ha AI-korrektúrát is használsz

macOS-en (Homebrew):
    brew install tesseract tesseract-lang poppler
    pip install anthropic

------------------------------------------------------------------
HASZNÁLAT:

  Csak gépi OCR (gyors, ingyenes, de a nikud nem lesz tökéletes):
    python3 ocr_slichot.py --pdf szlichot.pdf --outdir kimenet

  OCR + AI-korrektúra (jóval pontosabb nikud, de API-kulcs és apró költség kell):
        Írd be a kulcsot a szkript melletti .env fájlba:
                ANTHROPIC_API_KEY=sk-ant-...
    python3 ocr_slichot.py --pdf szlichot.pdf --outdir kimenet --ai-correct

  Csak bizonyos oldaltartományra (pl. teszthez, vagy folytatáshoz megszakítás után):
    python3 ocr_slichot.py --pdf szlichot.pdf --outdir kimenet --ai-correct --start 1 --end 20

  A szkript automatikusan kihagyja azokat az oldalakat, amikhez már van kész
  kimeneti fájl -- tehát ha megszakad 200 oldal közben, egyszerűen indítsd
  újra ugyanazzal a paranccsal, és onnan folytatja, ahol abbamaradt.
------------------------------------------------------------------
"""

import argparse
import base64
import os
import subprocess
import sys
import time
from pathlib import Path

try:
    from PIL import Image, UnidentifiedImageError
    PIL_IMPORT_ERROR = None
except ImportError as e:
    Image = None
    UnidentifiedImageError = Exception
    PIL_IMPORT_ERROR = e


def get_pdf_page_count(pdf_path: Path) -> int:
    """A PDF teljes oldalszámának lekérdezése (pdfinfo, poppler-utils)."""
    result = subprocess.run(["pdfinfo", str(pdf_path)], capture_output=True, text=True)
    if result.returncode != 0:
        print("[hiba] pdfinfo sikertelen:", result.stderr, file=sys.stderr)
        sys.exit(1)
    for line in result.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.split(":", 1)[1].strip())
    print("[hiba] pdfinfo kimenetéből nem sikerült kiolvasni az oldalszámot.", file=sys.stderr)
    sys.exit(1)


def convert_pdf_to_images(pdf_path: Path, images_dir: Path, dpi: int) -> list[Path]:
    """PDF oldalak PNG képekké alakítása pdftoppm-mel (poppler-utils)."""
    images_dir.mkdir(parents=True, exist_ok=True)
    prefix = images_dir / "page"

    total_pages = get_pdf_page_count(pdf_path)
    existing = sorted(images_dir.glob("page-*.png"))
    existing_nums = {page_number_from_filename(p) for p in existing}
    missing_nums = sorted(set(range(1, total_pages + 1)) - existing_nums)

    if existing and not missing_nums:
        print(f"[info] {len(existing)} kép már létezik a {images_dir} mappában, "
              f"a konvertálás kihagyva.")
        return existing

    if existing:
        print(f"[info] {len(existing)} kép már létezik, de a PDF {total_pages} oldalas -- "
              f"a hiányzó {len(missing_nums)} oldal képét most generálom le "
              f"({missing_nums[0]}. oldaltól).")
    else:
        print(f"[info] PDF -> kép konvertálás ({dpi} dpi)...")

    cmd = ["pdftoppm", "-r", str(dpi), "-png",
           "-f", str(missing_nums[0]), "-l", str(missing_nums[-1]),
           str(pdf_path), str(prefix)] if existing else \
          ["pdftoppm", "-r", str(dpi), "-png", str(pdf_path), str(prefix)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("[hiba] pdftoppm sikertelen:", result.stderr, file=sys.stderr)
        sys.exit(1)

    images = sorted(images_dir.glob("page-*.png"))
    print(f"[info] {len(images)} oldal képe elérhető.")
    return images


def page_number_from_filename(path: Path) -> int:
    """pl. page-007.png -> 7"""
    stem = path.stem  # "page-007"
    num_part = stem.split("-")[-1]
    return int(num_part)


def is_image_valid(image_path: Path) -> bool:
    """Ellenőrzi, hogy a PNG kép nem sérült/csonka-e.

    Ha a Pillow csomag hiányzik, kivételt dob -- ezt NEM szabad sima
    'sérült kép' esetként kezelni, mert az minden oldalt hibásan
    kihagyna (lásd main() eleji ellenőrzést).
    """
    if PIL_IMPORT_ERROR is not None:
        raise RuntimeError(
            "A Pillow csomag nincs telepítve/elérhető (valószínűleg nincs "
            "aktiválva a virtuális környezet: 'source .venv/bin/activate')."
        ) from PIL_IMPORT_ERROR
    try:
        with Image.open(image_path) as im:
            im.load()
        return True
    except Exception:
        return False


def regenerate_page_image(pdf_path: Path, images_dir: Path, page_num: int, dpi: int) -> bool:
    """Egyetlen oldal képének újragenerálása a PDF-ből (sérült kép esetén)."""
    prefix = images_dir / "page"
    cmd = [
        "pdftoppm", "-r", str(dpi), "-png",
        "-f", str(page_num), "-l", str(page_num),
        str(pdf_path), str(prefix),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[hiba] {page_num}. oldal képének újragenerálása sikertelen: {result.stderr}",
              file=sys.stderr)
        return False
    return True


def run_tesseract(image_path: Path, lang: str) -> str:
    """Tesseract OCR futtatása egyetlen oldalképen, sima szöveg kimenettel."""
    cmd = ["tesseract", str(image_path), "stdout", "-l", lang, "--psm", "6"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[hiba] tesseract sikertelen ehhez: {image_path.name}: {result.stderr}",
              file=sys.stderr)
        return ""
    return result.stdout


def ai_correct_page(image_path: Path, raw_text: str, client, model: str) -> str:
    """
    A Claude API-nak elküldi az oldal KÉPÉT és a nyers OCR-szöveget,
    és visszakéri a kép alapján kijavított, nikuddal ellátott szöveget.
    """
    with open(image_path, "rb") as f:
        image_b64 = base64.standard_b64encode(f.read()).decode("utf-8")

    system_prompt = (
        "Egy héber nyelvű vallási szöveg (szlichot/machzor) beolvasott oldalának "
        "képét és egy gépi OCR (Tesseract) által készített, hibás nyers átiratát "
        "kapod. A feladatod: a KÉP alapján javítsd ki az OCR szövegét úgy, hogy "
        "az pontosan kövesse a képen látható héber szöveget, beleértve a nikudot "
        "(magánhangzójeleket) és a helyesírási jeleket is. "
        "Ne modernizálj, ne értelmezz, ne fordíts, ne magyarázz semmit -- "
        "csak a pontos, javított héber szöveget add vissza, sortörésekkel "
        "úgy tagolva, ahogy az oldalon szerepel. Ha egy szó a képen olvashatatlan "
        "vagy bizonytalan, jelöld [?] jellel közvetlenül utána. "
        "Ne írj semmilyen bevezetőt, magyarázatot vagy Markdown-jelölést, "
        "kizárólag a héber szöveget add vissza."
    )

    user_content = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": image_b64,
            },
        },
        {
            "type": "text",
            "text": f"Nyers OCR-szöveg (csak referenciának, ne bízz meg benne vakon):\n\n{raw_text}",
        },
    ]

    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=2000,
                system=system_prompt,
                messages=[{"role": "user", "content": user_content}],
                timeout=60.0,
            )
            text_blocks = [b.text for b in response.content if b.type == "text"]
            return "\n".join(text_blocks).strip()
        except Exception as e:
            print(f"[figyelmeztetés] AI-korrektúra hiba ({attempt}/{max_retries}): {e}",
                  file=sys.stderr)
            if attempt < max_retries:
                time.sleep(2 * attempt)

    print(f"[hiba] AI-korrektúra végleg sikertelen ehhez: {image_path.name}, "
          f"a nyers OCR-t használom helyette.", file=sys.stderr)
    return raw_text


def main():
    parser = argparse.ArgumentParser(description="Héber PDF OCR + opcionális AI-korrektúra")
    parser.add_argument("--pdf", required=True, help="A bemeneti PDF fájl útvonala")
    parser.add_argument("--outdir", required=True, help="Kimeneti mappa")
    parser.add_argument("--dpi", type=int, default=400, help="Kép felbontás (alap: 400)")
    parser.add_argument("--lang", default="heb", help="Tesseract nyelvi kód (alap: heb)")
    parser.add_argument("--ai-correct", action="store_true",
                         help="AI-alapú korrektúra bekapcsolása (Claude API-kulcs szükséges)")
    parser.add_argument("--model", default="claude-sonnet-4-6",
                         help="Használt Claude modell (alap: claude-sonnet-4-6)")
    parser.add_argument("--start", type=int, default=None, help="Kezdő oldalszám (opcionális)")
    parser.add_argument("--end", type=int, default=None, help="Utolsó oldalszám (opcionális)")
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    outdir = Path(args.outdir)
    images_dir = outdir / "images"
    raw_dir = outdir / "raw_ocr"
    final_dir = outdir / "final"
    raw_dir.mkdir(parents=True, exist_ok=True)
    final_dir.mkdir(parents=True, exist_ok=True)

    if not pdf_path.exists():
        print(f"[hiba] Nem található a PDF: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    client = None
    if args.ai_correct:
        try:
            import anthropic
            from dotenv import load_dotenv
        except ImportError:
            print("[hiba] Az 'anthropic' vagy 'python-dotenv' csomag nincs telepítve. "
                  "Futtasd: pip install anthropic python-dotenv",
                  file=sys.stderr)
            sys.exit(1)
        load_dotenv(Path(__file__).with_name(".env"))
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            print("[hiba] Nincs beállítva az ANTHROPIC_API_KEY a .env fájlban.",
                  file=sys.stderr)
            sys.exit(1)
        # rövid timeout + saját retry-logika (ai_correct_page), hogy egy
        # kapcsolati hiba ne fagyassza le percekre a feldolgozást
        client = anthropic.Anthropic(api_key=api_key, timeout=60.0, max_retries=0)

    if PIL_IMPORT_ERROR is not None:
        print("[hiba] A Pillow csomag nem érhető el (a kép-sérülés-ellenőrzéshez "
              "szükséges). Valószínűleg nincs aktiválva a virtuális környezet -- "
              "futtasd előbb: source .venv/bin/activate -- vagy telepítsd: "
              "pip install Pillow", file=sys.stderr)
        sys.exit(1)

    images = convert_pdf_to_images(pdf_path, images_dir, args.dpi)

    combined_path = outdir / "teljes_szoveg.txt"
    # Ha van korábbi részleges kimenet, azt megőrizzük és csak hozzáfűzünk
    existing_pages = {page_number_from_filename(p) for p in final_dir.glob("page-*.txt")}

    for image_path in images:
        page_num = page_number_from_filename(image_path)

        if args.start and page_num < args.start:
            continue
        if args.end and page_num > args.end:
            continue

        final_txt_path = final_dir / f"page-{page_num:03d}.txt"
        if page_num in existing_pages:
            print(f"[info] {page_num}. oldal már kész, kihagyva.")
            continue

        try:
            if not is_image_valid(image_path):
                print(f"[figyelmeztetés] {page_num}. oldal képe sérült, "
                      f"újragenerálom a PDF-ből...", file=sys.stderr)
                regenerate_page_image(pdf_path, images_dir, page_num, args.dpi)
                if not is_image_valid(image_path):
                    print(f"[hiba] {page_num}. oldal képe az újragenerálás után is "
                          f"sérült, ezt az oldalt most kihagyom (a következő "
                          f"futtatáskor újra megpróbálom).", file=sys.stderr)
                    continue

            print(f"[info] {page_num}. oldal OCR-je...")
            raw_text = run_tesseract(image_path, args.lang)
            raw_path = raw_dir / f"page-{page_num:03d}.txt"
            raw_path.write_text(raw_text, encoding="utf-8")

            if args.ai_correct:
                print(f"[info] {page_num}. oldal AI-korrektúrája...")
                corrected = ai_correct_page(image_path, raw_text, client, args.model)
            else:
                corrected = raw_text

            final_txt_path.write_text(corrected, encoding="utf-8")
            print(f"[info] {page_num}. oldal kész -> {final_txt_path}")
        except Exception as e:
            print(f"[hiba] Váratlan hiba a(z) {page_num}. oldal feldolgozásakor: {e}. "
                  f"Kihagyom, és folytatom a következő oldallal (a következő "
                  f"futtatáskor ezt az oldalt újra megpróbálom).", file=sys.stderr)
            continue

    # Összefűzés egyetlen fájlba, oldalsorrendben
    all_final = sorted(final_dir.glob("page-*.txt"), key=page_number_from_filename)
    with open(combined_path, "w", encoding="utf-8") as out:
        for p in all_final:
            page_num = page_number_from_filename(p)
            out.write(f"\n\n===== {page_num}. oldal =====\n\n")
            out.write(p.read_text(encoding="utf-8"))

    print(f"\n[kész] Összes feldolgozott oldal: {len(all_final)}")
    print(f"[kész] Teljes szöveg itt: {combined_path}")


if __name__ == "__main__":
    main()
