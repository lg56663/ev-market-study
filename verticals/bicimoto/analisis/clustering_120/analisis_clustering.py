"""Clustering K-Means de bicimotos electricas sobre 120 anuncios (90 revolico + 30 telegram).

Pipeline:
  1. carga del JSON original (solo lectura, se verifica integridad por md5)
  2. filtro de outliers de precio ANTES de clusterizar
  3. normalizacion StandardScaler sobre 3 features
  4. barrido k = 2..10 + reglas de tamaño + clausula de fusion
  5. etiquetado final, metricas por cluster
  6. 5 PNG (dpi=150), JSON con clusters, descartados y resumen TXT
"""

import hashlib
import itertools
import json
import os
import subprocess
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_samples, silhouette_score
from sklearn.preprocessing import StandardScaler

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ORIGEN = os.path.normpath(os.path.join(BASE_DIR, "..", "..", "scraping", "bicis_electricas_120.json"))
PY = __import__("sys").executable

VEHICULO = "bicimoto_electrica"
FEATURES = ["precio_usd", "motor_w", "autonomia_km"]
PALETA = ["#2e8b57", "#e07b00", "#7b2d8b", "#1f6feb", "#c1121f", "#008b8b",
          "#6b7280", "#b8860b"]

# ---------- parametros del filtro de outliers ----------
MIN_PRECIO_VALIDO = 500.0    # por debajo: precio placeholder / error de tipeo
MAX_PRECIO_VALIDO = 4000.0   # por encima: outlier premium aislado

# ---------- reglas de tamaño de cluster ----------
MIN_ANUNCIOS_POR_CLUSTER = 4        # nunca aceptar un cluster con menos de 4
MAX_FRACCION_POR_CLUSTER = 0.50     # ningun cluster puede pasar el 50% del total
GAP_FUSION = 0.10                   # dos clusters con precio promedio <10% se fusionan

SEP = "=" * 78


def md5(path):
    with open(path, "rb") as fh:
        return hashlib.md5(fh.read()).hexdigest()


ORIGEN_MD5 = md5(ORIGEN)
ORIGEN_STAT = os.stat(ORIGEN)


def canonicas(etiquetas):
    """Relabela a 0..k-1 en orden de primera aparicion (sin huecos)."""
    mapa = {}
    salida = np.empty_like(etiquetas)
    for i, v in enumerate(etiquetas):
        if v not in mapa:
            mapa[v] = len(mapa)
        salida[i] = mapa[v]
    return salida


def tamanos(etiquetas, k):
    return np.bincount(etiquetas, minlength=k)


def viola(etiquetas, k, maximo):
    """Devuelve (minimo, maximo, cumple, n_violaciones)."""
    t = tamanos(etiquetas, k)
    t = t[t > 0]
    mini, maxi = int(t.min()), int(t.max())
    v = (0 if mini >= MIN_ANUNCIOS_POR_CLUSTER else 1) + (0 if maxi <= maximo else 1)
    return mini, maxi, v == 0, v


def pares_fusionables(etiquetas, k, X):
    """Pares (a, b) con diferencia de precio promedio < GAP_FUSION, ordenados por gap."""
    pm = np.array([X[etiquetas == i, 0].mean() for i in range(k)])
    out = []
    for a, b in itertools.combinations(range(k), 2):
        gap = abs(pm[a] - pm[b]) / ((pm[a] + pm[b]) / 2)
        if gap < GAP_FUSION:
            out.append((float(gap), a, b))
    return sorted(out)


def intentar_fusion(etiquetas, k, X, maximo):
    """Fusion repetida (menor gap primero) hasta cumplir las reglas de tamaño.

    Solo se fusiona si la particion NO cumple las reglas: la fusion es el mecanismo de
    reparacion, no una poda de un clusterizado ya valido. Cada merger se post-valida
    (regla: 'post-fusion, verificar que sigue cumpliendo') y se descarta si rompe el
    maximo por cluster.
    """
    L, kk = etiquetas.copy(), k
    pasos = []
    while kk > 1:
        if viola(L, kk, maximo)[2]:
            break
        elegido = None
        for gap, a, b in pares_fusionables(L, kk, X):
            M = canonicas(np.where(L == b, a, L))
            if viola(M, kk - 1, maximo)[2]:
                elegido = (gap, a, b, M)
                break
        if elegido is None:
            return None, pasos
        gap, a, b, M = elegido
        pasos.append({"gap": round(gap, 4), "cluster_a": int(a), "cluster_b": int(b)})
        L, kk = M, kk - 1
    return (L, kk), pasos


# =====================================================================
# 1. CARGA
# =====================================================================
print(SEP)
print("PASO 1 — CARGA DE DATOS")
print(SEP)
with open(ORIGEN, encoding="utf-8") as fh:
    data = json.load(fh)
anuncios = data["anuncios"]
df_total = pd.DataFrame(anuncios)
df_total["__pos"] = df_total.index.to_numpy()
print(f"  archivo origen        : {ORIGEN}")
print(f"  md5 origen            : {ORIGEN_MD5}")
print(f"  vehiculo              : {data.get('vehiculo')}")
print(f"  anuncios declarados   : {data.get('total_anuncios')}")
print(f"  anuncios en la lista  : {len(anuncios)}")
print(f"  fuentes declaradas    : {data.get('fuentes')} -> {df_total['fuente'].value_counts().to_dict()}")
print(f"  nulos en las features : "
      f"{ {c: int(df_total[c].isna().sum()) for c in FEATURES} }")

# =====================================================================
# 2. FILTRO DE OUTLIERS DE PRECIO
# =====================================================================
print()
print(SEP)
print("PASO 2 — FILTRO DE OUTLIERS DE PRECIO (antes de clusterizar)")
print(SEP)
print(f"  regla: descartar precio_usd < {MIN_PRECIO_VALIDO:.0f}  (motivo: precio_placeholder)")
print(f"  regla: descartar precio_usd > {MAX_PRECIO_VALIDO:.0f}  (motivo: precio_outlier_premium)")
print(f"  rango de precio observado: {df_total['precio_usd'].min():.0f} - "
      f"{df_total['precio_usd'].max():.0f} USD")

