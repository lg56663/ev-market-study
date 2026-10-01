#!/usr/bin/env python3
"""Fusiona el scraping original (30) con la ampliacion (60) en un JSON de 90.

Entradas:
  - bicis_electricas.json           : 30 anuncios originales (32 campos)
  - bicis_electricas_ampliacion.json: 60 anuncios nuevos (10 campos + municipio)
Salida:
  - bicis_electricas_90.json        : 90 anuncios con los 9 campos finales

Ambos conjuntos se proyectan a los mismos 9 campos. Lo que no existe en el
anuncio de origen se guarda como null: no se inventa ningun dato. La ubicacion
se copia tal cual venia.

Ninguno de los dos ficheros de entrada se modifica.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ARCHIVO_ORIGINAL = BASE_DIR / "bicis_electricas.json"
ARCHIVO_AMPLIACION = BASE_DIR / "bicis_electricas_ampliacion.json"
ARCHIVO_MERGE = BASE_DIR / "bicis_electricas_90.json"

FECHA_ORIGINAL = "2026-09-26"
FECHA_AMPLIACION = "2026-09-30"
TOTAL_ORIGINAL = 30
TOTAL_AMPLIACION = 64
TOTAL_OBJETIVO_MERGE = 90
PROVINCIA_OBJETIVO = "La Habana"
ANIO_MINIMO = 2026
MOTIVO_FUERA_DE_LA_HABANA = "fuera_de_la_habana_original"

CAMPOS = [
    "titulo",
    "marca",
    "motor_w",
    "autonomia_km",
    "precio_usd",
    "ubicacion",
    "url",
    "fecha_publicacion",
    "municipio",
]

RX_UBICACION_PARTIDA = re.compile(r"^(?P<provincia>[^/]+?)\s*/\s*(?P<municipio>[^/]+)$")


class ErrorMerge(Exception):
    pass


def cargar(ruta: Path, etiqueta: str) -> dict:
    if not ruta.exists():
        raise ErrorMerge(f"no existe el fichero de entrada {etiqueta}: {ruta.name}")
    crudo = ruta.read_text(encoding="utf-8").strip()
    if not crudo.startswith("{") or not crudo.endswith("}"):
        raise ErrorMerge(f"{ruta.name} no empieza con {{ ni termina con }}")
    try:
        return json.loads(crudo)
    except json.JSONDecodeError as exc:
        raise ErrorMerge(f"{ruta.name} no es JSON valido: {exc}") from exc


def extraer_municipio(anuncio: dict) -> str | None:
    """Campo 'municipio' del anuncio, o el derivado de 'Provincia / Municipio'."""
    municipio = anuncio.get("municipio")
    if isinstance(municipio, str) and municipio.strip():
        return municipio.strip()
    ubicacion = anuncio.get("ubicacion")
    if not isinstance(ubicacion, str) or "/" not in ubicacion:
        return None
    coincidencia = RX_UBICACION_PARTIDA.match(ubicacion)
    if not coincidencia:
        return None
    municipio = coincidencia.group("municipio").strip()
    if not municipio or any(c.isdigit() for c in municipio):
        return None
    return municipio


def proyectar(anuncio: dict) -> dict:
    """Reduce cualquier anuncio de entrada a los 9 campos finales, en orden."""
    return {
        "titulo": anuncio.get("titulo"),
        "marca": anuncio.get("marca"),
        "motor_w": anuncio.get("motor_w"),
        "autonomia_km": anuncio.get("autonomia_km"),
        "precio_usd": anuncio.get("precio_usd"),
        "ubicacion": anuncio.get("ubicacion"),
        "url": anuncio.get("url") or anuncio.get("url_anuncio"),
        "fecha_publicacion": anuncio.get("fecha_publicacion"),
        "municipio": extraer_municipio(anuncio),
    }


def es_de_la_habana(anuncio: dict) -> bool:
    ubicacion = anuncio.get("ubicacion")
    return isinstance(ubicacion, str) and ubicacion.strip() == PROVINCIA_OBJETIVO


def es_de_2026(anuncio: dict) -> bool:
    fecha = anuncio.get("fecha_publicacion")
    return isinstance(fecha, str) and fecha[:4].isdigit() and int(fecha[:4]) >= ANIO_MINIMO


def main() -> int:
    original = cargar(ARCHIVO_ORIGINAL, "original")
    ampliacion = cargar(ARCHIVO_AMPLIACION, "ampliacion")

    anuncios_original = original.get("anuncios") or []
    anuncios_ampliacion = ampliacion.get("anuncios") or []
    if not isinstance(anuncios_original, list) or not isinstance(anuncios_ampliacion, list):
        raise ErrorMerge('"anuncios" debe ser una lista en los dos ficheros de entrada')

    if len(anuncios_original) != TOTAL_ORIGINAL:
        raise ErrorMerge(
            f"el original tiene {len(anuncios_original)} anuncios, se esperaban {TOTAL_ORIGINAL}"
        )
    if len(anuncios_ampliacion) != TOTAL_AMPLIACION:
        raise ErrorMerge(
            f"la ampliacion tiene {len(anuncios_ampliacion)} anuncios, "
            f"se esperaban {TOTAL_AMPLIACION}"
        )

    originales = [proyectar(anuncio) for anuncio in anuncios_original]
    nuevos = [proyectar(anuncio) for anuncio in anuncios_ampliacion]

    # Los originales que no son de La Habana no entran en el merge: quedan
    # registrados aparte para no perder la trazabilidad. No se sustituye ningun
    # dato, simplemente no se incluyen.
    originales_habana = [a for a in originales if a["ubicacion"] == PROVINCIA_OBJETIVO]
    excluidos = [
        {
            "titulo": a["titulo"],
            "ubicacion": a["ubicacion"],
            "motivo": MOTIVO_FUERA_DE_LA_HABANA,
        }
        for a in originales
        if a["ubicacion"] != PROVINCIA_OBJETIVO
    ]
    merged = originales_habana + nuevos

    if len(originales_habana) + len(nuevos) != TOTAL_OBJETIVO_MERGE:
        raise ErrorMerge(
            f"{len(originales_habana)} originales de {PROVINCIA_OBJETIVO} + {len(nuevos)} nuevos "
            f"= {len(merged)} anuncios, se esperaban {TOTAL_OBJETIVO_MERGE}"
        )

    errores: list[str] = []
    for indice, anuncio in enumerate(merged, 1):
        if list(anuncio.keys()) != CAMPOS:
            errores.append(f"[{indice}] campos fuera de esquema: {list(anuncio.keys())}")
        if not anuncio["url"]:
            errores.append(f"[{indice}] sin url ({anuncio['titulo']!r})")
        if anuncio["ubicacion"] != PROVINCIA_OBJETIVO:
            errores.append(
                f"[{indice}] ubicacion {anuncio['ubicacion']!r} != {PROVINCIA_OBJETIVO!r} "
                f"({anuncio['titulo']!r})"
            )
        if not es_de_2026(anuncio):
            errores.append(
                f"[{indice}] fecha_publicacion {anuncio['fecha_publicacion']!r} "
                f"anterior a {ANIO_MINIMO} ({anuncio['titulo']!r})"
            )

    urls: dict[str, int] = {}
    for indice, anuncio in enumerate(merged, 1):
        url = anuncio["url"]
        if url in urls:
            errores.append(f"[{indice}] url duplicada {url} (ya estaba en [{urls[url]}])")
        else:
            urls[url] = indice

    if errores:
        print("ERRORES: el merge NO se escribe:")
        for error in errores[:40]:
            print(f"  - {error}")
        if len(errores) > 40:
            print(f"  ... y {len(errores) - 40} más")
        return 1

    salida = {
        "vehiculo": "bicimoto_electrica",
        "fecha_scraping_original": FECHA_ORIGINAL,
        "fecha_scraping_ampliacion": FECHA_AMPLIACION,
        "total_anuncios_validos": len(merged),
        "filtro_ubicacion": "La Habana",
        "filtro_fecha": ">= 2026-01-01",
        "fuente_original": "bicis_electricas.json",
        "fuente_ampliacion": "bicis_electricas_ampliacion.json",
        "total_originales_habana": len(originales_habana),
        "total_nuevos_habana": len(nuevos),
        "anuncios_originales_excluidos": excluidos,
        "campos": CAMPOS,
        "anuncios": merged,
    }
    with open(ARCHIVO_MERGE, "w", encoding="utf-8") as archivo:
        json.dump(salida, archivo, ensure_ascii=False, indent=2)
        archivo.write("\n")

    relectura = json.loads(ARCHIVO_MERGE.read_text(encoding="utf-8"))
    anuncios = relectura.get("anuncios") or []
    if (
        relectura.get("total_anuncios_validos") != TOTAL_OBJETIVO_MERGE
        or len(anuncios) != TOTAL_OBJETIVO_MERGE
    ):
        raise ErrorMerge(f"el JSON escrito no declara {TOTAL_OBJETIVO_MERGE} anuncios")
    if relectura.get("campos") != CAMPOS:
        raise ErrorMerge("el JSON escrito no declara los 9 campos esperados")
    if any(list(a.keys()) != CAMPOS for a in anuncios):
        raise ErrorMerge("algún anuncio del JSON escrito no tiene exactamente los 9 campos")
    if len({a["url"] for a in anuncios}) != TOTAL_OBJETIVO_MERGE:
        raise ErrorMerge(f"el JSON escrito no tiene {TOTAL_OBJETIVO_MERGE} urls unicas")
    if any(a["ubicacion"] != PROVINCIA_OBJETIVO for a in anuncios):
        raise ErrorMerge("el JSON escrito contiene anuncios fuera de La Habana")
    if relectura.get("anuncios_originales_excluidos") != excluidos:
        raise ErrorMerge("el JSON escrito no conserva los originales excluidos")
    n_originales = len(originales_habana)
    if anuncios[:n_originales] != originales_habana or anuncios[n_originales:] != nuevos:
        raise ErrorMerge(
            f"el orden del JSON escrito no es {n_originales} originales + {len(nuevos)} nuevos"
        )

    precios = [a["precio_usd"] for a in anuncios if a["precio_usd"] is not None]
    motores = [a["motor_w"] for a in anuncios if a["motor_w"] is not None]

    print("MERGE DE BICIMOTOS ELECTRICAS (La Habana)")
    print("=" * 52)
    print(f"  Total anuncios: {len(anuncios)} ({n_originales} originales + {len(nuevos)} nuevos)")
    print(f"  De La Habana: {sum(1 for a in anuncios if a['ubicacion'] == PROVINCIA_OBJETIVO)}/{len(anuncios)}")
    print(f"  Del anio {ANIO_MINIMO} o posterior: {sum(1 for a in anuncios if es_de_2026(a))}/{len(anuncios)}")
    print(f"  Total URLs unicas: {len({a['url'] for a in anuncios})}")
    print(f"  Campos por anuncio: {len(anuncios[0])} ({', '.join(CAMPOS)})")
    print(f"  con fecha_publicacion != null: {sum(1 for a in anuncios if a['fecha_publicacion'] is not None)}")
    print(f"  con municipio != null: {sum(1 for a in anuncios if a['municipio'] is not None)}")
    print(f"  precio_usd: {min(precios)} - {max(precios)}" if precios else "  precio_usd: sin datos")
    print(f"  motor_w: {min(motores)} - {max(motores)}" if motores else "  motor_w: sin datos")
    print("  top 5 marcas:")
    for marca, total in Counter(a["marca"] for a in anuncios if a["marca"]).most_common(5):
        print(f"    {total:>3}  {marca}")
    if excluidos:
        print(f"\n  ORIGINALES EXCLUIDOS ({len(excluidos)}, motivo {MOTIVO_FUERA_DE_LA_HABANA}):")
        for anuncio in excluidos:
            print(f"    - {anuncio['ubicacion']}: {anuncio['titulo']}")
    print(f"\nEscrito {ARCHIVO_MERGE.name}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ErrorMerge as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)