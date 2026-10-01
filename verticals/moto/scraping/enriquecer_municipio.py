#!/usr/bin/env python3
"""Enriquece los 60 anuncios de la ampliacion con el campo 'municipio'.

La ampliacion solo leyo paginas de listado, donde el municipio llega como
municipalityId sin resolverse siempre. Aqui se entra a la pagina de detalle de
cada anuncio (httpx, sin navegador) y se resuelve el stub Apollo
MunicipalityType que referencia AdType.municipality.__ref.

Si la pagina de detalle no expone municipio, se deja null. No se inventa nada.

Entrada : motos_electricas_ampliacion.json (se sobrescribe con el mismo
          contenido mas 'municipio'; ningun otro campo se toca)
Cache   : cache_detalle_ampliacion/{id}.html
No se modifica motos_electricas.json, merge_motos.py ni scraper_motos_ampliacion.py
"""

from __future__ import annotations

import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).resolve().parent
ARCHIVO_ANUNCIOS = BASE_DIR / "motos_electricas_ampliacion.json"
DIR_CACHE = BASE_DIR / "cache_detalle_ampliacion"

USER_AGENT = "MiProyectoInvestigacion/1.0"
DELAY_SEGUNDOS = 3.0
TIMEOUT_SEGUNDOS = 30.0
REINTENTOS = 2
ESPERA_REINTENTO = 5.0
MAX_PETICIONES = 100

CAMPOS_ANUNCIO = [
    "titulo",
    "marca",
    "motor_w",
    "tipo_bateria",
    "autonomia_km",
    "precio_usd",
    "ubicacion",
    "fecha_publicacion",
    "url",
    "descripcion",
    "municipio",
]

RX_ID_PERMALINK = re.compile(r"(\d+)\s*$")


def id_de_url(url: str) -> str:
    coincidencia = RX_ID_PERMALINK.search(url.split("?")[0].rstrip("/"))
    return coincidencia.group(1) if coincidencia else ""


class Sesion:
    def __init__(self, usar_cache: bool = True) -> None:
        self.peticiones = 0
        self.desde_cache = 0
        self.errores: list[str] = []
        self.usar_cache = usar_cache
        if usar_cache:
            DIR_CACHE.mkdir(exist_ok=True)

    def cabeceras(self) -> dict:
        return {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "es-CU,es;q=0.9,en;q=0.8",
        }

    def obtener(self, cliente: httpx.Client, url: str, id_anuncio: str) -> str | None:
        destino = DIR_CACHE / f"{id_anuncio or hash(url)}.html"
        if self.usar_cache and destino.exists():
            html = destino.read_text(encoding="utf-8", errors="replace")
            if "__NEXT_DATA__" in html:
                self.desde_cache += 1
                return html
        if self.peticiones >= MAX_PETICIONES:
            self.errores.append(f"limite de {MAX_PETICIONES} peticiones alcanzado: {url}")
            return None
        for intento in range(REINTENTOS):
            self.peticiones += 1
            try:
                respuesta = cliente.get(url, headers=self.cabeceras(), timeout=TIMEOUT_SEGUNDOS)
                if respuesta.status_code == 429:
                    self.errores.append(f"HTTP 429 en {url}; esperando 60s")
                    time.sleep(60)
                    continue
                if respuesta.status_code >= 400:
                    self.errores.append(f"HTTP {respuesta.status_code} en {url}")
                    return None
                if self.usar_cache:
                    destino.write_text(respuesta.text, encoding="utf-8")
                return respuesta.text
            except httpx.HTTPError as exc:
                self.errores.append(f"error de red en {url}: {type(exc).__name__}")
                if intento < REINTENTOS - 1:
                    time.sleep(ESPERA_REINTENTO)
        return None