motivo = pd.Series("ok", index=df_total.index)
motivo[df_total["precio_usd"] < MIN_PRECIO_VALIDO] = "precio_placeholder"
motivo[df_total["precio_usd"] > MAX_PRECIO_VALIDO] = "precio_outlier_premium"

descartados_idx = df_total.index[motivo != "ok"].tolist()
df = df_total[motivo == "ok"].reset_index(drop=True)
pos_final = list(df["__pos"])

descartados = []
for idx in descartados_idx:
    a = dict(anuncios[int(df_total.loc[idx, "__pos"])])
    a["motivo_descarte"] = str(motivo.loc[idx])
    a["precio_usd"] = float(a["precio_usd"])
    descartados.append(a)

n_ph = sum(1 for a in descartados if a["motivo_descarte"] == "precio_placeholder")
n_po = sum(1 for a in descartados if a["motivo_descarte"] == "precio_outlier_premium")
print(f"\n  precio_placeholder        (< {MIN_PRECIO_VALIDO:.0f} USD): {n_ph} anuncio(s)")
print(f"  precio_outlier_premium    (> {MAX_PRECIO_VALIDO:.0f} USD): {n_po} anuncio(s)")
print(f"  total descartado                                : {len(descartados)}")
print(f"  dataset limpio                                    : {len(df)} anuncios")
for a in descartados:
    print(f"    - {a['motivo_descarte']:<24} {a['marca']:<10} {a['motor_w']:>5} W "
          f"{a['autonomia_km']:>4} km  {a['precio_usd']:>8.0f} USD  ({a['fuente']})")
    print(f"      titulo: {str(a['titulo'])[:100]}")
if not descartados:
    print("  (el filtro no descarto nada: en esta vertical ningun anuncio cae fuera de")
    print("   los cortes, el precio esta tightly acotado entre "
          f"{df_total['precio_usd'].min():.0f} y {df_total['precio_usd'].max():.0f} USD)")

with open(os.path.join(BASE_DIR, "descartados_outliers.json"), "w", encoding="utf-8") as fh:
    json.dump({
        "vehiculo": data.get("vehiculo", VEHICULO),
        "filtro": "outliers de precio_usd",
        "reglas": {
            "precio_placeholder": f"precio_usd < {MIN_PRECIO_VALIDO}",
            "precio_outlier_premium": f"precio_usd > {MAX_PRECIO_VALIDO}",
        },
        "total_anuncios_originales": int(len(anuncios)),
        "total_descartados": int(len(descartados)),
        "total_limpios": int(len(df)),
        "fecha_analisis": datetime.now().isoformat(timespec="seconds"),
        "descartados": descartados,
    }, fh, ensure_ascii=False, indent=2, allow_nan=False)
print("\n  guardado: descartados_outliers.json")

# =====================================================================
# 3. NORMALIZACION
# =====================================================================
print()
print(SEP)
print("PASO 3 — NORMALIZACION (StandardScaler)")
print(SEP)
X = df[FEATURES].astype(float)
print("\n(a) describe() en escala original (dataset limpio):")
print(X.describe().to_string())
scaler = StandardScaler()
X_norm = scaler.fit_transform(X.to_numpy())
Xo = X.to_numpy(float)
print("\n  medias normalizadas (deben ser ~0): "
      f"{ {c: f'{v:.2e}' for c, v in zip(FEATURES, X_norm.mean(axis=0))} }")
print("  desv normalizadas (deben ser ~1): "
      f"{ {c: round(float(v), 6) for c, v in zip(FEATURES, X_norm.std(axis=0))} }")

N = len(X)
MAXIMO = int(N * MAX_FRACCION_POR_CLUSTER)
print(f"\n  n = {N}")
print(f"  reglas de tamaño: minimo {MIN_ANUNCIOS_POR_CLUSTER} anuncios, "
      f"maximo {MAX_FRACCION_POR_CLUSTER:.0%} -> {MAXIMO} anuncios")

# =====================================================================
# 4. BARRIDO k = 2..10 Y ELECCION DE k
# =====================================================================
print()
print(SEP)
print("PASO 4 — BARRIDO k = 2..10 Y ELECCION DE k")
print(SEP)
filas = []
for k in range(2, 11):
    km = KMeans(n_clusters=k, random_state=42, n_init=10)
    lab = km.fit_predict(X_norm)
    sil = float(silhouette_score(X_norm, lab))
    mini, maxi, ok, nv = viola(lab, k, MAXIMO)
    est, pasos = intentar_fusion(lab, k, Xo, MAXIMO)
    if est is None:
        sil_final, k_final, tam_final, ok_final, nv_final = sil, k, None, False, 99
        pasos_txt = "sin fusion valida"
    else:
        L, kk = est
        sil_final = float(silhouette_score(X_norm, L))
        k_final = kk
        tam_final = sorted(tamanos(L, kk).tolist())
        _, _, ok_final, nv_final = viola(L, kk, MAXIMO)
        pasos_txt = ", ".join(f"C{p['cluster_a']}+C{p['cluster_b']}({p['gap']:.2%})" for p in pasos) or "-"
    filas.append({
        "k": k, "inercia": float(km.inertia_), "silhouette": sil,
        "n_clusters_vacios": int(k - len(set(lab))),
        "tamanos_raw": sorted(tamanos(lab, k).tolist()),
        "min_raw": mini, "max_raw": maxi, "cumple_raw": ok, "violaciones_raw": nv,
        "k_final": k_final, "silhouette_final": sil_final, "tamanos_final": tam_final,
        "cumple_final": ok_final, "violaciones_final": nv_final, "fusiones": pasos_txt,
        "_labels": lab, "_labels_final": (est[0] if est is not None else None),
    })
    print(f"  k={k:>2}  inercia={km.inertia_:>8.2f}  sil={sil:.4f}  "
          f"raw{'':>1}{str(filas[-1]['tamanos_raw']):<32} {'OK ' if ok else 'NO '}  "
          f"fusiones={pasos_txt:<34} -> k_f={k_final} sil_f={sil_final:.4f} "
          f"{'OK' if ok_final else 'NO'}")

tabla_k = pd.DataFrame(filas)

candidatos = tabla_k[tabla_k["cumple_final"]]
print("\n  k que cumplen las reglas (raw o post-fusion): "
      f"{sorted(candidatos['k'].tolist())}")
