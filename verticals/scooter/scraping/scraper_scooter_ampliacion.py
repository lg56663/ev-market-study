#!/usr/bin/env python3
"""Ampliacion del scraping de scooters electricos en Revolico.com (30 -> 60).

Solo se consultan paginas de listado (search). NO se entra a paginas de detalle:
la marca, la autonomia, el motor y la quimica de bateria se leen del titulo del
anuncio, que en este portal los incluye. El precio, la moneda y la provincia
salen del propio listado (Next.js + Apollo, __NEXT_DATA__ -> __APOLLO_STATE__).

Los extractores ya validados del scraper original (scraper_scooters.py) se
reutilizan via import: normalizar, clave_titulo, verificar_inclusion,
verificar_exclusion_tipo, verificar_exclusion_combustion, extraer_marca_modelo,
extraer_motor_w, extraer_autonomia, extraer_tipo_bateria, extraer_plegable,
parsear_listado.

Reglas de los campos:
  - 4 obligatorios: marca, autonomia_km, precio_usd, ubicacion.
  - motor_w: si el anuncio lo reporta se guarda; si no, null (no se descarta).
    Si lo reporta y es < 200 se descarta con motivo motor_w_bajo.
  - tipo_bateria y plegable: solo si el titulo los dice; si no, null.
  - Nada se infiere: lo que no esta en el anuncio se queda en null.

Salidas:
  - scooters_electricos_ampliacion.json      : 30 anuncios validos nuevos
  - descartados_scooters_ampliacion.json     : descartes con trazabilidad
  - reporte_scraping_scooters_ampliacion.txt : reporte de la sesion
Cache (separada de la del scraper original):
  - cache_html_scooters_ampliacion/

No se modifica scooters_electricos.json ni scraper_scooters.py.
"""

from __future__ import annotations

import argparse
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

import scraper_scooters as base  # noqa: E402

ARCHIVO_VALIDOS = BASE_DIR / "scooters_electricos_ampliacion.json"
ARCHIVO_DESCARTADOS = BASE_DIR / "descartados_scooters_ampliacion.json"
ARCHIVO_REPORTE = BASE_DIR / "reporte_scraping_scooters_ampliacion.txt"
ARCHIVO_BASE = BASE_DIR / "scooters_electricos.json"
ARCHIVO_EXCLUIR = BASE_DIR / "urls_excluir_scooter.json"
DIR_CACHE = BASE_DIR / "cache_html_scooters_ampliacion"

BASE_URL = "https://www.revolico.com"
USER_AGENT = "MiProyectoInvestigacion/1.0"
DELAY_SEGUNDOS = 3.0
TIMEOUT_SEGUNDOS = 30.0
REINTENTOS = 2
ESPERA_REINTENTO = 5.0
MAX_PETICIONES = 260
MAX_PAGINAS_POR_URL = 12
OBJETIVO_VALIDOS = 30
FECHA_SCRAPING = "2026-09-30"
ANIO_MINIMO = 2026
MOTOR_MINIMO_W = 200
TIPO_CAMBIO_CUP = 250.0

SEARCH_QUERIES = [
    "scooter electrico",
    "patineta electrica",
    "patinete electrico",
    "scooter plegable",
    "patineta plegable",
    "scooter electrico cuba",
    "patineta electrica cuba",
    "scooter 500w",
    "patineta 500w",
    "scooter 1000w",
    "scooter litio",
    "patineta litio",
]

SEARCH_QUERIES_RESPALDO = [
    "scooter Centro Habana",
    "scooter Playa",
    "scooter Marianao",
    "scooter Cerro",
    "scooter Habana Vieja",
    "scooter Diez de Octubre",
    "scooter Arroyo Naranjo",
    "scooter Boyeros",
]

SEARCH_QUERIES_POTENCIA = [
    "scooter 350w",
    "scooter 800w",
    "scooter 1500w",
    "patinete 800w",
    "patineta 1000w",
    "scooter 2000w",
    "scooter en caja",
    "patinete plegable",
]

