#!/usr/bin/env python3
"""
ocr_slichot_openai.py
======================

Ugyanaz a kétlépcsős OCR-pipeline, mint az ocr_slichot.py, csak a
kép-alapú AI-korrektúra lépéshez az OpenAI API-t használja
(Chat Completions, vision-képes modellel) az Anthropic API helyett.

1. lépés: PDF oldalak -> nagy felbontású képek (pdftoppm)
2. lépés: Tesseract OCR héberül
3. lépés (opcionális): az oldal képét + a nyers OCR-t elküldi egy
          OpenAI vision-modellnek, ami a kép alapján kijavítja a szöveget.

------------------------------------------------------------------
TELEPÍTÉS:

    sudo apt-get update
    sudo apt-get install -y tesseract-ocr tesseract-ocr-heb poppler-utils
    pip install openai python-dotenv

------------------------------------------------------------------
HASZNÁLAT:

    Írd be a kulcsot a szkript melletti .env fájlba:
            OPENAI_API_KEY=sk-...
  python3 ocr_slichot_openai.py --pdf szlichot.pdf --outdir kimenet --ai-correct

  Alapértelmezett modell: gpt-5.6-terra (kiegyensúlyozott ár/teljesítmény,
  vision-képes). Ha másik modellt szeretnél, add meg a --model kapcsolóval,
  pl.:
      python3 ocr_slichot_openai.py --pdf szlichot.pdf --outdir kimenet \
          --ai-correct --model gpt-5.6-sol

  Csak gépi OCR (AI-korrektúra nélkül, ingyenes):
      python3 ocr_slichot_openai.py --pdf szlichot.pdf --outdir kimenet

  Oldaltartomány + folytatás megszakítás után: ugyanúgy működik, mint az
  Anthropic-verzióban (lásd --start / --end, és a már kész oldalak
  automatikus kihagyása).
------------------------------------------------------------------
"""

import argparse
import base64
import os
import subprocess
import sys
import time
from pathlib import Path


def convert_pdf_to_images(pdf_path: Path, images_dir: Path, dpi: int) -> list[Path]:
    """PDF oldalak PNG képekké alakítása pdftoppm-mel (poppler-utils)."""
    images_dir.mkdir(parents=True, exist_ok=True)
    prefix = images_dir / "page"

    existing = sorted(images_dir.glob("page-*.png"))
    if existing:
        print(f"[info] {len(existing)} kép már létezik a {images_dir} mappában, "
              f"a konvertálás kihagyva.")
        return existing

    print(f"[info] PDF -> kép konvertálás ({dpi} dpi)...")
    cmd = ["pdftoppm", "-r", str(dpi), "-png", str(pdf_path), str(prefix)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("[hiba] pdftoppm sikertelen:", result.stderr, file=sys.stderr)
        sys.exit(1)

    images = sorted(images_dir.glob("page-*.png"))
    print(f"[info] {len(images)} oldal képe elkészült.")
    return images


def page_number_from_filename(path: Path) -> int:
    stem = path.stem  # "page-007"
    num_part = stem.split("-")[-1]
    return int(num_part)


def run_tesseract(image_path: Path, lang: str) -> str:
    cmd = ["tesseract", str(image_path), "stdout", "-l", lang, "--psm", "6"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[hiba] tesseract sikertelen ehhez: {image_path.name}: {result.stderr}",
              file=sys.stderr)
        return ""
    return result.stdout


SYSTEM_PROMPT = (
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


def ai_correct_page(image_path: Path, raw_text: str, client, model: str) -> str:
    """
    Az OpenAI Chat Completions API-nak (vision-képes modellel) elküldi az
    oldal KÉPÉT és a nyers OCR-szöveget, visszakéri a kép alapján
    kijavított szöveget.
    """
    with open(image_path, "rb") as f:
        image_b64 = base64.standard_b64encode(f.read()).decode("utf-8")
    data_url = f"data:image/png;base64,{image_b64}"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": data_url}},
                {
                    "type": "text",
                    "text": (
                        "Nyers OCR-szöveg (csak referenciának, ne bízz meg "
                        f"benne vakon):\n\n{raw_text}"
                    ),
                },
            ],
        },
    ]

    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                max_tokens=2000,
                messages=messages,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            print(f"[figyelmeztetés] AI-korrektúra hiba ({attempt}/{max_retries}): {e}",
                  file=sys.stderr)
            time.sleep(2 * attempt)

    print(f"[hiba] AI-korrektúra végleg sikertelen ehhez: {image_path.name}, "
          f"a nyers OCR-t használom helyette.", file=sys.stderr)
    return raw_text


def main():
    parser = argparse.ArgumentParser(description="Héber PDF OCR + opcionális OpenAI AI-korrektúra")
    parser.add_argument("--pdf", required=True, help="A bemeneti PDF fájl útvonala")
    parser.add_argument("--outdir", required=True, help="Kimeneti mappa")
    parser.add_argument("--dpi", type=int, default=400, help="Kép felbontás (alap: 400)")
    parser.add_argument("--lang", default="heb", help="Tesseract nyelvi kód (alap: heb)")
    parser.add_argument("--ai-correct", action="store_true",
                         help="AI-alapú korrektúra bekapcsolása (OpenAI API-kulcs szükséges)")
    parser.add_argument("--model", default="gpt-5.6-terra",
                         help="Használt OpenAI modell (alap: gpt-5.6-terra). "
                              "Ellenőrizd a pontos elérhető modellneveket a "
                              "platform.openai.com/docs/models oldalon, mert "
                              "ezek gyakran változnak.")
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
            from openai import OpenAI
            from dotenv import load_dotenv
        except ImportError:
            print("[hiba] Az 'openai' vagy 'python-dotenv' csomag nincs telepítve. "
                  "Futtasd: pip install openai python-dotenv",
                  file=sys.stderr)
            sys.exit(1)
        load_dotenv(Path(__file__).with_name(".env"))
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            print("[hiba] Nincs beállítva az OPENAI_API_KEY a .env fájlban.",
                  file=sys.stderr)
            sys.exit(1)
        client = OpenAI(api_key=api_key)

    images = convert_pdf_to_images(pdf_path, images_dir, args.dpi)

    combined_path = outdir / "teljes_szoveg.txt"
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

        print(f"[info] {page_num}. oldal OCR-je...")
        raw_text = run_tesseract(image_path, args.lang)
        raw_path = raw_dir / f"page-{page_num:03d}.txt"
        raw_path.write_text(raw_text, encoding="utf-8")

        if args.ai_correct:
            print(f"[info] {page_num}. oldal AI-korrektúrája ({args.model})...")
            corrected = ai_correct_page(image_path, raw_text, client, args.model)
        else:
            corrected = raw_text

        final_txt_path.write_text(corrected, encoding="utf-8")
        print(f"[info] {page_num}. oldal kész -> {final_txt_path}")

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