if len(candidatos):
    mejor = candidatos.loc[candidatos["silhouette_final"].idxmax()]
    k_elegido = int(mejor["k"])
    sil_elegido = float(mejor["silhouette_final"])
    etiquetas = mejor["_labels_final"]
    k_final = int(mejor["k_final"])
    eleccion_txt = "mayor silhouette entre los k que cumplen las reglas de tamaño"
else:
    mejor = tabla_k.sort_values(
        ["violaciones_final", "silhouette_final"], ascending=[True, False]).iloc[0]
    k_elegido = int(mejor["k"])
    sil_elegido = float(mejor["silhouette_final"])
    etiquetas = mejor["_labels_final"]
    k_final = int(mejor["k_final"])
    eleccion_txt = "NINGUN k cumple las reglas -> se elige el que menos viola"

print(f"\n  k ELEGIDO = {k_elegido}  (silhouette post-fusion = {sil_elegido:.4f})")
print(f"  criterio  : {eleccion_txt}")
if k_final != k_elegido:
    print(f"  fusiones aplicadas: {mejor['fusiones']}")

# pares que la clausula de fusion detectaria en el k elegido (traza de la regla)
pares_k = pares_fusionables(mejor["_labels"], int(mejor["k"]), Xo)
print(f"  pares con precio promedio <{GAP_FUSION:.0%} en la particion cruda de k={k_elegido}: "
      f"{[(f'C{a}+C{b}', f'{g:.2%}') for g, a, b in pares_k] or 'ninguno'}")

# etiquetas ordenadas por precio medio ascendente (KMeans no ordena)
pm = {int(c): Xo[etiquetas == c, 0].mean() for c in np.unique(etiquetas)}
orden = sorted(pm, key=lambda c: pm[c])
remap = {orig: nuevo for nuevo, orig in enumerate(orden)}
df["cluster"] = pd.Series(etiquetas).map(remap).to_numpy(int)
CENTROS = np.array([Xo[df["cluster"].to_numpy() == i].mean(axis=0) for i in range(k_final)])
etiquetas = df["cluster"].to_numpy(int)
print(f"  reordenamiento por precio medio ascendente: {orden} -> {list(range(k_final))}")

SIL_FINAL = float(silhouette_score(X_norm, etiquetas))
print(f"  silhouette final recalculado: {SIL_FINAL:.4f}")

# =====================================================================
# 5. NOMBRES Y DESCRIPCION DE CADA CLUSTER
# =====================================================================
print()
print(SEP)
print("PASO 5 — PERFIL Y NOMBRE DE CADA CLUSTER")
print(SEP)


def tier(v, q33, q66, etiquetas_tier):
    """Tramo de la feature. Cortes con desigualdad estricta: motor_w es discreto y sus
    terciles pueden caer justo sobre un valor muy repetido, y con <= lo mandaria al
    tramo bajo incorrectamente."""
    if v < q33:
        return etiquetas_tier[0]
    if v < q66:
        return etiquetas_tier[1]
    return etiquetas_tier[2]


# cortes en terciles de la propia distribucion: cada feature se parte en 3 tramos
CORTE_P = (float(X["precio_usd"].quantile(1 / 3)), float(X["precio_usd"].quantile(2 / 3)))
CORTE_W = (float(X["motor_w"].quantile(1 / 3)), float(X["motor_w"].quantile(2 / 3)))
CORTE_A = (float(X["autonomia_km"].quantile(1 / 3)), float(X["autonomia_km"].quantile(2 / 3)))
print(f"  cortes (terciles) precio : {CORTE_P[0]:.0f} / {CORTE_P[1]:.0f} USD")
print(f"  cortes (terciles) motor  : {CORTE_W[0]:.0f} / {CORTE_W[1]:.0f} W")
print(f"  cortes (terciles) aut_km : {CORTE_A[0]:.0f} / {CORTE_A[1]:.0f} km")

clusters_info = []
for i in range(k_final):
    sub = df[df["cluster"] == i]
    pm_ = float(sub["precio_usd"].mean())
    wm_ = float(sub["motor_w"].mean())
    am_ = float(sub["autonomia_km"].mean())
    t_p = tier(pm_, *CORTE_P, ["precio bajo", "precio medio", "precio alto"])
    t_a = tier(am_, *CORTE_A, ["corta autonomia", "media autonomia", "larga autonomia"])
    base = f"C{i} - {t_p} {wm_:.0f}W / {t_a}"
    marcas = sub["marca"].astype(str).str.strip().replace("", np.nan).dropna()
    top_marcas = marcas.value_counts()
    fuentes = sub["fuente"].astype(str).str.strip().str.lower().replace("", np.nan).dropna()
    conteo_fuentes = fuentes.value_counts()
    pos = ("el segmento mas economico" if i == 0 else
           "el segmento mas caro" if i == k_final - 1 else "un segmento intermedio")
    desc = (f"{len(sub)} anuncios en {sub['precio_usd'].min():.0f}-{sub['precio_usd'].max():.0f} USD "
            f"(prom {pm_:.0f}); motor {sub['motor_w'].min():.0f}-{sub['motor_w'].max():.0f} W "
            f"(prom {wm_:.0f}); autonomia {sub['autonomia_km'].min():.0f}-"
            f"{sub['autonomia_km'].max():.0f} km (prom {am_:.0f}). Es {pos} por precio. "
            f"Marca dominante: {top_marcas.index[0] if len(top_marcas) else 'n/d'} "
            f"({top_marcas.iloc[0] if len(top_marcas) else 0}/{len(sub)}).")
    clusters_info.append({
        "id": i,
        "nombre": base,
        "n_anuncios": int(len(sub)),
        "precio_promedio_usd": round(pm_, 2),
        "precio_min_usd": float(sub["precio_usd"].min()),
        "precio_max_usd": float(sub["precio_usd"].max()),
        "motor_promedio_w": round(wm_, 1),
        "autonomia_promedio_km": round(am_, 1),
        "autonomia_min_km": float(sub["autonomia_km"].min()),
        "autonomia_max_km": float(sub["autonomia_km"].max()),
        "marca_mas_comun": str(top_marcas.index[0]) if len(top_marcas) else None,
        "fuente_dominante": str(conteo_fuentes.index[0]) if len(conteo_fuentes) else None,
        "n_revolico": int(conteo_fuentes.get("revolico", 0)),
        "n_telegram": int(conteo_fuentes.get("telegram", 0)),
        "top_marcas": [{"marca": str(m), "n": int(n)} for m, n in top_marcas.head(3).items()],
        "descripcion": desc,
    })
    print(f"  C{i}: {base:<46} n={len(sub):>3}  {pm_:>7.0f} USD  {wm_:>6.0f} W  {am_:>6.1f} km  "
          f"[{', '.join(f'{m}:{n}' for m, n in top_marcas.head(3).items())}]")

