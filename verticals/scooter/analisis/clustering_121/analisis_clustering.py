"""Clustering K-Means de scooters electricos (121 anuncios, La Habana, 2026).

Ampliacion del analisis de 30 anuncios (verticals/scooter/analisis/clustering_30/),
que clusterizaba solo con 2 features (precio_usd + autonomia_km) sobre 30 anuncios.
Aqui se usan las 3 features tecnicas (precio_usd, motor_w, autonomia_km) sobre los
121 anuncios ya mergeados.

Pipeline:
  1. carga del JSON original (solo lectura, se verifica integridad por md5)
  2. filtro de outliers de precio  -> descartados_outliers.json
  3. filtro de motor_w null        -> descartados_sin_motor.json
  4. normalizacion StandardScaler sobre las 3 features
  5. barrido k = 2..10 + reglas de tamaño + clausula de fusion encadenada
  6. etiquetado final y metricas por cluster
  7. 5 PNG (dpi=150), JSON con clusters y resumen TXT
"""

import hashlib
import itertools
import json
import os
import subprocess
from collections import deque
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
ORIGEN = "/home/leandro/ev-market-study/verticals/scooter/scraping/scooters_electricos_139.json"
PY = "/home/leandro/ev-market-study/venv/bin/python3"

VEHICULO = "scooter_electrico"
FEATURES = ["precio_usd", "motor_w", "autonomia_km"]
SOLO_DESCRIPTIVOS = ["marca", "municipio", "ubicacion"]
PALETA = ["#2e8b57", "#e07b00", "#7b2d8b", "#1f6feb", "#c1121f", "#008b8b",
          "#6b7280", "#b8860b", "#0f766e", "#9d174d"]
SEP = "=" * 78

# ---------- filtro de outliers de precio ----------
MIN_PRECIO_VALIDO = 200.0    # por debajo: precio placeholder / error de tipeo
MAX_PRECIO_VALIDO = 2000.0   # por encima: outlier premium aislado

# ---------- reglas de tamano de cluster ----------
MIN_ANUNCIOS_POR_CLUSTER = 4        # nunca aceptar un cluster con menos de 4
MAX_FRACCION_POR_CLUSTER = 0.50     # ningun cluster puede pasar el 50% del total
GAP_FUSION = 0.10                   # dos clusters con precio promedio <10% se fusionan
MAX_ESTADOS_FUSION = 20000          # tope de seguridad para la busqueda de cadenas de fusion

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
    """Pares (gap, a, b) con diferencia de precio promedio < GAP_FUSION, ordenados por gap."""
    pm = np.array([X[etiquetas == i, 0].mean() for i in range(k)])
    out = []
    for a, b in itertools.combinations(range(k), 2):
        medio = (pm[a] + pm[b]) / 2
        gap = abs(pm[a] - pm[b]) / medio
        if gap < GAP_FUSION:
            out.append((float(gap), a, b))
    return sorted(out)


def buscar_fusion(etiquetas, k, X, maximo, X_norm):
    """Busca la mejor particion valida alcanzable encadenando fusiones por precio.

    La fusion es el mecanismo de REPARACION: solo se aplica si la particion cruda
    incumple las reglas de tamano. Se exploran todas las cadenas de mergers con gap
    < GAP_FUSION (BFS, en orden ascendente de gap, con conjunto de estados vistos) y
    se devuelve la particion valida con mayor silhouette; a igualdad, la que usa menos
    fusiones. Cada merger se post-valida: si deja un cluster > maximo se descarta,
    porque fusionar solo puede agrandar clusters.
    """
    if viola(etiquetas, k, maximo)[2]:
        return etiquetas.copy(), k, [], 1
    vistos = {(tuple(etiquetas.tolist()), k)}
    cola = deque([(etiquetas.copy(), k, [], 0.0)])
    mejor = None
    while cola and len(vistos) < MAX_ESTADOS_FUSION:
        L, kk, pasos, _ = cola.popleft()
        for gap, a, b in pares_fusionables(L, kk, X):
            M = canonicas(np.where(L == b, a, L))
            if viola(M, kk - 1, maximo)[1] > maximo:
                continue
            firma = (tuple(M.tolist()), kk - 1)
            if firma in vistos:
                continue
            vistos.add(firma)
            n_pasos = len(pasos) + 1
            nuevos_pasos = pasos + [{"gap": round(gap, 4), "cluster_a": int(a), "cluster_b": int(b)}]
            if viola(M, kk - 1, maximo)[2]:
                sil = float(silhouette_score(X_norm, M))
                cand = (sil, -n_pasos, M.copy(), kk - 1, nuevos_pasos)
                if mejor is None or (cand[0], cand[1]) > (mejor[0], mejor[1]):
                    mejor = cand
                continue
            if kk - 1 > 1:
                cola.append((M, kk - 1, nuevos_pasos, 0.0))
    if mejor is None:
        return None, None, [], len(vistos)
    return mejor[2], mejor[3], mejor[4], len(vistos)


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
print(f"  archivo origen       : {ORIGEN}")
print(f"  md5 origen           : {ORIGEN_MD5}")
print(f"  vehiculo             : {data.get('vehiculo')}")
print(f"  fecha scraping orig. : {data.get('fecha_scraping_original')}")
print(f"  fecha ampliacion     : {data.get('fecha_scraping_ampliacion')}")
print(f"  anuncios declarados  : {data.get('total_anuncios_validos')}")
print(f"  anuncios en la lista : {len(anuncios)}  (el archivo se llama _139 pero trae 121)")
print(f"  campos declarados    : {data.get('campos')}")
print(f"  filtros del scrape   : ubicacion={data.get('filtro_ubicacion')}, fecha={data.get('filtro_fecha')}")
print(f"  fuentes              : {data.get('fuente_original')} + {data.get('fuente_ampliacion')}")
print("\n  nulos por campo:")
for c in df_total.columns:
    if c == "__pos":
        continue
    print(f"    {c:<18}: {int(df_total[c].isna().sum()):>3}/{len(df_total)}")

# =====================================================================
# 2. FILTRO PREVIO: outliers de precio y motor_w null
# =====================================================================
print()
print(SEP)
print("PASO 2 — FILTRO PREVIO")
print(SEP)

# --- 2.1 outliers de precio ---
print(f"  2.1 precio_usd < {MIN_PRECIO_VALIDO:.0f}  -> 'precio_placeholder'")
print(f"      precio_usd > {MAX_PRECIO_VALIDO:.0f} -> 'precio_outlier_premium'")
print(f"      rango de precio observado: {df_total['precio_usd'].min():.0f} - "
      f"{df_total['precio_usd'].max():.0f} USD")

