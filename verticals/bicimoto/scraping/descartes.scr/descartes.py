"""
Reconstruye el detalle de descartes SIN volver a pedir paginas al sitio.

Fuentes locales admitidas (0 peticiones de red):
  1. .cache_html/*.html      -> cache de paginas del scraper (si ya existe)
  2. /tmp/opencode/p1.html   -> 1 pagina real guardada (q=bicimoto&page=1)
  3. ../.cache_pool.json     -> corrida previa (25 sep), 203 stubs

La evaluacion usa EXACTAMENTE la misma logica que scraper.py (importa
es_excluido / construir / evaluar), de modo que los motivos son identicos
a los del scraping original.

Salida: descartes.csv + descartes.jsonl
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import scraper as S

CAMPOS = [
    "id_anuncio",
    "titulo",
    "precio_usd",
    "moneda_original",
    "ubicacion",
    "municipio",
    "campos_faltantes",
    "motivo",
    "clase_motivo",
    "url_busqueda",
    "url_anuncio",
]


def fila(aid: str, ad: dict, motivo: str, provs: dict, munis: dict, origen: str) -> dict:
    reg = S.construir(ad, provs, munis)
    if motivo.startswith("exclusion:"):
        faltan, clase = "", "exclusion"
    elif motivo.startswith("faltan campos obligatorios:"):
        faltan = motivo.split(":", 1)[1].strip()
        clase = "faltan_campos"
    else:
        faltan, clase = "", "otro"
    return {
        "id_anuncio": aid,
        "titulo": S.clean_text(ad.get("title")),
        "precio_usd": reg["precio_usd"],
        "moneda_original": reg["moneda_original"],
        "ubicacion": reg["ubicacion"],
        "municipio": reg["municipio"],
        "campos_faltantes": faltan,
        "motivo": motivo,
        "clase_motivo": clase,
        "url_busqueda": origen,
        "url_anuncio": reg["url_anuncio"],
    }


def desde_html(rutas: list[tuple[Path, str]]) -> list[dict]:
    filas: list[dict] = []
    for ruta, origen in rutas:
        if not ruta.exists():
            continue
        ads, provs, munis, _ = S.parse_listado(ruta.read_text(encoding="utf-8", errors="replace"))
        for aid, ad in ads.items():
            reg, motivo = S.evaluar(ad, provs, munis)
            if reg is None:
                filas.append(fila(aid, ad, motivo, provs, munis, origen))
    return filas


def desde_cache_pool(ruta: Path) -> list[dict]:
    if not ruta.exists():
        return []
    pool = json.loads(ruta.read_text(encoding="utf-8"))["pool"]
    # mapa de provincias proveniente de cualquier pagina cacheada
    provs = munis = {}
    for cand in [Path("/tmp/opencode/p1.html"), *sorted(Path(".cache_html").glob("*.html"))]:
        if cand.exists():
            _, provs, munis, _ = S.parse_listado(cand.read_text(encoding="utf-8", errors="replace"))
            if provs:
                break
    filas = []
    for aid, ad in pool.items():
        reg, motivo = S.evaluar(ad, provs, munis)
        if reg is None:
            # origen desconocido: .cache_pool.json no guarda de que URL salio
            filas.append(fila(aid, ad, motivo, provs, munis, "desconocida (crawl previo global)"))
    return filas


def main() -> None:
    filas: list[dict] = []

    cache = sorted(Path(".cache_html").glob("*.html"))
    if cache:
        idx = Path(".cache_html/index.json")
        url_por_archivo = {}
        if idx.exists():
            url_por_archivo = json.loads(idx.read_text(encoding="utf-8"))
        filas += desde_html(
            [(p, url_por_archivo.get(p.name, "cache (url desconocida)")) for p in cache]
        )

    p1 = Path("/tmp/opencode/p1.html")
    if p1.exists():
        filas += desde_html([(p1, "https://www.revolico.com/search?q=bicimoto&page=1")])

    filas += desde_cache_pool(Path("../.cache_pool.json"))

    # dedup por id de anuncio, priorizando la fuente con origen conocido
    unico: dict[str, dict] = {}
    for f in filas:
        if f["id_anuncio"] not in unico or f["url_busqueda"].startswith("http"):
            unico[f["id_anuncio"]] = f
    filas = list(unico.values())

    with open("descartes.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CAMPOS)
        w.writeheader()
        w.writerows(filas)
    with open("descartes.jsonl", "w", encoding="utf-8") as fh:
        for f in filas:
            fh.write(json.dumps(f, ensure_ascii=False) + "\n")

    print(f"descartes reconstruidos: {len(filas)}")
    print("  fuentes: .cache_html + p1.html + ../.cache_pool.json")
    print(f"  escrito: descartes.csv, descartes.jsonl")
    clases: dict[str, int] = {}
    for f in filas:
        clases[f["clase_motivo"]] = clases.get(f["clase_motivo"], 0) + 1
    print("  por clase:", clases)


if __name__ == "__main__":
    main()