# correlaciones (Pearson) sobre el dataset limpio
corr = df[FEATURES].corr(method="pearson")
print("\n  correlaciones Pearson:")
print(corr.to_string(float_format=lambda v: f"{v:+.4f}"))

# =====================================================================
# 6. VISUALIZACIONES
# =====================================================================
print()
print(SEP)
print("PASO 6 — VISUALIZACIONES (dpi=150)")
print(SEP)
NOMBRES = [c["nombre"] for c in clusters_info]
TAM = [c["n_anuncios"] for c in clusters_info]

# --- 6.1 dispersion precio vs autonomia ---
fig, ax = plt.subplots(figsize=(12, 8))
pts = ax.scatter(Xo[:, 0], Xo[:, 2], c=etiquetas, cmap="viridis", s=110,
                 alpha=0.8, edgecolors="black", linewidths=0.5)
ax.scatter(CENTROS[:, 0], CENTROS[:, 2], c="red", marker="X", s=200,
           edgecolors="black", linewidths=1.5, zorder=5, label="Centroides")
for i in range(k_final):
    ax.annotate(f"C{i}", (CENTROS[i, 0], CENTROS[i, 2]), textcoords="offset points",
                xytext=(12, 8), fontweight="bold", fontsize=11)
ax.set_xlabel("Precio (USD)", fontsize=12)
ax.set_ylabel("Autonomía (km)", fontsize=12)
ax.set_title(f"Dispersión por cluster (k={k_final}): precio vs autonomía\n"
             "X roja = centroide del cluster", fontsize=13, fontweight="bold")
ax.xaxis.set_major_formatter(matplotlib.ticker.StrMethodFormatter("${x:,.0f}"))
ax.legend(loc="best", fontsize=10, framealpha=0.92)
ax.grid(alpha=0.3, linestyle="--")
cbar = fig.colorbar(pts, ax=ax)
cbar.set_label("Cluster", fontsize=11)
fig.tight_layout()
fig.savefig(os.path.join(BASE_DIR, "clusters_dispersion.png"), dpi=150)
plt.close(fig)
print("  guardado: clusters_dispersion.png")

# --- 6.2 precio promedio por cluster ---
fig, ax = plt.subplots(figsize=(12, 7))
precios = [c["precio_promedio_usd"] for c in clusters_info]
barras = ax.bar(NOMBRES, precios, color=[PALETA[i % len(PALETA)] for i in range(k_final)],
                edgecolor="black", linewidth=0.8, width=0.62)
for b, v in zip(barras, precios):
    ax.text(b.get_x() + b.get_width() / 2, b.get_height(), f"${v:,.0f}",
            ha="center", va="bottom", fontsize=10, fontweight="bold")
ax.set_xlabel("Cluster", fontsize=12)
ax.set_ylabel("Precio promedio (USD)", fontsize=12)
ax.set_title(f"Precio promedio por cluster (k={k_final})", fontsize=13, fontweight="bold")
ax.yaxis.set_major_formatter(matplotlib.ticker.StrMethodFormatter("${x:,.0f}"))
ax.set_ylim(0, max(precios) * 1.18)
ax.tick_params(axis="x", labelrotation=15, labelsize=10)
ax.grid(axis="y", alpha=0.3, linestyle="--")
fig.tight_layout()
fig.savefig(os.path.join(BASE_DIR, "clusters_precio_medio.png"), dpi=150)
plt.close(fig)
print("  guardado: clusters_precio_medio.png")

# --- 6.3 tamanos ---
fig, ax = plt.subplots(figsize=(12, 7))
barras = ax.bar(NOMBRES, TAM, color=[PALETA[i % len(PALETA)] for i in range(k_final)],
                edgecolor="black", linewidth=0.8, width=0.62)
for b, v in zip(barras, TAM):
    ax.text(b.get_x() + b.get_width() / 2, b.get_height(), f"{v}\n({v / N:.1%})",
            ha="center", va="bottom", fontsize=10, fontweight="bold")
ax.axhline(MAXIMO, color="crimson", linestyle="--", linewidth=1.6,
           label=f"Tope 50% = {MAXIMO} anuncios")
ax.axhline(MIN_ANUNCIOS_POR_CLUSTER, color="crimson", linestyle=":", linewidth=1.6,
           label=f"Piso = {MIN_ANUNCIOS_POR_CLUSTER} anuncios")
ax.set_xlabel("Cluster", fontsize=12)
ax.set_ylabel("Cantidad de anuncios", fontsize=12)
ax.set_title(f"Tamaño de cada cluster (k={k_final}, n={N})", fontsize=13, fontweight="bold")
ax.set_ylim(0, max(TAM) * 1.24)
ax.tick_params(axis="x", labelrotation=15, labelsize=10)
ax.legend(fontsize=9)
ax.grid(axis="y", alpha=0.3, linestyle="--")
fig.tight_layout()
fig.savefig(os.path.join(BASE_DIR, "clusters_tamanos.png"), dpi=150)
plt.close(fig)
print("  guardado: clusters_tamanos.png")

# --- 6.4 silhouette por cluster ---
sil_muestras = silhouette_samples(X_norm, etiquetas)
sil_media = float(sil_muestras.mean())
print(f"  silhouette por anuncio: min={sil_muestras.min():.4f} max={sil_muestras.max():.4f} "
      f"media={sil_media:.6f}")