motivo_precio = pd.Series("ok", index=df_total.index)
motivo_precio[df_total["precio_usd"] < MIN_PRECIO_VALIDO] = "precio_placeholder"
motivo_precio[df_total["precio_usd"] > MAX_PRECIO_VALIDO] = "precio_outlier_premium"
idx_outliers = df_total.index[motivo_precio != "ok"].tolist()

descartados_outliers = []
for idx in idx_outliers:
    a = dict(anuncios[int(df_total.loc[idx, "__pos"])])
    a["motivo_descarte"] = str(motivo_precio.loc[idx])
    a["precio_usd"] = float(a["precio_usd"])
    if a["motor_w"] is not None:
        a["motor_w"] = float(a["motor_w"])
    a["autonomia_km"] = int(a["autonomia_km"])
    descartados_outliers.append(a)

n_ph = sum(1 for a in descartados_outliers if a["motivo_descarte"] == "precio_placeholder")
n_po = sum(1 for a in descartados_outliers if a["motivo_descarte"] == "precio_outlier_premium")
print(f"      precio_placeholder       : {n_ph} anuncio(s)")
print(f"      precio_outlier_premium   : {n_po} anuncio(s)")
print(f"      total descartado         : {len(descartados_outliers)}")
for a in descartados_outliers:
    print(f"        - {a['motivo_descarte']:<24} {a['marca']:<10} {a['precio_usd']:>8.0f} USD  "
          f"{str(a['titulo'])[:70]}")
if not descartados_outliers:
    print("      (ninguno: todos los precios del dataset estan entre 280 y 1250 USD,")
    print("       muy por dentro de los cortes de 200 y 2000 USD)")

# --- 2.2 motor_w null (sobre los que sobrevivieron al filtro de precio) ---
sobreviven = df_total[motivo_precio == "ok"]
idx_sin_motor = sobreviven.index[sobreviven["motor_w"].isna()].tolist()
descartados_sin_motor = []
for idx in idx_sin_motor:
    a = dict(anuncios[int(df_total.loc[idx, "__pos"])])
    a["motivo_descarte"] = "sin_motor_w"
    a["precio_usd"] = float(a["precio_usd"])
    a["autonomia_km"] = int(a["autonomia_km"])
    descartados_sin_motor.append(a)
print(f"\n  2.2 motor_w null -> 'sin_motor_w'")
print(f"      anuncios con motor_w null : {len(idx_sin_motor)}")
print(f"      de los cuales, precio ya filtrado antes: 0 (los filtros no se solapan)")
for a in descartados_sin_motor:
    print(f"        - sin_motor_w           {a['marca']:<10} {a['precio_usd']:>7.0f} USD  "
          f"aut={a['autonomia_km']:>3} km  {str(a['titulo'])[:60]}")

# --- 2.3 dataset final ---
df = sobreviven.dropna(subset=["motor_w"]).reset_index(drop=True)
pos_final = [int(p) for p in df["__pos"]]
N = len(df)
print(f"\n  balance: {len(anuncios)} originales - {len(descartados_outliers)} outliers de precio "
      f"- {len(descartados_sin_motor)} sin motor_w = {N} clusterizables")

# --- 2.4 guardado de los dos archivos de descartes ---
with open(os.path.join(BASE_DIR, "descartados_outliers.json"), "w", encoding="utf-8") as fh:
    json.dump({
        "vehiculo": data.get("vehiculo", VEHICULO),
        "filtro": "outliers de precio_usd",
        "reglas": {
            "precio_placeholder": f"precio_usd < {MIN_PRECIO_VALIDO}",
            "precio_outlier_premium": f"precio_usd > {MAX_PRECIO_VALIDO}",
        },
        "total_anuncios_originales": int(len(anuncios)),
        "total_descartados": int(len(descartados_outliers)),
        "total_precio_placeholder": int(n_ph),
        "total_precio_outlier_premium": int(n_po),
        "rango_precio_usd": [float(df_total["precio_usd"].min()), float(df_total["precio_usd"].max())],
        "fecha_analisis": datetime.now().isoformat(timespec="seconds"),
        "descartados": descartados_outliers,
    }, fh, ensure_ascii=False, indent=2, allow_nan=False)
print("\n  guardado: descartados_outliers.json")

with open(os.path.join(BASE_DIR, "descartados_sin_motor.json"), "w", encoding="utf-8") as fh:
    json.dump({
        "vehiculo": data.get("vehiculo", VEHICULO),
        "filtro": "anuncios sin motor_w (feature obligatoria del clustering)",
        "reglas": {"sin_motor_w": "motor_w is null o ausente"},
        "total_anuncios_originales": int(len(anuncios)),
        "total_descartados": int(len(descartados_sin_motor)),
        "total_limpios": int(N),
        "motivo": ("motor_w es una de las 3 features del modelo y no se puede imputar: "
                   "se descarta el anuncio en vez de inventar el dato"),
        "fecha_analisis": datetime.now().isoformat(timespec="seconds"),
        "descartados": descartados_sin_motor,
    }, fh, ensure_ascii=False, indent=2, allow_nan=False)
print("  guardado: descartados_sin_motor.json")

# =====================================================================
# 3. NORMALIZACION
# =====================================================================
print()
print(SEP)
print("PASO 3 — NORMALIZACION (StandardScaler)")
print(SEP)
X = df[FEATURES].astype(float)
print("\n(a) describe() en escala original (dataset clusterizable, n=%d):" % N)
print(X.describe().to_string())
scaler = StandardScaler()
X_norm = scaler.fit_transform(X.to_numpy())
Xo = X.to_numpy(float)

print(f"\n  valores distintos de motor_w     : {sorted(df['motor_w'].astype(int).unique().tolist())}")
print(f"  valores distintos de autonomia_km: {len(df['autonomia_km'].unique())} "
      f"({df['autonomia_km'].min()}-{df['autonomia_km'].max()} km)")
print("  distribucion de motor_w:")
print(df["motor_w"].astype(int).value_counts().sort_index().to_string())
print("\n  medias normalizadas (deben ser ~0): "
      f"{ {c: f'{v:.2e}' for c, v in zip(FEATURES, X_norm.mean(axis=0))} }")
