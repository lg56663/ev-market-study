import json
from pathlib import Path

BASE = Path("/home/leandro/ev-market-study/verticals/bicimoto/scraping")
MAIN = BASE / "bicis_electricas_120.json"
REPORTE = BASE / "reporte_correcciones.json"

CAMPOS = [
    "titulo", "marca", "motor_w", "autonomia_km", "precio_usd",
    "ubicacion", "url", "fecha_publicacion", "municipio", "fuente",
]

TRAZABILIDAD = [
    "precio_corregido_manual",
    "precio_original_erroneo",
    "ubicacion_corregida_manual",
    "ubicacion_original_erronea",
    "marca_corregida_manual",
    "marca_original_erronea",
]

MARCA_MAP = {
    "JMD": "Jmd",
    "TOPMAQ": "Topmaq",
    "YOAZAKY": "Yoazaky",
    "VEDCA": "Vedca",
    "MISHOZUKI": "Mishozuki",
    "HALCON": "Halcon",
    "TRANK": "Trank",
    "RX": "Rx",
    "RYDER": "Ryder",
    "BUCATTI": "Bucatti",
    "MZ": "Mz",
    "FLY": "Fly",
    "GRILLO": "Grillo",
    "YOAZAKI": "Yoazaky",
    "Maf Engle": "Maf",
}

# Trazabilidad de las 3 correcciones manuales aplicadas al dataset.
# "antes" = valor erroneo de la extraccion original; "ahora" = valor ya presente en el JSON.
CORRECCIONES = [
    {
        "url": "https://www.revolico.com/item/bicimoto-yoazaky-707-especificaciones-3000w-48v-50-ah-bateria-de-litio-antiexplosiva-hasta-120-km-de-au-54795693",
        "campo": "precio_usd",
        "antes": 707.0,
        "ahora": 1800.0,
        "motivo": "precio_erroneo_modelo",
    },
    {
        "url": "https://www.revolico.com/item/bicimoto-topmaq-evolution-envio-gratis-en-toda-la-habana-3000w-45v-45a-120-km-autonomia-57396679",
        "campo": "ubicacion",
        "antes": "Artemisa",
        "ahora": "La Habana",
        "motivo": "error_lectura_verificado",
    },
    {
        "url": "https://www.revolico.com/item/bicimoto-unizuki-2000w-48v-50amp-bateria-de-litio-antiexplosiva-hasta-130km-de-autono-56452534",
        "campo": "marca",
        "antes": "TRANK",
        "ahora": "Unizuki",
        "motivo": "extraccion_erronea",
    },
]

data = json.loads(MAIN.read_text(encoding="utf-8"))
anuncios = data["anuncios"]

renombradas = {}
for ad in anuncios:
    for traz in TRAZABILIDAD:
        ad.pop(traz, None)
    marca = ad.get("marca")
    nueva = MARCA_MAP.get(marca, marca)
    if nueva != marca:
        ad["marca"] = nueva
        renombradas[marca] = nueva
    faltantes = [c for c in CAMPOS if c not in ad]
    extra = [k for k in ad if k not in CAMPOS]
    if faltantes or extra:
        raise SystemExit(f"estructura inesperada: faltan={faltantes} extra={extra}")

data["campos"] = CAMPOS

# Validar que la trazabilidad registrada sigue cuadrando con el JSON limpio
por_url = {a["url"]: a for a in anuncios}
for c in CORRECCIONES:
    ad = por_url.get(c["url"])
    if ad is None:
        raise SystemExit(f"url no encontrado en el dataset: {c['url']}")
    if ad[c["campo"]] != c["ahora"]:
        raise SystemExit(f"valor no coincide en {c['url']}: {ad[c['campo']]} != {c['ahora']}")

MAIN.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
REPORTE.write_text(
    json.dumps({"correcciones": CORRECCIONES}, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)

print("correcciones registradas:", len(CORRECCIONES))
print("marcas renombradas:", renombradas)