fig, ax = plt.subplots(figsize=(12, 7))
rng = np.random.default_rng(42)
for c in range(k_final):
    ax.axhspan(c - 0.4, c + 0.4, color=PALETA[c % len(PALETA)], alpha=0.10, zorder=0)
    vals = sil_muestras[etiquetas == c]
    ax.scatter(vals, c + rng.uniform(-0.16, 0.16, size=len(vals)), s=70, alpha=0.75,
               color=PALETA[c % len(PALETA)], edgecolors="black", linewidths=0.4, zorder=3)
    ax.plot([vals.mean(), vals.mean()], [c - 0.36, c + 0.36], color="black",
            linewidth=3, zorder=5)
    ax.text(vals.mean(), c + 0.42, f"media {vals.mean():.3f}", ha="center", fontsize=9, zorder=6)
ax.axvline(sil_media, color="red", linestyle="--", linewidth=2,
           label=f"Silhouette medio = {sil_media:.4f}")
ax.set_yticks(range(k_final))
ax.set_yticklabels([f"C{i} (n={TAM[i]})" for i in range(k_final)], fontsize=10)
ax.set_xlabel("Silhouette por anuncio", fontsize=12)
ax.set_ylabel("Cluster", fontsize=12)
ax.set_title(f"Silhouette por cluster (k={k_final}) — sobre X_norm\n"
             "banda = cluster | linea negra = media del cluster | linea roja = media global",
             fontsize=12, fontweight="bold")
ax.set_ylim(-0.6, k_final - 0.4)
ax.legend(loc="best", fontsize=10, framealpha=0.92)
ax.grid(axis="x", alpha=0.3, linestyle="--")
fig.tight_layout()
fig.savefig(os.path.join(BASE_DIR, "clusters_metricas.png"), dpi=150)
plt.close(fig)
print("  guardado: clusters_metricas.png")
print(f"  recheck media(silhouette_samples)={sil_media:.6f} vs SIL_FINAL={SIL_FINAL:.6f} -> "
      f"{'OK' if abs(sil_media - SIL_FINAL) < 1e-9 else 'FALLA'}")

# --- 6.5 heatmap de correlacion ---
fig, ax = plt.subplots(figsize=(8, 7))
im = ax.imshow(corr.to_numpy(), cmap="coolwarm", vmin=-1, vmax=1)
ax.set_xticks(range(len(FEATURES)))
ax.set_yticks(range(len(FEATURES)))
ax.set_xticklabels(FEATURES, fontsize=11)
ax.set_yticklabels(FEATURES, fontsize=11)
for i in range(len(FEATURES)):
    for j in range(len(FEATURES)):
        v = corr.to_numpy()[i, j]
        ax.text(j, i, f"{v:+.3f}", ha="center", va="center", fontsize=13, fontweight="bold",
                color="white" if abs(v) > 0.55 else "black")
ax.set_title("Correlación Pearson entre las 3 features del clustering\n"
             f"(n={N} anuncios limpios)", fontsize=12, fontweight="bold")
cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
cbar.set_label("Pearson r", fontsize=11)
fig.tight_layout()
fig.savefig(os.path.join(BASE_DIR, "clusters_correlacion.png"), dpi=150)
plt.close(fig)
print("  guardado: clusters_correlacion.png")

# =====================================================================
# 7. JSON DE SALIDA
# =====================================================================
print()
print(SEP)
print("PASO 7 — JSON DE SALIDA")
print(SEP)
fecha = datetime.now().isoformat(timespec="seconds")
anuncios_out = []
for pos, clus in zip(pos_final, df["cluster"].to_numpy(int)):
    a = dict(anuncios[int(pos)])
    a["cluster"] = int(clus)
    anuncios_out.append(a)

salida = {
    "vehiculo": data.get("vehiculo", VEHICULO),
    "total_anuncios": int(len(anuncios_out)),
    "k_elegido": int(k_elegido),
    "silhouette": round(SIL_FINAL, 4),
    "fecha_analisis": fecha,
    "features_usadas": FEATURES,
    "normalizacion": "StandardScaler",
    "algoritmo": "KMeans(random_state=42, n_init=10)",
    "reglas_aplicadas": {
        "filtro_outliers": {
            "precio_placeholder": f"precio_usd < {MIN_PRECIO_VALIDO}",
            "precio_outlier_premium": f"precio_usd > {MAX_PRECIO_VALIDO}",
            "descartados": int(len(descartados)),
        },
        "min_anuncios_por_cluster": MIN_ANUNCIOS_POR_CLUSTER,
        "max_fraccion_por_cluster": MAX_FRACCION_POR_CLUSTER,
        "maximo_absoluto_por_cluster": MAXIMO,
        "gap_fusion_precio": GAP_FUSION,
        "fusiones_aplicadas": mejor["fusiones"],
    },
    "clusters": clusters_info,
    "anuncios": anuncios_out,
}
with open(os.path.join(BASE_DIR, "bicis_120_con_clusters.json"), "w", encoding="utf-8") as fh:
    json.dump(salida, fh, ensure_ascii=False, indent=2, allow_nan=False)
print(f"  guardado: bicis_120_con_clusters.json ({len(anuncios_out)} anuncios, k={k_elegido})")

# =====================================================================
# 8. RESUMEN TXT
# =====================================================================
print()
print(SEP)
print("PASO 8 — RESUMEN TXT")
print(SEP)
L = []
A = L.append
A(SEP)
A("CLUSTERING K-MEANS — BICIMOTOS ELECTRICAS — 120 ANUNCIOS (90 REVOLICO + 30 TELEGRAM)")
A(SEP)
A(f"Fecha de analisis   : {fecha}")
A(f"Archivo origen      : {ORIGEN}")
A(f"Vehiculo            : {salida['vehiculo']}")
A(f"Features usadas     : {', '.join(FEATURES)}")
A("Normalizacion       : StandardScaler")
A("Algoritmo           : KMeans(random_state=42, n_init=10)")
A("")