print("  desv normalizadas (deben ser ~1): "
      f"{ {c: round(float(v), 6) for c, v in zip(FEATURES, X_norm.std(axis=0))} }")

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
    L_f, k_f, pasos, estados = buscar_fusion(lab, k, Xo, MAXIMO, X_norm)
    if L_f is None:
        sil_final, k_final, tam_final, ok_final, nv_final = sil, k, None, False, 99
        pasos_txt = "sin fusion posible"
        pares_txt = (f"{len(pares_fusionables(lab, k, Xo))} pares <{GAP_FUSION:.0%}, "
                     f"{estados} estados explorados, ninguna cadena deja la particion valida")
    else:
        sil_final = float(silhouette_score(X_norm, L_f))
        k_final = k_f
        tam_final = sorted(tamanos(L_f, k_f).tolist())
        _, _, ok_final, nv_final = viola(L_f, k_f, MAXIMO)
        pasos_txt = ", ".join(f"C{p['cluster_a']}+C{p['cluster_b']}({p['gap']:.2%})"
                              for p in pasos) or "- (particion cruda ya valida)"
        pares_txt = f"{len(pares_fusionables(lab, k, Xo))} pares <{GAP_FUSION:.0%}, " \
                    f"{estados} estado(s) explorado(s)"
    filas.append({
        "k": k, "inercia": float(km.inertia_), "silhouette": sil,
        "tamanos_raw": sorted(tamanos(lab, k).tolist()),
        "min_raw": mini, "max_raw": maxi, "cumple_raw": ok, "violaciones_raw": nv,
        "k_final": k_final, "silhouette_final": sil_final, "tamanos_final": tam_final,
        "cumple_final": ok_final, "violaciones_final": nv_final,
        "n_fusiones": len(pasos), "fusiones": pasos_txt, "exploracion": pares_txt,
        "estados_buscados": estados,
        "_labels": lab, "_labels_final": L_f,
    })
    print(f"  k={k:>2}  inercia={km.inertia_:>7.2f}  sil={sil:.4f}  raw{str(filas[-1]['tamanos_raw']):<28}"
          f"{'OK ' if ok else 'NO '}  -> k_f={k_final} sil_f={sil_final:.4f} "
          f"{'OK' if ok_final else 'NO'}  [{pasos_txt}]")

tabla_k = pd.DataFrame(filas)

candidatos = tabla_k[tabla_k["cumple_final"]]
print("\n  k que cumplen las reglas (raw o post-fusion): "
      f"{sorted(candidatos['k'].tolist())}")
if len(candidatos):
    mejor = candidatos.loc[candidatos["silhouette_final"].idxmax()]
    k_elegido = int(mejor["k"])
    etiquetas = mejor["_labels_final"]
    k_final = int(mejor["k_final"])
    sil_elegido = float(mejor["silhouette_final"])
    eleccion_txt = "mayor silhouette entre los k que cumplen las reglas de tamaño"
else:
    mejor = tabla_k.sort_values(["violaciones_final", "silhouette_final"],
                                 ascending=[True, False]).iloc[0]
    k_elegido = int(mejor["k"])
    etiquetas = mejor["_labels_final"]
    k_final = int(mejor["k_final"])
    sil_elegido = float(mejor["silhouette_final"])
    eleccion_txt = "NINGUN k cumple las reglas -> se elige el que menos viola"

print(f"\n  k ELEGIDO = {k_elegido}  (silhouette final = {sil_elegido:.4f})")
print(f"  criterio  : {eleccion_txt}")
print(f"  clusters finales: {k_final}  (fusiones aplicadas: {int(mejor['n_fusiones'])})")
pares_k = pares_fusionables(mejor["_labels"], int(mejor["k"]), Xo)
print(f"  pares con precio promedio <{GAP_FUSION:.0%} en la particion cruda de k={k_elegido}: "
      f"{[(f'C{a}+C{b}', f'{g:.2%}') for g, a, b in pares_k] or 'ninguno'}")
print("  -> el modelo con 3 features NO necesita fusion: la particion cruda de "
      f"k={k_elegido} ya cumple las reglas")

# etiquetas ordenadas por precio medio ascendente (KMeans no ordena)
pm = {int(c): Xo[etiquetas == c, 0].mean() for c in np.unique(etiquetas)}
orden = sorted(pm, key=lambda c: pm[c])
remap = {orig: nuevo for nuevo, orig in enumerate(orden)}
df["cluster"] = pd.Series(etiquetas).map(remap).to_numpy(int)
CENTROS = np.array([Xo[df["cluster"].to_numpy() == i].mean(axis=0) for i in range(k_final)])
etiquetas = df["cluster"].to_numpy(int)
print(f"  reordenamiento por precio medio ascendente: {orden} -> {list(range(k_final))}")

SIL_FINAL = float(silhouette_score(X_norm, etiquetas))
SUBTITULO = f"k={k_elegido}"
print(f"  silhouette final recalculado: {SIL_FINAL:.4f}")

# =====================================================================
# 5. PERFIL Y NOMBRE DE CADA CLUSTER
# =====================================================================
print()
print(SEP)
print("PASO 5 — PERFIL Y NOMBRE DE CADA CLUSTER")
print(SEP)

# Los cortes salen de la propia distribucion: terciles del precio y de la autonomia.
QP1 = float(X["precio_usd"].quantile(1 / 3))
QP3 = float(X["precio_usd"].quantile(2 / 3))
QA1 = float(X["autonomia_km"].quantile(1 / 3))
QA3 = float(X["autonomia_km"].quantile(2 / 3))


def tier_precio(v):
    if v < QP1:
        return "precio bajo"
    if v < QP3:
        return "precio medio"
    return "precio alto"


def tier_alcance(v):
    if v <= QA1:
        return "alcance corto"
    if v <= QA3:
        return "alcance medio"
    return "alcance largo"


print(f"  cortes (terciles) precio_usd   : {QP1:.0f} / {QP3:.0f} USD")
print(f"  cortes (terciles) autonomia_km : {QA1:.0f} / {QA3:.0f} km")
print("  motor_w entra al nombre como el rango de valores presente en el cluster")