def parsear_municipio(html: str, id_anuncio: str) -> str | None:
    """Nombre del municipio desde __NEXT_DATA__ -> __APOLLO_STATE__."""
    try:
        soup = BeautifulSoup(html, "lxml")
        script = soup.find("script", id="__NEXT_DATA__")
        if script is None or not script.string:
            return None
        estado = json.loads(script.string)["props"]["pageProps"]["__APOLLO_STATE__"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
    if not isinstance(estado, dict):
        return None

    anuncios = [
        valor
        for clave, valor in estado.items()
        if clave.startswith("AdType:") and isinstance(valor, dict)
    ]
    if not anuncios:
        return None
    anuncio = next((a for a in anuncios if str(a.get("id")) == id_anuncio), anuncios[0])

    referencia = (anuncio.get("municipality") or {}).get("__ref")
    if not referencia:
        return None
    municipio = estado.get(referencia)
    if not isinstance(municipio, dict):
        return None
    nombre = municipio.get("name")
    if not isinstance(nombre, str):
        return None
    nombre = nombre.strip()
    return nombre or None


def main() -> int:
    datos = json.loads(ARCHIVO_ANUNCIOS.read_text(encoding="utf-8"))
    anuncios = datos.get("anuncios") or []
    if not anuncios:
        raise SystemExit(f"{ARCHIVO_ANUNCIOS.name} no tiene anuncios")

    sesion = Sesion()
    pendientes = [a for a in anuncios if not a.get("municipio")]
    print(f"Anuncios: {len(anuncios)} | sin municipio (a visitar): {len(pendientes)}\n")

    with httpx.Client(follow_redirects=True, verify=True) as cliente:
        for indice, anuncio in enumerate(pendientes, 1):
            url = anuncio.get("url")
            if not url:
                anuncio["municipio"] = None
                print(f"[{indice:>2}/{len(pendientes)}] sin url: {anuncio.get('titulo')!r}")
                continue
            id_anuncio = id_de_url(url)
            html = sesion.obtener(cliente, url, id_anuncio)
            municipio = parsear_municipio(html, id_anuncio) if html else None
            anuncio["municipio"] = municipio
            if indice % 10 == 0 or indice == len(pendientes):
                print(
                    f"[{indice:>2}/{len(pendientes)}] {str(municipio):<20} "
                    f"pets={sesion.peticiones} cache={sesion.desde_cache}"
                )
            time.sleep(DELAY_SEGUNDOS)

    for anuncio in anuncios:
        registro = {campo: anuncio.get(campo) for campo in CAMPOS_ANUNCIO[:-1]}
        registro["municipio"] = anuncio.get("municipio")
        anuncio.clear()
        anuncio.update(registro)

    datos["anuncios"] = anuncios
    with open(ARCHIVO_ANUNCIOS, "w", encoding="utf-8") as archivo:
        json.dump(datos, archivo, ensure_ascii=False, indent=2)
        archivo.write("\n")

    relectura = json.loads(ARCHIVO_ANUNCIOS.read_text(encoding="utf-8"))
    if relectura.get("total_anuncios_validos") != len(anuncios):
        raise SystemExit("el JSON reescrito no conserva total_anuncios_validos")
    if any(list(a.keys()) != CAMPOS_ANUNCIO for a in relectura["anuncios"]):
        raise SystemExit("algún anuncio no tiene los 11 campos esperados, en orden")

    con_municipio = [a for a in anuncios if a["municipio"]]
    sin_municipio = [a for a in anuncios if not a["municipio"]]
    print(f"\nPeticiones nuevas: {sesion.peticiones} | Desde caché: {sesion.desde_cache}")
    print(f"HTML en cache_detalle_ampliacion/: {len(list(DIR_CACHE.glob('*.html')))}")
    print(f"Con municipio: {len(con_municipio)} | Sin municipio (null): {len(sin_municipio)}")
    print("Municipios:")
    for nombre, total in Counter(a["municipio"] for a in con_municipio).most_common():
        print(f"  {total:>4}  {nombre}")
    if sin_municipio:
        print("Sin municipio:")
        for anuncio in sin_municipio:
            print(f"  - {anuncio['url']}")
    if sesion.errores:
        print(f"ERRORES ({len(sesion.errores)}):")
        for error in sesion.errores[:20]:
            print(f"  - {error}")
    return 0


if __name__ == "__main__":
    sys.exit(main())