# El buscador indexa el titulo completo, asi que las consultas con autonomia
# declarada ("60 km", "48v 20ah") son las que traen anuncios con el campo
# obligatorio.autonomia_km preenchido.
SEARCH_QUERIES_AUTONOMIA = [
    "scooter 60 km",
    "scooter 40 km",
    "scooter 30 km",
    "scooter 50 km",
    "patinete 60 km",
    "patineta 40 km",
    "scooter 48v 20ah",
    "scooter 48v 15ah",
    "patineta 48v 20ah",
    "scooter 36v",
    "scooter autonomia",
    "scooter 500w 20ah",
]

# Segundo lote, para completar el set tras la revision manual.
SEARCH_QUERIES_LIMPIEZA = [
    "scooter Vedado",
    "scooter Miramar",
    "scooter Guanabacoa",
    "scooter Regla",
    "scooter San Miguel",
    "patineta Vedado",
    "patineta Miramar",
    "scooter 750w",
    "scooter 350w",
    "scooter 1200w",
]

MUNICIPIOS_HABANA = {
    "centro habana", "playa", "marianao", "cerro", "habana vieja",
    "diez de octubre", "arroyo naranjo", "boyeros", "plaza",
    "plaza de la evolucion", "plaza de la revolucion", "san miguel del padron",
    "guanabacoa", "regla", "habana del este", "cotorro", "la lisa",
}

CAMPOS_ANUNCIO = [
    "titulo",
    "marca",
    "motor_w",
    "tipo_bateria",
    "plegable",
    "autonomia_km",
    "precio_usd",
    "ubicacion",
    "fecha_publicacion",
    "url",
    "descripcion",
]

CAMPOS_OBLIGATORIOS = ["marca", "autonomia_km", "precio_usd", "ubicacion"]

RX_SCOOTER = re.compile(r"\b(scooters?|patinetes?|patinetas?|e\s*-?\s*scooter)\b")
RX_TRICICLO = re.compile(
    r"\b(triciclos?|trimotos?|cuatriciclos?)\b|\b(?:3|4|cuatro|tres)\s+ruedas?\b"
)
RX_MOTO = re.compile(r"\b(motos?|moticos?|moticos)\b")
RX_BICIMOTO = re.compile(
    r"\b(bicimotos?|bicicletas?|bicis)\b|\bbici\s+electrica\b|\bbicimoto\b"
)
RX_PRECIO_TITULO = re.compile(r"\$\s*(\d{3,6})")

# El extractor de autonomia del scraper original descarta cualquier "N km" cuyo
# contexto inmediato contenga "maxima" (para no confundirse con la velocidad
# maxima). Eso descarta falsos casos como "motor de 750 W de potencia maxima,
# 48 km de autonomia", donde la autonomia si esta declarada de forma explicita.
# Estos patrones solo aceptan lo que el propio titulo dice, asi que no amplian
# lo que el anuncio reporta: solo dejan de perder lo que si dice.
RX_AUTONOMIA_EXPLICITA = re.compile(
    r"(\d{1,4})\s*km\s*(?:de\s+)?autonomia"
    r"|autonomia\s*(?:de\s+)?[:\-]?\s*(\d{1,4})\s*km"
    r"|(\d{1,4})\s*km\s*de\s+recorrido"
    r"|recorrido\s*(?:de\s+)?[:\-]?\s*(\d{1,4})\s*km"
)


def extraer_autonomia(texto: str) -> int | None:
    explicitos = [
        int(valor)
        for coincidencia in RX_AUTONOMIA_EXPLICITA.findall(texto)
        for valor in coincidencia
        if valor
    ]
    explicitos = [v for v in explicitos if 1 <= v <= 1000]
    if explicitos:
        return max(explicitos)
    return base.extraer_autonomia(texto)


NO_CANDIDATO = "__no_candidato__"