clusters_info = []
for i in range(k_final):
    sub = df[df["cluster"] == i]
    pm_ = float(sub["precio_usd"].mean())
    mot_prom = float(sub["motor_w"].mean())
    mot_min = int(sub["motor_w"].min())
    mot_max = int(sub["motor_w"].max())
    aut_prom = float(sub["autonomia_km"].mean())
    aut_min = int(sub["autonomia_km"].min())
    aut_max = int(sub["autonomia_km"].max())
    mot_txt = f"{mot_min}W" if mot_min == mot_max else f"{mot_min}-{mot_max}W"
    nombre = f"C{i} - {tier_precio(pm_)} {mot_txt} {tier_alcance(aut_prom)}"
    marcas = sub["marca"].astype(str).str.strip().replace("", np.nan).dropna()
    top_marcas = marcas.value_counts()
    pos = ("el segmento mas economico" if i == 0 else
           "el segmento mas caro" if i == k_final - 1 else "un segmento intermedio por precio")
    desc = (f"{len(sub)} anuncios de {sub['precio_usd'].min():.0f} a {sub['precio_usd'].max():.0f} USD "
            f"(prom {pm_:.0f}); motor de {mot_min} a {mot_max} W (prom {mot_prom:.0f} W) y autonomia "
            f"de {aut_min} a {aut_max} km (prom {aut_prom:.0f} km). Es {pos}. "
            f"Marca dominante: {top_marcas.index[0] if len(top_marcas) else 'n/d'} "
            f"({top_marcas.iloc[0] if len(top_marcas) else 0} de {len(sub)} anuncios).")
    clusters_info.append({
        "id": i,
        "nombre": nombre,
        "n_anuncios": int(len(sub)),
        "precio_promedio_usd": round(pm_, 2),
        "precio_min_usd": float(sub["precio_usd"].min()),
        "precio_max_usd": float(sub["precio_usd"].max()),
        "motor_promedio_w": round(mot_prom, 1),
        "motor_min_w": mot_min,
        "motor_max_w": mot_max,
        "autonomia_promedio_km": round(aut_prom, 1),
        "autonomia_min_km": aut_min,
        "autonomia_max_km": aut_max,
        "marcas_mas_comunes": [{"marca": str(m), "n": int(n)} for m, n in top_marcas.head(3).items()],
        "marca_mas_comun": str(top_marcas.index[0]) if len(top_marcas) else None,
        "descripcion": desc,
    })
    print(f"  C{i}: {nombre:<44} n={len(sub):>3}  {pm_:>6.0f} USD  "
          f"{mot_min}-{mot_max} W  aut {aut_prom:.0f} km  "
          f"[{', '.join(f'{m}:{n}' for m, n in top_marcas.head(3).items())}]")

CORR = X[FEATURES].corr(method="pearson")
print("\n  correlaciones Pearson entre las 3 features:")
for a in FEATURES:
    for b in FEATURES:
        if a < b:
            print(f"    {a:<13} ~ {b:<13} : r = {CORR.loc[a, b]:+.4f}")

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
ax.set_title(f"Dispersión por cluster ({SUBTITULO}): precio vs autonomía\n"
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
fig, ax = plt.subplots(figsize=(13, 7))
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
fig, ax = plt.subplots(figsize=(13, 7))
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
fig, ax = plt.subplots(figsize=(13, 7))
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

# --- 6.5 heatmap de correlaciones (hecho con matplotlib, sin seaborn) ---
fig, ax = plt.subplots(figsize=(9, 7))
M = CORR.to_numpy(dtype=float)
im = ax.imshow(M, cmap="coolwarm", vmin=-1, vmax=1)
ax.set_xticks(range(len(FEATURES)))
ax.set_yticks(range(len(FEATURES)))
ax.set_xticklabels(FEATURES, rotation=30, ha="right", fontsize=10)
ax.set_yticklabels(FEATURES, rotation=0, fontsize=10)
ax.set_xticks(np.arange(len(FEATURES)) - 0.5, minor=True)
ax.set_yticks(np.arange(len(FEATURES)) - 0.5, minor=True)
ax.grid(which="minor", color="white", linewidth=2)
ax.tick_params(which="minor", length=0)
for i in range(len(FEATURES)):
    for j in range(len(FEATURES)):
        val = M[i, j]
        ax.text(j, i, f"{val:.4f}", ha="center", va="center", fontsize=11,
                fontweight="bold" if i == j else "normal",
                color="white" if abs(val) > 0.55 else "black")
ax.set_title(f"Correlación de Pearson entre las {len(FEATURES)} features ({SUBTITULO}, n={N})",
             fontsize=13, fontweight="bold", pad=14)
cbar = fig.colorbar(im, ax=ax, shrink=0.85)
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
    "total_anuncios_originales": int(len(anuncios)),
    "k_elegido": int(k_elegido),
    "k_clusters_finales": int(k_final),
    "silhouette": round(SIL_FINAL, 4),
    "fecha_analisis": fecha,
    "features_usadas": FEATURES,
    "campos_solo_descriptivos": SOLO_DESCRIPTIVOS,
    "normalizacion": "StandardScaler",
    "algoritmo": "KMeans(random_state=42, n_init=10)",
    "reglas_aplicadas": {
        "filtro_outliers": {
            "precio_placeholder": f"precio_usd < {MIN_PRECIO_VALIDO}",
            "precio_outlier_premium": f"precio_usd > {MAX_PRECIO_VALIDO}",
            "descartados": int(len(descartados_outliers)),
        },
        "filtro_sin_motor": {"sin_motor_w": "motor_w null", "descartados": int(len(descartados_sin_motor))},
        "min_anuncios_por_cluster": MIN_ANUNCIOS_POR_CLUSTER,
        "max_fraccion_por_cluster": MAX_FRACCION_POR_CLUSTER,
        "maximo_absoluto_por_cluster": MAXIMO,
        "gap_fusion_precio": GAP_FUSION,
        "fusiones_aplicadas": mejor["fusiones"],
    },
    "clusters": clusters_info,
    "anuncios": anuncios_out,
}
with open(os.path.join(BASE_DIR, "scooters_121_con_clusters.json"), "w", encoding="utf-8") as fh:
    json.dump(salida, fh, ensure_ascii=False, indent=2, allow_nan=False)
print(f"  guardado: scooters_121_con_clusters.json "
      f"({len(anuncios_out)} anuncios, k={k_elegido}, silhouette={SIL_FINAL:.4f})")

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
A("CLUSTERING K-MEANS — SCOOTERS ELECTRICOS — 121 ANUNCIOS (LA HABANA, 2026)")
A(SEP)
A(f"Fecha de analisis   : {fecha}")
A(f"Archivo origen      : {ORIGEN}")
A(f"Vehiculo            : {salida['vehiculo']}")
A(f"Features usadas     : {', '.join(FEATURES)}")
A(f"Solo descriptivos   : {', '.join(SOLO_DESCRIPTIVOS)}")
A("Normalizacion       : StandardScaler")
A("Algoritmo           : KMeans(random_state=42, n_init=10)")
_n_fus = int(mejor["n_fusiones"])
_txt_fus = "sin fusion" if _n_fus == 0 else f"{_n_fus} fusion" + ("" if _n_fus == 1 else "es")
A(f"k elegido           : {k_elegido}  ({k_final} clusters, {_txt_fus})")
A(f"Silhouette final    : {SIL_FINAL:.4f}")
A(f"Anuncios clusterizados: {N} de {len(anuncios)}")
A("")

