#!/usr/bin/env python3
import json
import os
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

BASE_DIR = Path("/home/leandro/ev-market-study")
MOTOS_PATH = BASE_DIR / "verticals" / "moto" / "scraping" / "motos_electricas_120.json"
BICIS_PATH = BASE_DIR / "verticals" / "bicimoto" / "scraping" / "bicis_electricas_120.json"
OUT_DIR = BASE_DIR / "verticals" / "analisis_mercado_electrico"

ALIAS_MAP = {
    "jmd": "Jmd",
    "jmd.": "Jmd",
    "topmaq": "Topmaq",
    "toqmap": "Topmaq",
    "yoazaky": "Yoazaky",
    "yoazaki": "Yoazaky",
    "mishozuki": "Mishozuki",
    "mizhozuki": "Mishozuki",
    "mishuzuki": "Mishozuki",
    "halcon": "Halcon",
    "halcón": "Halcon",
    "izuki": "Izuki",
    "isuki": "Izuki",
    "unizuki": "Izuki",
    "vedca": "Vedca",
    "bucatti": "Bucatti",
    "challenger": "Challenger",
    "treck": "Treck",
    "motostreck": "Treck",
}


def normalize_marca(s):
    if s is None:
        return "Sin Marca"
    s_stripped = str(s).strip()
    if s_stripped == "":
        return "Sin Marca"
    key = s_stripped.lower()
    if key in ALIAS_MAP:
        return ALIAS_MAP[key]
    # Title Case simple
    return s_stripped.title()


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Cargar
    with open(MOTOS_PATH, "r", encoding="utf-8") as f:
        motos_data = json.load(f)
    with open(BICIS_PATH, "r", encoding="utf-8") as f:
        bicis_data = json.load(f)

    motos_anuncios = motos_data.get("anuncios", []) or []
    bicis_anuncios = bicis_data.get("anuncios", []) or []

    rows = []
    for a in motos_anuncios:
        marca_n = normalize_marca(a.get("marca"))
        rows.append(
            {
                "vertical": "moto",
                "marca": marca_n,
                "precio_usd": a.get("precio_usd"),
                "fuente": a.get("fuente"),
            }
        )
    for a in bicis_anuncios:
        marca_n = normalize_marca(a.get("marca"))
        rows.append(
            {
                "vertical": "bicimoto",
                "marca": marca_n,
                "precio_usd": a.get("precio_usd"),
                "fuente": a.get("fuente"),
            }
        )

    df = pd.DataFrame(rows)

    # Agregación
    agg_list = []
    for marca, g in df.groupby("marca"):
        g_m = g[g["vertical"] == "moto"]
        g_b = g[g["vertical"] == "bicimoto"]

        n_motos = int(len(g_m))
        n_bicimotos = int(len(g_b))
        n_total = int(len(g))

        precio_moto = g_m["precio_usd"].mean() if n_motos > 0 else 0.0
        precio_bici = g_b["precio_usd"].mean() if n_bicimotos > 0 else 0.0
        precio_global = g["precio_usd"].mean() if n_total > 0 else 0.0

        n_rev = int((g["fuente"].fillna("").astype(str).str.lower() == "revolico").sum()) if "fuente" in g else 0
        n_tel = int(n_total - n_rev)

        agg_list.append(
            {
                "marca": marca,
                "n_motos": n_motos,
                "n_bicimotos": n_bicimotos,
                "n_total": n_total,
                "porcentaje": round(n_total / 240.0 * 100.0, 2),
                "precio_promedio_moto": round(float(precio_moto), 2),
                "precio_promedio_bicimoto": round(float(precio_bici), 2),
                "precio_promedio_global": round(float(precio_global), 2),
                "n_revolico": n_rev,
                "n_telegram": n_tel,
            }
        )

    agg_df = pd.DataFrame(agg_list).sort_values(by="n_total", ascending=False).reset_index(drop=True)
    total_marcas_distintas = int(len(agg_df))

    # Guardar JSON completo
    top_15 = agg_df.head(15).to_dict(orient="records")
    todas = agg_df.to_dict(orient="records")
    marca_dominante = agg_df.iloc[0]["marca"] if len(agg_df) > 0 else ""
    marca_dominante_n = int(agg_df.iloc[0]["n_total"]) if len(agg_df) > 0 else 0
    marca_dominante_pct = round(marca_dominante_n / 240.0 * 100.0, 2) if marca_dominante_n > 0 else 0.0

    out_json = {
        "total_anuncios": 240,
        "total_motos": 120,
        "total_bicimotos": 120,
        "total_marcas_distintas": total_marcas_distintas,
        "marca_dominante": marca_dominante,
        "marca_dominante_n": marca_dominante_n,
        "marca_dominante_pct": marca_dominante_pct,
        "top_15": top_15,
        "todas_las_marcas": todas,
    }
    with open(OUT_DIR / "marcas_mercado_electrico.json", "w", encoding="utf-8") as f:
        json.dump(out_json, f, ensure_ascii=False, indent=2)

    # 1) frecuencia
    top15_df = agg_df.head(15).copy()
    fig, ax = plt.subplots(figsize=(10, 6))
    y = range(len(top15_df))
    ax.barh(y, top15_df["n_motos"], height=0.4, left=0, color="#4C78A8", label="motos")
    ax.barh(y, top15_df["n_bicimotos"], height=0.4, left=top15_df["n_motos"], color="#54A24B", label="bicimotos")
    for i, r in enumerate(top15_df.itertuples()):
        ax.text(r.n_total + 0.3, i, str(r.n_total), va="center")
    ax.set_yticks(y)
    ax.set_yticklabels(top15_df["marca"])
    ax.set_xlabel("Número de anuncios")
    ax.set_title("Top 15 marcas — Frecuencia por vertical (motos + bicimotos)")
    ax.legend()
    plt.tight_layout()
    plt.savefig(OUT_DIR / "marcas_frecuencia.png", dpi=150)
    plt.close(fig)

    # 2) precio
    fig, ax = plt.subplots(figsize=(10, 6))
    y = range(len(top15_df))
    ax.barh(y, top15_df["precio_promedio_moto"], height=0.4, left=0, color="#4C78A8", label="precio moto")
    ax.barh(y, top15_df["precio_promedio_bicimoto"], height=0.4, left=top15_df["precio_promedio_moto"], color="#54A24B", label="precio bicimoto")
    for i, r in enumerate(top15_df.itertuples()):
        ax.text(r.precio_promedio_global + 15, i, f"${r.precio_promedio_global:.0f}", va="center")
    ax.set_yticks(y)
    ax.set_yticklabels(top15_df["marca"])
    ax.set_xlabel("Precio promedio (USD)")
    ax.set_title("Top 15 marcas — Precio promedio (moto vs bicimoto)")
    ax.legend()
    plt.tight_layout()
    plt.savefig(OUT_DIR / "marcas_precio.png", dpi=150)
    plt.close(fig)

    # 3) agrupado top10
    top10_df = agg_df.head(10).copy()
    fig, ax = plt.subplots(figsize=(10, 6))
    x = range(len(top10_df))
    width = 0.35
    ax.bar([p - width / 2 for p in x], top10_df["n_motos"], width=width, color="#4C78A8", label="moto")
    ax.bar([p + width / 2 for p in x], top10_df["n_bicimotos"], width=width, color="#54A24B", label="bicimoto")
    ax.set_xticks(x)
    ax.set_xticklabels(top10_df["marca"], rotation=30, ha="right")
    ax.set_ylabel("Número de anuncios")
    ax.set_title("Top 10 marcas — Comparación por vertical")
    ax.legend()
    plt.tight_layout()
    plt.savefig(OUT_DIR / "marcas_por_vertical.png", dpi=150)
    plt.close(fig)

    # resumen
    solo_moto = agg_df[(agg_df["n_bicimotos"] == 0) & (agg_df["n_motos"] > 0)]
    solo_bici = agg_df[(agg_df["n_motos"] == 0) & (agg_df["n_bicimotos"] > 0)]
    ambos = agg_df[(agg_df["n_motos"] > 0) & (agg_df["n_bicimotos"] > 0)]

    lines = []
    lines.append("ANÁLISIS CRUZADO DE MARCAS — MERCADO ELÉCTRICO CUBANO (MOTOS + BICIMOTOS)")
    lines.append("=" * 70)
    lines.append("")
    lines.append(f"Total anuncios: {240}")
    lines.append(f"  - Motos: {120}")
    lines.append(f"  - Bicimotos: {120}")
    lines.append("")
    lines.append(f"Total marcas distintas: {total_marcas_distintas}")
    lines.append("")
    lines.append(f"Marca dominante (por n_total): {marca_dominante}")
    lines.append(f"  - Anuncios: {marca_dominante_n}")
    lines.append(f"  - % de mercado: {marca_dominante_pct:.2f}%")
    lines.append("")
    lines.append("TOP 15 MARCAS")
    lines.append("-" * 70)
    lines.append("Marca | n_motos | n_bicimotos | n_total | % | precio_prom_global(USD)")
    lines.append("-" * 70)
    for r in top_15:
        lines.append(
            f"{r['marca']:<15} | {r['n_motos']:>7} | {r['n_bicimotos']:>11} | {r['n_total']:>8} | {r['porcentaje']:>4.1f}% | {r['precio_promedio_global']:>20.2f}"
        )
    lines.append("")
    lines.append(f"Marcas presentes solo en motos: {len(solo_moto)}")
    if len(solo_moto) > 0:
        lines.append(", ".join(solo_moto["marca"].tolist()))
    lines.append("")
    lines.append(f"Marcas presentes solo en bicimotos: {len(solo_bici)}")
    if len(solo_bici) > 0:
        lines.append(", ".join(solo_bici["marca"].tolist()))
    lines.append("")
    lines.append(f"Marcas presentes en ambos verticales: {len(ambos)}")
    if len(ambos) > 0:
        lines.append(", ".join(ambos["marca"].tolist()))
    lines.append("")
    lines.append("PRECIO PROMEDIO DE LAS TOP 5 MARCAS")
    lines.append("-" * 40)
    for r in top_15[:5]:
        lines.append(
            f"{r['marca']:<12} | moto {r['precio_promedio_moto']:>8.2f} USD | bici {r['precio_promedio_bicimoto']:>8.2f} USD | global {r['precio_promedio_global']:>8.2f} USD"
        )
    lines.append("")
    lines.append("LIMITACIONES")
    lines.append("- Solo se usaron los 120 de motos y 120 de bicimotos provistos.")
    lines.append("- Normalización de marcas basada en alias solicitados.")
    lines.append("- Precios promedio calculados con valores presentes en los anuncios.")
    lines.append("- No se modificaron los archivos originales.")

    with open(OUT_DIR / "resumen_marcas.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print("OK")
    print(f"Marcas distintas: {total_marcas_distintas}")
    print(f"Dominante: {marca_dominante} ({marca_dominante_pct:.2f}%)")


if __name__ == "__main__":
    main()