MOTIVO_TIPO = {
    "moto": "es_moto",
    "motico": "es_moto",
    "bicimoto": "es_bicimoto",
    "bicimotos": "es_bicimoto",
    "bicicleta": "es_bicimoto",
    "bicicletas": "es_bicimoto",
    "bici electrica": "es_bicimoto",
    "bicicleta electrica": "es_bicimoto",
    "triciclo": "es_triciclo",
    "triciclos": "es_triciclo",
    "trimoto": "es_triciclo",
    "infantil": "es_triciclo",
    "nino": "es_triciclo",
    "ninos": "es_triciclo",
    "nina": "es_triciclo",
    "ninas": "es_triciclo",
    "juguete": "es_triciclo",
    "juguetes": "es_triciclo",
    "juguetero": "es_triciclo",
    "ciclomotor": "es_triciclo",
}


def search_url(query: str) -> str:
    return f"{BASE_URL}/search?q={query.replace(' ', '+')}"


def es_habana(provincias: dict, municipios: dict, anuncio: dict) -> bool:
    provincia = provincias.get(str(anuncio.get("provinceId") or ""))
    municipio = municipios.get(str(anuncio.get("municipalityId") or ""))
    if provincia and base.normalizar(provincia) == "la habana":
        return True
    if municipio and base.normalizar(municipio) in MUNICIPIOS_HABANA:
        return True
    return False


def parsear_fecha(anuncio: dict) -> str | None:
    crudo = anuncio.get("updatedOnToOrder") or anuncio.get("updatedOnByUser")
    if not crudo:
        return None
    coincidencia = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(crudo))
    return "-".join(coincidencia.groups()) if coincidencia else None


def url_de(anuncio: dict) -> str | None:
    permalink = anuncio.get("permalink") or ""
    if permalink.startswith("/"):
        return BASE_URL + permalink
    return permalink or None


def precio_de(anuncio: dict, texto: str):
    moneda = (anuncio.get("currency") or "USD").upper()
    precio = anuncio.get("price")
    if isinstance(precio, (int, float)) and precio > 0:
        if moneda == "CUP":
            return round(float(precio) / TIPO_CAMBIO_CUP, 2)
        return round(float(precio), 2)
    coincidencia = RX_PRECIO_TITULO.search(texto)
    return int(coincidencia.group(1)) if coincidencia else None


def construir_campos(anuncio: dict, provincias: dict, municipios: dict) -> dict:
    titulo = (anuncio.get("title") or "").strip()
    texto = base.normalizar(titulo)
    marca, _modelo = base.extraer_marca_modelo(titulo, texto)
    provincia = provincias.get(str(anuncio.get("provinceId") or ""))
    return {
        "titulo": titulo or None,
        "marca": marca,
        "motor_w": base.extraer_motor_w(texto),
        "tipo_bateria": base.extraer_tipo_bateria(texto),
        "plegable": True if base.extraer_plegable(texto) else None,
        "autonomia_km": extraer_autonomia(texto),
        "precio_usd": precio_de(anuncio, texto),
        "ubicacion": provincia,
        "fecha_publicacion": parsear_fecha(anuncio),
        "url": url_de(anuncio),
        "descripcion": None,
        "texto": texto,
    }


def registro_descarte(campos: dict, motivo: str) -> dict:
    return {campo: campos.get(campo) for campo in CAMPOS_ANUNCIO} | {"motivo": motivo}


