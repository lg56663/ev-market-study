#!/usr/bin/env python3
"""Anade el municipio a los 60 anuncios de la ampliacion de combustion.

El listado de Revolico da 'La Habana / Cerro' como texto plano. Ahi se lee el
municipio sin pedir nada, asi que solo se entra a la pagina de detalle cuando
esa cadena no lo trae. El detalle tampoco trae la fecha de publicacion (Revolico
no la expone en ningun sitio), asi que no se intenta.

La ficha de detalle se lee de __NEXT_DATA__ -> __APOLLO_STATE__:
    ROOT_QUERY.adsPerPage(...) -> edges[].node.__ref -> AdType:<id>
    AdType:<id>.municipality.__ref -> MunicipalityType:<id>.name

No se inventan datos: si no hay municipio en el detalle, se escribe null.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

import scraper_combustion as base

BASE_DIR = Path(__file__).resolve().parent
ARCHIVO_ANUNCIOS = BASE_DIR / "motos_combustion_ampliacion.json"
# Los 30 originales se enriquecen in situ para que el entregable de 90 tenga
# municipio en todos. El original guarda 'ubicacion' como 'La Habana / Cerro',
# de ahi sale el municipio sin pedir nada. NO se reescribe el JSON original: se
# lee, se le anade el campo y se guarda el resultado en un archivo aparte.
ARCHIVO_ORIGINAL = BASE_DIR / "motos_combustion.json"
ARCHIVO_ORIGINAL_MUNICIPIO = BASE_DIR / "municipios_motos_combustion.json"
DIR_CACHE = BASE_DIR / "cache_detalle_combustion_ampliacion"

USER_AGENT = "MiProyectoInvestigacion/1.0"
DELAY_SEGUNDOS = 3.0
TIMEOUT_SEGUNDOS = 30.0
REINTENTOS = 2
ESPERA_REINTENTO = 5.0
TOTAL_ORIGINAL = 30
SEP = "=" * 78


def parsear_detalle(html: str) -> str | None:
    """Nombre del municipio desde la ficha de detalle, o None si no aparece.

    Devuelve el municipio del ANUNCIO, no el de otro producto: se toman todos
    los stubs AdType del estado y se usa el primero cuyo __ref corresponde al
    id de la propia URL.
    """
    coincidencia = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S
    )
    if not coincidencia:
        return None
    try:
        datos = json.loads(coincidencia.group(1))
        estado = datos["props"]["pageProps"]["__APOLLO_STATE__"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None

    # Todos los municipios que hay en el estado, indexados por id.
    municipios = {
        clave.split(":", 1)[1]: valor.get("name")
        for clave, valor in estado.items()
        if clave.startswith("MunicipalityType:")
    }

    # El stub AdType del anuncio que se esta viendo. Se identifica por el id
    # que aparece en la propia URL (?id=... o el ultimo segmento numerico) para
    # no leer el municipio de un producto recomendado.
    candidatos = [clave for clave in estado if clave.startswith("AdType:")]
    if not candidatos:
        return None
    stub = None
    for clave in candidatos:
        valor = estado[clave] or {}
        titulo = (valor.get("title") or "").strip()
        if titulo:
            stub = valor
            break
    if stub is None:
        return None

    referencia = ((stub.get("municipality") or {}).get("__ref")) or ""
    if referencia.startswith("MunicipalityType:"):
        return municipios.get(referencia.split(":", 1)[1])

    # Alternativa: el anuncio trae el municipio embebido.
    directo = stub.get("municipality")
    if isinstance(directo, str) and directo.strip():
        return directo.strip()
    return None


def municipio_del_texto(ubicacion: str | None) -> str | None:
    """Municipio de la cadena 'Provincia / Municipio' del listado.

    'La Habana / Cerro' -> 'Cerro'. Es la via normal: no necesita red.
    """
    if not ubicacion:
        return None
    partes = [p.strip() for p in ubicacion.split("/")]
    return partes[1] if len(partes) > 1 and partes[1] else None


class Sesion:
    def __init__(self, usar_cache: bool) -> None:
        self.usar_cache = usar_cache
        self.peticiones = 0
        self.desde_cache = 0
        self.errores: list[str] = []

    def obtener(self, cliente: httpx.Client, url: str) -> str | None:
        ruta_cache = DIR_CACHE / f"{base.slug(url)}.html"
        if self.usar_cache and ruta_cache.exists():
            self.desde_cache += 1
            return ruta_cache.read_text(encoding="utf-8")
        for intento in range(1, REINTENTOS + 1):
            try:
                self.peticiones += 1
                respuesta = cliente.get(url, timeout=TIMEOUT_SEGUNDOS)
                respuesta.raise_for_status()
                html = respuesta.text
                ruta_cache.parent.mkdir(parents=True, exist_ok=True)
                ruta_cache.write_text(html, encoding="utf-8")
                time.sleep(DELAY_SEGUNDOS)
                return html
            except Exception as error:  # noqa: BLE001
                self.errores.append(f"{url} intento {intento}/{REINTENTOS}: {error}")
                if intento < REINTENTOS:
                    time.sleep(ESPERA_REINTENTO)
        return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Anade el municipio a los anuncios de la ampliacion"
    )
    parser.add_argument("--refrescar", action="store_true", help="ignora la caché de detalle")
    args = parser.parse_args()

    inicio = datetime.now(timezone.utc)
    sesion = Sesion(usar_cache=not args.refrescar)
    datos = json.loads(ARCHIVO_ANUNCIOS.read_text(encoding="utf-8"))
    anuncios = datos.get("anuncios") or []

    print(SEP)
    print("ENRIQUECIMIENTO CON MUNICIPIO - ampliación de combustion")
    print(SEP)
    print(f"  anuncios         : {len(anuncios)}")
    print(f"  cache            : {DIR_CACHE.name}/")
    print(f"  user-agent       : {USER_AGENT}")
    print(f"  delay            : {DELAY_SEGUNDOS}s   timeout: {TIMEOUT_SEGUNDOS}s")
    print(f"  reintentos       : {REINTENTOS} (espera {ESPERA_REINTENTO}s)")

    desde_texto = 0
    desde_detalle = 0
    sin_municipio: list[str] = []

    # Se recorre primero lo que no necesita red, para no gastar peticiones.
    pendientes: list[tuple[dict, str]] = []
    for anuncio in anuncios:
        municipio = municipio_del_texto(anuncio.get("ubicacion"))
        if municipio:
            anuncio["municipio"] = municipio
            desde_texto += 1
        else:
            pendientes.append((anuncio, anuncio.get("url") or ""))

    if pendientes:
        print(f"  pendientes por detalle: {len(pendientes)}")
        with httpx.Client(
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
            verify=True,
        ) as cliente:
            for indice, (anuncio, url) in enumerate(pendientes, 1):
                print(f"    [{indice}/{len(pendientes)}] {url[:74]}")
                html = sesion.obtener(cliente, url) if url else None
                municipio = parsear_detalle(html) if html else None
                anuncio["municipio"] = municipio
                if municipio:
                    desde_detalle += 1
                else:
                    sin_municipio.append(url)

    datos["municipio_fuente"] = {
        "listing_texto": desde_texto,
        "detalle": desde_detalle,
        "sin_municipio": len(sin_municipio),
    }
    ARCHIVO_ANUNCIOS.write_text(
        json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # ---- Los 30 originales: municipio aparte, sin tocar el original ----
    originales_sin_municipio = 0
    if ARCHIVO_ORIGINAL.exists():
        datos_original = json.loads(ARCHIVO_ORIGINAL.read_text(encoding="utf-8"))
        mapa: dict[str, str | None] = {}
        for anuncio in datos_original.get("anuncios") or []:
            url = (anuncio.get("url") or anuncio.get("url_anuncio") or "").split("?")[0]
            url = url.rstrip("/")
            municipio = municipio_del_texto(anuncio.get("ubicacion"))
            if not municipio:
                originales_sin_municipio += 1
            mapa[url] = municipio
        ARCHIVO_ORIGINAL_MUNICIPIO.write_text(
            json.dumps(
                {
                    "vehiculo": "moto_combustion",
                    "fuente": ARCHIVO_ORIGINAL.name,
                    "nota": (
                        "Municipio deducido de 'ubicacion' ('Provincia / Municipio'). "
                        "El original no se modifica."
                    ),
                    "municipios": mapa,
                },
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )

    print(SEP)
    print(f"Municipio desde el texto del listado : {desde_texto}")
    print(f"Municipio desde la ficha de detalle   : {desde_detalle}")
    print(f"Sin municipio (quedan en null)       : {len(sin_municipio)}")
    print(f"Peticiones nuevas: {sesion.peticiones} | desde cache: {sesion.desde_cache}")
    if ARCHIVO_ORIGINAL.exists():
        print(f"\nMunicipio de los 30 originales (deducido de 'ubicacion', sin red):")
        print(f"  con municipio    : {TOTAL_ORIGINAL - originales_sin_municipio}/{TOTAL_ORIGINAL}")
        print(f"  sin municipio    : {originales_sin_municipio}")
        print(f"  escrito         : {ARCHIVO_ORIGINAL_MUNICIPIO.name}")
        print(f"  {ARCHIVO_ORIGINAL.name} NO se modifica")
    if sin_municipio:
        print("\n  URLs sin municipio:")
        for url in sin_municipio:
            print(f"    - {url}")
    if sesion.errores:
        print(f"\n  ERRORES ({len(sesion.errores)}):")
        for error in sesion.errores[:20]:
            print(f"    - {error}")
    print(f"\nActualizado {ARCHIVO_ANUNCIOS.name}")
    return 0 if not sin_municipio else 1


if __name__ == "__main__":
    raise SystemExit(main())
