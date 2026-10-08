"""Clustering K-Means de motos de combustion sobre 90 anuncios (La Habana, 2026).

Pipeline:
  1. carga del JSON original (solo lectura, se verifica integridad por md5)
  2. filtro de outliers de precio ANTES de clusterizar
  3. normalizacion StandardScaler sobre 2 features (precio_usd, cilindrada_cc)
  4. barrido k = 2..10 + reglas de tamaño + clausula de fusion
  5. etiquetado final, metricas por cluster
  6. 4 PNG (dpi=150), JSON con clusters, descartados y resumen TXT

Nota de esquema: el dataset no tiene motor_w, autonomia_km util, tipo_bateria ni
capacidad_tanque_l. La unica variable tecnica es cilindrada_cc.
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
ORIGEN = os.path.normpath(os.path.join(BASE_DIR, "..", "..", "scraping", "motos_combustion_90.json"))
PY = __import__("sys").executable

VEHICULO = "moto_combustion"
FEATURES = ["precio_usd", "cilindrada_cc"]
DESCRIPTIVOS = ["marca", "autonomia_km", "municipio"]
PALETA = ["#2e8b57", "#e07b00", "#7b2d8b", "#1f6feb", "#c1121f", "#008b8b",
          "#6b7280", "#b8860b"]

# ---------- parametros del filtro de outliers ----------
MIN_PRECIO_VALIDO = 500.0    # por debajo: precio placeholder / error de tipeo
MAX_PRECIO_VALIDO = 5000.0   # por encima: outlier premium aislado

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
print(f"  anuncios declarados   : {data.get('total_anuncios_validos')}")
print(f"  anuncios en la lista  : {len(anuncios)}")
print(f"  campos declarados     : {data.get('campos')}")
print(f"  nulos en las features : "
      f"{ {c: int(df_total[c].isna().sum()) for c in FEATURES} }")

# ---- STEP 0: verificacion previa del campo capacidad_tanque_l ----
print()
print(SEP)
print("PASO 0 — VERIFICACION PREVIA DE capacidad_tanque_l")
print(SEP)
con_campo = int(df_total["capacidad_tanque_l"].notna().sum()) if "capacidad_tanque_l" in df_total.columns else 0
sin_campo = int(len(df_total) - con_campo)
print(f"  anuncios totales                                  : {len(df_total)}")
print(f"  anuncios que TIENEN la clave capacidad_tanque_l    : {con_campo}")
print(f"  anuncios SIN la clave capacidad_tanque_l          : {sin_campo}")
if "capacidad_tanque_l" in df_total.columns:
    print(f"  anuncios con valor NO null en capacidad_tanque_l  : "
          f"{int(df_total['capacidad_tanque_l'].notna().sum())}")
else:
    print("  valores, rango y distribucion                     : N/A (la clave no existe)")
print("  Conclusion: capacidad_tanque_l NO existe en este dataset. No se usa como")
print("  feature ni como descriptivo, y NO se agrega como clave null a los anuncios:")
print("  el esquema de 9 campos del scraper no la define y anadirla seria inventar")
print("  un campo que el origen no tiene. Queda documentada aca y en el resumen.")
for campo in DESCRIPTIVOS:
    if campo in df_total.columns:
        print(f"  (descriptivo '{campo}': {int(df_total[campo].notna().sum())}/{len(df_total)} con valor)")

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
    print(f"    - {a['motivo_descarte']:<24} {a['marca']:<10} {a['cilindrada_cc']:>5} cc  "
          f"{a['precio_usd']:>8.0f} USD")
    print(f"      titulo: {str(a['titulo'])[:100]}")
if not descartados:
    print("  (el filtro no descarto nada: en esta vertical ningun anuncio cae fuera de los")
    print("   cortes, el precio esta acotado entre "
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
print("\n  valores distintos de cilindrada_cc: "
      f"{sorted(df['cilindrada_cc'].astype(int).unique().tolist())}")
print("  distribucion de cilindrada_cc:")
print(df["cilindrada_cc"].astype(int).value_counts().sort_index().to_string())
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
    print(f"  k={k:>2}  inercia={km.inertia_:>7.2f}  sil={sil:.4f}  "
          f"raw{'':>1}{str(filas[-1]['tamanos_raw']):<26} {'OK ' if ok else 'NO '}  "
          f"fusiones={pasos_txt:<28} -> k_f={k_final} sil_f={sil_final:.4f} "
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
print(f"  clusters finales tras la clausula de fusion: {k_final}")
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
SUBTITULO = (f"k={k_elegido} ({k_final} clusters tras fusion)" if k_final != k_elegido
             else f"k={k_final}")
print(f"  silhouette final recalculado: {SIL_FINAL:.4f}")

# =====================================================================
# 5. NOMBRES Y DESCRIPCION DE CADA CLUSTER
# =====================================================================
print()
print(SEP)
print("PASO 5 — PERFIL Y NOMBRE DE CADA CLUSTER")
print(SEP)

# el precio se parte en 3 tramos con los terciles; la cilindrada entra como valor
Q1 = float(X["precio_usd"].quantile(1 / 3))
Q3 = float(X["precio_usd"].quantile(2 / 3))


def tier_precio(v):
    if v < Q1:
        return "precio bajo"
    if v < Q3:
        return "precio medio"
    return "precio alto"


print(f"  cortes (terciles) precio: {Q1:.0f} / {Q3:.0f} USD")

clusters_info = []
for i in range(k_final):
    sub = df[df["cluster"] == i]
    pm_ = float(sub["precio_usd"].mean())
    cc_ = float(sub["cilindrada_cc"].mean())
    ccs = sorted(sub["cilindrada_cc"].astype(int).unique().tolist())
    cc_txt = f"{cc_:.0f}cc" if len(ccs) == 1 else f"{cc_:.0f}cc mixto"
    base = f"C{i} - {tier_precio(pm_)} {cc_txt}"
    marcas = sub["marca"].astype(str).str.strip().replace("", np.nan).dropna()
    top_marcas = marcas.value_counts()
    pos = ("el segmento mas economico" if i == 0 else
           "el segmento mas caro" if i == k_final - 1 else "un segmento intermedio")
    rango_precio = ("el rango mas bajo de los tres" if pm_ < Q1 else
                    "el rango mas alto de los tres" if pm_ >= Q3 else "el rango intermedio")
    desc = (f"{len(sub)} anuncios en {sub['precio_usd'].min():.0f}-{sub['precio_usd'].max():.0f} USD "
            f"(prom {pm_:.0f}, {rango_precio}); cilindrada {cc_:.0f} cc promedio, con cc "
            f"{', '.join(str(c) for c in ccs)} presentes. Es {pos} por precio. "
            f"Marca dominante: {top_marcas.index[0] if len(top_marcas) else 'n/d'} "
            f"({top_marcas.iloc[0] if len(top_marcas) else 0}/{len(sub)}).")
    clusters_info.append({
        "id": i,
        "nombre": base,
        "n_anuncios": int(len(sub)),
        "precio_promedio_usd": round(pm_, 2),
        "precio_min_usd": float(sub["precio_usd"].min()),
        "precio_max_usd": float(sub["precio_usd"].max()),
        "cilindrada_promedio_cc": round(cc_, 1),
        "cilindrada_min_cc": int(sub["cilindrada_cc"].min()),
        "cilindrada_max_cc": int(sub["cilindrada_cc"].max()),
        "marcas_mas_comunes": [{"marca": str(m), "n": int(n)} for m, n in top_marcas.head(3).items()],
        "marca_mas_comun": str(top_marcas.index[0]) if len(top_marcas) else None,
        "descripcion": desc,
    })
    print(f"  C{i}: {base:<36} n={len(sub):>3}  {pm_:>7.0f} USD  {cc_:>6.1f} cc  "
          f"[{', '.join(f'{m}:{n}' for m, n in top_marcas.head(3).items())}]")

corr_pc = float(np.corrcoef(X["precio_usd"], X["cilindrada_cc"])[0, 1])
print(f"\n  correlacion Pearson precio_usd ~ cilindrada_cc: {corr_pc:+.4f}")

# =====================================================================
# 6. VISUALIZACIONES
# =====================================================================
print()
print(SEP)
print("PASO 6 — VISUALIZACIONES (dpi=150)")
print(SEP)
NOMBRES = [c["nombre"] for c in clusters_info]
TAM = [c["n_anuncios"] for c in clusters_info]

# --- 6.1 dispersion precio vs cilindrada ---
fig, ax = plt.subplots(figsize=(12, 8))
pts = ax.scatter(Xo[:, 0], Xo[:, 1], c=etiquetas, cmap="viridis", s=110,
                 alpha=0.8, edgecolors="black", linewidths=0.5)
ax.scatter(CENTROS[:, 0], CENTROS[:, 1], c="red", marker="X", s=200,
           edgecolors="black", linewidths=1.5, zorder=5, label="Centroides")
for i in range(k_final):
    ax.annotate(f"C{i}", (CENTROS[i, 0], CENTROS[i, 1]), textcoords="offset points",
                xytext=(12, 8), fontweight="bold", fontsize=11)
ax.set_xlabel("Precio (USD)", fontsize=12)
ax.set_ylabel("Cilindrada (cc)", fontsize=12)
ax.set_title(f"Dispersión por cluster ({SUBTITULO}): precio vs cilindrada\n"
             "X roja = centroide del cluster", fontsize=13, fontweight="bold")
ax.xaxis.set_major_formatter(matplotlib.ticker.StrMethodFormatter("${x:,.0f}"))
ax.yaxis.set_major_formatter(matplotlib.ticker.StrMethodFormatter("{x:,.0f}"))
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
ax.set_title(f"Precio promedio por cluster ({SUBTITULO})", fontsize=13, fontweight="bold")
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
ax.set_title(f"Tamaño de cada cluster ({SUBTITULO}, n={N})", fontsize=13, fontweight="bold")
ax.set_ylim(0, max(TAM) * 1.26)
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
ax.set_title(f"Silhouette por cluster ({SUBTITULO}) — sobre X_norm\n"
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
    "k_clusters_finales": int(k_final),
    "silhouette": round(SIL_FINAL, 4),
    "fecha_analisis": fecha,
    "features_usadas": FEATURES,
    "campos_solo_descriptivos": DESCRIPTIVOS,
    "campos_no_existentes": ["capacidad_tanque_l"],
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
with open(os.path.join(BASE_DIR, "motos_combustion_90_con_clusters.json"), "w", encoding="utf-8") as fh:
    json.dump(salida, fh, ensure_ascii=False, indent=2, allow_nan=False)
print(f"  guardado: motos_combustion_90_con_clusters.json "
      f"({len(anuncios_out)} anuncios, k={k_elegido} -> {k_final} clusters)")

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
A("CLUSTERING K-MEANS — MOTOS DE COMBUSTION — 90 ANUNCIOS (LA HABANA, 2026)")
A(SEP)
A(f"Fecha de analisis   : {fecha}")
A(f"Archivo origen      : {ORIGEN}")
A(f"Vehiculo            : {salida['vehiculo']}")
A(f"Features usadas     : {', '.join(FEATURES)}")
A(f"Solo descriptivos   : {', '.join(DESCRIPTIVOS)}")
A("Normalizacion       : StandardScaler")
A("Algoritmo           : KMeans(random_state=42, n_init=10)")
A(f"k elegido           : {k_elegido}  ->  {k_final} clusters tras la clausula de fusion")
A(f"Silhouette final    : {SIL_FINAL:.4f}")
A("")

A("0. VERIFICACION PREVIA DEL CAMPO capacidad_tanque_l")
A(SEP)
A(f"  anuncios totales                                 : {len(df_total)}")
A(f"  anuncios que tienen la clave capacidad_tanque_l  : {con_campo}")
A(f"  anuncios sin la clave capacidad_tanque_l         : {sin_campo}")
A(f"  anuncios con valor NO null                       : {con_campo}")
A(f"  rango / distribucion de valores                  : N/A (la clave no existe)")
A("")
A("  Conclusion: el campo capacidad_tanque_l NO existe en este dataset. El esquema del")
A("  scraper tiene 9 campos (titulo, marca, cilindrada_cc, autonomia_km, precio_usd,")
A("  ubicacion, url, fecha_publicacion, municipio) y ninguno es la capacidad del tanque.")
A("  Decisiones que se tomaron por eso:")
A("    - no se usa como feature ni como campo descriptivo de los clusters;")
A("    - NO se agrega como clave null a los anuncios del JSON de salida, porque el")
A("      origen no la tiene y anadirla seria inventar un campo del esquema. Los")
A("      anuncios de salida conservan exactamente las 9 claves originales + 'cluster';")
A("    - queda registrada en campos_no_existentes del JSON de salida.")
A("")
A("  Estado de los otros campos (con valor sobre %d anuncios):" % len(df_total))
for campo in DESCRIPTIVOS:
    if campo in df_total.columns:
        A(f"    {campo:<16}: {int(df_total[campo].notna().sum()):>3}/{len(df_total)}")
A("    autonomia_km tiene dato en solo 7 de 90 anuncios, asi que no sirve para")
A("    segmentar: por eso queda como campo descriptivo y no como feature.")
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
    A("   motivo                   | marca    | cc | precio_usd")
    A("   ------------------------ + -------- + --- + ----------")
    for a in descartados:
        A(f"   {a['motivo_descarte']:<24} | {str(a['marca']):<8} | {a['cilindrada_cc']:>3} | "
          f"{a['precio_usd']:>10.0f}")
    A("")
    A("  Nota: los descartes NO se corrigen ni se reasignan de precio; se eliminan del")
    A("  dataset de clustering y quedan listados en descartados_outliers.json.")
else:
    A("  No se descarto ningun anuncio. El precio esta acotado entre "
      f"{df_total['precio_usd'].min():.0f} y")
    A(f"  {df_total['precio_usd'].max():.0f} USD, muy por dentro de los cortes de 500 y 5000 USD.")
    A("  Este dataset ya venia limpio: el merge previo excluyo los 3 anuncios dudosos")
    A("  (el DKW de 700 USD marcado como 'precio_outlier_sospechoso', el lote de dos motos")
    A("  y las dos motos usadas), asi que el filtro queda como red de seguridad")
    A("  documentada. (descartados_outliers.json queda generado con total_descartados=0.)")
A("")

A("2. BARRIDO k = 2..10 SOBRE EL DATASET LIMPIO (n=%d)" % N)
A(SEP)
A("   k |   inercia  |  silhouette  | min raw | max raw | cumple | k_fin | sil_fin | cumple_f")
A("  --- + ---------- + ------------ + -------- + -------- + ------ + ------ + ------- + --------")
for _, r in tabla_k.iterrows():
    A(f"  {int(r['k']):<3d} | {r['inercia']:>8.2f}  | {r['silhouette']:>10.4f}  | "
      f"{int(r['min_raw']):>7} | {int(r['max_raw']):>7} | {'  si  ' if r['cumple_raw'] else '  no  '} | "
      f"{int(r['k_final']):>5} | {r['silhouette_final']:>7.4f} | {'si' if r['cumple_final'] else 'no'}")
A("")

A("   TAMANOS DE CLUSTER POR k (ordenados de menor a mayor, n=%d)" % N)
A(SEP)
for _, r in tabla_k.iterrows():
    tf = r["tamanos_final"]
    A(f"    k={int(r['k']):<2} crudo: {str(r['tamanos_raw']):<24} "
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
A(f"  k ELEGIDO (barrido)      : {k_elegido}")
_n_fus = k_elegido - k_final
_txt_fus = "sin fusion" if _n_fus == 0 else f"tras {_n_fus} fusion" + ("" if _n_fus == 1 else "es")
A(f"  clusters finales         : {k_final}  ({_txt_fus})")
A(f"  silhouette (final)       : {SIL_FINAL:.4f}")
A(f"  fusiones aplicadas       : {mejor['fusiones']}")
A("")
cands = tabla_k[tabla_k["cumple_final"]].sort_values("silhouette_final", ascending=False)
A("  k que cumplen las reglas (silhouette post-fusion, de mayor a menor):")
for _, r in cands.iterrows():
    A(f"    k={int(r['k']):<2} sil={r['silhouette_final']:.4f}  tamanos={r['tamanos_final']}")
A("")
grandes = tabla_k[tabla_k["max_raw"] > MAXIMO]
chicos = tabla_k[tabla_k["min_raw"] < MIN_ANUNCIOS_POR_CLUSTER]
A(f"  Motivo de la eleccion: k={k_elegido} es el de mayor silhouette entre los k que")
A("  respetan el piso de 4 anuncios y el tope del 50%.")
A(f"  k descartados por romper el tope del 50% (cluster mayor > {MAXIMO} anuncios):")
for _, r in grandes.iterrows():
    A(f"    k={int(r['k']):<2} sil={r['silhouette']:.4f} pero su cluster mayor tiene "
      f"{int(r['max_raw'])} anuncios = {int(r['max_raw']) / N:.0%} del total")
A("  k descartados por tener un cluster de menos de 4 anuncios:")
for _, r in chicos.iterrows():
    A(f"    k={int(r['k']):<2} sil={r['silhouette']:.4f} pero su cluster menor tiene "
      f"{int(r['min_raw'])} anuncio(s)")
A("")
A("  Clausula de fusion: con solo 2 features (una de ellas con 4 valores discretos),")
A(f"  casi todos los k dejan un cluster de 2 o 3 anuncios. La fusion por precio (gap")
A(f"  <{GAP_FUSION:.0%}) solo alcanza para reparar k=8 (-> sil "
  f"{float(tabla_k.loc[tabla_k['k'] == 8, 'silhouette_final'].iloc[0]):.4f}, "
  f"{int(tabla_k.loc[tabla_k['k'] == 8, 'k_final'].iloc[0])} clusters) y k=9 (-> sil "
  f"{float(tabla_k.loc[tabla_k['k'] == 9, 'silhouette_final'].iloc[0]):.4f}, "
  f"{int(tabla_k.loc[tabla_k['k'] == 9, 'k_final'].iloc[0])} clusters).")
A("  k=2 a k=7 y k=10 no tienen ningun par con precios promedio a menos del 10% que")
A("  permita arreglar el cluster chico sin romper el tope del 50%, asi que quedan")
A("  fuera. Gana k=9 -> 8 clusters con silhouette "
  f"{SIL_FINAL:.4f}.")
A("")
A(f"  Fusion aplicada: {mejor['fusiones']}.")
A("")
A(f"  En la particion cruda de k={k_elegido} habia {len(pares_k)} pares con diferencia de precio")
A(f"  promedio < {GAP_FUSION:.0%}, pero la mayoria no toca los dos clusters de 3 anuncios, asi que")
A("  su merge deja todavia un cluster chico y la fusion se descarta en la post-validacion:")
for g, a_, b_ in pares_k:
    usado = f"C{a_}+C{b_}" in str(mejor["fusiones"])
    etiqueta = "-> FUSIONADO" if usado else "   descartado (deja un cluster < 4)"
    A(f"    C{a_}+C{b_}  gap {g:>6.2%}  {etiqueta}")
A("")
A(f"  Tras la fusion los {k_final} clusters quedan entre {min(TAM)} y {max(TAM)} anuncios, todos dentro del")
A("  piso de 4 y del tope de 45.")
A("")

A("4. CLUSTERS FINALES")
A(SEP)
A("  Los nombres se derivan de los datos: el precio se parte en 3 tramos con los")
A("  terciles de la propia distribucion y cada cluster se nombra por el tramo en que")
A("  cae su precio promedio, mas la cilindrada promedio. El prefijo C0..C{n} garantiza")
A("  que el nombre sea unico.")
A("")
A("   id | nombre                            |   n |   %  | precio |  cc | cc rango | marca    | cc presentes")
A("  --- + --------------------------------- + ---- + ---- + ------ + --- + -------- + -------- + ------------")
for c in clusters_info:
    A(f"  {c['id']:<3d} | {c['nombre']:<33} | {c['n_anuncios']:>4} | {c['n_anuncios'] / N:>5.1%} | "
      f"{c['precio_promedio_usd']:>6.0f} | {c['cilindrada_promedio_cc']:>3.0f} | "
      f"{c['cilindrada_min_cc']:>3}-{c['cilindrada_max_cc']:<4} | {str(c['marca_mas_comun']):<8} | "
      f"{sorted(df.loc[df['cluster'] == c['id'], 'cilindrada_cc'].astype(int).unique().tolist())}")
A("")
for c in clusters_info:
    A(f"  {c['nombre']}")
    A(f"    anuncios     : {c['n_anuncios']} ({c['n_anuncios'] / N:.1%} del total)")
    A(f"    precio USD   : prom {c['precio_promedio_usd']:.0f} | "
      f"min {c['precio_min_usd']:.0f} | max {c['precio_max_usd']:.0f}")
    A(f"    cilindrada cc: prom {c['cilindrada_promedio_cc']:.0f} | "
      f"min {c['cilindrada_min_cc']} | max {c['cilindrada_max_cc']}")
    A(f"    top marcas   : "
      f"{', '.join(f'{m['marca']} ({m['n']})' for m in c['marcas_mas_comunes'])}")
    A(f"    descripcion  : {c['descripcion']}")
    A("")

A("5. CORRELACION PRECIO-CILINDRADA")
A(SEP)
A(f"  Pearson precio_usd ~ cilindrada_cc (n={N}) : r = {corr_pc:+.4f}")
fuerza = ("fuerte" if abs(corr_pc) >= 0.7 else "moderada" if abs(corr_pc) >= 0.4 else "debil")
A(f"  correlacion {fuerza}, {'positiva' if corr_pc > 0 else 'negativa'}: a mayor cilindrada,")
A("  mayor precio, pero no de forma tight. Ojo con el rango: cilindrada_cc solo toma")
A("  4 valores (125, 150, 200 y 250 cc) y el 68% de los anuncios se concentra en 150 y")
A("  200 cc, asi que la correlacion de Pearson entre un continuo y una variable casi")
A("  categorica es fragil y no debe leerse como una relacion de oferta y demanda.")
A("  Distribucion cruzada cc x cluster (de la particion final):")
ccs = sorted(df["cilindrada_cc"].astype(int).unique().tolist())
A("      cluster |" + "".join(f" {c:>4} cc |" for c in ccs) + "  total")
for c in clusters_info:
    fila = df[df["cluster"] == c["id"]]["cilindrada_cc"].astype(int)
    A(f"      C{c['id']:<6} |" + "".join(f" {int((fila == cc).sum()):>7} |" for cc in ccs)
      + f" {len(fila):>6}")
A("")

A("6. TOP MARCAS POR CLUSTER")
A(SEP)
A("   id | nombre                            | marcas mas frecuentes (n)")
A("  --- + --------------------------------- + ------------------------------------------")
for c in clusters_info:
    A(f"  {c['id']:<3d} | {c['nombre']:<33} | "
      f"{', '.join(f'{m['marca']} ({m['n']})' for m in c['marcas_mas_comunes'])}")
A("")
todas_marcas = df["marca"].astype(str).str.strip().replace("", np.nan).dropna()
print("  top 15 marcas del dataset:", list(todas_marcas.value_counts().head(15).items()))
A(f"  Top 15 marcas del dataset completo ({len(todas_marcas)} anuncios con marca, "
  f"{todas_marcas.nunique()} marcas distintas):")
for m, n in todas_marcas.value_counts().head(15).items():
    A(f"    {m:<20} {n:>4}")
A("")

A("7. LIMITACIONES")
A(SEP)
A(f"  1. Solo 2 features y una sola variable tecnica (cilindrada_cc). En un mercado")
A("     donde el precio lo define tambien el modelo, el ano, el estado y el vendedor,")
A("     el clustering con precio + cc no puede separar segmentos reales: separa por")
A("     donde cae cada combinacion precio/cc, no por producto.")
A("  2. cilindrada_cc toma solo 4 valores (125, 150, 200, 250 cc) y el precio es")
A("     continuo, asi que los clusters tienen forma de franjas de precio dentro de una")
A("     cilindrada. El silhouette alto no significa que los segmentos de mercado esten")
A("     bien definidos: en parte premia que los datos sean casi categoricos.")
A(f"  3. Con n={N} anuncios y {k_final} clusters, el promedio es de "
  f"{N / k_final:.1f} anuncios por cluster.")
A("     Los tres clusters mas chicos (6 anuncios) son demasiado chicos para decidir")
A("     precio o compra; sirven como senal, no como segmento.")
A("  4. Muestra de una sola ciudad (La Habana) y de un solo canal (revolico): no hay")
A("     variacion geografica ni de fuente, asi que no se puede medir su efecto.")
A("  5. capacidad_tanque_l no existe en el dataset y autonomia_km tiene 7 datos de 90,")
A("     asi que las variables que suelen explicar el precio de una moto (capacidad,")
A("     autonomia real, tipo de motor) no estan disponibles. Recomendacion: agregar")
A("     capacidad_tanque_l y tipo_motor al esquema del scraper y re-scrapear.")
A("  6. Los cortes de outliers (500 / 5000 USD) no descartaron nada en esta muestra.")
A("")
A(SEP)
with open(os.path.join(BASE_DIR, "resumen_clustering_90.txt"), "w", encoding="utf-8") as fh:
    fh.write("\n".join(L) + "\n")
print("  guardado: resumen_clustering_90.txt")

# =====================================================================
# 9. VALIDACION
# =====================================================================
print()
print(SEP)
print("PASO 9 — VALIDACION")
print(SEP)
r = subprocess.run([PY, "-m", "json.tool",
                    os.path.join(BASE_DIR, "motos_combustion_90_con_clusters.json")],
                   capture_output=True, text=True)
print(f"  json.tool motos_combustion_90_con_clusters.json -> exit={r.returncode} "
      f"({'JSON VALIDO' if r.returncode == 0 else 'ERROR'})")
if r.returncode != 0:
    print(r.stderr[:800])

texto = open(os.path.join(BASE_DIR, "motos_combustion_90_con_clusters.json"), encoding="utf-8").read()
malos = [t for t in ("NaN", "Infinity") if t in texto]
print(f"  sin literales NaN/Infinity -> {'OK' if not malos else 'FALLA ' + str(malos)}")

con = json.loads(texto, parse_constant=lambda c: (_ for _ in ()).throw(
    ValueError(f"constante JSON invalida: {c}")))
print("  recheck parseo estricto -> OK")
print(f"  vehiculo={con['vehiculo']} total_anuncios={con['total_anuncios']} "
      f"k_elegido={con['k_elegido']} k_clusters_finales={con['k_clusters_finales']} "
      f"silhouette={con['silhouette']} clusters={len(con['clusters'])} "
      f"anuncios={len(con['anuncios'])}")
print(f"  features_usadas={con['features_usadas']} | "
      f"campos_solo_descriptivos={con['campos_solo_descriptivos']}")
print(f"  suma n_anuncios = {sum(c['n_anuncios'] for c in con['clusters'])} (debe ser {N}) -> "
      f"{'OK' if sum(c['n_anuncios'] for c in con['clusters']) == N else 'FALLA'}")
print(f"  los {len(con['anuncios'])} anuncios tienen campo 'cluster' -> "
      f"{all('cluster' in a for a in con['anuncios'])}")
print(f"  cada anuncio conserva sus 9 campos originales -> "
      f"{all({k: v for k, v in a.items() if k != 'cluster'} in anuncios for a in con['anuncios'])}")
print(f"  ningun anuncio tiene capacidad_tanque_l -> "
      f"{not any('capacidad_tanque_l' in a for a in con['anuncios'])}")
ids = [c['id'] for c in con['clusters']]
print(f"  ids de cluster 0..{k_final - 1} sin huecos -> {ids == list(range(k_final))}")
requeridos = {"id", "nombre", "n_anuncios", "precio_promedio_usd", "precio_min_usd",
              "precio_max_usd", "cilindrada_promedio_cc", "cilindrada_min_cc",
              "cilindrada_max_cc", "marca_mas_comun", "descripcion"}
print(f"  cada cluster tiene todos los campos pedidos -> "
      f"{all(requeridos <= set(c) for c in con['clusters'])}")
print(f"  anunciados fuera del rango [{MIN_ANUNCIOS_POR_CLUSTER}, {MAXIMO}]: "
      f"{[c['id'] for c in con['clusters'] if not (MIN_ANUNCIOS_POR_CLUSTER <= c['n_anuncios'] <= MAXIMO)]} "
      f"-> {'OK' if all(MIN_ANUNCIOS_POR_CLUSTER <= c['n_anuncios'] <= MAXIMO for c in con['clusters']) else 'FALLA'}")
print(f"  cluster mas grande: {max(c['n_anuncios'] for c in con['clusters'])}/{N} = "
      f"{max(c['n_anuncios'] for c in con['clusters']) / N:.1%} (<= 50% -> "
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

for fn in ["motos_combustion_90_con_clusters.json", "resumen_clustering_90.txt",
           "descartados_outliers.json", "clusters_dispersion.png",
           "clusters_precio_medio.png", "clusters_tamanos.png", "clusters_metricas.png"]:
    p = os.path.join(BASE_DIR, fn)
    print(f"  {'OK ' if os.path.exists(p) else 'FALLA'} {fn:<38} {os.path.getsize(p):>9,} bytes")

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
print(f"  capacidad_tanque_l        : ausente en {sin_campo}/{len(df_total)} anuncios")
print(f"  Outliers descartados      : {len(descartados)} ({n_ph} placeholder, {n_po} premium)")
print(f"  Anuncios clusterizados    : {N}")
print(f"  k elegido                 : {k_elegido} -> {k_final} clusters tras fusion "
      f"(silhouette {SIL_FINAL:.4f})")
print(f"  Tamanos                   : {TAM} (min {min(TAM)}, max {max(TAM)}, tope {MAXIMO})")
print(f"  Correlacion precio~cc     : {corr_pc:+.4f}")
print(SEP)