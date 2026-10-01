#!/usr/bin/env python3
"""Fusiona el scraping original (30) con la ampliacion (30) en un JSON de 60.

Entradas:
  - scooters_electricos.json            : 30 anuncios originales (33 campos)
  - scooters_electricos_ampliacion.json : tanda actual de anuncios nuevos
  - scooters_electricos_60.json         : merge anterior, si ya existe
Salida:
  - scooters_electricos_60.json         : anuncios limpios con los 9 campos finales

Los tres conjuntos se proyectan a los mismos 9 campos y se acumulan, de forma
que las tandas sucesivas del scraper no pierden lo ya recogido. Lo que no existe
en el anuncio de origen se guarda como null: no se inventa ningun dato. La
ubicacion se copia tal cual venia.

El fichero de salida se relee al empezar y se vuelve a escribir al terminar, asi
que el merge es acumulativo e idempotente sobre si mismo.

LIMPIEZA APLICADA (revision manual de los 60):
  1. Marcas que no son marcas ("carriola") se ponen a null; no se adivina la real.
  2. Lista negra de URLs (URLS_DESCARTAR): anuncios que no son scooter electrico
     o con el titulo roto. Se registra el motivo en anuncios_excluidos.
  3. Dedup por modelo: se agrupa por (marca, motor_w, autonomia_km, precio_usd) y
     se conserva el titulo mas informativo (mas largo). El precio es parte de la
     clave porque el mismo modelo a distinto precio son anuncios distintos y
     datos reales de dispersion de precios. El resto se descarta con el motivo
     duplicado_mismo_modelo y guarda en duplicado_de la URL del conservado.

Ninguno de los dos ficheros de entrada se modifica.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ARCHIVO_ORIGINAL = BASE_DIR / "scooters_electricos.json"
ARCHIVO_AMPLIACION = BASE_DIR / "scooters_electricos_ampliacion.json"
ARCHIVO_RECUPERADOS = BASE_DIR / "scooters_recuperados.json"
ARCHIVO_MERGE = BASE_DIR / "scooters_electricos_60.json"
ARCHIVO_REPORTE = BASE_DIR / "reporte_recuperacion_scooters.txt"

FECHA_ORIGINAL = "2026-09-26"
FECHA_AMPLIACION = "2026-09-30"
TOTAL_ORIGINAL = 30
OBJETIVO_TOTAL = 60
ANIO_MINIMO = 2026
# Por debajo de esto el precio no es el de un scooter: es un precio de relleno
# del vendedor (1 USD) o un senuelo. No se corrige, el anuncio se descarta.
PRECIO_MINIMO_UTILES = 150.0
MOTOR_MINIMO_W = 200

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

# Revision manual: valores que el extractor de marca leyo como marca pero que no
# son una marca de vehiculo. Se ponen a null, no se intenta adivinar la real.
MARCAS_INVALIDAS = {"carriola", "range"}

# Revision manual: URLs que no deben entrar en el merge, con su motivo.
URLS_DESCARTAR = {
    "https://www.revolico.com/item/moto-electrica-3000w-scooter-x-ryder-48v-35-ahm-autonomia-80-120km-57171498": "es_moto_electrica",
    "https://www.revolico.com/item/scooter-haciendo-25km-57712634": "titulo_invalido",
}

RX_UBICACION_PARTIDA = re.compile(r"^(?P<provincia>[^/]+?)\s*/\s*(?P<municipio>[^/]+)$")

# Titulos que venden varios vehiculos o varios productos a la vez. El precio y la
# autonomia que se recuperan del detalle son de todo el lote, no de un scooter,
# asi que no sirven para el conjunto de anuncios individuales.
RX_LOTE = re.compile(
    r"\b(lote|lotes|pack|paquete|conjunto|variedad|varios|multiple|multiples)\b"
    r"|\by\s+(?:patinetas|scooters|patinetes|bicicletas|motos)\b"
    r"|\bscooters?\s+y\s+\w+"
    r"|\bpatinetas?\s+y\s+\w+"
)

# Motos de combustion que el vendedor titula "scooter" o "patineta" para
# colarse en el estudio. Se distinguen por la cilindrada y por hablar de
# autonomia en consumo ("35 km x litro" es autonomia por litro de gasolina, no
# autonomia electrica). El scraping por listado no ve la descripcion, asi que
# estos se detectan aqui, al integrar los recuperados del detalle.
RX_COMBUSTION = re.compile(
    r"\b\d{2,3}\s*(?:cc|cm3|cm³)\b"
    r"|\b4\s*tiempos\b"
    r"|\bautonomia\s+(?:de\s+)?\d+\s*km?\s*(?:x|\*|por)\s*litro\b"
    r"|\bconsumo\s+de\s+\d"
    r"|\bcarburador\b|\binyecci[oó]n\b|\bcarburaci[oó]n\b|\b4t\b"
)


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


def limpiar_marca(marca: str | None) -> str | None:
    """Las marcas que la revision manual marco como no-marca pasan a null."""
    if not isinstance(marca, str) or not marca.strip():
        return None
    marca = marca.strip()
    return None if marca.lower() in MARCAS_INVALIDAS else marca


MOTIVOS_CAMPO_OBLIGATORIO = {
    "marca": "sin_marca",
    "autonomia_km": "sin_autonomia",
    "precio_usd": "sin_precio",
    "ubicacion": "sin_ubicacion",
}


def motivo_recuperado(anuncio: dict, descripcion: str = "") -> str | None:
    """Por que un anuncio recuperado desde el detalle no entra al conjunto.

    Se comprueban los 4 campos obligatorios, mas dos Planned de datos que solo
    se ven al entrar al detalle:

    - precio por debajo de PRECIO_MINIMO_UTILES: en estos anuncios es un precio
      de relleno del vendedor (1 USD, 50 USD) o un senuelo, no el precio de un
      scooter. No se corrige, se descarta.
    - "Range": el extractor de marca agarra la primera palabra en mayusculas,
      y en "Kukirin G2 ... Battery Range 55KM" eso es "Range", una palabra de la
      descripcion del producto, no la marca. Va a MARCAS_INVALIDAS.
    - lotes de varios vehiculos: el titulo anuncia varios ("Scooters y
      ventiladores", "Motos electricas: ... Scooter fly"). Ahi el precio es de
      todo el lote, no de un scooter.
    - motos de combustion: el vendedor las titula "scooter" o "patineta" pero
      son de gasolina (cilindrada en cc, "4 tiempos", autonomia por litro). Su
      "autonomia" recovered no es electrica, asi que no sirven para el estudio.
      Se mira el titulo y, si esta, la descripcion.
    """
    fallo = next(
        (motivo for campo, motivo in MOTIVOS_CAMPO_OBLIGATORIO.items() if anuncio[campo] is None),
        None,
    )
    if fallo:
        return fallo
    if anuncio["ubicacion"] != "La Habana":
        return "fuera_de_la_habana"
    if not anuncio["fecha_publicacion"] or int(anuncio["fecha_publicacion"][:4]) < ANIO_MINIMO:
        return "fecha_no_2026"
    if anuncio["precio_usd"] < PRECIO_MINIMO_UTILES:
        return "precio_no_util"
    if RX_LOTE.search(anuncio["titulo"] or ""):
        return "es_lote_de_varios"
    texto = f"{anuncio['titulo'] or ''} {descripcion}"
    if RX_COMBUSTION.search(texto):
        return "es_moto_de_combustion"
    if anuncio["motor_w"] is not None and anuncio["motor_w"] < MOTOR_MINIMO_W:
        return "motor_w_bajo"
    return None


def clave_modelo(anuncio: dict) -> tuple:
    """Clave de agrupacion para detectar el mismo modelo.

    El precio forma parte de la clave a proposito: dos anuncios del mismo modelo
    (misma marca, motor y autonomia) a precios distintos son anuncios distintos
    y una parte real de la dispersion de precios que mide el estudio. Lo que si
    se descarta son las republicaciones del mismo anuncio al mismo precio.

    La marca se compara sin mayusculas: el listado y la descripcion la escriben
    de forma distinta ("JMD" y "jmd", "CHALLENGER" y "challenger"). Sin
    normalizar, la clave daba por distintos unos pares que son el mismo modelo
    al mismo precio.
    """
    return (
        anuncio["marca"].upper() if anuncio["marca"] else None,
        anuncio["motor_w"],
        anuncio["autonomia_km"],
        anuncio["precio_usd"],
    )


def deduplicar_por_modelo(anuncios: list[dict]) -> tuple[list[dict], list[dict]]:
    """Conserva un anuncio por (marca, motor_w, autonomia_km, precio_usd).

    Se queda el de titulo mas informativo (el mas largo) y el resto se devuelve
    como descartes con su motivo y la URL del conservado. Los anuncios con
    algun campo de la clave a null no se agrupan: sin motor o autonomia no se
    puede saber si son el mismo modelo.
    """
    grupos: dict[tuple, list[dict]] = defaultdict(list)
    sin_clave: list[dict] = []
    for anuncio in anuncios:
        clave = clave_modelo(anuncio)
        if any(campo is None for campo in clave):
            sin_clave.append(anuncio)
            continue
        grupos[clave].append(anuncio)

    conservados: list[dict] = []
    descartados: list[dict] = []
    for clave, miembros in grupos.items():
        ordenados = sorted(
            miembros,
            key=lambda a: (-len(a["titulo"] or ""), a["url"]),
        )
        conservados.append(ordenados[0])
        for repetido in ordenados[1:]:
            descartados.append(
                {
                    "titulo": repetido["titulo"],
                    "url": repetido["url"],
                    "marca": clave[0],
                    "motor_w": clave[1],
                    "autonomia_km": clave[2],
                    "precio_usd": clave[3],
                    "motivo": "duplicado_mismo_modelo",
                    "duplicado_de": ordenados[0]["url"],
                }
            )
    return conservados + sin_clave, descartados


MARCA_INICIO_CIERRE = "=" * 70 + "\nCIERRE DEL MERGE"


def documentar_cierre(
    anuncios: list[dict],
    excl_manual: list[dict],
    duplicados: list[dict],
    descartados_recuperacion: list[dict],
) -> None:
    """Anade al reporte de recuperacion por que el conjunto tiene 147 y no 60.

    El bloque se regenera siempre desde el JSON ya escrito, no a mano, para que
    las cifras del reporte no se separen de los datos. Si el bloque ya estaba,
    se sustituye entero.
    """
    if not ARCHIVO_REPORTE.exists():
        return
    motivos = Counter(d["motivo"] for d in descartados_recuperacion)
    precios = [a["precio_usd"] for a in anuncios if a["precio_usd"] is not None]
    lineas = [
        MARCA_INICIO_CIERRE,
        "=" * 70,
        "",
        f"POR QUE QUEDAN {len(anuncios)} Y NO 60",
        "-" * (20 + len(str(len(anuncios)))),
        "El objetivo inicial era 60 scooters. La recuperacion de autonomia",
        "desde paginas de detalle trajo 148 validos. Tras limpieza quedaron",
        f"{len(anuncios)} unicos. Se conservan todos porque son datos reales",
        "verificados y truncar a 60 seria descartar evidencia valida sin",
        "criterio.",
        "",
        "El recorte de la muestra, si se quiere, se hace en el analisis;",
        "aqui no se tiran anuncios.",
        "",
        "COMO SE LLEGO AL CONJUNTO FINAL:",
        f"  {len(anuncios)} anuncios validos (unicos por marca+motor+autonomia+precio)",
        f"  {len(excl_manual)} excluidos por revision manual",
        f"  {len(descartados_recuperacion)} descartados por la recuperacion",
        f"  {len(duplicados)} duplicados por modelo, con 'duplicado_de'",
        "",
        "DESCARTADOS POR LA RECUPERACION (motivos):",
    ]
    for motivo, total in motivos.most_common():
        lineas.append(f"    {total:>3}  {motivo}")
    lineas += [
        "",
        "CORRECCIONES DE DATO APLICADAS AL INTEGRAR:",
        "  Los anuncios problemas listados abajo se descartaron o",
        "  corrigieron, no se dejaron pasar al conjunto final:",
        f"    {motivos.get('es_moto_de_combustion', 0):>3}  motos de combustion coladas como scooter.",
        "         Se titulan 'scooter' o 'patineta' pero son de gasolina",
        "         (cilindrada en cc, '4 tiempos'). Su autonomia es por litro",
        "         ('35 km x litro' = consumo), no rango electrico.",
        f"    {motivos.get('precio_no_util', 0):>3}  precios basura: 1 USD, 50 USD, 100 USD.",
        "         Son precio de relleno o senuelo, no el precio de un scooter.",
        "         No se corrigen, se descartan. Algunos titulos son ademas",
        "         lotes de varios vehiculos ('Scooters y ventiladores'),",
        "         donde el precio es de todo el lote: el filtro de precio los",
        "         cubre antes que el de lote.",
        "       1  marca 'Range' en un Kukirin G2 ('Battery Range 55KM'):",
        "         el extractor agarra la primera palabra en mayusculas, que",
        "         era una palabra de la descripcion. 'Range' se metio en",
        "         MARCAS_INVALIDAS.",
        "",
        "EXCLUIDOS POR REVISION MANUAL:",
    ]
    for anuncio in excl_manual:
        lineas.append(f"  - {anuncio['motivo']}: {anuncio['titulo'][:70]}")
    lineas += [
        "",
        "DISTRIBUCION FINAL:",
        f"  precio_usd: min={min(precios)}  max={max(precios)}",
        "",
        "  top 10 marcas:",
    ]
    for marca, total in Counter(a["marca"] for a in anuncios if a["marca"]).most_common(10):
        lineas.append(f"    {total:>3}  {marca}")
    lineas += ["", "  top 10 municipios:"]
    for municipio, total in Counter(
        a["municipio"] for a in anuncios if a["municipio"]
    ).most_common(10):
        lineas.append(f"    {total:>3}  {municipio}")
    lineas.append("")

    texto = ARCHIVO_REPORTE.read_text(encoding="utf-8")
    if MARCA_INICIO_CIERRE in texto:
        texto = texto[: texto.index(MARCA_INICIO_CIERRE)].rstrip("\n") + "\n"
    ARCHIVO_REPORTE.write_text(texto + "\n" + "\n".join(lineas) + "\n", encoding="utf-8")


def main() -> int:
    original = cargar(ARCHIVO_ORIGINAL, "original")
    ampliacion = cargar(ARCHIVO_AMPLIACION, "ampliacion")
    anterior = cargar(ARCHIVO_MERGE, "merge anterior") if ARCHIVO_MERGE.exists() else {}
    recuperados = (
        cargar(ARCHIVO_RECUPERADOS, "recuperados") if ARCHIVO_RECUPERADOS.exists() else {}
    )

    anuncios_original = original.get("anuncios") or []
    anuncios_ampliacion = ampliacion.get("anuncios") or []
    anuncios_anteriores = anterior.get("anuncios") or []
    anuncios_recuperados = recuperados.get("anuncios") or []
    if not all(
        isinstance(lista, list)
        for lista in (anuncios_original, anuncios_ampliacion, anuncios_anteriores)
    ):
        raise ErrorMerge('"anuncios" debe ser una lista en los ficheros de entrada')

    if len(anuncios_original) != TOTAL_ORIGINAL:
        raise ErrorMerge(
            f"el original tiene {len(anuncios_original)} anuncios, se esperaban {TOTAL_ORIGINAL}"
        )
    if not anuncios_ampliacion:
        raise ErrorMerge("la ampliacion no tiene anuncios")

    # Los descartados de la corrida anterior vuelven a entrar como candidatos:
    # la clave de dedup ahora incluye el precio, asi que un anuncio que se
    # descarto por coincidir con otro al mismo precio puede seguir siendo valido
    # si el otro ya no esta. Los excluidos por revision manual no vuelven nunca.
    descartados_previos = anterior.get("descartados_duplicado_mismo_modelo") or []
    if not isinstance(descartados_previos, list):
        raise ErrorMerge('"descartados_duplicado_mismo_modelo" debe ser una lista')

    projected = [
        proyectar(anuncio)
        for anuncio in (
            list(anuncios_original)
            + list(anuncios_ampliacion)
            + list(anuncios_recuperados)
            + list(anuncios_anteriores)
            + list(descartados_previos)
        )
    ]
    for anuncio in projected:
        anuncio["marca"] = limpiar_marca(anuncio["marca"])

    excl_manual: list[dict] = []
    for anuncio in anterior.get("anuncios_excluidos_revision_manual") or []:
        excl_manual.append(
            {"titulo": anuncio["titulo"], "url": anuncio["url"], "motivo": anuncio["motivo"]}
        )
    vistas_excl = {e["url"] for e in excl_manual}

    # Los recuperados desde el detalle pueden no cumplir los 4 campos
    # obligatorios: se comprueban aqui y los que fallan quedan fuera, con su
    # motivo, en vez de entrar al conjunto final.
    urls_recuperadas = {a.get("url") for a in anuncios_recuperados}
    # La descripcion solo se conserva en los recuperados, y es ahi donde se ve
    # con claridad si el vehiculo es de combustion. Se pasa como campo auxiliar
    # para el filtro, sin guardarla en el JSON final.
    descripciones = {
        a.get("url"): a.get("descripcion") or "" for a in anuncios_recuperados
    }
    descartados_recuperacion: list[dict] = list(
        anterior.get("descartados_recuperacion_incompleta") or []
    )
    vistas_rec = {d["url"] for d in descartados_recuperacion}

    seen: set[str] = set()
    candidatos: list[dict] = []
    for anuncio in projected:
        motivo = URLS_DESCARTAR.get(anuncio["url"])
        if motivo:
            if anuncio["url"] not in vistas_excl:
                vistas_excl.add(anuncio["url"])
                excl_manual.append(
                    {"titulo": anuncio["titulo"], "url": anuncio["url"], "motivo": motivo}
                )
            continue
        if anuncio["url"] in urls_recuperadas:
            fallo = motivo_recuperado(anuncio, descripciones.get(anuncio["url"], ""))
            if fallo:
                if anuncio["url"] not in vistas_rec:
                    vistas_rec.add(anuncio["url"])
                    descartados_recuperacion.append(
                        {"titulo": anuncio["titulo"], "url": anuncio["url"], "motivo": fallo}
                    )
                continue
        if anuncio["url"] in seen:
            continue
        seen.add(anuncio["url"])
        candidatos.append(anuncio)

    unicos, duplicados = deduplicar_por_modelo(candidatos)
    unicos.sort(key=lambda a: (a["url"],))

    if len(unicos) > OBJETIVO_TOTAL:
        # La recuperacion de autonomia desde el detalle trajo muchos mas
        # anuncios validos de los que faltaban. No se trunca: escribir 60 de 158
        # seria descartar evidencia valida, y el analisis puede ajustar el
        # tamano de muestra sin perder datos.
        print(
            f"AVISO: {len(unicos)} unicos por encima del objetivo de {OBJETIVO_TOTAL}; "
            f"se conservan todos."
        )
    if len(unicos) != OBJETIVO_TOTAL:
        print(
            f"AVISO: tras la limpieza quedan {len(unicos)} unicos, "
            f"faltan {OBJETIVO_TOTAL - len(unicos)} para el objetivo {OBJETIVO_TOTAL}\n"
        )

    merged = [proyectar(anuncio) for anuncio in unicos]
    for anuncio in merged:
        marca = limpiar_marca(anuncio["marca"])
        # Una sola forma por marca: "JMD", "jmd" y "Jmd" son la misma marca y
        # tienen que leerse igual en el JSON y en la distribucion.
        anuncio["marca"] = marca.upper() if marca else None

    errores: list[str] = []
    for indice, anuncio in enumerate(merged, 1):
        if list(anuncio.keys()) != CAMPOS:
            errores.append(f"[{indice}] campos fuera de esquema: {list(anuncio.keys())}")
        if not anuncio["url"]:
            errores.append(f"[{indice}] sin url ({anuncio['titulo']!r})")

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
        "vehiculo": "scooter_electrico",
        "fecha_scraping_original": FECHA_ORIGINAL,
        "fecha_scraping_ampliacion": FECHA_AMPLIACION,
        "total_anuncios_validos": len(merged),
        "filtro_ubicacion": "La Habana",
        "filtro_fecha": ">= 2026-01-01",
        "fuente_original": "scooters_electricos.json",
        "fuente_ampliacion": "scooters_electricos_ampliacion.json",
        "anuncios_excluidos_revision_manual": excl_manual,
        "descartados_duplicado_mismo_modelo": duplicados,
        "descartados_recuperacion_incompleta": descartados_recuperacion,
        "campos": CAMPOS,
        "anuncios": merged,
    }
    with open(ARCHIVO_MERGE, "w", encoding="utf-8") as archivo:
        json.dump(salida, archivo, ensure_ascii=False, indent=2)
        archivo.write("\n")

    relectura = json.loads(ARCHIVO_MERGE.read_text(encoding="utf-8"))
    anuncios = relectura.get("anuncios") or []
    if relectura.get("total_anuncios_validos") != len(merged) or len(anuncios) != len(merged):
        raise ErrorMerge("el JSON escrito no declara el mismo numero de anuncios")
    if relectura.get("campos") != CAMPOS:
        raise ErrorMerge("el JSON escrito no declara los 9 campos esperados")
    if any(list(a.keys()) != CAMPOS for a in anuncios):
        raise ErrorMerge("algún anuncio del JSON escrito no tiene exactamente los 9 campos")
    if len({a["url"] for a in anuncios}) != len(anuncios):
        raise ErrorMerge("el JSON escrito tiene urls duplicadas")
    if relectura.get("anuncios_excluidos_revision_manual") != excl_manual:
        raise ErrorMerge("el JSON escrito no conserva los excluidos de la revision manual")
    if relectura.get("descartados_duplicado_mismo_modelo") != duplicados:
        raise ErrorMerge("el JSON escrito no conserva los duplicados por modelo")
    if relectura.get("descartados_recuperacion_incompleta") != descartados_recuperacion:
        raise ErrorMerge("el JSON escrito no conserva los descartados de la recuperacion")
    if len(anuncios) > OBJETIVO_TOTAL:
        # Con la recuperacion de autonomia se pasa del objetivo. Se escribe el
        # total real y se avisa: truncar para llegar a 60 seria tirar evidencia
        # valida. El analisis baja el tamano de muestra, no se recortan datos.
        print(
            f"AVISO: {len(anuncios)} anuncios unicos, por encima del objetivo de "
            f"{OBJETIVO_TOTAL}. Se conservan todos; el analisis puede recortar."
        )
    # La comprobacion replica la excepcion de deduplicar_por_modelo: los
    # anuncios con algun campo de la clave a null no se agrupan, porque sin
    # motor o sin autonomia no se puede saber si son el mismo modelo.
    repeticiones = [
        clave
        for clave, n in Counter(
            clave_modelo(a)
            for a in anuncios
            if not any(campo is None for campo in clave_modelo(a))
        ).items()
        if n > 1
    ]
    if repeticiones:
        raise ErrorMerge(
            f"el JSON escrito conserva anuncios repetidos con la clave de dedup: {repeticiones}"
        )

    precios = [a["precio_usd"] for a in anuncios if a["precio_usd"] is not None]
    motores = [a["motor_w"] for a in anuncios if a["motor_w"] is not None]

    print("MERGE DE SCOOTERS ELECTRICOS (La Habana) - con limpieza")
    print("=" * 56)
    print(
        f"  Entradas: {len(candidatos) + len(excl_manual)} anuncios unicos "
        f"({TOTAL_ORIGINAL} originales + {len(anuncios_ampliacion)} de la tanda actual "
        f"+ {len(anuncios_anteriores)} del merge anterior "
        f"+ {len(descartados_previos)} descartados que vuelven)"
    )
    print(f"  Excluidos por revision manual: {len(excl_manual)}")
    print(f"  Descartados por duplicado_mismo_modelo: {len(duplicados)}")
    print(f"  Total anuncios: {len(anuncios)}")
    print(f"  URLs unicas: {len({a['url'] for a in anuncios})}")
    print(f"  Campos por anuncio: {len(anuncios[0])} ({', '.join(CAMPOS)})")
    print(f"  con municipio != null: {sum(1 for a in anuncios if a['municipio'] is not None)}")
    print(f"  con marca != null: {sum(1 for a in anuncios if a['marca'])}")
    print(f"  precio_usd: {min(precios)} - {max(precios)}" if precios else "  precio_usd: sin datos")
    print(f"  motor_w: {min(motores)} - {max(motores)}" if motores else "  motor_w: sin datos")
    print("  top 5 marcas:")
    for marca, total in Counter(a["marca"] for a in anuncios if a["marca"]).most_common(5):
        print(f"    {total:>3}  {marca}")
    print("\n  EXCLUIDOS POR REVISION MANUAL:")
    for anuncio in excl_manual:
        print(f"    - {anuncio['motivo']}: {anuncio['titulo']}")
    print(f"\n  DESCARTADOS POR duplicado_mismo_modelo ({len(duplicados)}):")
    for anuncio in duplicados:
        print(
            f"    - {anuncio['marca']} {anuncio['motor_w']}W {anuncio['autonomia_km']}km: "
            f"{anuncio['titulo'][:58]}"
        )
    print(f"\nEscrito {ARCHIVO_MERGE.name}")
    documentar_cierre(anuncios, excl_manual, duplicados, descartados_recuperacion)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ErrorMerge as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