def evaluar(
    campos: dict,
    anuncio: dict,
    provincias: dict,
    municipios: dict,
    urls_base: set[str],
) -> str | None:
    if not campos["titulo"]:
        return "titulo_vacio"
    texto = campos["texto"]
    if not RX_SCOOTER.search(texto):
        return NO_CANDIDATO
    if not base.verificar_inclusion(texto) and not RX_SCOOTER.search(texto):
        return NO_CANDIDATO
    if campos["url"] in urls_base:
        return "url_duplicada"
    if not es_habana(provincias, municipios, anuncio):
        return "fuera_de_la_habana"
    if RX_TRICICLO.search(texto):
        return "es_triciclo"
    if RX_BICIMOTO.search(texto) and not RX_SCOOTER.search(texto):
        return "es_bicimoto"
    if RX_MOTO.search(texto) and not RX_SCOOTER.search(texto):
        return "es_moto"
    excluido = base.verificar_exclusion_tipo(texto)
    if excluido:
        return MOTIVO_TIPO.get(excluido, "es_triciclo" if "ruedas" in excluido or "cuatri" in excluido else "es_bicimoto")
    if base.verificar_exclusion_combustion(texto):
        return "es_combustion"
    if campos["motor_w"] is not None and campos["motor_w"] < MOTOR_MINIMO_W:
        return "motor_w_bajo"
    if not campos["ubicacion"]:
        return "sin_ubicacion"
    if not campos["marca"]:
        return "sin_marca"
    if campos["autonomia_km"] is None:
        return "sin_autonomia"
    if campos["precio_usd"] is None:
        return "sin_precio"
    fecha = campos["fecha_publicacion"]
    if not fecha or int(fecha[:4]) < ANIO_MINIMO:
        return "fecha_no_2026"
    return None


class Sesion:
    def __init__(self, usar_cache: bool = True) -> None:
        self.peticiones = 0
        self.desde_cache = 0
        self.paginas = 0
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

    def cabe(self) -> bool:
        return self.peticiones < MAX_PETICIONES

    def obtener(self, cliente: httpx.Client, url: str) -> str | None:
        destino = DIR_CACHE / (hashlib.sha1(url.encode("utf-8")).hexdigest() + ".html")
        if self.usar_cache and destino.exists():
            html = destino.read_text(encoding="utf-8", errors="replace")
            if "__NEXT_DATA__" in html:
                self.desde_cache += 1
                return html
        if not self.cabe():
            self.errores.append(f"limite de {MAX_PETICIONES} peticiones alcanzado: {url}")
            return None
        for intento in range(REINTENTOS):
            self.peticiones += 1
            try:
                respuesta = cliente.get(
                    url, headers=self.cabeceras(), timeout=TIMEOUT_SEGUNDOS
                )
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


def cargar_urls_base() -> set[str]:
    """URLs que ya no se pueden volver a usar.

    Incluye los anuncios del scraping original y, si existe, la lista de urls
    excluidas a mano (urls_excluir_scooter.json), que permite que una segunda
    tanda de ampliacion no repita lo ya recogido ni lo descartado.
    """
    urls: set[str] = set()
    if ARCHIVO_BASE.exists():
        datos = json.loads(ARCHIVO_BASE.read_text(encoding="utf-8"))
        urls.update(
            anuncio["url_anuncio"]
            for anuncio in datos.get("anuncios", [])
            if anuncio.get("url_anuncio")
        )
    if ARCHIVO_EXCLUIR.exists():
        datos = json.loads(ARCHIVO_EXCLUIR.read_text(encoding="utf-8"))
        for anuncio in datos.get("anuncios", []):
            if anuncio.get("url"):
                urls.add(anuncio["url"])
    return urls