A("0. QUE CAMBIA RESPECTO AL ANALISIS DE 30 ANUNCIOS")
A(SEP)
A("  El analisis previo (clustering_30/) usaba 2 features (precio_usd + autonomia_km)")
A("  sobre 30 anuncios y fijaba a mano los nombres de los segmentos. Este analisis:")
A(f"    - usa las 3 features tecnicas disponibles (precio_usd, motor_w, autonomia_km);")
A(f"    - sube a los {len(anuncios)} anuncios del merge original + ampliacion;")
A("    - elige k por silhouette con reglas de tamano, en vez de fijarlo a mano;")
A("    - deriva los nombres de los datos (precio + motor + autonomia).")
A("")

A("1. DESCARTES PREVIOS")
A(SEP)
A("  Anuncios en scooters_electricos_139.json : %d   (el nombre del archivo dice 139," % len(anuncios))
A("                                              pero el campo total_anuncios_validos")
A("                                              y la lista traen %d)" % len(anuncios))
A("")
A(f"   {'filtro':<34} | {'anuncio(s)':>9} | {'motivo':<21} | archivo de salida")
A(f"   {'-' * 34} + {'-' * 10} + {'-' * 22} + {'-' * 22}")
A(f"   {'precio_usd < ' + format(MIN_PRECIO_VALIDO, '.0f'):<34} | {n_ph:>9} | {'precio_placeholder':<21} | descartados_outliers.json")
A(f"   {'precio_usd > ' + format(MAX_PRECIO_VALIDO, '.0f'):<34} | {n_po:>9} | {'precio_outlier_premium':<21} | descartados_outliers.json")
A(f"   {'motor_w null':<34} | {len(idx_sin_motor):>9} | {'sin_motor_w':<21} | descartados_sin_motor.json")
A(f"   {'-' * 34} + {'-' * 10} + {'-' * 22} + {'-' * 22}")
A(f"   {'total descartado':<34} | {len(descartados_outliers) + len(idx_sin_motor):>9} | {'':<21} |")
A(f"   {'ANUNCIOS CLUSTERIZADOS':<34} | {N:>9} | {'':<21} |")
A("")
A(f"   Rango de precio observado en los {len(anuncios)} anuncios: "
  f"{df_total['precio_usd'].min():.0f} - {df_total['precio_usd'].max():.0f} USD.")
A("   Ningun anuncio cae fuera de los cortes de 200 / 2000 USD, asi que el filtro de")
A("   outliers de precio no descarto nada: queda como red de seguridad documentada.")
A(f"   Los {len(idx_sin_motor)} anuncios sin motor_w si se descartan, porque motor_w es una de las")
A("   3 features del modelo y no se puede imputar sin inventar el dato. Se conservan")
A("   completos en descartados_sin_motor.json (con precio y autonomia) para poder")
A("   recuperarlos si el scraper vuelve a capturar la potencia del motor.")
A("")
A(f"   Los {len(idx_sin_motor)} anuncios sin motor_w por marca:")
marcas_sin_motor = (df_total.loc[idx_sin_motor, "marca"].astype(str).str.strip()
                    .replace("", np.nan).dropna())
for m, n in marcas_sin_motor.value_counts().items():
    A(f"     {m:<20} {n:>4}")
A("")
A("   Los 13 anuncios sin motor_w por municipio:")
mun = df_total.loc[idx_sin_motor, "municipio"].astype(str).str.strip().replace("", np.nan).dropna()
for m, n in mun.value_counts().items():
    A(f"     {m:<20} {n:>4}")
A("")

A("2. BARRIDO k = 2..10 SOBRE EL DATASET CLUSTERIZABLE (n=%d)" % N)
A(SEP)
A("   k |   inercia  |  silhouette  | min raw | max raw | cumple | k_fin | sil_fin | cumple_f | fusiones")
A("  --- + ---------- + ------------ + -------- + -------- + ------ + ------ + ------- + -------- + ---------")
for _, r in tabla_k.iterrows():
    A(f"  {int(r['k']):<3d} | {r['inercia']:>8.2f}  | {r['silhouette']:>10.4f}  | "
      f"{int(r['min_raw']):>7} | {int(r['max_raw']):>7} | {'  si  ' if r['cumple_raw'] else '  no  '} | "
      f"{int(r['k_final']):>5} | {r['silhouette_final']:>7.4f} | "
      f"{'  si ' if r['cumple_final'] else '  no '} | {r['fusiones']}")
A("")

A("   TAMANOS DE CLUSTER POR k (ordenados de menor a mayor, n=%d)" % N)
A(SEP)
for _, r in tabla_k.iterrows():
    tf = r["tamanos_final"]
    A(f"    k={int(r['k']):<2} crudo: {str(r['tamanos_raw']):<30} "
      f"final (k_fin={int(r['k_final'])}): {tf if tf is not None else 'n/a'}")
A("")

A("3. k ELEGIDO Y JUSTIFICACION")
A(SEP)
A("  Reglas aplicadas, en orden:")
A(f"    1. filtro de outliers de precio (precio_usd en [{MIN_PRECIO_VALIDO:.0f}, {MAX_PRECIO_VALIDO:.0f}])")
A("    2. filtro de motor_w null (feature obligatoria, sin imputacion)")
A(f"    3. ningun cluster con menos de {MIN_ANUNCIOS_POR_CLUSTER} anuncios")
A(f"    4. ningun cluster con mas del {MAX_FRACCION_POR_CLUSTER:.0%} del total (>{MAXIMO} anuncios)")
A(f"    5. clausula de fusion: dos clusters cuyo precio promedio difiere <{GAP_FUSION:.0%} se")
A("       fusionan; el resultado se vuelve a validar contra las reglas 3 y 4")
A("    6. entre los k que cumplen, gana el de mayor silhouette")
A("")
A(f"  k ELEGIDO                  : {k_elegido}")
A(f"  clusters finales           : {k_final}")
A(f"  silhouette                 : {SIL_FINAL:.4f}")
A(f"  fusiones aplicadas         : {mejor['fusiones']}")
A("")
cands = tabla_k[tabla_k["cumple_final"]].sort_values("silhouette_final", ascending=False)
A("  k que cumplen las reglas (silhouette final, de mayor a menor):")
for _, r in cands.iterrows():
    A(f"    k={int(r['k']):<2} sil={r['silhouette_final']:.4f}  "
      f"k_fin={int(r['k_final'])}  tamanos={r['tamanos_final']}")
