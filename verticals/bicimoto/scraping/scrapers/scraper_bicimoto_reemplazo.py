#!/usr/bin/env python3
"""Reemplazo: obtener 18 anuncios nuevos de Revolico con motor_w."""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import httpx

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import scraper as base
import scraper_bicimoto_ampliacion as amp

ARCHIVO_VALIDOS_OUT = BASE_DIR / "bicis_electricas_reemplazo.json"
ARCHIVO_DESCARTADOS = BASE_DIR / "descartados_bicis_reemplazo.json"
ARCHIVO_REPORTE = BASE_DIR / "reporte_scraping_bicis_reemplazo.txt"
ARCHIVO_BASE_120 = BASE_DIR / "bicis_electricas_120.json"
DIR_CACHE = BASE_DIR / "cache_html_bicis_ampliacion"

BASE_URL = "https://www.revolico.com"
USER_AGENT = "MiProyectoInvestigacion/1.0"
DELAY_SEGUNDOS = 3.0
TIMEOUT_SEGUNDOS = 30.0
REINTENTOS = 2
ESPERA_REINTENTO = 5.0
MAX_PETICIONES = 150
MAX_PAGINAS_POR_URL = 8
OBJETIVO_VALIDOS = 18
FECHA_SCRAPING = "2026-10-01"
ANIO_MINIMO = 2026

SEARCH_QUERIES = [
    "bicimoto 3000w",
    "bicimoto 2000w",
    "bicimoto 1500w",
    "bicimoto 1000w",
    "bicimoto 800w",
    "bicimoto 500w",
    "motico electrica 3000w",
    "motico electrica 1500w",
    "bici electrica 48v",
    "bici electrica 60v",
]

def search_url(query: str) -> str:
    return f"{BASE_URL}/search?q={query.replace(' ', '+')}"


class Sesion:
    def __init__(self) -> None:
        self.peticiones = 0
        self.desde_cache = 0
        self.paginas = 0
        self.errores: list[str] = []


def obtener_html(url: str, sesion: Sesion) -> str:
    ruta = DIR_CACHE / (hashlib.md5(url.encode()).hexdigest() + ".html")
    if ruta.exists():
        sesion.desde_cache += 1
        try:
            return ruta.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass
    for intento in range(REINTENTOS + 1):
        try:
            with httpx.Client(
                headers={"User-Agent": USER_AGENT},
                timeout=TIMEOUT_SEGUNDOS,
                follow_redirects=True,
            ) as client:
                respuesta = client.get(url)
            sesion.peticiones += 1
            if respuesta.status_code == 429:
                sesion.errores.append(f"429 en {url}, esperando 60s")
                time.sleep(60)
                continue
            if respuesta.status_code >= 400:
                sesion.errores.append(f"{respuesta.status_code} en {url}")
                if intento < REINTENTOS:
                    time.sleep(ESPERA_REINTENTO)
                continue
            html = respuesta.text
            try:
                ruta.write_text(html, encoding="utf-8")
            except Exception:
                pass
            return html
        except Exception as exc:
            sesion.errores.append(f"Error {url}: {exc}")
            if intento < REINTENTOS:
                time.sleep(ESPERA_REINTENTO)
    return ""


def extraer_anuncios_dict(html: str):
    anuncios, provs, munis, npag = base.parse_listado(html)
    return anuncios, provs, munis


def cargar_base_urls() -> set[str]:
    p = ARCHIVO_BASE_120
    if not p.exists():
        return set()
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return {a.get("url") for a in data.get("anuncios", []) if a.get("url")}
    except Exception:
        return set()

def main() -> int:
    DIR_CACHE.mkdir(exist_ok=True)
    existentes = cargar_base_urls()
    vistos_titulos: set[str] = set()
    vistos_urls: set[str] = set(existentes)
    validos: list[dict] = []
    descartados: list[dict] = []
    motivos = Counter()
    sesion = Sesion()
    urls_consultadas: set[str] = set()
    paginas_por_query: dict[str, int] = {}

    def procesar_pagina(url_listado: str, query: str) -> bool:
        html = obtener_html(url_listado, sesion)
        if not html:
            return False
        sesion.paginas += 1
        paginas_por_query[query] = paginas_por_query.get(query, 0) + 1
        anuncios, provs, munis = extraer_anuncios_dict(html)
        for anuncio in anuncios.values():
            url_a = amp.url_de(anuncio)
            titulo = base.clean_text(anuncio.get("title")) or ""
            clave = amp.clave_titulo(titulo)
            if url_a and url_a in vistos_urls:
                continue
            if clave in vistos_titulos:
                continue
            vistos_urls.add(url_a or "")
            vistos_titulos.add(clave)
            campos = amp.construir_campos(anuncio, provs, munis)
            if campos is None:
                desc = {"titulo": titulo, "url": url_a, "motivo": "no_candidato"}
                descartados.append(desc)
                motivos["no_candidato"] += 1
                continue
            if campos["ubicacion"] is None:
                desc = {"titulo": campos["titulo"], "url": url_a, "motivo": "ubicacion_no_identificada"}
                descartados.append(desc)
                motivos["ubicacion_no_identificada"] += 1
                continue
            if campos["motor_w"] is None:
                desc = {"titulo": campos["titulo"], "url": url_a, "motivo": "sin_motor_w"}
                descartados.append(desc)
                motivos["sin_motor_w"] += 1
                continue
            if campos["motor_w"] < 200 or campos["motor_w"] > 5000:
                desc = {"titulo": campos["titulo"], "url": url_a, "motivo": "motor_w_fuera_rango"}
                descartados.append(desc)
                motivos["motor_w_fuera_rango"] += 1
                continue
            campos["fuente"] = "revolico"
            validos.append(campos)
            motivos["valido"] += 1
            if len(validos) >= OBJETIVO_VALIDOS:
                return True
        return False

    for query in SEARCH_QUERIES:
        if len(validos) >= OBJETIVO_VALIDOS or sesion.peticiones >= MAX_PETICIONES:
            break
        for page in range(1, MAX_PAGINAS_POR_URL + 1):
            if len(validos) >= OBJETIVO_VALIDOS or sesion.peticiones >= MAX_PETICIONES:
                break
            url_q = search_url(query) + f"&page={page}"
            if url_q in urls_consultadas:
                continue
            urls_consultadas.add(url_q)
            if procesar_pagina(url_q, query):
                break
            time.sleep(DELAY_SEGUNDOS)

    ahora = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    with open(ARCHIVO_VALIDOS_OUT, "w", encoding="utf-8") as f:
        json.dump(
            {
                "vehiculo": "bicimoto_electrica",
                "tipo": "reemplazo_sin_motor_w",
                "fecha_scraping": FECHA_SCRAPING,
                "total_anuncios_validos": len(validos),
                "anuncios": validos,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
        f.write("\n")

    with open(ARCHIVO_DESCARTADOS, "w", encoding="utf-8") as f:
        json.dump(
            {
                "vehiculo": "bicimoto_electrica",
                "tipo": "reemplazo_sin_motor_w",
                "fecha_scraping": FECHA_SCRAPING,
                "total_descartados": len(descartados),
                "descartes": descartados,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
        f.write("\n")

    print(f"OK: {len(validos)} nuevos con motor_w")
    return 0


if __name__ == "__main__":
    sys.exit(main())
