#!/usr/bin/env python3
"""
Comparador de mercados: formal vs informal (Revolico/Telegram con clustering).
Lee solo desde data/ y escribe solo en output/.
Campos obligatorios: marca, motor_w, autonomia_km, precio_usd
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"

JSON_BICIS = DATA_DIR / "bicis_120_con_clusters.json"
JSON_MOTOS = DATA_DIR / "motos_120_con_clusters.json"
CSV_FORMAL_BICIMOTOS = DATA_DIR / "mercado_formal_bicimotos.csv"
CSV_FORMAL_MOTOS = DATA_DIR / "mercado_formal_motos.csv"

CSV_COMPARACION = OUTPUT_DIR / "comparacion_formal_vs_informal.csv"
PNG_DISPERSION = OUTPUT_DIR / "dispersion_formal_informal.png"

ALIAS = {
    "marca": ["marca", "brand", "modelo_marca", "model", "nombre", "modelo"],
    "motor_w": ["motor_w", "potencia_w", "motor", "potencia", "watts", "w"],
    "autonomia_km": ["autonomia_km", "autonomia", "autonomy_km", "rango_km", "km", "rango"],
    "precio_usd": ["precio_usd", "precio", "price_usd", "price", "precio_final_usd", "usd"],
}

TOL_MOTOR_W = 300.0
TOL_AUTONOMIA_KM = 15.0
OUTLIER_PCT = 0.30

COUNTERS = {"informal_descartados": 0, "formal_descartados": 0}


def ensure_output_dir() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def is_valid_value(v: Any) -> bool:
    if v is None:
        return False
    try:
        if pd.isna(v):
            return False
    except Exception:
        pass
    if isinstance(v, str):
        return v.strip() != ""
    if isinstance(v, (int, float, np.integer, np.floating)):
        return v != 0
    return True


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    col_map = {}
    for std, aliases in ALIAS.items():
        for c in df.columns:
            if str(c).strip().lower() in [a.lower() for a in aliases]:
                col_map[c] = std
                break
    return df.rename(columns=col_map) if col_map else df


def cargar_informal() -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    descartados = 0
    for path, vert in [(JSON_BICIS, "bicimoto"), (JSON_MOTOS, "moto")]:
        if not path.exists():
            raise FileNotFoundError(f"No existe: {path}")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        anuncios = data.get("anuncios", []) if isinstance(data, dict) else []
        for a in anuncios:
            row = {
                "marca": a.get("marca"),
                "motor_w": a.get("motor_w"),
                "autonomia_km": a.get("autonomia_km"),
                "precio_usd": a.get("precio_usd"),
                "vertical": vert,
                "origen": "informal",
            }
            if all(is_valid_value(row[k]) for k in ("marca", "motor_w", "autonomia_km", "precio_usd")):
                rows.append(row)
            else:
                descartados += 1
    COUNTERS["informal_descartados"] = descartados
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["marca", "motor_w", "autonomia_km", "precio_usd", "vertical", "origen"])
    for c in ("motor_w", "autonomia_km", "precio_usd"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["marca"] = df["marca"].astype(str).str.strip()
    return df


def _cargar_formal_csv(path: Path, vertical: str) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"No existe: {path}")
    df = pd.read_csv(path)
    df = normalize_columns(df)
    required = ["marca", "motor_w", "autonomia_km", "precio_usd"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Faltan columnas {missing} en {path.name}. Tiene: {list(df.columns)}")

    mask = (
        df["marca"].apply(lambda x: isinstance(x, str) and str(x).strip() != "")
        & pd.to_numeric(df["motor_w"], errors="coerce").notna()
        & (pd.to_numeric(df["motor_w"], errors="coerce") != 0)
        & pd.to_numeric(df["autonomia_km"], errors="coerce").notna()
        & (pd.to_numeric(df["autonomia_km"], errors="coerce") != 0)
        & pd.to_numeric(df["precio_usd"], errors="coerce").notna()
        & (pd.to_numeric(df["precio_usd"], errors="coerce") != 0)
    )
    descartados = int((~mask).sum())
    COUNTERS["formal_descartados"] += descartados

    df = df[mask].copy()
    for c in ("motor_w", "autonomia_km", "precio_usd"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["marca"] = df["marca"].astype(str).str.strip()
    df["vertical"] = vertical
    df["origen"] = "formal"
    return df[["marca", "motor_w", "autonomia_km", "precio_usd", "vertical", "origen"]]


def cargar_formal() -> pd.DataFrame:
    b = _cargar_formal_csv(CSV_FORMAL_BICIMOTOS, "bicimoto")
    m = _cargar_formal_csv(CSV_FORMAL_MOTOS, "moto")
    return pd.concat([b, m], ignore_index=True)


def encontrar_homologos(formal_row: pd.Series, informal_df: pd.DataFrame) -> pd.DataFrame:
    """
    Devuelve anuncios informales homólogos al registro formal.

    Criterio de emparejamiento (la marca NO se usa):
      1. Mismo vertical (moto vs bicimoto). Si no hay homólogos,
         se busca en el otro vertical como fallback.
      2. |motor_w formal - motor_w informal| <= TOL_MOTOR_W
      3. |autonomia_km formal - autonomia_km informal| <= TOL_AUTONOMIA_KM
    """
    if informal_df.empty:
        return informal_df.iloc[0:0].copy(), None

    def _filtrar(df_base: pd.DataFrame) -> pd.DataFrame:
        if df_base.empty:
            return df_base.iloc[0:0].copy()
        d_m = np.abs(df_base["motor_w"].astype(float) - float(formal_row["motor_w"]))
        d_a = np.abs(df_base["autonomia_km"].astype(float) - float(formal_row["autonomia_km"]))
        return df_base[(d_m <= TOL_MOTOR_W) & (d_a <= TOL_AUTONOMIA_KM)].copy()

    # 1º intento: mismo vertical
    mismo = informal_df[informal_df["vertical"] == formal_row["vertical"]]
    hom = _filtrar(mismo)
    if not hom.empty:
        m_vals = hom["motor_w"].astype(float).values
        a_vals = hom["autonomia_km"].astype(float).values
        fm, fa = float(formal_row["motor_w"]), float(formal_row["autonomia_km"])
        m_all = np.concatenate([m_vals, [fm]])
        a_all = np.concatenate([a_vals, [fa]])
        m_std = m_all.std() or 1.0
        a_std = a_all.std() or 1.0
        dist = np.sqrt(((m_vals-fm)/m_std)**2 + ((a_vals-fa)/a_std)**2)
        hom = hom.copy()
        hom["dist_euc_norm"] = dist
        closest = hom.iloc[dist.argmin()]
        return hom

    # 2º intento (fallback): otro vertical
    otro = informal_df[informal_df["vertical"] != formal_row["vertical"]]
    hom = _filtrar(otro)
    if hom.empty:
        return hom
    m_vals = hom["motor_w"].astype(float).values
    a_vals = hom["autonomia_km"].astype(float).values
    fm, fa = float(formal_row["motor_w"]), float(formal_row["autonomia_km"])
    m_all = np.concatenate([m_vals, [fm]])
    a_all = np.concatenate([a_vals, [fa]])
    m_std = m_all.std() or 1.0
    a_std = a_all.std() or 1.0
    dist = np.sqrt(((m_vals-fm)/m_std)**2 + ((a_vals-fa)/a_std)**2)
    hom = hom.copy()
    hom["dist_euc_norm"] = dist
    closest = hom.iloc[dist.argmin()]
    return hom


def filtrar_outliers(df_hom: pd.DataFrame) -> pd.DataFrame:
    if df_hom.empty:
        return df_hom.copy()
    med = df_hom["precio_usd"].median()
    if pd.isna(med) or med <= 0:
        return df_hom.copy()
    lo, hi = med * (1 - OUTLIER_PCT), med * (1 + OUTLIER_PCT)
    return df_hom[(df_hom["precio_usd"] >= lo) & (df_hom["precio_usd"] <= hi)].copy()


def analizar() -> pd.DataFrame:
    """Realiza análisis completo formal vs informal."""
    ensure_output_dir()
    df_inf = cargar_informal()
    df_form = cargar_formal()

    resultados: List[Dict[str, Any]] = []

    if df_form.empty:
        return pd.DataFrame(columns=[
            "vertical", "marca_formal", "motor_formal_w", "autonomia_formal_km", "precio_formal",
            "n_homologos", "precio_informal_prom", "precio_informal_min", "precio_informal_max",
            "dif_usd", "dif_pct", "mas_barato"
        ])

    for _, fr in df_form.iterrows():
        hom = encontrar_homologos(fr, df_inf)
        n_h = len(hom)
        if n_h > 0:
            hom_f = filtrar_outliers(hom)
            n_h_f = len(hom_f)
            if n_h_f > 0:
                p_inf_prom = float(hom_f["precio_usd"].mean())
                p_inf_min = float(hom_f["precio_usd"].min())
                p_inf_max = float(hom_f["precio_usd"].max())
                n_h_eff = int(n_h_f)
            else:
                p_inf_prom = np.nan
                p_inf_min = np.nan
                p_inf_max = np.nan
                n_h_eff = 0
        else:
            p_inf_prom = np.nan
            p_inf_min = np.nan
            p_inf_max = np.nan
            n_h_eff = 0

        p_form = float(fr["precio_usd"])
        if pd.notna(p_inf_prom) and p_inf_prom > 0 and p_form > 0:
            dif_usd = p_form - p_inf_prom
            dif_pct = (dif_usd / p_inf_prom) * 100.0
            if dif_usd < 0:
                mas_barato = "formal"
            elif dif_usd > 0:
                mas_barato = "informal"
            else:
                mas_barato = "igual"
        else:
            dif_usd = np.nan
            dif_pct = np.nan
            mas_barato = "sin_homologos"

        res = {
            "vertical": fr["vertical"],
            "marca_formal": fr["marca"],
            "motor_formal_w": float(fr["motor_w"]),
            "autonomia_formal_km": float(fr["autonomia_km"]),
            "precio_formal": p_form,
            "n_homologos": n_h_eff,
            "precio_informal_prom": p_inf_prom,
            "precio_informal_min": p_inf_min,
            "precio_informal_max": p_inf_max,
            "dif_usd": dif_usd,
            "dif_pct": dif_pct,
            "mas_barato": mas_barato,
        }
        if n_h > 0 and n_h_eff == 0:
            res["n_homologos"] = 0
            res["mas_barato"] = "sin_homologos"
        resultados.append(res)

    df_res = pd.DataFrame(resultados)
    return df_res


def graficar(df_res: pd.DataFrame) -> None:
    """Genera dispersión precio formal vs precio informal promedio."""
    if df_res.empty:
        return
    dfp = df_res.dropna(subset=["precio_informal_prom", "precio_formal"]).copy()
    if dfp.empty:
        return
    fig, ax = plt.subplots(figsize=(8, 6))
    for vert, g in dfp.groupby("vertical"):
        ax.scatter(g["precio_formal"], g["precio_informal_prom"], label=vert, alpha=0.7, s=60)
    # línea de paridad
    xlim = ax.get_xlim()
    ylim = ax.get_ylim()
    lims = [min(xlim[0], ylim[0]), max(xlim[1], ylim[1])]
    ax.plot(lims, lims, ls="--", color="gray", lw=1, label="paridad")
    ax.set_xlim(lims)
    ax.set_ylim(lims)
    ax.set_xlabel("Precio formal (USD)")
    ax.set_ylabel("Precio informal promedio (USD)")
    ax.set_title("Dispersión: Formal vs Informal (homólogos)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(PNG_DISPERSION, dpi=150)
    plt.close(fig)


def imprimir_resumen(df_res: pd.DataFrame) -> None:
    """Imprime resumen en consola."""
    print("=" * 80)
    print("COMPARACIÓN FORMAL VS INFORMAL")
    print("=" * 80)
    print("\nArchivos leídos:")
    print(f"  - {JSON_BICIS}")
    print(f"  - {JSON_MOTOS}")
    print(f"  - {CSV_FORMAL_BICIMOTOS}")
    print(f"  - {CSV_FORMAL_MOTOS}")
    print("\nArchivos escritos:")
    print(f"  - {CSV_COMPARACION}")
    print(f"  - {PNG_DISPERSION}")
    print("\n" + "=" * 80)
    print("TABLA COMPLETA")
    print("=" * 80)
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 200)
    print(df_res.to_string(index=False))
    print("\n" + "=" * 80)
    print("RESUMEN POR VERTICAL")
    print("=" * 80)
    if df_res.empty:
        print("(sin datos)")
    else:
        for vert, g in df_res.groupby("vertical"):
            g_ok = g.dropna(subset=["dif_pct"])
            dif_prom = g_ok["dif_pct"].mean() if not g_ok.empty else np.nan
            print(f"\n[{vert}]")
            print(f"  Ofertas formales: {len(g)}")
            print(f"  Con homólogos válidos: {len(g_ok)}")
            if pd.notna(dif_prom):
                print(f"  dif_pct promedio: {dif_prom:.2f}% (positivo = formal más caro)")
            else:
                print("  dif_pct promedio: N/A (sin homólogos válidos)")
    print("\n" + "=" * 80)
    print("CONTADORES")
    print("=" * 80)
    print(f"  Informal - descartados por campos incompletos: {COUNTERS['informal_descartados']}")
    print(f"  Formal   - descartados por campos incompletos:   {COUNTERS['formal_descartados']}")
    print("\n" + "=" * 80)
    print("CONCLUSIÓN")
    print("=" * 80)
    if not df_res.empty:
        g_ok = df_res.dropna(subset=["dif_pct"])
        if not g_ok.empty:
            dif_prom_total = g_ok["dif_pct"].mean()
            if dif_prom_total > 0:
                barato = "informal"
                sentido = f"formal cuesta en promedio {dif_prom_total:.2f}% más que informal"
            elif dif_prom_total < 0:
                barato = "formal"
                sentido = f"formal cuesta en promedio {abs(dif_prom_total):.2f}% menos que informal"
            else:
                barato = "empate"
                sentido = "sin diferencia promedio"
            print(f"  Mercado más barato en promedio: {barato} ({sentido})")
            for vert, g in g_ok.groupby("vertical"):
                dpv = g["dif_pct"].mean()
                print(f"  [{vert}] dif_pct promedio: {dpv:.2f}%")
            idx = g_ok["dif_pct"].abs().idxmax()
            row_max = g_ok.loc[idx]
            print(f"  Mayor diferencia absoluta: [{row_max['vertical']}] {row_max['marca_formal']} ({row_max['dif_pct']:.2f}%)")
        else:
            print("  Sin homólogos válidos para calcular diferencias.")
    sin_h = df_res[df_res["mas_barato"] == "sin_homologos"]
    if len(sin_h) > 0:
        print(f"  ADVERTENCIA: {len(sin_h)} oferta(s) formal(es) sin homólogos informales")
        for _, r in sin_h.iterrows():
            print(f"    - [{r['vertical']}] {r['marca_formal']} (moto_w={r['motor_formal_w']:.0f}, aut={r['autonomia_formal_km']:.0f}km)")
    print("=" * 80)


def main() -> None:
    try:
        df_res = analizar()
        df_res.to_csv(CSV_COMPARACION, index=False, encoding="utf-8")
        graficar(df_res)
        imprimir_resumen(df_res)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
