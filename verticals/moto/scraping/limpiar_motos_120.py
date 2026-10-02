import json
from pathlib import Path

BASE = Path("/home/leandro/ev-market-study/verticals/moto/scraping")
MAIN = BASE / "motos_electricas_120.json"
REPORTE = BASE / "reporte_correcciones_motos.json"

CAMPOS = [
    "titulo", "marca", "motor_w", "autonomia_km", "precio_usd",
    "ubicacion", "url", "fecha_publicacion", "municipio", "fuente",
]

TRAZABILIDAD = [
    "precio_corregido_manual", "precio_original_erroneo",
    "ubicacion_corregida_manual", "ubicacion_original_erronea",
    "marca_corregida_manual", "marca_original_erronea",
]

MARCA_MAP = {
    "HALCÓN": "Halcon",
    "HALCON": "Halcon",
    "HALCÓN-MOUNTAIN": "Halcon",
    "MOUNTAIN": "Halcon",
    "YOAZAKI": "Yoazaky",
    "Yoazaki": "Yoazaky",
    "YOAZAKY": "Yoazaky",
    "vedga": "Vedca",
    "VEDCA": "Vedca",
    "BUCATTI": "Bucatti",
    "TOPMAQ TANK": "Topmaq",
    "TOPMAQ": "Topmaq",
    "IZUKI": "Izuki",
    "KVITOVA": "Kvitova",
    "FLY": "Fly",
    "RACING": "Fly",
    "PANDA": "Panda",
    "AVA": "Ava",
    "CHALLENGER": "Challenger",
    "JMD": "Jmd",
    "MAF": "Maf",
    "MAOX": "Maox",
    "OIM": "Oim",
    "SHIKRA": "Shikra",
    "VOLTRIDER": "Voltrider",
    "VORTAX": "Volta X",
    "GS": "Gs Racing",
    "UNIZUKI": "Unizuki",
    "VOLTA X": "Volta X",
}

CORRECCIONES = []

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

MAIN.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
REPORTE.write_text(
    json.dumps({"correcciones": CORRECCIONES}, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)

print("correcciones registradas:", len(CORRECCIONES))
print("marcas renombradas:", renombradas)