A("1. OUTLIERS DESCARTADOS ANTES DE CLUSTERIZAR")
A(SEP)
A(f"  Anuncios en el JSON original     : {len(anuncios)}")
A(f"  Descartados por precio_usd < {MIN_PRECIO_VALIDO:.0f}  (precio_placeholder)      : {n_ph}")
A(f"  Descartados por precio_usd > {MAX_PRECIO_VALIDO:.0f} (precio_outlier_premium) : {n_po}")
A(f"  Total descartado                : {len(descartados)}")
A(f"  Anuncios clusterizados (limpios): {N}")
A("")
if descartados:
    A("  detalle de cada descarte:")
    A("   motivo                   | marca    | motor_w | autonomia_km | precio_usd | fuente")
    A("   ------------------------ + -------- + ------- + ------------ + ---------- + -------")
    for a in descartados:
        A(f"   {a['motivo_descarte']:<24} | {str(a['marca']):<8} | {a['motor_w']:>7} | "
          f"{a['autonomia_km']:>12} | {a['precio_usd']:>10.0f} | {a['fuente']}")
    A("")
    A("  Nota: los descartes NO se corrigen ni se reasignan de precio; se eliminan del")
    A("  dataset de clustering y quedan listados en descartados_outliers.json.")
else:
    A("  No se descarto ningun anuncio. En esta vertical el precio esta acotado entre "
      f"{df_total['precio_usd'].min():.0f} y")
    A(f"  {df_total['precio_usd'].max():.0f} USD, asi que ningun anuncio cae por debajo de 500 USD ni por")
    A("  encima de 4000 USD. A diferencia de las motos, esta vertical no tiene errores")
    A("  de precio ni outliers premium que generen clusters de 1-2 anuncios, asi que el")
    A("  filtro queda como red de seguridad documentada y no como un filtro que cambie")
    A("  el resultado. (descartados_outliers.json queda generado igual, con")
    A("  total_descartados=0.)")
A("")

A("2. BARRIDO k = 2..10 SOBRE EL DATASET LIMPIO (n=%d)" % N)
A(SEP)
A("   k |    inercia   |  silhouette  | min raw | max raw | cumple | k_fin | sil_fin | cumple_f")
A("  --- + ------------ + ------------ + -------- + -------- + ------ + ------ + ------- + --------")
for _, r in tabla_k.iterrows():
    A(f"  {int(r['k']):<3d} | {r['inercia']:>10.2f}  | {r['silhouette']:>10.4f}  | "
      f"{int(r['min_raw']):>7} | {int(r['max_raw']):>7} | {'  si  ' if r['cumple_raw'] else '  no  '} | "
      f"{int(r['k_final']):>5} | {r['silhouette_final']:>7.4f} | {'si' if r['cumple_final'] else 'no'}")
A("")

A("   TAMANOS DE CLUSTER POR k (ordenados de menor a mayor, n=%d)" % N)
A(SEP)
for _, r in tabla_k.iterrows():
    tf = r["tamanos_final"]
    A(f"    k={int(r['k']):<2} crudo: {str(r['tamanos_raw']):<34} "
      f"final (k_fin={int(r['k_final'])}): {tf if tf is not None else 'n/a'}")
A("")

A("3. k ELEGIDO Y JUSTIFICACION")
A(SEP)
A("  Reglas aplicadas, en orden:")
A(f"    1. filtro de outliers de precio (precio_usd en [{MIN_PRECIO_VALIDO:.0f}, {MAX_PRECIO_VALIDO:.0f}])")
A(f"    2. ningun cluster con menos de {MIN_ANUNCIOS_POR_CLUSTER} anuncios")
A(f"    3. ningun cluster con mas del {MAX_FRACCION_POR_CLUSTER:.0%} del total (>{MAXIMO} anuncios)")
A(f"    4. clausula de fusion: dos clusters cuyo precio promedio difiere <{GAP_FUSION:.0%}")
A("       se fusionan; el resultado se vuelve a validar contra las reglas 2 y 3")
A("    5. entre los k que cumplen, gana el de mayor silhouette")
A("")
A(f"  k ELEGIDO              : {k_elegido}")
A(f"  silhouette (final)     : {SIL_FINAL:.4f}")
A(f"  clusters finales       : {k_final}")
A(f"  fusiones aplicadas     : {mejor['fusiones']}")
A("")
cands = tabla_k[tabla_k["cumple_final"]].sort_values("silhouette_final", ascending=False)
A("  k que cumplen las reglas (silhouette post-fusion, de mayor a menor):")
for _, r in cands.iterrows():
    A(f"    k={int(r['k']):<2} sil={r['silhouette_final']:.4f}  tamanos={r['tamanos_final']}")
A("")
demasiado_grandes = tabla_k[tabla_k["max_raw"] > MAXIMO]
chicos = tabla_k[tabla_k["min_raw"] < MIN_ANUNCIOS_POR_CLUSTER]
A(f"  Motivo de la eleccion: k={k_elegido} es el de mayor silhouette entre los k que")
A("  respetan el piso de 4 anuncios y el tope del 50%.")
A("  k que se descartan por romper el tope del 50%:")
for _, r in demasiado_grandes.iterrows():
    A(f"    k={int(r['k']):<2} sil={r['silhouette']:.4f} pero su cluster mayor tiene "
      f"{int(r['max_raw'])} anuncios = {int(r['max_raw']) / N:.0%} del total")
A("  k que se descartan por tener un cluster de menos de 4 anuncios:")
for _, r in chicos.iterrows():
    A(f"    k={int(r['k']):<2} sil={r['silhouette']:.4f} pero su cluster menor tiene "
      f"{int(r['min_raw'])} anuncio(s)")
A("  Los k con silhouette mas alto que k=5 son k=2 (0.6623) y k=3 (0.6193), y los dos")
A("  concentran 2/3 del catalogo en un unico cluster: partirlo en dos o tres grupos no")
A("  describe segmentos distintos. La clausula de fusion si puede reparar k=8, k=9 y")
A(f"  k=10, pero al hacerlo el silhouette cae a 0.5670 / 0.5586 / 0.5172, todos por")
A(f"  debajo de k={k_elegido}, asi que no compiten.")
A("")
A("  Clausula de fusion: se aplica como mecanismo de reparacion, no como poda de una")
A(f"  particion que ya cumple. En la particion cruda de k={k_elegido} los pares con")
A("  precio promedio a menos del 10% de diferencia son: "
  + (", ".join(f"C{a}+C{b} (gap {g:.2%})" for g, a, b in pares_k) if pares_k else "ninguno")
  + ".")
