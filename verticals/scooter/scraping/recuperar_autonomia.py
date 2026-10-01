#!/usr/bin/env python3
"""Recupera anuncios descartados por sin_autonomia entrando a su detalle.

El listado de Revolico no expone la autonomia, y el scraper de ampliacion solo
lee listados, asi que 253 anuncios que si son scooters electricos de La Habana
de 2026 quedaron fuera por no declarar autonomia en el titulo. Aqui se entra a
su pagina de detalle y se extrae la autonomia de la descripcion completa, que si
la lleva.

Solo se acepta lo que el propio anuncio dice: si la descripcion no declara una
autonomia, el anuncio se queda en descartados con el null puesto. No se inventa
ningun valor.

Cache separada: cache_detalle_scooters_recuperacion/
Salida: scooters_recuperados.json, que merge_scooter.py consume como entrada.
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
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import scraper_scooter_ampliacion as ampliacion  # noqa: E402

ARCHIVO_DESCARTADOS = BASE_DIR / "descartados_scooters_ampliacion.json"
ARCHIVO_SALIDA = BASE_DIR / "scooters_recuperados.json"
ARCHIVO_REPORTE = BASE_DIR / "reporte_recuperacion_scooters.txt"
DIR_CACHE = BASE_DIR / "cache_detalle_scooters_recuperacion"

USER_AGENT = "MiProyectoInvestigacion/1.0"
DELAY_SEGUNDOS = 3.0
TIMEOUT_SEGUNDOS = 30.0
REINTENTOS = 2
ESPERA_REINTENTO = 5.0
MAX_PETICIONES = 300
ANIO_MINIMO = 2026
AUTONOMIA_MAXIMA = 300
FECHA_SCRAPING = "2026-09-30"

MUNICIPIOS_HABANA = ampliacion.MUNICIPIOS_HABANA

CAMPOS_ANUNCIO = ampliacion.CAMPOS_ANUNCIO

# Los cuatro patrones que pide el enunciado, mas el rango explicito
# "60 a 80 km", que es la forma habitual en estos anuncios.
PATRONES_AUTONOMIA = [
    re.compile(r"(\d{1,3})\s*(?:km|kilometros?)\s*(?:de\s*)?(?:autonomia|recorrido|alcance)"),
    re.compile(r"autonomia\s*(?:de\s*)?(\d{1,3})\s*(?:km|kilometros?)"),
    re.compile(r"(\d{1,3})\s*(?:km|kilometros?)\s*(?:por|con)\s*carga"),
    re.compile(r"hasta\s*(\d{1,3})\s*(?:km|kilometros?)"),
]
PATRON_AUTONOMIA_RANGO = re.compile(
    r"autonomia\s*(?:de\s*)?(\d{1,3})\s*(?:a|-|to)\s*(\d{1,3})\s*(?:km|kilometros?)"
)
# Contexto que delata que el "km" es velocidad y no autonomia.
CONTEXTO_VELOCIDAD = ("velocidad", "vmax", "maxima", "max", "rapidez")
RX_MOTOR_W = re.compile(r"(\d{3,5})\s*w(?![a-z0-9])")


def descripcion_de(html: str) -> str | None:
    """Descripcion completa del anuncio (__NEXT_DATA__ -> __APOLLO_STATE__)."""
    soup = BeautifulSoup(html, "lxml")
    script = soup.find("script", id="__NEXT_DATA__")
    if script is None or not script.string:
        return None
    try:
        estado = json.loads(script.string)["props"]["pageProps"]["__APOLLO_STATE__"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
    for clave, valor in estado.items():
        if clave.startswith("AdType:") and isinstance(valor, dict):
            descripcion = valor.get("description")
            if descripcion:
                return str(descripcion)
    return None


def extraer_autonomia_descripcion(texto: str) -> int | None:
    """Autonomia declarada en la descripcion. None si no dice ninguna."""
    candidatos: list[int] = []
    for patron in PATRONES_AUTONOMIA:
        for coincidencia in patron.finditer(texto):
            antes = texto[max(0, coincidencia.start() - 30) : coincidencia.start()]
            if any(palabra in antes for palabra in CONTEXTO_VELOCIDAD):
                continue
            candidatos.append(int(coincidencia.group(1)))
    for bajo, alto in PATRON_AUTONOMIA_RANGO.findall(texto):
        candidatos.extend([int(bajo), int(alto)])

    # Ultimo recurso: un "N km" suelto, descartando los que son km/h.
    if not candidatos:
        for coincidencia in re.finditer(r"(\d{1,3})\s*(?:km|kilometros?)\b(?!/h)", texto):
            antes = texto[max(0, coincidencia.start() - 30) : coincidencia.start()]
            if any(palabra in antes for palabra in CONTEXTO_VELOCIDAD):
                continue
            candidatos.append(int(coincidencia.group(1)))

    validos = [v for v in candidatos if 0 < v < AUTONOMIA_MAXIMA]
    return max(validos) if validos else None


def extraer_motor_w_descripcion(texto: str) -> int | None:
    valores = [int(m) for m in RX_MOTOR_W.findall(texto)]
    valores = [v for v in valores if 50 <= v <= 60000]
    return max(valores) if valores else None


def municipio_de(html: str) -> str | None:
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
        if clave.startswith("AdType:") and isinstance(valor, dict):
            municipio = valor.get("municipality")
            if isinstance(municipio, dict) and municipio.get("__ref"):
                referencia = municipio["__ref"]
                break
    if not referencia:
        return None
    for clave, valor in estado.items():
        if clave.startswith("MunicipalityType:") and isinstance(valor, dict) and valor.get("name"):
            id_municipio = str(valor.get("id"))
            if referencia.endswith(f":{id_municipio}") or id_municipio in referencia:
                return valor["name"]
    return None


def escribir_json(ruta: Path, datos) -> None:
    with open(ruta, "w", encoding="utf-8") as archivo:
        json.dump(datos, archivo, ensure_ascii=False, indent=2)
        archivo.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Recupera sin_autonomia desde el detalle")
    parser.add_argument("--limite", type=int, default=0, help="maximo a procesar (0 = todos)")
    parser.add_argument("--refrescar", action="store_true", help="ignora la caché de detalle")
    args = parser.parse_args()
    usar_cache = not args.refrescar

    ahora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    descartados = json.loads(ARCHIVO_DESCARTADOS.read_text(encoding="utf-8")).get("descartes") or []
    candidatos = [d for d in descartados if d.get("motivo") == "sin_autonomia" and d.get("url")]
    if args.limite:
        candidatos = candidatos[: args.limite]
    print(f"Descartes con motivo sin_autonomia: {len(candidatos)}")
    print(f"A procesar: {len(candidatos)} (cache: {DIR_CACHE.name}/)\n")
    if usar_cache:
        DIR_CACHE.mkdir(exist_ok=True)

    recuperados: list[dict] = []
    sin_autonomia: list[dict] = []
    sin_descripcion: list[dict] = []
    peticiones = 0
    desde_cache = 0
    errores: list[str] = []

    with httpx.Client(follow_redirects=True, verify=True) as cliente:
        for indice, descarte in enumerate(candidatos, 1):
            url = descarte["url"]
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

            if html is None:
                sin_descripcion.append(dict(descarte, motivo_recuperacion="sin_peticion"))
                continue

            descripcion = descripcion_de(html)
            if not descripcion:
                sin_descripcion.append(dict(descarte, motivo_recuperacion="sin_descripcion"))
                continue

            texto = ampliacion.base.normalizar(descripcion)
            autonomia = extraer_autonomia_descripcion(texto)
            if autonomia is None:
                sin_autonomia.append(
                    dict(descarte, motivo_recuperacion="sin_autonomia_en_descripcion")
                )
                continue

            registro = {campo: descarte.get(campo) for campo in CAMPOS_ANUNCIO}
            registro["autonomia_km"] = autonomia
            registro["municipio"] = municipio_de(html)
            if registro.get("motor_w") is None:
                registro["motor_w"] = extraer_motor_w_descripcion(texto)
            registro["descripcion"] = descripcion
            registro["motivo_recuperacion"] = "recuperado_de_detalle"
            recuperados.append(registro)

            if indice % 10 == 0 or indice == len(candidatos):
                print(
                    f"[{indice:>3}/{len(candidatos)}] recuperados={len(recuperados)} "
                    f"sin_autonomia={len(sin_autonomia)} pets={peticiones} cache={desde_cache}"
                )

    escribir_json(
        ARCHIVO_SALIDA,
        {
            "vehiculo": "scooter_electrico",
            "fecha_scraping": FECHA_SCRAPING,
            "tipo": "recuperacion_de_detalle",
            "filtro_ubicacion": "La Habana",
            "filtro_fecha": ">= 2026-01-01",
            "total_procesados": len(candidatos),
            "total_recuperados_autonomia": len(recuperados),
            "anuncios": recuperados,
        },
    )
    escribir_json(
        ARCHIVO_SALIDA.with_name("recuperados_no_resueltos.json"),
        {
            "vehiculo": "scooter_electrico",
            "tipo": "recuperacion_de_detalle",
            "total_sin_autonomia": len(sin_autonomia),
            "total_sin_descripcion": len(sin_descripcion),
            "sin_autonomia": sin_autonomia,
            "sin_descripcion": sin_descripcion,
        },
    )

    # Cuantos de los recuperados pasan ademas el resto de filtros del scraper.
    # Ya entran filtrados por La Habana y 2026 (asi los dejo el scraper de
    # ampliacion), asi que aqui solo se comprueba lo que faltaba: precio, marca
    # y el minimo de motor.
    pasan = 0
    motivos: Counter = Counter()
    for registro in recuperados:
        if registro.get("ubicacion") != "La Habana":
            motivos["fuera_de_la_habana"] += 1
        elif not registro.get("fecha_publicacion") or int(registro["fecha_publicacion"][:4]) < ANIO_MINIMO:
            motivos["fecha_no_2026"] += 1
        elif registro.get("precio_usd") is None:
            motivos["sin_precio"] += 1
        elif registro.get("marca") is None:
            motivos["sin_marca"] += 1
        elif registro.get("motor_w") is not None and registro["motor_w"] < ampliacion.MOTOR_MINIMO_W:
            motivos["motor_w_bajo"] += 1
        else:
            motivos["pasa_todos_los_filtros"] += 1
            pasan += 1

    lineas = [
        "RECUPERACION DE AUTONOMIA DESDE EL DETALLE - SCOOTERS (Revolico)",
        "=" * 70,
        f"Fecha: {ahora}",
        "",
        "CONFIGURACION:",
        f"  User-Agent: {USER_AGENT}",
        f"  Delay entre peticiones: {DELAY_SEGUNDOS}s",
        f"  Timeout: {TIMEOUT_SEGUNDOS}s",
        f"  Reintentos por URL: {REINTENTOS} (espera {ESPERA_REINTENTO}s)",
        f"  Cache: {DIR_CACHE.name}/",
        f"  Limite por sesion: {MAX_PETICIONES} peticiones",
        "",
        "RED:",
        f"  Peticiones nuevas: {peticiones}",
        f"  HTML desde cache: {desde_cache}",
        "",
        "RESULTADOS:",
        f"  Descartes sin_autonomia procesados: {len(candidatos)}",
        f"  Recuperaron autonomia de la descripcion: {len(recuperados)}",
        f"  Sin autonomia en la descripcion: {len(sin_autonomia)}",
        f"  Sin descripcion / sin peticion: {len(sin_descripcion)}",
        f"  De los recuperados, pasan todos los filtros: {pasan}",
        "",
        "RECUPERADOS POR ESTADO:",
    ]
    for motivo, total in motivos.most_common():
        lineas.append(f"  {total:>5}  {motivo}")
    if recuperados:
        lineas += [
            "",
            "AUTONOMIA DE LOS RECUPERADOS:",
            f"  min={min(a['autonomia_km'] for a in recuperados)}"
            f"  max={max(a['autonomia_km'] for a in recuperados)}",
        ]
    if errores:
        lineas += ["", f"ERRORES REGISTRADOS ({len(errores)}):"]
        for error in errores[:40]:
            lineas.append(f"  - {error}")
    ARCHIVO_REPORTE.write_text("\n".join(lineas) + "\n", encoding="utf-8")

    print("\n" + "=" * 52)
    print(f"Procesados (sin_autonomia): {len(candidatos)}")
    print(f"Recuperaron autonomia: {len(recuperados)}")
    print(f"  de ellos pasan todos los filtros: {pasan}")
    print(f"Sin autonomia en la descripcion: {len(sin_autonomia)}")
    print(f"Sin descripcion / sin peticion: {len(sin_descripcion)}")
    print(f"Peticiones nuevas: {peticiones} | Desde cache: {desde_cache}")
    print(f"\nEscrito {ARCHIVO_SALIDA.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
