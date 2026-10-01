#!/usr/bin/env python3
"""Enriquece los anuncios de la ampliacion con el municipio.

El listado de Revolico no expone el municipio del anuncio, solo el id
(AdType.municipality.__ref -> MunicipalityType:<id>.name en __APOLLO_STATE__).
Por eso aqui si se entran a las paginas de detalle, guardando el HTML en
cache_detalle_scooters_ampliacion/ para no repetir la peticion.

Entrada y salida: scooters_electricos_ampliacion.json (se anade el campo
"municipio" a cada anuncio; si no se puede determinar queda en null).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).resolve().parent
ARCHIVO_ANUNCIOS = BASE_DIR / "scooters_electricos_ampliacion.json"
DIR_CACHE = BASE_DIR / "cache_detalle_scooters_ampliacion"

BASE_URL = "https://www.revolico.com"
USER_AGENT = "MiProyectoInvestigacion/1.0"
DELAY_SEGUNDOS = 3.0
TIMEOUT_SEGUNDOS = 30.0
REINTENTOS = 2
ESPERA_REINTENTO = 5.0
MAX_PETICIONES = 60

RX_MUNICIPIO = re.compile(r"^MunicipalityType:")


def municipio_de(html: str) -> str | None:
    """Nombre del municipio del anuncio (__NEXT_DATA__ -> __APOLLO_STATE__)."""
    soup = BeautifulSoup(html, "lxml")
    script = soup.find("script", id="__NEXT_DATA__")
    if script is None or not script.string:
        return None
    try:
        estado = json.loads(script.string)["props"]["pageProps"]["__APOLLO_STATE__"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None

    referencia = None
    for clave, valor in estado.items():
        if not (clave.startswith("AdType:") and isinstance(valor, dict)):
            continue
        municipio = valor.get("municipality")
        if isinstance(municipio, dict) and municipio.get("__ref"):
            referencia = municipio["__ref"]
            break
    if not referencia:
        return None

    for clave, valor in estado.items():
        if RX_MUNICIPIO.match(clave) and isinstance(valor, dict) and valor.get("name"):
            id_municipio = str(valor.get("id"))
            if referencia.endswith(f":{id_municipio}") or id_municipio in referencia:
                return valor["name"]
    return None


def escribir_json(ruta: Path, datos) -> None:
    with open(ruta, "w", encoding="utf-8") as archivo:
        json.dump(datos, archivo, ensure_ascii=False, indent=2)
        archivo.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Enriquece con municipio la ampliacion")
    parser.add_argument("--refrescar", action="store_true", help="ignora la caché de detalle")
    args = parser.parse_args()
    usar_cache = not args.refrescar

    ahora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    datos = json.loads(ARCHIVO_ANUNCIOS.read_text(encoding="utf-8"))
    anuncios = datos.get("anuncios") or []
    if usar_cache:
        DIR_CACHE.mkdir(exist_ok=True)

    pendientes = [a for a in anuncios if not a.get("municipio")]
    print(f"Anuncios: {len(anuncios)} | sin municipio (a visitar): {len(pendientes)}\n")

    peticiones = 0
    desde_cache = 0
    errores: list[str] = []

    with httpx.Client(follow_redirects=True, verify=True) as cliente:
        for indice, anuncio in enumerate(anuncios, 1):
            url = anuncio.get("url")
            if not url:
                anuncio["municipio"] = None
                continue
            destino = DIR_CACHE / (re.sub(r"[^a-z0-9]+", "-", url.lower()).strip("-") + ".html")
            html = None
            if usar_cache and destino.exists():
                html = destino.read_text(encoding="utf-8", errors="replace")
                desde_cache += 1
            elif peticiones < MAX_PETICIONES:
                for intento in range(REINTENTOS):
                    peticiones += 1
                    try:
                        respuesta = cliente.get(
                            url,
                            headers={"User-Agent": USER_AGENT, "Accept-Language": "es-CU,es;q=0.9"},
                            timeout=TIMEOUT_SEGUNDOS,
                        )
                        if respuesta.status_code == 429:
                            errores.append(f"HTTP 429 en {url}; esperando 60s")
                            time.sleep(60)
                            continue
                        if respuesta.status_code >= 400:
                            errores.append(f"HTTP {respuesta.status_code} en {url}")
                            break
                        html = respuesta.text
                        if usar_cache:
                            destino.write_text(html, encoding="utf-8")
                        break
                    except httpx.HTTPError as exc:
                        errores.append(f"error de red en {url}: {type(exc).__name__}")
                        if intento < REINTENTOS - 1:
                            time.sleep(ESPERA_REINTENTO)
                    finally:
                        if intento == 0 and html is None:
                            time.sleep(DELAY_SEGUNDOS)
            else:
                errores.append(f"limite de {MAX_PETICIONES} peticiones alcanzado: {url}")

            anuncio["municipio"] = municipio_de(html) if html else None
            if indice % 5 == 0 or indice == len(anuncios):
                print(
                    f"[{indice:>3}/{len(anuncios)}] {anuncio['municipio'] or 'null':<20} "
                    f"pets={peticiones} cache={desde_cache}"
                )

    datos["fecha_enriquecimiento_municipio"] = ahora
    escribir_json(ARCHIVO_ANUNCIOS, datos)

    con_municipio = sum(1 for a in anuncios if a.get("municipio"))
    print(f"\nAnuncios: {len(anuncios)}")
    print(f"Con municipio: {con_municipio}/{len(anuncios)}")
    print(f"Sin municipio (null): {len(anuncios) - con_municipio}")
    print(f"Peticiones nuevas: {peticiones} | Desde caché: {desde_cache}")
    if con_municipio:
        print("Municipios:")
        for municipio, total in Counter(a["municipio"] for a in anuncios).most_common():
            print(f"  {total:>3}  {municipio}")
    if errores:
        print(f"Errores registrados: {len(errores)}")
        for error in errores[:10]:
            print(f"  - {error}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