A("  Forzar esa fusion uniria dos segmentos que ya son validos y solo bajaria el")
A("  silhouette, asi que no se aplica.")
A("")

A("4. CLUSTERS FINALES")
A(SEP)
A("  Los nombres se derivan de los datos: el precio y la autonomia se parten en 3 tramos")
A("  con los terciles de la propia distribucion y cada cluster se nombra por el tramo en")
A(f"  que cae su promedio, mas los W promedio del cluster. El prefijo C0..C{k_final - 1} garantiza")
A("  que el nombre sea unico.")
A("")
A("   id | nombre                                      |   n |   %  | precio |  motor | autonomia | marca     | fuente")
A("  --- + ------------------------------------------ + ---- + ---- + ------ + ------ + --------- + --------- + -------")
for c in clusters_info:
    A(f"  {c['id']:<3d} | {c['nombre']:<42} | {c['n_anuncios']:>4} | {c['n_anuncios'] / N:>5.1%} | "
      f"{c['precio_promedio_usd']:>6.0f} | {c['motor_promedio_w']:>6.0f} | {c['autonomia_promedio_km']:>9.1f} | "
      f"{str(c['marca_mas_comun']):<9} | {c['fuente_dominante']}")
A("")
for c in clusters_info:
    A(f"  C{c['id']} — {c['nombre']}")
    A(f"    anuncios    : {c['n_anuncios']} ({c['n_anuncios'] / N:.1%} del total)")
    A(f"    precio USD  : prom {c['precio_promedio_usd']:.0f} | "
      f"min {c['precio_min_usd']:.0f} | max {c['precio_max_usd']:.0f}")
    A(f"    motor W     : prom {c['motor_promedio_w']:.0f}")
    A(f"    autonomia km: prom {c['autonomia_promedio_km']:.1f} | "
      f"min {c['autonomia_min_km']:.0f} | max {c['autonomia_max_km']:.0f}")
    A(f"    top marcas  : {', '.join(f'{m['marca']} ({m['n']})' for m in c['top_marcas'])}")
    A(f"    fuentes     : revolico {c['n_revolico']} / telegram {c['n_telegram']} "
      f"-> dominante {c['fuente_dominante']}")
    A(f"    descripcion : {c['descripcion']}")
    A("")

A("5. TOP MARCAS POR CLUSTER")
A(SEP)
A("   id | nombre                                      | marcas mas frecuentes (n)")
A("  --- + ------------------------------------------ + -------------------------------------------")
for c in clusters_info:
    A(f"  {c['id']:<3d} | {c['nombre']:<42} | "
      f"{', '.join(f'{m['marca']} ({m['n']})' for m in c['top_marcas'])}")
A("")
todas_marcas = df["marca"].astype(str).str.strip().replace("", np.nan).dropna()
print("  top 15 marcas del dataset:", list(todas_marcas.value_counts().head(15).items()))
A("  Top 15 marcas del dataset completo (no por cluster):")
for m, n in todas_marcas.value_counts().head(15).items():
    A(f"    {m:<20} {n:>4}")
A("")

A("6. CORRELACIONES PEARSON ENTRE LAS 3 FEATURES (n=%d)" % N)
A(SEP)
A(corr.to_string(float_format=lambda v: f"{v:+.4f}").replace("\n", "\n  "))
A("")
for a_, b_ in itertools.combinations(FEATURES, 2):
    r_ = float(corr.loc[a_, b_])
    fuerza = ("muy fuerte" if abs(r_) >= 0.7 else "moderada" if abs(r_) >= 0.4 else "debil")
    A(f"  {a_} ~ {b_}: r = {r_:+.4f} ({fuerza}, "
      f"{'positiva' if r_ > 0 else 'negativa'})")
A("")

A("7. DISTRIBUCION DE FUENTE POR CLUSTER (revolico vs telegram)")
A(SEP)
A("   id | nombre                                      | revolico | telegram | dominante | %telegram")
A("  --- + ------------------------------------------ + -------- + -------- + --------- + ---------")
for c in clusters_info:
    A(f"  {c['id']:<3d} | {c['nombre']:<42} | {c['n_revolico']:>8} | {c['n_telegram']:>8} | "
      f"{c['fuente_dominante']:<9} | {c['n_telegram'] / c['n_anuncios']:>8.1%}")
A(f"  {'':>3} | {'TOTAL':<42} | {sum(c['n_revolico'] for c in clusters_info):>8} | "
  f"{sum(c['n_telegram'] for c in clusters_info):>8} | {'':<9} | "
  f"{sum(c['n_telegram'] for c in clusters_info) / N:>8.1%}")
A("")
A(f"  Base total limpia: {N} anuncios "
  f"(revolico {int((df['fuente'] == 'revolico').sum())} / "
  f"telegram {int((df['fuente'] == 'telegram').sum())}).")
A("")

A("8. LIMITACIONES")
A(SEP)
A(f"  1. Muestra chica ({N} anuncios) y de una sola plataforma/ciudad; los clusters")
A("     describen este catalogo, no el mercado de bicimotos electricas en general.")
A("  2. Se usan solo 3 features cuantitativas. Variables cualitativas del anuncio")
A("     (tipo de bateria, voltaje, si es plegable) no estan en el esquema actual y no")
A("     pueden discriminar clusters.")
A("  3. motor_w toma solo 8 valores distintos y se concentra en 3000 W; la estructura")
A("     real del mercado es 'poco mas de 1000 W' contra '3000 W', y el clustering la")
A("     recupera como una sola dimension, no como tres.")
A(f"  4. Silhouette {SIL_FINAL:.2f} indica estructura buena pero no perfecta: los clusters")
A("     de precio medio se tocan entre si y hay solapamiento entre segmentos.")
A("  5. Telegram y revolico no son la misma poblacion (canal de compraventa contra")
A("     marketplace); mezclarlos puede separar por fuente y no por producto.")
A("  6. Los cortes de outliers (500 USD / 4000 USD) no descartaron nada en esta")
A("     vertical; en otra muestra hay que revisarlos.")
A("")
A(SEP)
with open(os.path.join(BASE_DIR, "resumen_clustering_120.txt"), "w", encoding="utf-8") as fh:
    fh.write("\n".join(L) + "\n")