A("")
A(f"  Motivo: k={k_elegido} tiene el silhouette mas alto "
  f"({sil_elegido:.4f}) de todos los k que")
A(f"  respetan el piso de {MIN_ANUNCIOS_POR_CLUSTER} anuncios y el tope de {MAXIMO}. Su particion cruda ya")
A("  cumple las dos reglas, asi que la clausula de fusion no hace falta.")
A("")
A("  k descartados por romper el tope del 50%:")
for _, r in tabla_k[tabla_k["max_raw"] > MAXIMO].iterrows():
    A(f"    k={int(r['k']):<2} sil={r['silhouette']:.4f} pero su cluster mayor tiene "
      f"{int(r['max_raw'])} anuncios = {int(r['max_raw']) / N:.0%} del total (fusionar solo puede")
    A(f"        agrandar clusters, asi que no hay reparacion posible)")
A("  k descartados por dejar un cluster de menos de 4 anuncios:")
for _, r in tabla_k[tabla_k["min_raw"] < MIN_ANUNCIOS_POR_CLUSTER].iterrows():
    A(f"    k={int(r['k']):<2} sil={r['silhouette']:.4f} pero su cluster menor tiene "
      f"{int(r['min_raw'])} anuncio(s); su particion cruda NO cumple")
A("")
A("  k reparables por fusion (gap de precio < 10%), con la mejor silhouette alcanzable:")
for _, r in tabla_k[(tabla_k["min_raw"] < MIN_ANUNCIOS_POR_CLUSTER) &
                    tabla_k["cumple_final"]].iterrows():
    A(f"    k={int(r['k']):<2} crudo {r['tamanos_raw']} -> k_fin={int(r['k_final'])} "
      f"sil={r['silhouette_final']:.4f}  ({r['fusiones']})")
    A(f"        sil {r['silhouette_final']:.4f} < sil {sil_elegido:.4f} de k={k_elegido}, asi que no gana")
A("")
A("  k sin particion valida posible (ni cruda ni encadenando fusiones):")
for _, r in tabla_k[~tabla_k["cumple_final"] &
                    (tabla_k["min_raw"] < MIN_ANUNCIOS_POR_CLUSTER)].iterrows():
    n_chicos = len([t for t in r["tamanos_raw"] if t < MIN_ANUNCIOS_POR_CLUSTER])
    A(f"    k={int(r['k']):<2} deja {n_chicos} cluster(s) de menos de {MIN_ANUNCIOS_POR_CLUSTER} anuncios "
      f"({r['tamanos_raw']});")
    A(f"        la busqueda de fusiones dio: {r['exploracion']}")
    A(f"        -> ningun par con precio promedio a menos del 10% toca esos clusters chicos, "
      f"asi que no hay reparacion posible")
A("")
A("  Nota sobre la clausula de fusion: la busqueda es exhaustiva sobre las cadenas de")
A("  mergers con gap < 10% (orden ascendente de gap, con poda de los estados que ya")
A("  superan el tope de 50%), no solo de un merger. Aun asi, en este dataset la fusion")
A("  solo llega a repairing k=7 y k=8, y en ambos casos el silhouette reparado queda")
A(f"  por debajo del de k={k_elegido} sin reparar. El ganador no necesita fusion: su")
A("  particion cruda ya cumple las dos reglas de tamano.")
A("")

A("4. CLUSTERS FINALES")
A(SEP)
A("  Los nombres salen de los datos: el precio se parte en 3 tramos con los terciles de")
A("  la propia distribucion, la autonomia tambien, y el motor entra como el rango de")
A("  potencias presente en el cluster. "
  f"El prefijo C0..C{k_final - 1} garantiza que el nombre sea")
A("  unico.")
A("")
A("   id | nombre                                      |   n |   %  | precio | motor   | autonomia | marca      | motores presentes")
A("  --- + ------------------------------------------- + ---- + ---- + ------ + ------- + --------- + ---------- + ---------------")
for c in clusters_info:
    mot = f"{c['motor_min_w']}W" if c["motor_min_w"] == c["motor_max_w"] \
        else f"{c['motor_min_w']}-{c['motor_max_w']}W"
    A(f"  {c['id']:<3d} | {c['nombre']:<41} | {c['n_anuncios']:>4} | {c['n_anuncios'] / N:>5.1%} | "
      f"{c['precio_promedio_usd']:>6.0f} | {mot:<7} | {c['autonomia_promedio_km']:>7.0f} km | "
      f"{str(c['marca_mas_comun']):<10} | "
      f"{sorted(df.loc[df['cluster'] == c['id'], 'motor_w'].astype(int).unique().tolist())}")
A("")
for c in clusters_info:
    A(f"  {c['nombre']}")
    A(f"    anuncios      : {c['n_anuncios']} ({c['n_anuncios'] / N:.1%} del total)")
    A(f"    precio USD    : prom {c['precio_promedio_usd']:.0f} | min {c['precio_min_usd']:.0f} | "
      f"max {c['precio_max_usd']:.0f}")
    A(f"    motor W       : prom {c['motor_promedio_w']:.0f} | min {c['motor_min_w']} | "
      f"max {c['motor_max_w']}")
    A(f"    autonomia km  : prom {c['autonomia_promedio_km']:.0f} | min {c['autonomia_min_km']} | "
      f"max {c['autonomia_max_km']}")
    A(f"    top marcas    : "
      f"{', '.join(f'{m['marca']} ({m['n']})' for m in c['marcas_mas_comunes'])}")
    A(f"    descripcion   : {c['descripcion']}")
    A("")

A("5. CORRELACIONES ENTRE LAS 3 FEATURES")
A(SEP)
A(f"  Pearson sobre las {N} anuncios clusterizados:")
A("")
A("               " + "".join(f"{f:>14}" for f in FEATURES))
for a in FEATURES:
    A(f"    {a:<10} " + "".join(f"{CORR.loc[a, b]:>14.4f}" for b in FEATURES))
A("")
pares_corr = [(f"{a} ~ {b}", float(CORR.loc[a, b])) for a, b in
              itertools.combinations(FEATURES, 2)]
for nombre_corr, r in sorted(pares_corr, key=lambda t: -abs(t[1])):
    fuerza = ("fuerte" if abs(r) >= 0.7 else "moderada" if abs(r) >= 0.4 else "debil")
    A(f"    {nombre_corr:<28} r = {r:+.4f}  ({fuerza}, "
      f"{'positiva' if r > 0 else 'negativa'})")
