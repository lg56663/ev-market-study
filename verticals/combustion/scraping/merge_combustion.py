#!/usr/bin/env python3
"""Fusiona los 30 originales con los 60 de la ampliacion: 90 motos de combustion.

Proyecta ambos sets a los mismos 9 campos y no inventa nada: si un campo no
existe en el anuncio queda en null. Al final comprueba que no haya URLs
repetidas y que el total sea el esperado, antes de escribir.

Los originales traen 'url'; si un anuncio usara 'url_anuncio' se acepta igual.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ARCHIVO_ORIGINAL = BASE_DIR / "motos_combustion.json"
ARCHIVO_AMPLIACION = BASE_DIR / "motos_combustion_ampliacion.json"
ARCHIVO_DESCARTADOS = BASE_DIR / "descartados_combustion_ampliacion.json"
ARCHIVO_MERGE = BASE_DIR / "motos_combustion_90.json"
# Municipios de los 30 originales, deducidos de su 'ubicacion' por
# enriquecer_municipio_combustion.py. El original no tiene campo 'municipio'.
ARCHIVO_MUNICIPIOS = BASE_DIR / "municipios_motos_combustion.json"
ARCHIVO_REPORTE = BASE_DIR / "reporte_scraping_combustion_ampliacion.txt"

FECHA_ORIGINAL = "2026-09-29"
FECHA_AMPLIACION = "2026-09-30"
FECHA_MINIMA = "2026-01-01"
TOTAL_ESPERADO = 90
TOTAL_ORIGINAL = 30
# De los 30 originales, 2 estan fuera de La Habana (el scraper original nunca
# filtro por provincia). Como el original es intocable, se excluyen del merge
# con su motivo y la ampliacion aporta 2 anuncios mas para no perder tamano.
TOTAL_ORIGINALES_FUERA = 2
# 62 = los 59 anuncios que el merge puede usar + 3 que repusieron a los que se
# descartan por revision manual (ver TITULOS_DESCARTAR_REVISION). Los 3
# descartes ya no estan en la ampliacion: el scraper los excluyo por URL al
# escribirlos, para que no volvieran a salir, asi que de aqui salen 62 y el set
# queda en 28 + 62 = 90.
TOTAL_AMPLIACION = 62

CAMPOS = [
    "titulo",
    "marca",
    "cilindrada_cc",
    "autonomia_km",
    "precio_usd",
    "ubicacion",
    "url",
    "fecha_publicacion",
    "municipio",
]
SEP = "=" * 78

# Anuncios que se apartan del set a mano, con su motivo.
#
# La DKW 125CC esta a 700 USD cuando el siguiente mas barato del set esta a
# 1200 y la mediana a 2200. Ojo al motivo: NO es un error de parseo del precio
# (el listado de Revolico publica 700 USD, comprobado en su HTML). Es un
# anuncio con una ficha poorisima: el listado no trae descripcion, estado ni
# kilometraje, y el titulo es solo 'MOTO DKW 125CC'. DKW es una marca antigua
# germana, de modo que lo mas probable es que sea una moto usada o de
# republica, y el set es de motos nuevas. Se descarta por anuncio sospechoso,
# no por el numero.
URLS_DESCARTAR_OUTLIER: dict[str, str] = {
    "https://www.revolico.com/item/moto-dkw-56323200": "precio_outlier_sospechoso",
}


def norm(texto: str | None) -> str:
    """Normaliza un titulo para poder compararlo con las tablas de a mano.

    Los titulos de Revolico vienen con emojis, comillas raras, guiones largos y
    espacios dobles, asi que se queda solo con letras y digitos en minuscula.
    Las tablas se buscan por fragmento dentro de esa cadena, no por igualdad,
    para que un retoque del anunciante no rompa la regla.
    """
    return re.sub(r"[^a-z0-9]+", "", (texto or "").lower())


def coincidencias(clave_titulo: str, tabla: dict[str, str]) -> list[str]:
    """Valores de la tabla cuya clave aparece dentro del titulo normalizado."""
    return [valor for clave_tabla, valor in tabla.items() if clave_tabla in clave_titulo]


# Anuncios que se descartan por revision manual, con su motivo. Se buscan por
# fragmento del titulo normalizado (ver norm()), no por URL, porque son anuncios
# que el scraper aprueba y que solo se detectan leyendo el texto. Se usan
# fragmentos distintivos, no el titulo entero, para que un retoque del
# anunciante (emojis, espacios, el numero de WhatsApp al final) no rompa la
# regla.
#
#   es_lote_de_varios     -> el titulo ofrece dos motos en una sola venta
#                             ('HONGLI 150cc + YAMAKI AUTOMATICA 150cc'), asi
#                             que el precio no es el de una unidad y rompe la
#                             comparacion del set.
#   moto_usada_no_nueva   -> el set es de motos nuevas y estos dos anuncios
#                             anuncian una unidad de segunda mano.
TITULOS_DESCARTAR_REVISION: dict[str, str] = {
    "hongli150ccyamaki": "es_lote_de_varios",
    "japonesaaa": "moto_usada_no_nueva",
    "torvanalphaone": "moto_usada_no_nueva",
}

# Marcas mal escritas o mal deducidas en el scrape, corregidas a mano. Tambien
# por fragmento de titulo normalizado.
#
# Evidencia de cada correccion, todas dentro del propio set:
#   Toqmap   -> Topmaq  'Toqmap' es 'Topmaq' con las letras transpuestas, y ya
#                         hay 4 anuncios de Topmaq en el set.
#   Trank    -> Tank    'Trank' es 'Tank' transpuesto, y ya hay 4 de Tank.
#   Krathos  -> Treck   el anuncio 'Moto de Combustion ... 200CC Treck trek
#                         krathos 200 krato' lista Krathos como nombre de
#                         modelo dentro de la gama Treck.
#   Motostreck retrepa -> Treck  el mismo vendedor publica
#                         'MOTORSTRECK RESTREPA 150CC' con marca ya Treck.
#   Jincheng -> Jincheng  el anuncio '* Moto de Combustion Jincheng 125 cc *
#                         La copia del Suzuki kid H * salio con marca Suzuki
#                         porque la heuristica vio 'Suzuki' en el titulo, pero
#                         el modelo es Jincheng y los otros anuncios identicos
#                         del mismo vendedor ya traen Jincheng.
CORRECCIONES_MARCA: dict[str, str] = {
    "jincheng125cc": "Jincheng",
    "toqmaptultramax": "Topmaq",
    "tranktranspro": "Tank",
    "krathos250cc": "Treck",
    "motorkrathos200cc": "Treck",
    "motostreckretrepa": "Treck",
}

# Precios corregidos a mano, por URL, con la evidencia que los respalda.
#
# La UNIZUKI 2026 200CC tiene precio_usd = 2026, que es el anio del modelo
# colado en el campo de precio de Revolico. Se abrio la ficha del anuncio para
# confirmarlo: el HTML sigue devolviendo price=2026 (el error es del anunciante,
# no del parser), pero el titulo dice '$2600 USD' y la descripcion del anuncio
# cierra con 'Precio: $2600 USD'. Dos menciones independientes, asi que 2600 es
# el precio real.
CORRECCIONES_PRECIO: dict[str, dict] = {
    "https://www.revolico.com/item/moto-automatica-unizuki-2026-200cc-2600-usd-57622356": {
        "precio_usd": 2600.0,
        "motivo": "precio_anoso_colado_en_el_campo_de_precio",
        "evidencia": "titulo y descripcion del anuncio dicen '$2600 USD'",
    },
}



class ErrorMerge(Exception):
    """El merge no se escribe porque un dato no cuadra."""


def cargar(ruta: Path, etiqueta: str) -> dict:
    if not ruta.exists():
        raise ErrorMerge(f"falta el archivo de {etiqueta}: {ruta.name}")
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ErrorMerge(f"{ruta.name} no es JSON valido: {error}") from error


def es_de_la_habana(anuncio: dict) -> bool:
    """True si el anuncio es de La Habana.

    El entregable tiene que ser 100% La Habana, y el set original no lo era:
    el scraper original nunca filtro por provincia y se le colaron 2 anuncios
    de Villa Clara y Santiago de Cuba.
    """
    ubicacion = (anuncio.get("ubicacion") or "").strip()
    if not ubicacion:
        return False
    partes = [p.strip() for p in ubicacion.split("/")]
    return partes[0].lower() == "la habana"


def proyectar(anuncio: dict, municipios: dict[str, str | None] | None = None) -> dict:
    """Los 9 campos finales, siempre presentes. Lo que falte, null.

    Los 30 originales no traen 'municipio': se toma del mapa que arma
    enriquecer_municipio_combustion.py. Si tampoco ahi esta, queda null; no se
    deduce nada aqui.
    """
    url = anuncio.get("url") or anuncio.get("url_anuncio")
    limpio = url.split("?")[0].rstrip("/") if url else None
    municipio = anuncio.get("municipio")
    if municipio is None and municipios and limpio:
        municipio = municipios.get(limpio)
    return {
        "titulo": anuncio.get("titulo"),
        "marca": anuncio.get("marca"),
        "cilindrada_cc": anuncio.get("cilindrada_cc"),
        "autonomia_km": anuncio.get("autonomia_km"),
        "precio_usd": anuncio.get("precio_usd"),
        "ubicacion": anuncio.get("ubicacion"),
        "url": limpio,
        "fecha_publicacion": anuncio.get("fecha_publicacion"),
        "municipio": municipio,
    }


def main() -> int:
    original = cargar(ARCHIVO_ORIGINAL, "original")
    ampliacion = cargar(ARCHIVO_AMPLIACION, "ampliacion")
    # El historico de descartes aporta los anuncios que se apartaron a mano,
    # que el scraper ya filtro y por tanto no aparecen en la ampliacion.
    descartes_ampliacion: list[dict] = []
    if ARCHIVO_DESCARTADOS.exists():
        descartes_ampliacion = json.loads(
            ARCHIVO_DESCARTADOS.read_text(encoding="utf-8")
        ).get("descartes") or []

    originales = original.get("anuncios") or []
    nuevos = ampliacion.get("anuncios") or []

    municipios: dict[str, str | None] = {}
    if ARCHIVO_MUNICIPIOS.exists():
        datos_municipios = json.loads(ARCHIVO_MUNICIPIOS.read_text(encoding="utf-8"))
        municipios = {
            (url or "").rstrip("/"): valor
            for url, valor in (datos_municipios.get("municipios") or {}).items()
        }

    if len(originales) != TOTAL_ORIGINAL:
        raise ErrorMerge(
            f"el original tiene {len(originales)} anuncios, se esperaban {TOTAL_ORIGINAL}")
    if len(nuevos) != TOTAL_AMPLIACION:
        raise ErrorMerge(
            f"la ampliacion tiene {len(nuevos)} anuncios, se esperaban {TOTAL_AMPLIACION}")

    # Los originales que no son de La Habana se apartan con su motivo. El
    # original no se modifica: solo dejan de entrar en el entregable.
    originales_habana: list[dict] = []
    originales_excluidos: list[dict] = []
    for anuncio in originales:
        if es_de_la_habana(anuncio):
            originales_habana.append(anuncio)
        else:
            originales_excluidos.append(
                {
                    "titulo": anuncio.get("titulo"),
                    "ubicacion": anuncio.get("ubicacion"),
                    "url": (anuncio.get("url") or "").split("?")[0].rstrip("/") or None,
                    "motivo": "fuera_de_la_habana_original",
                }
            )
    if len(originales_excluidos) != TOTAL_ORIGINALES_FUERA:
        raise ErrorMerge(
            f"se esperaban {TOTAL_ORIGINALES_FUERA} originales fuera de La Habana y "
            f"hay {len(originales_excluidos)}: el set original cambio")

    for anuncio in nuevos:
        if not es_de_la_habana(anuncio):
            raise ErrorMerge(
                f"un anuncio de la ampliacion no es de La Habana: {anuncio.get('ubicacion')}")

    # Correcciones de marca y de precio, sobre los DOS sets y antes de
    # proyectar, para que el entregable salga ya con los datos buenos. Se
    # cuentan para poder reportar cuantas hubo.
    marcas_corregidas: list[dict] = []
    precios_corregidos: list[dict] = []
    ambiguos: list[str] = []
    for anuncio in originales_habana + nuevos:
        k = norm(anuncio.get("titulo"))
        candidatas = sorted(set(coincidencias(k, CORRECCIONES_MARCA)))
        if len(candidatas) > 1:
            # Dos reglas distintas piden marcas distintas para el mismo
            # anuncio. No se tira una moneda: se deja como esta y se avisa.
            ambiguos.append(f"marca: {anuncio.get('titulo')[:50]} -> {candidatas}")
            candidatas = []
        marca_buena = candidatas[0] if candidatas else None
        if marca_buena and anuncio.get("marca") != marca_buena:
            marcas_corregidas.append(
                {
                    "titulo": anuncio.get("titulo"),
                    "url": (anuncio.get("url") or "").split("?")[0].rstrip("/"),
                    "marca_antes": anuncio.get("marca"),
                    "marca_despues": marca_buena,
                }
            )
            anuncio["marca"] = marca_buena

        url = (anuncio.get("url") or "").split("?")[0].rstrip("/")
        correccion = CORRECCIONES_PRECIO.get(url)
        if correccion and anuncio.get("precio_usd") != correccion["precio_usd"]:
            precios_corregidos.append(
                {
                    "titulo": anuncio.get("titulo"),
                    "url": url,
                    "precio_antes": anuncio.get("precio_usd"),
                    "precio_despues": correccion["precio_usd"],
                    "motivo": correccion["motivo"],
                    "evidencia": correccion["evidencia"],
                }
            )
            anuncio["precio_usd"] = correccion["precio_usd"]

    # Descartes por revision manual (lotes, usadas). Van por titulo, no por
    # URL, y se aplican a los dos sets igual que el resto.
    revision_excluidos: list[dict] = []
    vistas_revision: set[str] = set()

    def sin_revision(anuncios: list[dict]) -> list[dict]:
        quedan: list[dict] = []
        for anuncio in anuncios:
            motivos = sorted(set(coincidencias(norm(anuncio.get("titulo")), TITULOS_DESCARTAR_REVISION)))
            if motivos:
                if len(motivos) > 1:
                    ambiguos.append(f"descarte: {anuncio.get('titulo')[:50]} -> {motivos}")
                url = (anuncio.get("url") or "").split("?")[0].rstrip("/")
                if url not in vistas_revision:
                    vistas_revision.add(url)
                    revision_excluidos.append(
                        {
                            "titulo": anuncio.get("titulo"),
                            "url": url,
                            "precio_usd": anuncio.get("precio_usd"),
                            "motivo": motivos[0],
                        }
                    )
                continue
            quedan.append(anuncio)
        return quedan

    originales_habana = sin_revision(originales_habana)
    nuevos = sin_revision(nuevos)

    # Los descartes manuales por URL (outliers y similares) se aplican a los
    # DOS sets: llegan como originales o como nuevos de la ampliacion, y en
    # ambos casos quedan fuera con su motivo.
    outliers_excluidos: list[dict] = []
    vistos_outlier: set[str] = set()
    # URLs que el merge quito de verdad de los dos sets. Lleva la cuenta
    # aparte de outliers_excluidos, que tambien incluye los que el scraper ya
    # habia apartado antes: esos no restan del total porque su hueco ya lo
    # cubre el anuncio de repuesto que entro en su lugar.
    urls_quitadas: set[str] = set()

    def sin_outliers(anuncios: list[dict]) -> list[dict]:
        """Quita los anuncios con URL en URLS_DESCARTAR_OUTLIER."""
        quedan: list[dict] = []
        for anuncio in anuncios:
            url = (anuncio.get("url") or "").split("?")[0].rstrip("/")
            motivo = URLS_DESCARTAR_OUTLIER.get(url)
            if motivo:
                urls_quitadas.add(url)
                if url not in vistos_outlier:
                    vistos_outlier.add(url)
                    outliers_excluidos.append(
                        {
                            "titulo": anuncio.get("titulo"),
                            "url": url,
                            "precio_usd": anuncio.get("precio_usd"),
                            "motivo": motivo,
                        }
                    )
                continue
            quedan.append(anuncio)
        return quedan

    originales_habana = sin_outliers(originales_habana)
    nuevos = sin_outliers(nuevos)

    # Los descartes a mano los saca el scraper antes de escribir la ampliacion
    # (URLS_EXCLUIDAS_FIJAS), asi que el merge no los encuentra en ninguno de
    # los dos sets. Sin esto, el motivo del descarte se perderia. Se recuperan
    # del historico de descartes, que es donde el scraper los dejo auditados.
    for descarte in descartes_ampliacion:
        url = (descarte.get("url") or "").split("?")[0].rstrip("/")
        motivo = URLS_DESCARTAR_OUTLIER.get(url)
        if motivo and url not in vistos_outlier:
            vistos_outlier.add(url)
            outliers_excluidos.append(
                {
                    "titulo": descarte.get("titulo"),
                    "url": url,
                    "precio_usd": descarte.get("precio_usd"),
                    "motivo": motivo,
                }
            )
        # Mismo razonamiento para los descartes por revision: el scraper los
        # saco por URL antes de escribir la ampliacion, asi que sin esto el
        # entregable no dejaria constancia de por que faltan.
        motivo_revision = next(
            (
                motivo
                for motivo in coincidencias(
                    norm(descarte.get("titulo")), TITULOS_DESCARTAR_REVISION
                )
            ),
            None,
        )
        if motivo_revision and url not in vistas_revision:
            vistas_revision.add(url)
            revision_excluidos.append(
                {
                    "titulo": descarte.get("titulo"),
                    "url": url,
                    "precio_usd": descarte.get("precio_usd"),
                    "motivo": motivo_revision,
                }
            )

    # Los originales van primero, sin reordenar: el entregable conserva el
    # orden del set original y detras los nuevos.
    anuncios = [proyectar(a, municipios) for a in originales_habana] + [
        proyectar(a, municipios) for a in nuevos
    ]

    # Las tablas de a mano se declaran para un set concreto. Si un titulo deja
    # de aparecer (el anuncio se borro de Revolico, o cambio el texto), el
    # ajuste no se esta aplicando y el set sale con datos malos en silencio. Se
    # avisa en vez de fallar, porque el set puede cambiar sin que cambie el
    # codigo, y eso no es un error del merge.
    claves_set = [norm(a.get("titulo")) for a in anuncios] + [
        norm(d.get("titulo")) for d in descartes_ampliacion
    ]
    faltantes_marca = [k for k in CORRECCIONES_MARCA if not any(k in c for c in claves_set)]
    faltantes_revision = [
        k for k in TITULOS_DESCARTAR_REVISION if not any(k in c for c in claves_set)
    ]

    sin_url = [a for a in anuncios if not a["url"]]
    if sin_url:
        raise ErrorMerge(f"{len(sin_url)} anuncios sin URL: {sin_url[:3]}")

    repetidas = [url for url, n in Counter(a["url"] for a in anuncios).items() if n > 1]
    if repetidas:
        raise ErrorMerge(f"{len(repetidas)} URLs repetidas entre los dos sets: {repetidas[:5]}")

    # El total esperado descuenta solo lo que el merge Quito de verdad de los
    # dos sets en esta corrida. Los descartes que ya hizo el scraper antes de
    # escribir la ampliacion (la DKW) no restan: su hueco ya lo cubre el
    # anuncio de repuesto, y los descartes por revision tampoco, porque la
    # ampliacion se repuso con 3 anuncios nuevos para devolver los 90.
    esperado = TOTAL_ESPERADO - len(urls_quitadas)
    if len(anuncios) != esperado:
        raise ErrorMerge(
            f"el merge tiene {len(anuncios)} anuncios, se esperaban {esperado} "
            f"({TOTAL_ESPERADO} -{len(urls_quitadas)} descartados por URL)")

    for anuncio in anuncios:
        if set(anuncio) != set(CAMPOS):
            raise ErrorMerge(
                f"un anuncio no tiene los 9 campos exactos: {sorted(anuncio)}")

    fechas_viejas = [
        a for a in anuncios
        if a["fecha_publicacion"] and a["fecha_publicacion"] < FECHA_MINIMA
    ]
    # A diferencia de antes, esto ya no es un aviso: el entregable tiene que
    # ser 100% La Habana, asi que si se colara uno el merge no se escribe.
    fuera_habana = [a for a in anuncios if not es_de_la_habana(a)]
    if fuera_habana:
        raise ErrorMerge(
            f"{len(fuera_habana)} anuncios fuera de La Habana en el merge: "
            f"{[a['ubicacion'] for a in fuera_habana[:3]]}")
    sin_precio = [a for a in anuncios if a["precio_usd"] is None]
    sin_cilindrada = [a for a in anuncios if a["cilindrada_cc"] is None]

    salida = {
        "vehiculo": "moto_combustion",
        "fecha_scraping_original": FECHA_ORIGINAL,
        "fecha_scraping_ampliacion": FECHA_AMPLIACION,
        "total_anuncios_validos": len(anuncios),
        "filtro_ubicacion": "La Habana",
        "filtro_fecha": ">= 2026-01-01",
        "fuente_original": ARCHIVO_ORIGINAL.name,
        "fuente_ampliacion": ARCHIVO_AMPLIACION.name,
        "campos": CAMPOS,
        "anuncios_originales_excluidos": originales_excluidos,
        "anuncios_excluidos_outlier": outliers_excluidos,
        "anuncios_descartados_revision": revision_excluidos,
        "correcciones_marca": marcas_corregidas,
        "correcciones_precio": precios_corregidos,
        "anuncios": anuncios,
    }
    ARCHIVO_MERGE.write_text(
        json.dumps(salida, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Relectura: se comprueba lo que se escribio de verdad.
    try:
        relectura = json.loads(ARCHIVO_MERGE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ErrorMerge(f"el JSON escrito no se puede leer: {error}") from error
    if len(relectura.get("anuncios") or []) != esperado:
        raise ErrorMerge(
            f"el JSON escrito tiene {len(relectura.get('anuncios') or [])} anuncios, "
            f"se esperaban {esperado}")
    if relectura.get("campos") != CAMPOS:
        raise ErrorMerge("el JSON escrito no declara los 9 campos esperados")

    precios = [a["precio_usd"] for a in anuncios if a["precio_usd"] is not None]
    cc = [a["cilindrada_cc"] for a in anuncios if a["cilindrada_cc"] is not None]

    print(SEP)
    print("MERGE DE MOTOS DE COMBUSTIÓN (30 originales + 60 ampliación)")
    print(SEP)
    print(f"  Total anuncios        : {len(anuncios)}")
    print(f"  URLs únicas           : {len({a['url'] for a in anuncios})}")
    print(f"  Campos por anuncio    : {len(CAMPOS)} ({', '.join(CAMPOS)})")
    print(f"  con municipio != null : {sum(1 for a in anuncios if a['municipio'] is not None)}")
    print(f"  con marca != null     : {sum(1 for a in anuncios if a['marca'])}")
    print(f"  con precio != null    : {len(precios)}")
    print(f"  con cc != null        : {len(cc)}")
    print(f"  precio_usd            : {min(precios)} - {max(precios)}")
    print(f"  cilindrada_cc         : {min(cc)} - {max(cc)}")
    print(f"\n  Originales de La Habana : {len(originales_habana)}"
          f" (de {TOTAL_ORIGINAL}, {TOTAL_ORIGINALES_FUERA} excluidos por provincia)")
    print(f"  Nuevos de La Habana     : {len(nuevos)}")
    print(f"  Fuera de La Habana      : {len(fuera_habana)}")
    if originales_excluidos:
        print("\n  ORIGINALES EXCLUIDOS (no son de La Habana):")
        for anuncio in originales_excluidos:
            print(f"    - {anuncio['motivo']}: {anuncio['ubicacion']}: "
                  f"{anuncio['titulo'][:48]}")
    if outliers_excluidos:
        print("\n  EXCLUIDOS POR ANUNCIO SOSPECHOSO (fuera de URLS_DESCARTAR_OUTLIER):")
        for anuncio in outliers_excluidos:
            print(f"    - {anuncio['motivo']}: {anuncio['precio_usd']} USD: "
                  f"{anuncio['titulo'][:48]}")
    if revision_excluidos:
        print(f"\n  DESCARTADOS POR REVISION MANUAL ({len(revision_excluidos)}):")
        for anuncio in revision_excluidos:
            print(f"    - {anuncio['motivo']}: {anuncio['precio_usd']} USD: "
                  f"{anuncio['titulo'][:52]}")
    if marcas_corregidas:
        print(f"\n  MARCAS CORREGIDAS ({len(marcas_corregidas)}):")
        for c in marcas_corregidas:
            print(f"    - {c['marca_antes']} -> {c['marca_despues']}: {c['titulo'][:50]}")
    if precios_corregidos:
        print(f"\n  PRECIOS CORREGIDOS ({len(precios_corregidos)}):")
        for c in precios_corregidos:
            print(f"    - {c['precio_antes']} -> {c['precio_despues']} USD "
                  f"({c['motivo']}): {c['titulo'][:44]}")
            print(f"      {c['evidencia']}")
    if ambiguos:
        print(f"\n  AVISO: {len(ambiguos)} anuncios coinciden con mas de una regla:")
        for a in ambiguos:
            print(f"    - {a}")
    if faltantes_marca:
        print(f"  AVISO: {len(faltantes_marca)} correcciones de marca no aplican a "
              f"ningun anuncio del set: {faltantes_marca}")
    if faltantes_revision:
        print(f"  AVISO: {len(faltantes_revision)} titulos de TITULOS_DESCARTAR_REVISION "
              f"no aparecen ni en el set ni en los descartes: {faltantes_revision}")
    if fechas_viejas:
        print(f"  AVISO: {len(fechas_viejas)} anuncios con fecha anterior a 2026")
    if sin_precio or sin_cilindrada:
        print(f"  AVISO: {len(sin_precio)} sin precio, {len(sin_cilindrada)} sin cilindrada")
    print("\n  CILINDRADAS:")
    for valor, total in Counter(a["cilindrada_cc"] for a in anuncios).most_common():
        print(f"    {total:>3}  {valor} cc")
    print("\n  TOP 10 MARCAS:")
    for marca, total in Counter(a["marca"] for a in anuncios if a["marca"]).most_common(10):
        print(f"    {total:>3}  {marca}")
    print("\n  TOP 10 MUNICIPIOS:")
    for municipio, total in Counter(
        a["municipio"] for a in anuncios if a["municipio"]
    ).most_common(10):
        print(f"    {total:>3}  {municipio}")
    print(f"\n  merge: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")
    print(f"  Escrito {ARCHIVO_MERGE.name}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ErrorMerge as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