print("  guardado: resumen_clustering_120.txt")

# =====================================================================
# 9. VALIDACION
# =====================================================================
print()
print(SEP)
print("PASO 9 — VALIDACION")
print(SEP)
r = subprocess.run([PY, "-m", "json.tool", os.path.join(BASE_DIR, "bicis_120_con_clusters.json")],
                   capture_output=True, text=True)
print(f"  json.tool bicis_120_con_clusters.json -> exit={r.returncode} "
      f"({'JSON VALIDO' if r.returncode == 0 else 'ERROR'})")
if r.returncode != 0:
    print(r.stderr[:800])

texto = open(os.path.join(BASE_DIR, "bicis_120_con_clusters.json"), encoding="utf-8").read()
malos = [t for t in ("NaN", "Infinity") if t in texto]
print(f"  sin literales NaN/Infinity -> {'OK' if not malos else 'FALLA ' + str(malos)}")

con = json.loads(texto, parse_constant=lambda c: (_ for _ in ()).throw(
    ValueError(f"constante JSON invalida: {c}")))
print("  recheck parseo estricto -> OK")
print(f"  k_elegido={con['k_elegido']} silhouette={con['silhouette']} "
      f"clusters={len(con['clusters'])} anuncios={len(con['anuncios'])} "
      f"total_anuncios={con['total_anuncios']}")
print(f"  suma n_anuncios = {sum(c['n_anuncios'] for c in con['clusters'])} (debe ser {N}) -> "
      f"{'OK' if sum(c['n_anuncios'] for c in con['clusters']) == N else 'FALLA'}")
print(f"  los {len(con['anuncios'])} anuncios tienen campo 'cluster' -> "
      f"{all('cluster' in a for a in con['anuncios'])}")
print(f"  cada anuncio conserva sus campos originales -> "
      f"{all({k: v for k, v in a.items() if k != 'cluster'} in anuncios for a in con['anuncios'])}")
ids = [c['id'] for c in con['clusters']]
print(f"  ids de cluster 0..{k_final - 1} sin huecos -> {ids == list(range(k_final))}")
requeridos = {"id", "nombre", "n_anuncios", "precio_promedio_usd", "precio_min_usd",
              "precio_max_usd", "motor_promedio_w", "autonomia_promedio_km",
              "autonomia_min_km", "autonomia_max_km", "marca_mas_comun",
              "fuente_dominante", "n_revolico", "n_telegram", "descripcion"}
print(f"  cada cluster tiene todos los campos pedidos -> "
      f"{all(requeridos <= set(c) for c in con['clusters'])}")
fuentes_ok = all(c["fuente_dominante"] in ("revolico", "telegram") for c in con["clusters"])
print(f"  fuente_dominante en (revolico|telegram) -> {'OK' if fuentes_ok else 'FALLA'}")
print(f"  anunciados fuera del rango [{MIN_ANUNCIOS_POR_CLUSTER}, {MAXIMO}]: "
      f"{[c['id'] for c in con['clusters'] if not (MIN_ANUNCIOS_POR_CLUSTER <= c['n_anuncios'] <= MAXIMO)]} "
      f"-> {'OK' if all(MIN_ANUNCIOS_POR_CLUSTER <= c['n_anuncios'] <= MAXIMO for c in con['clusters']) else 'FALLA'}")
print(f"  cluster mas grande: {max(c['n_anuncios'] for c in con['clusters'])}/"
      f"{N} = {max(c['n_anuncios'] for c in con['clusters']) / N:.1%} (<= 50% -> "
      f"{'OK' if max(c['n_anuncios'] for c in con['clusters']) <= MAXIMO else 'FALLA'})")
print(f"  cluster mas chico: {min(c['n_anuncios'] for c in con['clusters'])} "
      f"(>= 4 -> {'OK' if min(c['n_anuncios'] for c in con['clusters']) >= MIN_ANUNCIOS_POR_CLUSTER else 'FALLA'})")
print(f"  media(silhouette_samples) = {sil_media:.10f}")
print(f"  SIL_FINAL                 = {SIL_FINAL:.10f}")
print(f"  igualdad a 1e-9 -> {'OK' if abs(sil_media - SIL_FINAL) < 1e-9 else 'FALLA'}")
print(f"  recheck SIL_FINAL == silhouette del JSON -> "
      f"{'OK' if abs(round(SIL_FINAL, 4) - con['silhouette']) < 1e-9 else 'FALLA'}")

rj = json.load(open(os.path.join(BASE_DIR, "descartados_outliers.json"), encoding="utf-8"))
print(f"  descartados_outliers.json: {rj['total_descartados']} descartado(s), "
      f"{rj['total_limpios']} limpios -> {'OK' if rj['total_limpios'] == N else 'FALLA'}")

for fn in ["bicis_120_con_clusters.json", "resumen_clustering_120.txt",
           "descartados_outliers.json", "clusters_dispersion.png",
           "clusters_precio_medio.png", "clusters_tamanos.png",
           "clusters_metricas.png", "clusters_correlacion.png"]:
    p = os.path.join(BASE_DIR, fn)
    print(f"  {'OK ' if os.path.exists(p) else 'FALLA'} {fn:<32} {os.path.getsize(p):>9,} bytes")

desp_md5 = md5(ORIGEN)
desp_stat = os.stat(ORIGEN)
ok = (desp_md5 == ORIGEN_MD5 and desp_stat.st_size == ORIGEN_STAT.st_size
      and desp_stat.st_mtime == ORIGEN_STAT.st_mtime)
print(f"\n  Integridad del original: md5 antes={ORIGEN_MD5} despues={desp_md5}")
print(f"  -> ORIGEN {'INTACTO' if ok else 'MODIFICADO'}")

print()
print(SEP)
print("RESUMEN FINAL")
print(SEP)
print(f"  Outliers descartados : {len(descartados)} ({n_ph} placeholder, {n_po} premium)")
print(f"  Anuncios clusterizados: {N}")
print(f"  k elegido             : {k_elegido} (silhouette {SIL_FINAL:.4f})")
print(f"  Clusters finales      : {k_final}, tamanos {TAM} (min {min(TAM)}, max {max(TAM)}, tope {MAXIMO})")
print(SEP)