def escribir_json(ruta: Path, datos) -> None:
    with open(ruta, "w", encoding="utf-8") as archivo:
        json.dump(datos, archivo, ensure_ascii=False, indent=2)
        archivo.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Ampliacion scraping scooters electricos")
    parser.add_argument("--refrescar", action="store_true", help="ignora la caché")
    args = parser.parse_args()

    inicio = datetime.now(timezone.utc)
    ahora = inicio.strftime("%Y-%m-%dT%H:%M:%SZ")
    sesion = Sesion(usar_cache=not args.refrescar)
    urls_base = cargar_urls_base()
    print(f"Anuncios ya existentes en scooters_electricos.json: {len(urls_base)} urls\n")

    provincias: dict = {}
    municipios: dict = {}
    vistos: dict[str, dict] = {}
    urls_consultadas: list[str] = []
    paginas_por_query: dict[str, int] = {}

    def rastrear(queries: list[str], etiqueta: str) -> None:
        for query in queries:
            base_busqueda = search_url(query)
            for pagina in range(1, MAX_PAGINAS_POR_URL + 1):
                if not sesion.cabe():
                    print("  (limite de peticiones alcanzado)")
                    return
                url = f"{base_busqueda}&page={pagina}"
                html = sesion.obtener(cliente, url)
                if html is None:
                    break
                urls_consultadas.append(url)
                catalogo, anuncios = base.parsear_listado(html)
                if not anuncios:
                    break
                sesion.paginas += 1
                paginas_por_query[query] = pagina
                provincias.update(catalogo.get("provincias") or {})
                municipios.update(catalogo.get("municipios") or {})
                nuevos = 0
                for anuncio in anuncios:
                    clave = base.clave_titulo(anuncio.get("title") or "")
                    if not clave or clave in vistos:
                        continue
                    vistos[clave] = anuncio
                    nuevos += 1
                print(
                    f"[{etiqueta} p{pagina:>2}] {query:<26} n={len(anuncios):>3} "
                    f"nuevos={nuevos:>3} (unicos {len(vistos)}, pets {sesion.peticiones})"
                )
                if nuevos == 0:
                    break
                time.sleep(DELAY_SEGUNDOS)

    def evaluar_vistos() -> tuple[list, list, Counter]:
        validos: list[dict] = []
        descartados: list[dict] = []
        motivos: Counter = Counter()
        urls_validas: set[str] = set()
        for anuncio in vistos.values():
            campos = construir_campos(anuncio, provincias, municipios)
            motivo = evaluar(
                campos, anuncio, provincias, municipios, urls_base
            )
            if motivo is None and campos["url"] in urls_validas:
                motivo = "url_duplicada"
            if motivo == NO_CANDIDATO:
                continue
            if motivo:
                descartados.append(registro_descarte(campos, motivo))
                motivos[motivo] += 1
                continue
            urls_validas.add(campos["url"])
            validos.append({campo: campos[campo] for campo in CAMPOS_ANUNCIO})
        return validos, descartados, motivos

    with httpx.Client(follow_redirects=True, verify=True) as cliente:
        rastrear(SEARCH_QUERIES, "q")
        validos, descartados, motivos = evaluar_vistos()
        print(f"\nTras queries base: {len(validos)} validos (objetivo {OBJETIVO_VALIDOS})")

        if len(validos) < OBJETIVO_VALIDOS:
            rastrear(SEARCH_QUERIES_RESPALDO, "res")
            validos, descartados, motivos = evaluar_vistos()
            print(f"Tras queries de respaldo: {len(validos)} validos")

        if len(validos) < OBJETIVO_VALIDOS:
            rastrear(SEARCH_QUERIES_POTENCIA, "pot")
            validos, descartados, motivos = evaluar_vistos()
            print(f"Tras queries de potencia: {len(validos)} validos")

        if len(validos) < OBJETIVO_VALIDOS:
            rastrear(SEARCH_QUERIES_AUTONOMIA, "aut")
            validos, descartados, motivos = evaluar_vistos()
            print(f"Tras queries de autonomia: {len(validos)} validos")

        if len(validos) < OBJETIVO_VALIDOS:
            rastrear(SEARCH_QUERIES_LIMPIEZA, "lim")
            validos, descartados, motivos = evaluar_vistos()
            print(f"Tras queries de limpieza: {len(validos)} validos")

    validos = validos[:OBJETIVO_VALIDOS]

    escribir_json(
        ARCHIVO_VALIDOS,
        {
            "vehiculo": "scooter_electrico",
            "fecha_scraping": FECHA_SCRAPING,
            "tipo": "ampliacion",
            "filtro_ubicacion": "La Habana",
            "filtro_fecha": ">= 2026-01-01",
            "total_anuncios_validos": len(validos),
            "urls_consultadas": urls_consultadas,
            "anuncios": validos,
        },
    )
    escribir_json(
        ARCHIVO_DESCARTADOS,
        {
            "vehiculo": "scooter_electrico",
            "fecha_scraping": FECHA_SCRAPING,
            "tipo": "ampliacion",
            "total_descartados": len(descartados),
            "descartes": descartados,
        },
    )

    precios = [a["precio_usd"] for a in validos if a["precio_usd"] is not None]
    motores = [a["motor_w"] for a in validos if a["motor_w"] is not None]
    marcas = Counter(a["marca"] for a in validos)

    lineas = [
        "REPORTE DE SCRAPING - SCOOTERS ELECTRICOS, AMPLIACION 30 -> 60 (Revolico)",
        "=" * 70,
        f"Fecha de scraping: {FECHA_SCRAPING}",
        f"Ejecucion: {ahora}",
        "",
        "CONFIGURACION:",
        f"  User-Agent: {USER_AGENT}",
        f"  Delay entre peticiones: {DELAY_SEGUNDOS}s",
        f"  Timeout: {TIMEOUT_SEGUNDOS}s",
        f"  Reintentos por URL: {REINTENTOS} (espera {ESPERA_REINTENTO}s)",
        "  Cache: cache_html_scooters_ampliacion/",
        "  Paginas de detalle visitadas: 0",
        f"  Limite por sesion: {MAX_PETICIONES} peticiones",
        "",
        "RED:",
        f"  Peticiones nuevas a revolico.com: {sesion.peticiones}",
        f"  HTML servidos desde cache: {sesion.desde_cache}",
        f"  Paginas de listado procesadas: {sesion.paginas}",
        f"  URLs de listado registradas: {len(urls_consultadas)}",
        "",
        "QUERIES:",
    ]
    for query, total in paginas_por_query.items():
        lineas.append(f"  {total:>2} pag.  {query}")
    lineas += [
        "",
        "RESULTADOS:",
        f"  Anuncios unicos revisados: {len(vistos)}",
        f"  VÁLIDOS nuevos (objetivo {OBJETIVO_VALIDOS}): {len(validos)}",
        f"  DESCARTADOS: {len(descartados)}",
        "",
        f"CAMPOS OBLIGATORIOS ({len(CAMPOS_OBLIGATORIOS)}): "
        + ", ".join(CAMPOS_OBLIGATORIOS),
        f"  Válidos con motor_w null: {sum(1 for a in validos if a['motor_w'] is None)}",
        f"  Válidos con tipo_bateria null: {sum(1 for a in validos if a['tipo_bateria'] is None)}",
        f"  Válidos con plegable null: {sum(1 for a in validos if a['plegable'] is None)}",
        "",
        "DESCARTADOS POR MOTIVO:",
    ]
    for motivo, total in motivos.most_common():
        lineas.append(f"  {total:>5}  {motivo}")
    lineas += [
        "",
        "RANGOS DE LOS VÁLIDOS:",
        f"  precio_usd: min={min(precios):.2f}  max={max(precios):.2f}  n={len(precios)}"
        if precios
        else "  precio_usd: sin datos",
        f"  motor_w: min={min(motores)}  max={max(motores)}  n={len(motores)}"
        if motores
        else "  motor_w: sin datos",
        "",
        "MARCAS MÁS FRECUENTES:",
    ]
    for marca, total in marcas.most_common():
        lineas.append(f"  {total:>5}  {marca}")
    if sesion.errores:
        lineas += ["", f"ERRORES REGISTRADOS ({len(sesion.errores)}):"]
        for error in sesion.errores[:40]:
            lineas.append(f"  - {error}")
    ARCHIVO_REPORTE.write_text("\n".join(lineas) + "\n", encoding="utf-8")

    print(f"\nRevisados: {len(vistos)} | VÁLIDOS: {len(validos)} | Descartados: {len(descartados)}")
    print(f"Peticiones nuevas: {sesion.peticiones} | Desde caché: {sesion.desde_cache}")
    for motivo, total in motivos.most_common()[:10]:
        print(f"  {total:>5}  {motivo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