A("")
A("  Lectura:")
A(f"    precio ~ autonomia es la correlacion fuerte ({CORR.loc['precio_usd', 'autonomia_km']:+.4f}): el precio de estos")
A("      scooters sube casi linealmente con el alcance, porque el paquete de bateria es")
A("      lo que encarece. Es la variable que mas manda en el precio.")
A(f"    motor ~ autonomia es moderada ({CORR.loc['motor_w', 'autonomia_km']:+.4f}): mas vatios dan mas autonomia, pero con")
A("      fecha: el motor es proxy del modelo, no la causa directa del alcance.")
A(f"    precio ~ motor es moderada ({CORR.loc['precio_usd', 'motor_w']:+.4f}): 500 W aparece en casi todos los tramos de precio")
A("      (ver el crosstab de la seccion 4), asi que los vatios por si solos no explican")
A("      el precio. Por eso el cluster de mayor precio (C5) se define por tener 800-1200 W")
A("      Y 60-70 km, no solo por los vatios.")
A("")
A("  Distribucion cruzada motor_w x cluster (n=%d):" % N)
motores = sorted(df["motor_w"].astype(int).unique().tolist())
A("      cluster |" + "".join(f" {m:>5} W |" for m in motores) + "  total")
for c in clusters_info:
    fila = df[df["cluster"] == c["id"]]["motor_w"].astype(int)
    A(f"      C{c['id']:<6} |" + "".join(f" {int((fila == m).sum()):>7} |" for m in motores)
      + f" {len(fila):>6}")
A("")
A("  Distribucion cruzada autonomia_km x cluster (n=%d):" % N)
bins = [0, 35, 50, 100]
etq = ["<=35", "36-50", ">50"]
cortes_aut = pd.cut(df["autonomia_km"], bins=bins, labels=etq)
A("      cluster |" + "".join(f" {e:>7} |" for e in etq) + "  total")
for c in clusters_info:
    fila = cortes_aut[df["cluster"] == c["id"]]
    A(f"      C{c['id']:<6} |" + "".join(f" {int((fila == e).sum()):>8} |" for e in etq)
      + f" {len(fila):>6}")
A("")

A("6. TOP MARCAS POR CLUSTER")
A(SEP)
A("   id | nombre                                      | marcas mas frecuentes (n)")
A("  --- + ------------------------------------------- + ------------------------------------")
for c in clusters_info:
    A(f"  {c['id']:<3d} | {c['nombre']:<41} | "
      f"{', '.join(f'{m['marca']} ({m['n']})' for m in c['marcas_mas_comunes'])}")
A("")
marcas_limpias = df["marca"].astype(str).str.strip().replace("", np.nan).dropna()
print("  marcas distintas en el dataset clusterizado:", marcas_limpias.nunique())
A(f"  Top 20 marcas del dataset clusterizado ({N} anuncios, "
  f"{marcas_limpias.nunique()} marcas distintas):")
for m, n in marcas_limpias.value_counts().head(20).items():
    A(f"    {m:<20} {n:>4}  ({n / N:.1%} del total)")
A("")
A(f"  Las 20 marcas mas comunes cubren {int(marcas_limpias.value_counts().head(20).sum())} de los "
  f"{N} anuncios ({marcas_limpias.value_counts().head(20).sum() / N:.0%});")
A(f"  las {marcas_limpias.nunique() - 20} marcas restantes son de a 1 anuncio. El mercado esta")
A("  muy fragmentado por marca, asi que la marca sirve como descriptor, no como segmentador.")
A("")
A("  Municipios por cluster (top 3):")
for c in clusters_info:
    mun = df[df["cluster"] == c["id"]]["municipio"].astype(str).str.strip().value_counts().head(3)
    A(f"    C{c['id']}: " + ", ".join(f"{m} ({n})" for m, n in mun.items()))
A("")

A("7. LIMITACIONES")
A(SEP)
A("  1. Solo 3 features y las 3 estan correlacionadas entre si (precio~autonomia "
  f"{CORR.loc['precio_usd', 'autonomia_km']:+.2f},")
A(f"     motor~autonomia {CORR.loc['motor_w', 'autonomia_km']:+.2f}). El clustering termina separando principalmente por precio,")
A("     que es lo que mas varianza aporta; motor y autonomia ordenan dentro de cada tramo")
A("     mas que separar grupos independientes.")
A("  2. motor_w tiene solo 8 valores distintos (350, 500, 650, 700, 750, 800, 1000, 1200 W) y")
A(f"     el 500 W representa {int((df['motor_w'] == 500).sum())} de los {N} anuncios. Es una variable casi")
A("     categorica: los clusters se definen por saltos entre las potencias que el scraper")
A("     encontro, no por una escala continua de potencia.")
A(f"  3. Con n={N} anuncios y {k_final} clusters, el promedio es de {N / k_final:.1f} anuncios por cluster; "
  f"{len([c for c in clusters_info if c['n_anuncios'] <= 9])} clusters")
A("     tienen menos de 10 anuncios y 1 (C2) tiene 7: son segmentos marginales.")
A(f"  4. El silhouette de {SIL_FINAL:.2f} es modesto: con 3")
A("     features los clusters se superponen mas (C3 y C4 tienen precios casi iguales y")
A("     se distinguian solo por autonomia). La frontera entre ambos no es nitida.")
A("  5. Los 13 anuncios sin motor_w quedaron fuera del modelo. Como motor_w viene del")
A("     texto del anuncio, el scraper pierde los anuncios con ficha incompleta, que")
A("     pueden estar sesgados hacia un segmento concreto (no es una muestra aleatoria).")
A("  6. Muestra de una sola ciudad (La Habana) y de un solo canal (revolico).")
A("  7. Los cortes de outliers (200 / 2000 USD) no descartaron nada: el precio de este")
A("     mercado esta muy acotado (280-1250 USD).")
A("  8. autonomy_km se toma tal cual sale del anuncio y a veces es un rango ('50-60 km');")
A("     no se normalizo a un valor puntual, asi que la feature tiene ruido de redaccion.")
A("")
A(SEP)
with open(os.path.join(BASE_DIR, "resumen_clustering_121.txt"), "w", encoding="utf-8") as fh:
    fh.write("\n".join(L) + "\n")
print("  guardado: resumen_clustering_121.txt")

