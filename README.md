# Szlichot / héber PDF OCR pipeline

Ez a `ocr_slichot.py` szkript egy ~200 oldalas nikudos héber PDF-et alakít
géppel olvasható, javított szöveggé.

## Hogyan működik

1. **Kép-konverzió**: minden PDF-oldalt nagy felbontású PNG képpé alakít
   (`pdftoppm`, poppler-utils).
2. **Gépi OCR**: minden képen lefuttatja a Tesseract OCR-t héber nyelvi
   csomaggal.
3. **AI-korrektúra (opcionális, `--ai-correct` kapcsolóval)**: minden oldal
   *képét* és a nyers OCR-szöveget elküldi a Claude API-nak, amely a kép
   alapján kijavítja az OCR hibáit -- főleg a nikud (magánhangzójelek)
   pontatlanságait, amikre a sima Tesseract nem elég megbízható.

A saját tesztem szerint (egy mintaoldalon) a nyers Tesseract a
mássalhangzókat többnyire jól ismeri fel, de a nikudot gyakran hibásan
teszi ki vagy máshova helyezi. Az AI-korrektúra ezt jelentősen javítja,
mert ténylegesen "látja" a betűket a képen, nem csak vak szövegjavítást
végez.

## Telepítés

**Ubuntu / Debian / WSL:**
```bash
sudo apt-get update
sudo apt-get install -y tesseract-ocr tesseract-ocr-heb poppler-utils
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**macOS (Homebrew):**
```bash
brew install tesseract tesseract-lang poppler
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> ⚠️ **Fontos:** minden futtatás előtt aktiváld a virtuális környezetet:
> ```bash
> source .venv/bin/activate
> ```
> Ennélkül a szkript nem találja a `Pillow`/`anthropic`/`python-dotenv`
> csomagokat -- ez pl. azt okozhatja, hogy minden képet "sérült"-nek jelez
> (a Pillow hiánya miatt), vagy hogy az AI-korrektúra egyből hibával leáll.
> A parancssorban az aktivált környezetet a `(.venv)` előtag jelzi.

**Windows:** legegyszerűbb WSL (Windows Subsystem for Linux) alatt, Ubuntu
disztribúcióval, és onnan a fenti Ubuntu-s lépéseket követni.

## Használat

Csak gépi OCR (ingyenes, gyors, de a nikud pontatlan lesz):
```bash
python3 ocr_slichot.py --pdf szlichot.pdf --outdir kimenet
```

OCR + AI-korrektúra (ajánlott nikudos szöveghez):
```bash
```

Hozz létre vagy tölts ki egy `.env` fájlt a projekt mappájában:

```dotenv
ANTHROPIC_API_KEY=sk-ant-...
```

Ezután futtasd:

```bash
python3 ocr_slichot.py --pdf szlichot.pdf --outdir kimenet --ai-correct
```

Csak egy oldaltartomány feldolgozása (pl. teszthez):
```bash
python3 ocr_slichot.py --pdf szlichot.pdf --outdir kimenet --ai-correct --start 1 --end 10
```

Ha a futás megszakad (pl. 120 oldalnál internetprobléma miatt), egyszerűen
indítsd újra **ugyanazzal a paranccsal** -- a szkript kihagyja a már kész
oldalakat, és onnan folytatja.

## Kimenet

```
kimenet/
  images/          -- minden oldal PNG képe
  raw_ocr/         -- nyers Tesseract-szöveg oldalanként
  final/            -- végleges (AI-korrigált vagy nyers) szöveg oldalanként
  teljes_szoveg.txt -- az összes oldal egyetlen fájlba fűzve, oldaljelzőkkel
```

## API-kulcs beszerzése (ha AI-korrektúrát szeretnél)

1. Regisztrálj a https://console.anthropic.com oldalon.
2. Hozz létre egy API-kulcsot.
3. Tölts fel néhány dollár kreditet a fiókodba (kártyás egyenlegfeltöltés).
4. Írd a kulcsot a projekt mappájában lévő `.env` fájlba: `ANTHROPIC_API_KEY=sk-ant-...`

### Költségbecslés

A `claude-sonnet-4-6` modellel, oldalanként kb. 1 kép + pár száz szó
szöveg = nagyjából 1500-2500 input token + kb. 500-1000 output token
oldalanként. 200 oldalra ez összesen durván **1-3 USD** körüli költséget
jelent -- pontos ár a mindenkori Anthropic API árazástól függ
(https://www.anthropic.com/pricing -- érdemes ellenőrizni, mielőtt
elindítod a teljes futást).

## OpenAI-kompatibilis verzió

Ha nem az Anthropic API-t, hanem OpenAI-krediteket szeretnél használni az
AI-korrektúrához, használd az `ocr_slichot_openai.py` fájlt -- pontosan
ugyanúgy működik, csak az `openai` csomagot és az `OPENAI_API_KEY`
környezeti változót várja.

```bash
pip install openai python-dotenv
```

Hozz létre vagy tölts ki egy `.env` fájlt a projekt mappájában:

```dotenv
OPENAI_API_KEY=sk-...
```

Ezután futtasd:

```bash
python3 ocr_slichot_openai.py --pdf szlichot.pdf --outdir kimenet --ai-correct
```

Alapértelmezett modell: **`gpt-5.6-terra`** -- ez az OpenAI 2026 közepén
kiadott, kiegyensúlyozott ár/teljesítményű, vision-képes modellje, nagyjából
a Claude Sonnet megfelelője a kínálatukban. Ha nem ez a modellnév érhető
el a fiókodban (a modellnevek időnként változnak), ellenőrizd a pontos
elérhető neveket a https://platform.openai.com/docs/models oldalon, és
add meg a `--model` kapcsolóval, pl.:

```bash
python3 ocr_slichot_openai.py --pdf szlichot.pdf --outdir kimenet --ai-correct --model gpt-5.6-sol
```

**Megjegyzés az árazásról:** ezt a részt 2026 szeptemberében kerestem ki
élőben, mert az OpenAI 2026-os modelljei már a saját ismereteim határán
túl vannak. A keresés idején a GPT-5.6 Terra kb. $2-2,5 / 1M input token
és $12-15 / 1M output token áron volt elérhető -- nagyságrendileg
hasonló, mint a Claude Sonnet ára erre a feladatra, de érdemes közvetlenül
az OpenAI árlistáján megnézni a pontos, aktuális számokat, mielőtt
elindítod a teljes 200 oldalas futást.

## Tippek a jobb eredményhez

- **Felbontás**: 400 dpi jó kiindulás; ha a nikud sűrű vagy apró betűs az
  eredeti, próbáld 500-600 dpi-vel is.
- **Kontraszt**: ha az eredeti szkennelt oldal sárgás vagy alacsony
  kontrasztú, egy egyszerű kontrasztnövelés (pl. ImageMagick
  `convert -contrast-stretch` a PNG-ken az OCR előtt) sokat javíthat.
- **Ellenőrzés**: még AI-korrektúrával is érdemes néhány oldalt manuálisan
  átnézni, különösen a ritka szavaknál vagy elmosódott szkenneknél -- a
  szkript `[?]` jelöléssel jelzi, ha az AI bizonytalan volt egy szóban.
