#!/usr/bin/env python3
"""Ampliacion del scraping de bicimotos electricas en Revolico.com (30 -> 90).

Solo se consultan paginas de listado (search). NO se entra a paginas de detalle:
todo se extrae del titulo del anuncio mas el precio/moneda/ubicacion del
listado (Next.js + Apollo, __NEXT_DATA__ -> __APOLLO_STATE__).

Los extractores ya validados del scraper original (scraper.py) se reutilizan
via import: norm, clean_text, es_excluido, extraer_marca, extraer_autonomia,
precio_a_usd, parse_listado.

Salidas:
  - bicis_electricas_ampliacion.json      : hasta 60 anuncios validos nuevos
  - descartados_bicis_ampliacion.json     : todos los descartados con trazabilidad
  - reporte_scraping_bicis_ampliacion.txt : reporte de la sesion
Cache (separada):
  - cache_html_bicis_ampliacion/

No se modifica bicis_electricas.json ni scraper.py.
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

import scraper as base  # noqa: E402

ARCHIVO_VALIDOS = BASE_DIR / "bicis_electricas_ampliacion.json"
ARCHIVO_DESCARTADOS = BASE_DIR / "descartados_bicis_ampliacion.json"
ARCHIVO_REPORTE = BASE_DIR / "reporte_scraping_bicis_ampliacion.txt"
ARCHIVO_BASE = BASE_DIR / "bicis_electricas.json"
DIR_CACHE = BASE_DIR / "cache_html_bicis_ampliacion"

BASE_URL = "https://www.revolico.com"
USER_AGENT = "MiProyectoInvestigacion/1.0"
DELAY_SEGUNDOS = 3.0
TIMEOUT_SEGUNDOS = 30.0
REINTENTOS = 2
ESPERA_REINTENTO = 5.0
MAX_PETICIONES = 260
MAX_PAGINAS_POR_URL = 12
OBJETIVO_VALIDOS = 64
FECHA_SCRAPING = "2026-09-30"
ANIO_MINIMO = 2026

SEARCH_QUERIES = [
    "bicimoto electrica litio",
    "bicimoto electrica barata",
    "bicimoto electrica nueva",
    "bicimoto electrica 500w",
    "bicimoto electrica 750w",
    "bicimoto electrica 1000w",
    "bicimoto electrica 1500w",
    "bicimoto plegable",
    "bicimoto sin chapa",
    "bicimoto con chapa",
    "bicicleta electrica",
    "e-bike cuba",
]

SEARCH_QUERIES_RESPALDO = [
    "bicimoto Centro Habana",
    "bicimoto Playa",
    "bicimoto Marianao",
    "bicimoto Cerro",
    "bicimoto Habana Vieja",
    "bicimoto Diez de Octubre",
    "bicimoto Arroyo Naranjo",
    "bicimoto Boyeros",
]

SEARCH_QUERIES_MUNICIPIOS = [
    "bicimoto Vedado",
    "bicimoto Miramar",
    "bicimoto Guanabacoa",
    "bicimoto Regla",
    "bicimoto San Miguel",
    "bicimoto Cotorro",
    "bicimoto La Lisa",
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
    "autonomia_km",
    "precio_usd",
    "ubicacion",
    "fecha_publicacion",
    "url",
    "descripcion",
]

CAMPOS_OBLIGATORIOS = ["marca", "autonomia_km", "precio_usd", "ubicacion"]

RX_BICI = re.compile(r"\b(bicimot|bicimoto|bicicletas?|bici|e-?bike|ebike)\b", re.I)
RX_ID_PERMALINK = re.compile(r"(\d+)\s*$")

PALABRAS_COMBUSTION = [
    "gasolina", "gasolinera", "combustion", "combustible", "nafta", "bencina",
    "diesel", "cilindrada", "cilindro", "carburador", "carburada",
    "tanque de gasolina", "tanque de combustible",
]
PATRONES_COMBUSTION = [
    r"\b2t\b", r"\b4t\b",
    r"\b(?:50|70|90|100|110|125|150|200|250|300|400|500|600|750|1000)\s*cc\b",
    r"\bmotogasolina\b", r"\bchoperitas?\b", r"\bmoped\b",
]

NO_CANDIDATO = "__no_candidato__"

MOTIVO_EXCLUSION = {
    "triciclo": "es_triciclo",
    "trimoto": "es_triciclo",
    "3 ruedas": "es_triciclo",
    "4 ruedas": "es_triciclo",
    "cuatriciclo": "es_triciclo",
    "moto de combustib. / no es e-bike": "es_combustion",
    "patineta/scooter": "es_scooter",
    "moto": "es_moto",
}


def search_url(query: str) -> str:
    return f"{BASE_URL}/search?q={query.replace(' ', '+')}"


def normalizar(texto: str) -> str:
    return base.norm(texto)


def clave_titulo(titulo: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", normalizar(titulo))


def es_habana(provincias: dict, municipios: dict, anuncio: dict) -> bool:
    provincia = provincias.get(str(anuncio.get("provinceId") or ""))
    municipio = municipios.get(str(anuncio.get("municipalityId") or ""))
    if provincia and normalizar(provincia.get("name") or "") == "la habana":
        return True
    if municipio and normalizar(municipio.get("name") or "") in MUNICIPIOS_HABANA:
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


def motor_w_de(_anuncio: dict, texto: str) -> int | None:
    valor = base.num(base.RE_MOTOR.search(texto))
    return int(valor) if valor is not None else None


def es_combustion(texto: str) -> bool:
    n = normalizar(texto)
    for palabra in PALABRAS_COMBUSTION:
        if re.search(r"\b" + re.escape(palabra).replace(r"\ ", r"\s+") + r"\b", n):
            return True
    for patron in PATRONES_COMBUSTION:
        if re.search(patron, n):
            return True
    return False


def construir_campos(anuncio: dict, provincias: dict, municipios: dict) -> dict:
    titulo = base.clean_text(anuncio.get("title"))
    texto = titulo or ""
    precio_usd, _precio_cup, _moneda = base.precio_a_usd(anuncio)
    autonomia = base.extraer_autonomia(texto)
    provincia = provincias.get(str(anuncio.get("provinceId") or ""))
    return {
        "titulo": titulo or None,
        "marca": base.extraer_marca(texto),
        "motor_w": motor_w_de(anuncio, texto),
        "autonomia_km": int(autonomia) if autonomia is not None else None,
        "precio_usd": precio_usd,
        "ubicacion": provincia.get("name") if provincia else None,
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
    titulos_base: set[str],
) -> str | None:
    if not campos["titulo"]:
        return "titulo_vacio"
    if not RX_BICI.search(normalizar(campos["texto"])):
        return NO_CANDIDATO
    if campos["url"] in urls_base or clave_titulo(campos["titulo"]) in titulos_base:
        return "url_duplicada"
    if not es_habana(provincias, municipios, anuncio):
        return "fuera_de_la_habana"
    excluido = base.es_excluido(campos["texto"])
    if excluido:
        return MOTIVO_EXCLUSION.get(excluido, "es_moto")
    if es_combustion(campos["texto"]):
        return "es_combustion"
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


def cargar_urls_base() -> tuple[set[str], set[str]]:
    if not ARCHIVO_BASE.exists():
        return set(), set()
    datos = json.loads(ARCHIVO_BASE.read_text(encoding="utf-8"))
    urls, titulos = set(), set()
    for anuncio in datos.get("anuncios", []):
        url = anuncio.get("url_anuncio")
        if url:
            urls.add(url)
        titulos.add(clave_titulo(anuncio.get("titulo") or ""))
    return urls, titulos


def escribir_json(ruta: Path, datos) -> None:
    with open(ruta, "w", encoding="utf-8") as archivo:
        json.dump(datos, archivo, ensure_ascii=False, indent=2)
        archivo.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Ampliacion scraping bicimotos electricas")
    parser.add_argument("--refrescar", action="store_true", help="ignora la caché")
    args = parser.parse_args()

    inicio = datetime.now(timezone.utc)
    ahora = inicio.strftime("%Y-%m-%dT%H:%M:%SZ")
    sesion = Sesion(usar_cache=not args.refrescar)
    urls_base, titulos_base = cargar_urls_base()
    print(f"Anuncios ya existentes en bicis_electricas.json: {len(urls_base)} urls\n")

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
                anuncios, provs, munis, _paginas = base.parse_listado(html)
                if not anuncios:
                    break
                sesion.paginas += 1
                paginas_por_query[query] = pagina
                provincias.update(provs)
                municipios.update(munis)
                nuevos = 0
                for anuncio in anuncios.values():
                    clave = clave_titulo(base.clean_text(anuncio.get("title")))
                    if not clave or clave in vistos:
                        continue
                    vistos[clave] = anuncio
                    nuevos += 1
                print(
                    f"[{etiqueta} p{pagina:>2}] {query:<28} n={len(anuncios):>3} "
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
                campos, anuncio, provincias, municipios, urls_base, titulos_base
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
            rastrear(SEARCH_QUERIES_MUNICIPIOS, "mun")
            validos, descartados, motivos = evaluar_vistos()
            print(f"Tras queries de municipios: {len(validos)} validos")

    validos = validos[:OBJETIVO_VALIDOS]

    escribir_json(
        ARCHIVO_VALIDOS,
        {
            "vehiculo": "bicimoto_electrica",
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
            "vehiculo": "bicimoto_electrica",
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
        "REPORTE DE SCRAPING - BICIMOTOS ELECTRICAS, AMPLIACION 30 -> 90 (Revolico)",
        "=" * 70,
        f"Fecha de scraping: {FECHA_SCRAPING}",
        f"Ejecucion: {ahora}",
        "",
        "CONFIGURACION:",
        f"  User-Agent: {USER_AGENT}",
        f"  Delay entre peticiones: {DELAY_SEGUNDOS}s",
        f"  Timeout: {TIMEOUT_SEGUNDOS}s",
        f"  Reintentos por URL: {REINTENTOS} (espera {ESPERA_REINTENTO}s)",
        "  Cache: cache_html_bicis_ampliacion/",
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
        "CAMPOS OBLIGATORIOS (4): marca, autonomia_km, precio_usd, ubicacion",
        f"  Válidos con motor_w null: {sum(1 for a in validos if a['motor_w'] is None)}",
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
    for motivo, total in motivos.most_common()[:8]:
        print(f"  {total:>5}  {motivo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())