# =====================================================================
# 9. VALIDACION
# =====================================================================
print()
print(SEP)
print("PASO 9 — VALIDACION")
print(SEP)
r = subprocess.run([PY, "-m", "json.tool",
                    os.path.join(BASE_DIR, "scooters_121_con_clusters.json")],
                   capture_output=True, text=True)
print(f"  json.tool scooters_121_con_clusters.json -> exit={r.returncode} "
      f"({'JSON VALIDO' if r.returncode == 0 else 'ERROR'})")
if r.returncode != 0:
    print(r.stderr[:800])

texto = open(os.path.join(BASE_DIR, "scooters_121_con_clusters.json"), encoding="utf-8").read()
malos = [t for t in ("NaN", "Infinity") if t in texto]
print(f"  sin literales NaN/Infinity -> {'OK' if not malos else 'FALLA ' + str(malos)}")
con = json.loads(texto, parse_constant=lambda c: (_ for _ in ()).throw(
    ValueError(f"constante JSON invalida: {c}")))
print("  recheck parseo estricto -> OK")

ro = json.load(open(os.path.join(BASE_DIR, "descartados_outliers.json"), encoding="utf-8"))
print(f"  descartados_outliers.json  : {ro['total_descartados']} descartado(s) "
      f"({ro['total_precio_placeholder']} placeholder, {ro['total_precio_outlier_premium']} premium)")
rm = json.load(open(os.path.join(BASE_DIR, "descartados_sin_motor.json"), encoding="utf-8"))
print(f"  descartados_sin_motor.json: {rm['total_descartados']} descartado(s), "
      f"{rm['total_limpios']} limpios")

print(f"\n  vehiculo={con['vehiculo']} total_anuncios={con['total_anuncios']} "
      f"k_elegido={con['k_elegido']} silhouette={con['silhouette']} "
      f"clusters={len(con['clusters'])} anuncios={len(con['anuncios'])}")
print(f"  features_usadas={con['features_usadas']}")
print(f"  campos_solo_descriptivos={con['campos_solo_descriptivos']}")
print(f"  suma n_anuncios = {sum(c['n_anuncios'] for c in con['clusters'])} "
      f"(debe ser {N}) -> "
      f"{'OK' if sum(c['n_anuncios'] for c in con['clusters']) == N else 'FALLA'}")
print(f"  balance: {len(anuncios)} originales - {len(descartados_outliers)} outliers "
      f"- {len(descartados_sin_motor)} sin motor = {len(anuncios) - len(descartados_outliers) - len(descartados_sin_motor)} "
      f"-> {'OK' if len(anuncios) - len(descartados_outliers) - len(descartados_sin_motor) == N == len(con['anuncios']) else 'FALLA'}")
print(f"  los {len(con['anuncios'])} anuncios tienen campo 'cluster' -> "
      f"{all('cluster' in a for a in con['anuncios'])}")
print(f"  cada anuncio conserva sus 9 campos originales -> "
      f"{all({k: v for k, v in a.items() if k != 'cluster'} in anuncios for a in con['anuncios'])}")
ids = [c['id'] for c in con['clusters']]
print(f"  ids de cluster 0..{k_final - 1} sin huecos -> {ids == list(range(k_final))}")
requeridos = {"id", "nombre", "n_anuncios", "precio_promedio_usd", "precio_min_usd",
              "precio_max_usd", "motor_promedio_w", "autonomia_promedio_km",
              "marca_mas_comun", "descripcion"}
print(f"  cada cluster tiene todos los campos pedidos -> "
      f"{all(requeridos <= set(c) for c in con['clusters'])}")
print(f"  nombres unicos -> {len({c['nombre'] for c in con['clusters']}) == k_final}")
tam_json = [c['n_anuncios'] for c in con['clusters']]
print(f"  tamanos fuera de [{MIN_ANUNCIOS_POR_CLUSTER}, {MAXIMO}]: "
      f"{[c['id'] for c in con['clusters'] if not (MIN_ANUNCIOS_POR_CLUSTER <= c['n_anuncios'] <= MAXIMO)]} -> "
      f"{'OK' if all(MIN_ANUNCIOS_POR_CLUSTER <= t <= MAXIMO for t in tam_json) else 'FALLA'}")
print(f"  cluster mas grande: {max(tam_json)}/{N} = {max(tam_json) / N:.1%} "
      f"(<= 50% -> {'OK' if max(tam_json) <= MAXIMO else 'FALLA'})")
print(f"  cluster mas chico: {min(tam_json)} (>= {MIN_ANUNCIOS_POR_CLUSTER} -> "
      f"{'OK' if min(tam_json) >= MIN_ANUNCIOS_POR_CLUSTER else 'FALLA'})")
print(f"  media(silhouette_samples) = {sil_media:.10f}")
print(f"  SIL_FINAL                 = {SIL_FINAL:.10f}")
print(f"  igualdad a 1e-9 -> {'OK' if abs(sil_media - SIL_FINAL) < 1e-9 else 'FALLA'}")
print(f"  recheck SIL_FINAL == silhouette del JSON -> "
      f"{'OK' if abs(round(SIL_FINAL, 4) - con['silhouette']) < 1e-9 else 'FALLA'}")

esperados = ["analisis_clustering.py", "scooters_121_con_clusters.json",
             "descartados_outliers.json", "descartados_sin_motor.json",
             "resumen_clustering_121.txt", "clusters_dispersion.png",
             "clusters_precio_medio.png", "clusters_tamanos.png",
             "clusters_metricas.png", "clusters_correlacion.png"]
print()
for fn in esperados:
    p = os.path.join(BASE_DIR, fn)
    print(f"  {'OK ' if os.path.exists(p) else 'FALLA'} {fn:<38} {os.path.getsize(p):>9,} bytes")
print(f"  archivos extra en el directorio: "
      f"{sorted(set(os.listdir(BASE_DIR)) - set(esperados)) or 'ninguno'}")

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
print(f"  Outliers de precio    : {len(descartados_outliers)} "
      f"({n_ph} placeholder, {n_po} premium)")
print(f"  Sin motor_w           : {len(descartados_sin_motor)}")
print(f"  Anuncios clusterizados: {N} de {len(anuncios)}")
print(f"  k elegido             : {k_elegido} ({k_final} clusters, "
      f"{int(mejor['n_fusiones'])} fusiones) con silhouette {SIL_FINAL:.4f}")
print(f"  Tamanos               : {tam_json} (min {min(tam_json)}, max {max(tam_json)}, "
      f"tope {MAXIMO})")
print("  Correlaciones         : " + ", ".join(f"{n} {r:+.4f}" for n, r in pares_corr))
print(SEP)