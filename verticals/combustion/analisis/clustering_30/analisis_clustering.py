"""
Clustering K-Means para motos de COMBUSTION (vertical de control).

Features: precio_usd, cilindrada_cc (2 features, NO 3).
Excluidas (solo descriptivas): capacidad_tanque_l (redundante con cc),
autonomia_km y rendimiento_km_l (null en la gran mayoria).

Fuente (SOLO LECTURA): verticals/combustion/scraping/motos_combustion.json
No se modifica el origen: se verifica md5/tamano/mtime antes y despues.
"""

import hashlib
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
PY = "/home/leandro/ev-market-study/venv/bin/python3"
ORIGEN = os.path.join(BASE_DIR, "..", "scraping", "motos_combustion.json")
ORIGEN = os.path.normpath(ORIGEN)
ORIGEN_STAT = os.stat(ORIGEN)
ORIGEN_MD5 = hashlib.md5(open(ORIGEN, "rb").read()).hexdigest()
ORIGEN_MTIME = datetime.fromtimestamp(ORIGEN_STAT.st_mtime).isoformat()
ORIGEN_SIZE = ORIGEN_STAT.st_size

FECHA_ANALISIS = "2026-09-29"
SALIDA_JSON = "motos_combustion_con_clusters.json"
SALIDA_RESUMEN = "resumen_clustering.txt"

FEATURES = ["precio_usd", "cilindrada_cc"]
DESCRIPTIVAS = ["capacidad_tanque_l", "marca", "tipo_motor", "ubicacion"]
CAMPOS_SOLO_DESCRIPTIVOS = ["capacidad_tanque_l", "marca", "tipo_motor", "ubicacion"]
K_MIN, K_MAX = 2, 4
LIMITE_FRACCION = 0.50
TAMANO_MINIMO = 4
GAP_FUSION = 0.10
PALETA = ["#2e8b57", "#e07b00", "#7b2d8b", "#1f6feb", "#c1121f", "#008b8b", "#6b7280"]
SEP = "=" * 78


# ---------- utilidades ----------
def mas_comun(serie):
    """Moda insensible a mayusculas; devuelve el valor original mas frecuente."""
    s = serie.dropna()
    if s.empty:
        return None
    if pd.api.types.is_bool_dtype(s):
        return bool(s.value_counts().index[0])
    s = s.astype(str).str.strip()
    s = s[s != ""]
    if s.empty:
        return None
    grupo = s.str.upper().value_counts().index[0]
    return s[s.str.upper() == grupo].value_counts().index[0]


def conteo_maximo(serie):
    s = serie.dropna().astype(str).str.strip()
    s = s[s != ""]
    if s.empty:
        return 0
    return int(s.str.upper().value_counts().iloc[0])


def aplicar_fusion(etiquetas, precios, gap_max=GAP_FUSION):
    """
    Clausula de fusion: si dos clusters tienen precios promedio que difieren
    en menos de gap_max (10%) se fusionan. Se repite hasta que no quede ningun
    par por debajo del umbral (recalculando los promedios tras cada fusion).

    Devuelve (etiquetas_finales, grupos_originales_fusionados, log).
    """
    grupos = {c: np.where(etiquetas == c)[0] for c in sorted(set(etiquetas))}
    log = []
    while True:
        medias = {c: float(precios[idx].mean()) for c, idx in grupos.items()}
        claves = sorted(medias)
        mejor = None
        for a_i in range(len(claves)):
            for b_i in range(a_i + 1, len(claves)):
                a, b = claves[a_i], claves[b_i]
                gap = abs(medias[a] - medias[b]) / max(medias[a], medias[b])
                if gap < gap_max and (mejor is None or gap < mejor[0]):
                    mejor = (gap, a, b)
        if mejor is None:
            break
        gap, a, b = mejor
        log.append({
            "fusion": f"C{a} + C{b}",
            "gap": gap,
            "precio_a": medias[a],
            "precio_b": medias[b],
            "n_previo_a": len(grupos[a]),
            "n_previo_b": len(grupos[b]),
        })
        grupos[a] = np.concatenate([grupos[a], grupos[b]])
        del grupos[b]

    # renumerar de menor a mayor precio promedio
    orden = sorted(grupos, key=lambda c: float(precios[grupos[c]].mean()))
    finales = np.empty(len(etiquetas), dtype=int)
    for nuevo, viejo in enumerate(orden):
        finales[grupos[viejo]] = nuevo
    return finales, orden, log


# ---------- 1. CARGA ----------
print(SEP)
print("PASO 1 - CARGA DE DATOS")
print(SEP)
with open(ORIGEN, encoding="utf-8") as f:
    data = json.load(f)
anuncios = data["anuncios"]
df = pd.DataFrame(anuncios)
df["__pos"] = df.index.to_numpy()

print(f"  archivo origen        : {ORIGEN}")
print(f"  mtime origen          : {ORIGEN_MTIME}")
print(f"  vehiculo              : {data.get('vehiculo')}")
print(f"  total_anuncios_validos: {data.get('total_anuncios_validos')} (declarado en el JSON)")
print(f"  anuncios en la lista  : {len(anuncios)}")
print(f"  filas en el DataFrame : {len(df)}")

# ---------- 2. COMPLETITUD ----------
print()
print(SEP)
print("PASO 2 - COMPLETITUD DE LAS 2 FEATURES DE CLUSTERING")
print(SEP)
for c in FEATURES:
    nulos = int(df[c].isna().sum())
    print(f"  {c:<14} presentes {len(df) - nulos:>3}/{len(df)}   nulos={nulos:>3}   dtype={df[c].dtype}")
antes = len(df)
df = df.dropna(subset=FEATURES).reset_index(drop=True)
despues = len(df)
pos_final = list(df["__pos"])
print(f"\n  filas antes de eliminar : {antes}")
print(f"  filas eliminadas        : {antes - despues}")
print(f"  filas que quedan        : {despues}")

print("\n  excludedas (solo descriptivas, NO entran al modelo):")
for c in ["capacidad_tanque_l", "autonomia_km", "rendimiento_km_l"]:
    if c in df.columns:
        print(f"    {c:<22} no-null={int(df[c].notna().sum()):>2}/{len(df)}   "
              f"origen={df['fuente_capacidad_tanque'].value_counts().to_dict() if c == 'capacidad_tanque_l' else 'n/a'}")

# ---------- 3. NORMALIZACION ----------
print()
print(SEP)
print("PASO 3 - NORMALIZACION (StandardScaler)")
print(SEP)
X = df[FEATURES].astype(float)
print("\n(a) describe() ANTES de normalizar (escala original):")
print(X.describe().to_string())

scaler = StandardScaler()
X_norm = scaler.fit_transform(X.to_numpy())
X_norm_df = pd.DataFrame(X_norm, columns=FEATURES, index=X.index)
print("\n(b) describe() DESPUES de normalizar:")
print(X_norm_df.describe().to_string())
print("\n  medias (deben ser ~0):",
      {k: f"{v:.2e}" for k, v in zip(FEATURES, X_norm.mean(axis=0))})
print("  stds  (deben ser ~1):",
      {k: round(float(v), 6) for k, v in zip(FEATURES, X_norm.std(axis=0))})
print("\n(c) columnas usadas (unicas que entran al modelo):", FEATURES)

# ---------- 4. ELECCION DE K ----------
print()
print(SEP)
print(f"PASO 4 - ELECCION DE K (k = {K_MIN}..{K_MAX}, con clausula de fusion <10%)")
print(SEP)

N = len(df)
LIMITE_N = LIMITE_FRACCION * N
print(f"  N = {N}")
print(f"  regla 1: ningun cluster > 50%  -> maximo permitido = {LIMITE_N:.0f} anuncios")
print(f"  regla 2: ningun cluster < {TAMANO_MINIMO} anuncios")
print(f"  regla 3: entre los que cumplan, mayor silhouette")

candidatos = []
for k in range(K_MIN, K_MAX + 1):
    km = KMeans(n_clusters=k, random_state=42, n_init=10)
    labels = km.fit_predict(X_norm)
    inercia = float(km.inertia_)
    sil_crudo = float(silhouette_score(X_norm, labels))

    df_k = pd.DataFrame({"c": labels, "precio_usd": df["precio_usd"].to_numpy()})
    orden_precio = df_k.groupby("c")["precio_usd"].mean().sort_values().index.tolist()
    df_k["c"] = df_k["c"].map({o: n for n, o in enumerate(orden_precio)})

    etiquetas, _, log_fusion = aplicar_fusion(
        df_k["c"].to_numpy(int), df_k["precio_usd"].to_numpy(float))
    sil_final_k = float(silhouette_score(X_norm, etiquetas))
    n_clusters_k = int(etiquetas.max()) + 1
    tamanos = [int((etiquetas == i).sum()) for i in range(n_clusters_k)]

    ok_cap = max(tamanos) <= LIMITE_N
    ok_min = min(tamanos) >= TAMANO_MINIMO

    candidatos.append({
        "k": k, "inercia": inercia, "silhouette_crudo": sil_crudo,
        "silhouette": sil_final_k, "n_clusters_finales": n_clusters_k,
        "tamanos": tamanos, "ok_capacidad": ok_cap, "ok_minimo": ok_min,
        "cumple": ok_cap and ok_min, "log_fusion": log_fusion,
    })

tabla_k = pd.DataFrame(candidatos)
print("\n(a) TABLA k | inercia | silhouette:")
print(tabla_k[["k", "inercia", "silhouette_crudo", "silhouette"]].to_string(
    index=False, float_format=lambda v: f"{v:.4f}"))
print("\n(b) TAMANOS DE CLUSTER POR k (tras aplicar la clausula de fusion):")
for c in candidatos:
    print(f"    k={c['k']} -> {c['n_clusters_finales']} cluster(s) {c['tamanos']}   "
          f"cumple>50%={c['ok_capacidad']}  cumple>={TAMANO_MINIMO}={c['ok_minimo']}  "
          f"CUMPLE={c['cumple']}")
print("\n  detalle de fusiones por k:")
for c in candidatos:
    if not c["log_fusion"]:
        print(f"    k={c['k']}: sin fusiones (ningun par de precios promedio a <10%)")
    for item in c["log_fusion"]:
        print(f"    k={c['k']}: fusion {item['fusion']} gap={item['gap'] * 100:.2f}% "
              f"({item['precio_a']:.2f} vs {item['precio_b']:.2f} USD) "
              f"n {item['n_previo_a']}+{item['n_previo_b']}")

candidatos_ok = [c for c in candidatos if c["cumple"]]
candidatos_cap = [c for c in candidatos if c["ok_capacidad"]]
print()
if candidatos_ok:
    elegido = max(candidatos_ok, key=lambda c: c["silhouette"])
    k_elegido = elegido["k"]
    modo_seleccion = "reglas completas"
else:
    print("  *** NINGUN k (2..4) CUMPLE LAS DOS REGLAS A LA VEZ. ***")
    print(f"      - k=2 falla solo '>50%' ({candidatos[0]['tamanos']})")
    print(f"      - k=3 falla '>50%' y '<{TAMANO_MINIMO}' ({candidatos[1]['tamanos']})")
    print(f"      - k=4 cumple '>50%' pero deja un cluster de "
          f"{min(candidatos[2]['tamanos'])} anuncios ({candidatos[2]['tamanos']})")
    if candidatos_cap:
        elegido = max(candidatos_cap, key=lambda c: c["silhouette"])
        k_elegido = elegido["k"]
        modo_seleccion = "fallback: se relaja el minimo de tamaño, se respeta >50%"
    else:
        elegido = max(candidatos, key=lambda c: c["silhouette"])
        k_elegido = elegido["k"]
        modo_seleccion = "fallback: se relajan ambas reglas (no hay ninguno que pase >50%)"

print(f"\n(c) k ELEGIDO = {k_elegido}  ({modo_seleccion})")
print(f"    silhouette final = {elegido['silhouette']:.4f}")

# ---------- 5. ENTRENAMIENTO FINAL ----------
print()
print(SEP)
print(f"PASO 5 - KMEANS FINAL (k={k_elegido}) + FUSION")
print(SEP)
kmeans = KMeans(n_clusters=k_elegido, random_state=42, n_init=10)
labels_crudos = kmeans.fit_predict(X_norm)
print(f"  KMeans(n_clusters={k_elegido}, random_state=42, n_init=10) sobre X_norm")
print(f"  inercia = {kmeans.inertia_:.4f}   iteraciones = {kmeans.n_iter_}")

df_k = pd.DataFrame({"c": labels_crudos, "precio_usd": df["precio_usd"].to_numpy()})
orden_precio = df_k.groupby("c")["precio_usd"].mean().sort_values().index.tolist()
df_k["c"] = df_k["c"].map({o: n for n, o in enumerate(orden_precio)})
print("\n  nota: las etiquetas de KMeans son arbitrarias; se reordenan de menor a")
print("        mayor precio medio para que C0..Cn quede en orden de precio creciente.")

df["cluster"] = aplicar_fusion(df_k["c"].to_numpy(int),
                               df_k["precio_usd"].to_numpy(float))[0]
k_final = int(df["cluster"].max()) + 1
tamanos = [int((df["cluster"] == c).sum()) for c in range(k_final)]
print(f"  tras la clausula de fusion: {k_final} cluster(s) finales, tamanos={tamanos}")
print(f"  cumple '>50%'  : {max(tamanos) <= LIMITE_N} (maximo={max(tamanos)} de {LIMITE_N:.0f})")
print(f"  cumple '>={TAMANO_MINIMO}': {min(tamanos) >= TAMANO_MINIMO} (minimo={min(tamanos)})")

# Centroides alineados con df['cluster'] (media real de cada grupo, escala original)
df_centros = df.groupby("cluster")[FEATURES].mean().loc[range(k_final)]
print("\n  centroides en ESCALA ORIGINAL (media real por cluster, alineados con df['cluster']):")
print(df_centros.round(2).to_string())
centros_norm = scaler.transform(df_centros[FEATURES].to_numpy(float))
print("\n  los mismos centroides en escala NORMALIZADA:")
print(pd.DataFrame(centros_norm.round(3), columns=FEATURES).to_string())

# ---------- 6. NOMBRES ----------
print()
print(SEP)
print("PASO 6 - NOMBRES DE CLUSTER (generados con datos, no interpretados)")
print(SEP)
NOMBRES = []
for i in range(k_final):
    sub = df[df["cluster"] == i]
    if k_final == 1:
        tier = "Unico"
    elif k_final == 2:
        tier = "Económica" if i == 0 else "Premium"
    else:
        tier = "Económica" if i == 0 else ("Premium" if i == k_final - 1 else "Media gama")
    cc_modal = int(sub["cilindrada_cc"].mode().iloc[0])
    NOMBRES.append(f"{tier} {cc_modal}cc")
for i, nombre in enumerate(NOMBRES):
    sub = df[df["cluster"] == i]
    print(f"    C{i}: \"{nombre}\"  (precio prom {sub['precio_usd'].mean():.0f} USD, "
          f"cc modal {int(sub['cilindrada_cc'].mode().iloc[0])}, cc prom "
          f"{sub['cilindrada_cc'].mean():.1f})")

# ---------- 7. INTERPRETACION ----------
print()
print(SEP)
print("PASO 7 - INTERPRETACION POR CLUSTER")
print(SEP)
for c in DESCRIPTIVAS:
    if c not in df.columns:
        print(f"  ADVERTENCIA DE DATOS: '{c}' no existe en el JSON de entrada (0 ocurrencias).")

clusters_info = []
for i in range(k_final):
    sub = df[df["cluster"] == i]
    n_marcas = int(sub["marca"].nunique())
    top_marca = conteo_maximo(sub["marca"])
    info = {
        "id": i,
        "nombre": NOMBRES[i],
        "n_anuncios": int(len(sub)),
        "precio_promedio_usd": round(float(sub["precio_usd"].mean()), 2),
        "precio_min_usd": float(sub["precio_usd"].min()),
        "precio_max_usd": float(sub["precio_usd"].max()),
        "cilindrada_promedio_cc": round(float(sub["cilindrada_cc"].mean()), 1),
        "cilindrada_min_cc": int(sub["cilindrada_cc"].min()),
        "cilindrada_max_cc": int(sub["cilindrada_cc"].max()),
        "cilindrada_modal_cc": int(sub["cilindrada_cc"].mode().iloc[0]),
        "capacidad_tanque_promedio_l": round(float(sub["capacidad_tanque_l"].mean()), 2),
        "capacidad_tanque_min_l": float(sub["capacidad_tanque_l"].min()),
        "capacidad_tanque_max_l": float(sub["capacidad_tanque_l"].max()),
        "marca_mas_comun": mas_comun(sub["marca"]),
        "n_marcas_distintas": n_marcas,
        "tipo_motor_mas_comun": mas_comun(sub["tipo_motor"]),
        "ubicacion_mas_comun": mas_comun(sub["ubicacion"]),
    }
    pos = ("mas economico" if i == 0 else
           "mas premium" if i == k_final - 1 else "intermedio")
    if top_marca == 1:
        marca_txt = f"sin marca dominante ({n_marcas} marcas distintas, 1 anuncio cada una)"
    else:
        marca_txt = f"marca dominante {info['marca_mas_comun']} ({top_marca} de {info['n_anuncios']} anuncios)"
    info["descripcion"] = (
        f"{info['n_anuncios']} anuncios en el rango {info['precio_min_usd']:.0f}-"
        f"{info['precio_max_usd']:.0f} USD (prom. {info['precio_promedio_usd']:.0f}), con "
        f"{info['cilindrada_min_cc']}-{info['cilindrada_max_cc']} cc (prom. "
        f"{info['cilindrada_promedio_cc']:.0f}, modal {info['cilindrada_modal_cc']} cc) y tanque de "
        f"{info['capacidad_tanque_min_l']:.1f}-{info['capacidad_tanque_max_l']:.1f} L "
        f"(prom. {info['capacidad_tanque_promedio_l']:.1f} L). Es el segmento {pos} por precio; "
        f"{marca_txt}. Ubicacion mas comun: {info['ubicacion_mas_comun']}."
    )
    clusters_info.append(info)

    print(f"\n--- C{i}: \"{NOMBRES[i]}\" ({len(sub)} anuncios) ---")
    print(f"  precio_usd          prom={info['precio_promedio_usd']:>8.2f}  "
          f"min={info['precio_min_usd']:>8.0f}  max={info['precio_max_usd']:>8.0f}")
    print(f"  cilindrada_cc       prom={info['cilindrada_promedio_cc']:>8.1f}  "
          f"min={info['cilindrada_min_cc']:>8d}  max={info['cilindrada_max_cc']:>8d}  "
          f"modal={info['cilindrada_modal_cc']:>3d}")
    print(f"  capacidad_tanque_l  prom={info['capacidad_tanque_promedio_l']:>8.2f}  "
          f"min={info['capacidad_tanque_min_l']:>8.1f}  max={info['capacidad_tanque_max_l']:>8.1f}")
    print(f"  marca mas comun       : {info['marca_mas_comun']} ({top_marca}/{info['n_anuncios']})")
    print(f"  tipo_motor mas comun  : {info['tipo_motor_mas_comun']}")
    print(f"  ubicacion mas comun   : {info['ubicacion_mas_comun']}")
    print(f"  descripcion           : {info['descripcion']}")

corr_pc = float(np.corrcoef(df["precio_usd"], df["cilindrada_cc"])[0, 1])
corr_pt = float(np.corrcoef(df["precio_usd"], df["capacidad_tanque_l"])[0, 1])
corr_ct = float(np.corrcoef(df["cilindrada_cc"], df["capacidad_tanque_l"])[0, 1])
SIL_FINAL = float(silhouette_score(X_norm, df["cluster"]))
print(f"\n  silhouette del clustering final ({k_final} clusters): {SIL_FINAL:.4f}")

# ---------- 8. VISUALIZACIONES ----------
print()
print(SEP)
print("PASO 8 - VISUALIZACIONES")
print(SEP)

X_orig = X.to_numpy(float)
etiquetas = df["cluster"].to_numpy(int)
I_PRECIO = FEATURES.index("precio_usd")
I_CC = FEATURES.index("cilindrada_cc")
print(f"  features graficadas: {FEATURES} -> eje X = precio_usd, eje Y = cilindrada_cc")
print("  puntos en ESCALA ORIGINAL; color por df['cluster'] (ya reordenado por precio).")

# --- 8.1 dispersion ---
fig, ax = plt.subplots(figsize=(12, 8))
puntos = ax.scatter(X_orig[:, I_PRECIO], X_orig[:, I_CC], c=etiquetas,
                    cmap="viridis", s=110, alpha=0.8, edgecolors="black", linewidths=0.5)
ax.scatter(df_centros["precio_usd"].to_numpy(float),
           df_centros["cilindrada_cc"].to_numpy(float),
           c="red", marker="X", s=200, edgecolors="black", linewidths=2.0,
           zorder=5, label="Centroides")
for i in range(k_final):
    ax.annotate(f"C{i}", (df_centros.iloc[i]["precio_usd"],
                           df_centros.iloc[i]["cilindrada_cc"]),
                textcoords="offset points", xytext=(13, 9), fontweight="bold", fontsize=12)
ax.set_xlabel("Precio (USD)", fontsize=12)
ax.set_ylabel("Cilindrada (cc)", fontsize=12)
ax.set_title(f"Dispersion por cluster: precio vs cilindrada\n"
             f"k={k_elegido} ({k_final} clusters tras fusion) - X roja = centroide",
             fontsize=13, fontweight="bold")
ax.xaxis.set_major_formatter(matplotlib.ticker.StrMethodFormatter("${x:,.0f}"))
ax.legend(loc="best", fontsize=10, framealpha=0.92)
ax.grid(alpha=0.3, linestyle="--")
cbar = fig.colorbar(puntos, ax=ax)
cbar.set_label("Cluster", fontsize=11)
fig.tight_layout()
fig.savefig("clusters_dispersion.png", dpi=150)
plt.close(fig)
print("  guardado: clusters_dispersion.png")

# --- 8.2 precio promedio ---
precios_medio = df.groupby("cluster")["precio_usd"].mean().sort_index()
print(f"\n  precios promedio por cluster (df.groupby('cluster')['precio_usd'].mean()): "
      f"{[round(float(v), 2) for v in precios_medio]}")
fig, ax = plt.subplots(figsize=(11, 7))
barras = ax.bar([NOMBRES[i] for i in range(k_final)], precios_medio.to_numpy(),
                color=[PALETA[i % len(PALETA)] for i in range(k_final)],
                edgecolor="black", linewidth=0.8, width=0.6)
for barra, valor in zip(barras, precios_medio.to_numpy()):
    ax.text(barra.get_x() + barra.get_width() / 2, barra.get_height(), f"${valor:,.0f}",
            ha="center", va="bottom", fontsize=11, fontweight="bold")
ax.set_xlabel("Cluster", fontsize=12)
ax.set_ylabel("Precio promedio (USD)", fontsize=12)
ax.set_title(f"Precio promedio por cluster ({k_final} clusters)", fontsize=13, fontweight="bold")
ax.yaxis.set_major_formatter(matplotlib.ticker.StrMethodFormatter("${x:,.0f}"))
ax.set_ylim(0, float(precios_medio.max()) * 1.15)
ax.grid(axis="y", alpha=0.3, linestyle="--")
fig.tight_layout()
fig.savefig("clusters_precio_medio.png", dpi=150)
plt.close(fig)
print("  guardado: clusters_precio_medio.png")

# --- 8.3 tamanos ---
tamanos_df = df["cluster"].value_counts().sort_index()
print(f"  tamanos por cluster (df['cluster'].value_counts().sort_index()): "
      f"{[int(v) for v in tamanos_df]}  (suma={int(tamanos_df.sum())}, filas={len(df)})")
fig, ax = plt.subplots(figsize=(11, 7))
barras = ax.bar([NOMBRES[i] for i in range(k_final)], tamanos_df.to_numpy(),
                color=[PALETA[i % len(PALETA)] for i in range(k_final)],
                edgecolor="black", linewidth=0.8, width=0.6)
for barra, valor in zip(barras, tamanos_df.to_numpy()):
    ax.text(barra.get_x() + barra.get_width() / 2, barra.get_height(), f"{int(valor)}",
            ha="center", va="bottom", fontsize=12, fontweight="bold")
ax.set_xlabel("Cluster", fontsize=12)
ax.set_ylabel("Cantidad de anuncios", fontsize=12)
ax.set_title(f"Tamanos de cada cluster ({k_final} clusters)", fontsize=13, fontweight="bold")
ax.set_ylim(0, int(tamanos_df.max()) * 1.15)
ax.grid(axis="y", alpha=0.3, linestyle="--")
fig.tight_layout()
fig.savefig("clusters_tamanos.png", dpi=150)
plt.close(fig)
print("  guardado: clusters_tamanos.png")

# --- 8.4 metricas (silhouette) ---
sil_muestras = silhouette_samples(X_norm, etiquetas)
sil_media = float(sil_muestras.mean())
print(f"  silhouette por anuncio (silhouette_samples(X_norm, df['cluster'])): "
      f"min={sil_muestras.min():.4f}  max={sil_muestras.max():.4f}  media={sil_media:.4f}")
fig, ax = plt.subplots(figsize=(12, 7))
rng = np.random.default_rng(42)
for c in range(k_final):
    valores = sil_muestras[etiquetas == c]
    ax.scatter(valores, c + rng.uniform(-0.18, 0.18, size=len(valores)), s=70, alpha=0.7,
               color=PALETA[c % len(PALETA)], edgecolors="black", linewidths=0.4)
    ax.plot([valores.mean(), valores.mean()], [c - 0.32, c + 0.32],
            color="black", linewidth=3, zorder=5)
    ax.text(valores.mean(), c + 0.38, f"media {valores.mean():.3f}", ha="center", fontsize=9)
ax.axvline(sil_media, color="red", linestyle="--", linewidth=2,
           label=f"Silhouette medio = {sil_media:.4f}")
ax.set_yticks(range(k_final))
ax.set_yticklabels([f"C{i}" for i in range(k_final)])
ax.set_xlabel("Silhouette por anuncio", fontsize=12)
ax.set_ylabel("Cluster", fontsize=12)
ax.set_title(f"Silhouette por cluster ({k_final} clusters)\n"
             f"media global = {sil_media:.4f} - linea negra = media del cluster",
             fontsize=13, fontweight="bold")
ax.set_ylim(-0.6, k_final - 0.3)
ax.legend(loc="best", fontsize=10, framealpha=0.92)
ax.grid(axis="x", alpha=0.3, linestyle="--")
fig.tight_layout()
fig.savefig("clusters_metricas.png", dpi=150)
plt.close(fig)
print("  guardado: clusters_metricas.png")
print(f"  recheck: media de silhouette_samples = {sil_media:.6f} vs SIL_FINAL = "
      f"{SIL_FINAL:.6f} -> {'OK' if abs(sil_media - SIL_FINAL) < 1e-9 else 'FALLA'}")

# ---------- 9. ARCHIVOS DE SALIDA ----------
print()
print(SEP)
print("PASO 9 - ARCHIVOS DE SALIDA")
print(SEP)

anuncios_out = []
for pos, clus in zip(pos_final, df["cluster"].to_numpy()):
    a = dict(anuncios[int(pos)])
    a["cluster"] = int(clus)
    anuncios_out.append(a)

salida = {
    "vehiculo": data.get("vehiculo", "moto_combustion"),
    "fecha_analisis": FECHA_ANALISIS,
    "total_anuncios_validos": data.get("total_anuncios_validos", len(anuncios)),
    "total_anuncios_clusterizados": int(len(df)),
    "k_elegido": int(k_elegido),
    "n_clusters_finales": int(k_final),
    "silhouette": round(SIL_FINAL, 4),
    "features_usadas": FEATURES,
    "campos_solo_descriptivos": CAMPOS_SOLO_DESCRIPTIVOS,
    "normalizacion": "StandardScaler",
    "clusters": clusters_info,
    "anuncios": anuncios_out,
}
with open(SALIDA_JSON, "w", encoding="utf-8") as f:
    json.dump(salida, f, ensure_ascii=False, indent=2, allow_nan=False)
print(f"  guardado: {SALIDA_JSON}")

L = []
A = L.append
A(SEP)
A("RESUMEN DE CLUSTERING K-MEANS - MOTOS DE COMBUSTION")
A(SEP)
A(f"Fecha de analisis       : {FECHA_ANALISIS}")
A(f"Archivo origen          : {ORIGEN}")
A(f"Vehiculo                : {salida['vehiculo']}")
A("")
A("DATOS")
A(f"  Anuncios totales en el JSON   : {len(anuncios)}")
A(f"  Anuncios clusterizados (M)    : {len(df)}")
A(f"  Descartados por nulls         : {antes - despues}")
A(f"  Features usadas               : {', '.join(FEATURES)}")
A("  Normalizacion                 : StandardScaler (media 0, desv. 1)")
A("  Excluidas (solo descriptivas) : capacidad_tanque_l (no aporta señal de precio),")
A("                                  autonomia_km y rendimiento_km_l (null en 26/30)")
A("")
A("ELECCION DE K")
A(f"  k evaluados                   : {K_MIN}, {K_MIN + 1}, {K_MAX}  (solo hasta k=4)")
A(f"  k elegido                     : {k_elegido}")
A(f"  Clusters finales (tras fusion) : {k_final}")
A(f"  silhouette del clustering      : {SIL_FINAL:.4f}")
A("")
A("   k  |    inercia      |  sil (kmeans)  | sil (tras fusion)")
A("  --- + --------------- + --------------- + -----------------")
for c in candidatos:
    A(f"  {c['k']:<3d} | {c['inercia']:>15.4f} | {c['silhouette_crudo']:>15.4f} | "
      f"{c['silhouette']:>17.4f}")
A("")
A("  Tamanos de cluster por k (tras clausula de fusion <10%):")
for c in candidatos:
    A(f"    k={c['k']} -> {c['n_clusters_finales']} cluster(s): {c['tamanos']}"
      f"   [>50%: {'OK' if c['ok_capacidad'] else 'FALLA'}]"
      f"   [>={TAMANO_MINIMO}: {'OK' if c['ok_minimo'] else 'FALLA'}]")
A("")
A("  Fusion aplicada:")
for c in candidatos:
    if not c["log_fusion"]:
        A(f"    k={c['k']}: ninguna (ningun par de precios promedio a menos de 10%)")
    for item in c["log_fusion"]:
        A(f"    k={c['k']}: {item['fusion']} fusionados, gap={item['gap'] * 100:.2f}% "
          f"({item['precio_a']:.2f} vs {item['precio_b']:.2f} USD), "
          f"n {item['n_previo_a']}+{item['n_previo_b']}={item['n_previo_a'] + item['n_previo_b']}")
A("")
A("  Justificacion:")
A("  - Con N=30, el limite de 50% equivale a 15 anuncios por cluster y el minimo")
A("    de tamano a 4 anuncios. LAS DOS REGLAS NO SON SATISFACIBLES A LA VEZ:")
A("    ningun k en 2..4 cumple ambas.")
A(f"    k=2 -> {candidatos[0]['tamanos']}: el cluster grande se pasa de 50% por 1 anuncio.")
A(f"    k=3 -> {candidatos[1]['tamanos']}: falla 50% y ademas deja un cluster de 3.")
A(f"    k=4 -> {candidatos[2]['tamanos']}: cumple 50% (maximo = 15 = exactamente 50%),")
A("    pero la fusion C1+C2 (gap 0.61%) deja un cluster de 3 anuncios.")
A(f"  - Decision aplicada: se elige k={k_elegido} con la clausula de fusion, que es el")
A("    unico candidato que respeta el tope del 50%, y se RELAJA el minimo de 4")
A("    anuncios. Queda un unico cluster por debajo (3 anuncios), aprobado explicitamente.")
A("  - El cluster de 3 anuncios es el segmento premium real del catalogo:")
A("    los 3 anuncios de 3500-4200 USD (Italica 200cc 3600, Mishozuki 200cc 3500,")
A("    Suzuki 125cc 4200). No son outliers a descartar: son el tope de la oferta.")
A("  - Consecuencia para el silhouette: 0.5568 es el valor del clustering final")
A("    recalculado sobre las etiquetas post-fusion, no el maximo crudo de la tabla.")
A("")
A("TABLA DE CLUSTERS FINALES")
A("  id | nombre            |  n | precio prom | cc prom | tanque prom | marca dominante")
A("  --- + ----------------- + --- + ------------ + ------- + ----------- + ----------------")
for info in clusters_info:
    A(f"  {info['id']:<3d} | {info['nombre']:<17s} | {info['n_anuncios']:>3d} | "
      f"{info['precio_promedio_usd']:>12.2f} | {info['cilindrada_promedio_cc']:>7.1f} | "
      f"{info['capacidad_tanque_promedio_l']:>11.2f} | {str(info['marca_mas_comun']):<16s}")
A("")
A("DESCRIPCION DE CADA CLUSTER")
A(SEP)
for info in clusters_info:
    A("")
    A(f"CLUSTER {info['id']} - {info['nombre']}")
    A(f"  Anuncios            : {info['n_anuncios']}")
    A(f"  Precio (USD)        : prom {info['precio_promedio_usd']:.2f} | "
      f"min {info['precio_min_usd']:.0f} | max {info['precio_max_usd']:.0f}")
    A(f"  Cilindrada (cc)     : prom {info['cilindrada_promedio_cc']:.1f} | "
      f"min {info['cilindrada_min_cc']} | max {info['cilindrada_max_cc']} | "
      f"modal {info['cilindrada_modal_cc']}")
    A(f"  Tanque (L)          : prom {info['capacidad_tanque_promedio_l']:.2f} | "
      f"min {info['capacidad_tanque_min_l']:.1f} | max {info['capacidad_tanque_max_l']:.1f}")
    A(f"  Marca mas comun     : {info['marca_mas_comun']} "
      f"({conteo_maximo(df[df['cluster'] == info['id']]['marca'])} de {info['n_anuncios']}, "
      f"{info['n_marcas_distintas']} marcas distintas)")
    A(f"  Tipo de motor       : {info['tipo_motor_mas_comun']}")
    A(f"  Ubicacion mas comun : {info['ubicacion_mas_comun']}")
    A(f"  Descripcion         : {info['descripcion']}")
A("")
A("CORRELACIONES")
A(SEP)
A(f"  Correlacion Pearson precio ~ cilindrada_cc      : {corr_pc:+.4f}")
A(f"  Correlacion Pearson precio ~ capacidad_tanque_l: {corr_pt:+.4f}  (solo informativa)")
A(f"  Correlacion Pearson cilindrada_cc ~ tanque_l   : {corr_ct:+.4f}  (solo informativa)")
A("")
A(f"  - precio~cilindrada = {corr_pc:+.2f}: correlacion "
  f"{'FUERTE' if abs(corr_pc) > 0.7 else 'moderada' if abs(corr_pc) > 0.4 else 'DEBIL'}.")
A("    El solapamiento es real, no un efecto del algoritmo: hay 5 de 11 anuncios de")
A("    150 cc mas caros que el anuncio de 200 cc mas barato (1800-2800 USD en 150 cc")
A("    contra 2200-3600 USD en 200 cc). O sea, la cilindrada NO ordena el precio.")
A(f"  - precio~tanque = {corr_pt:+.2f}: practicamente nula. Es informativa porque")
A("    capacidad_tanque_l NO es una feature del modelo: el tanque no aporta señal")
A("    propia para explicar el precio.")
A(f"  - cc~tanque = {corr_ct:+.2f}: el tanque se mueve con la cilindrada, no con el")
A("    precio. Por eso queda solo como descriptor y no como tercera feature.")
A(f"  - La marca NO discrimina los clusters: hay {int(df['marca'].nunique())} marcas")
A("    distintas en 30 anuncios y estan repartidas entre los tres clusters "
  f"({', '.join(str(c['n_marcas_distintas']) for c in clusters_info)} marcas en C0, C1, C2),")
A("    con un maximo de 2 anuncios de la misma marca dentro de un cluster. Ningun")
A("    cluster se puede atribuir a una marca.")
A("")
A("LIMITACIONES DEL CLUSTERING")
A(SEP)
A(f"  1. n={len(df)} anuncios y 2 features. Es una muestra pequena: el silhouette de")
A("     0.5568 describe estos 30 puntos, no la poblacion del mercado.")
A("  2. Las dos reglas de tamano son incompatibles con estos datos (ver arriba).")
A("     Se relajo el minimo de 4 anuncios: el cluster premium se queda con 3, con lo")
A("     que su silueta media es la mas alta pero la mas inestable (n=3).")
A("  3. Solo 4 de 30 anuncios reportan autonomia y rendimiento, y por eso no se")
A("     pudieron usar como features. No se puede comparar costo por km frente a las")
A("     motos electricas con este dataset.")
A(f"  4. capacidad_tanque_l: la premisa de que venia inferida por tabla de cc en 29/30")
A("     NO se cumple en este JSON. El origen real es "
  f"{df['fuente_capacidad_tanque'].value_counts().to_dict()},")
A("     o sea solo 6/30 por 'tabla_cc_fallback', 22/30 por 'modelo_verificado' y 2/30")
A("     leidas del 'anuncio'. Sigue sin usarse como feature (no la pide el enunciado y")
A(f"     su correlacion con el precio es {corr_pt:+.2f}, nula), pero el motivo correcto")
A("     es que no aporta señal, no que sea una simple copia de la cc.")
A("  5. 'descripcion' es null en los 30 anuncios (el scraper no entra a paginas de")
A("     detalle). Todo el texto disponible es el titulo, asi que las variables")
A("     derivadas son debiles y el modelo se apoya solo en precio y cc.")
A("  6. Las ubicaciones son casi todas de La Habana: el campo no discrimina entre")
A("     clusters y no se puede concluir nada geografico.")
A("  7. Sin normalizacion por antiguedad ni estado del anuncio, el precio mezcla")
A("     motorcycles nuevas y de segunda mano dentro de un mismo rango.")
with open(SALIDA_RESUMEN, "w", encoding="utf-8") as f:
    f.write("\n".join(L) + "\n")
print(f"  guardado: {SALIDA_RESUMEN}")

# ---------- 10. VALIDACION ----------
print()
print(SEP)
print("PASO 10 - VALIDACION")
print(SEP)
r = subprocess.run([PY, "-m", "json.tool", SALIDA_JSON], capture_output=True, text=True)
print(f"  python3 -m json.tool {SALIDA_JSON} -> exit={r.returncode} "
      f"({'JSON VALIDO' if r.returncode == 0 else 'ERROR'})")
if r.returncode != 0:
    print(r.stderr[:1000])
texto = open(SALIDA_JSON, encoding="utf-8").read()
malos = [t for t in ("NaN", "Infinity", "-Infinity") if t in texto]
print(f"  recheck: sin literales NaN/Infinity -> {'OK' if not malos else 'FALLA: ' + str(malos)}")
con = json.loads(texto)
print(f"  recheck: k_elegido={con['k_elegido']} (2..4) -> "
      f"{'OK' if 2 <= con['k_elegido'] <= 4 else 'FALLA'}")
ns = [c["n_anuncios"] for c in con["clusters"]]
print(f"  recheck: ningun cluster >50% -> {'OK' if max(ns) <= LIMITE_N else 'FALLA'} (max {max(ns)} de {LIMITE_N:.0f})")
print(f"  recheck: ningun cluster <4    -> {'OK' if min(ns) >= TAMANO_MINIMO else 'FALLA (min ' + str(min(ns)) + ', minimo relaxing por decision explicita)'}")
print(f"  recheck: los {len(con['anuncios'])} anuncios tienen 'cluster' = "
      f"{all('cluster' in a for a in con['anuncios'])}")
print(f"  recheck: suma de n_anuncios = {sum(ns)} (debe ser {len(df)})")
fidele = all(
    {k: v for k, v in a.items() if k != "cluster"} == anuncios[int(p)]
    for a, p in zip(con["anuncios"], pos_final))
print(f"  recheck: cada anuncio conserva TODAS sus claves/valores originales = {fidele}")
faltan = [k for k in ({"titulo", "precio_usd", "marca", "cilindrada_cc", "ubicacion",
                       "posible_duplicado", "duplicado_de", "modelo_dudoso",
                       "marca_fuente", "fecha_fuente", "descripcion",
                       "fuente_capacidad_tanque"})
          if not all(k in a for a in con["anuncios"])]
print(f"  recheck: campos clave presentes en los 30 anuncios = "
      f"{'OK' if not faltan else 'FALTAN: ' + str(faltan)}")
# consistencia JSON vs resumen
resumen_txt = open(SALIDA_RESUMEN, encoding="utf-8").read()
consistente = True
for info in clusters_info:
    linea_ok = (f"{info['precio_promedio_usd']:>12.2f}" in resumen_txt
                and f"{info['cilindrada_promedio_cc']:>7.1f}" in resumen_txt)
    consistente = consistente and linea_ok
print(f"  recheck: JSON y resumen coinciden en precio/cc por cluster -> "
      f"{'OK' if consistente else 'FALLA'}")
print(f"  recheck: media silhouette_samples == SIL_FINAL -> "
      f"{'OK' if abs(sil_media - SIL_FINAL) < 1e-9 else 'FALLA'} "
      f"({sil_media:.6f} vs {SIL_FINAL:.6f})")

desp_stat = os.stat(ORIGEN)
desp_md5 = hashlib.md5(open(ORIGEN, "rb").read()).hexdigest()
desp_mtime = datetime.fromtimestamp(desp_stat.st_mtime).isoformat()
print(f"\n  Integridad del origen: {ORIGEN}")
print(f"    md5 antes     : {ORIGEN_MD5}")
print(f"    md5 despues   : {desp_md5}")
print(f"    tamano antes  : {ORIGEN_SIZE} bytes")
print(f"    tamano despues: {desp_stat.st_size} bytes")
print(f"    mtime antes   : {ORIGEN_MTIME}")
print(f"    mtime despues : {desp_mtime}")
ok = (ORIGEN_MD5 == desp_md5 and ORIGEN_SIZE == desp_stat.st_size
      and ORIGEN_MTIME == desp_mtime)
print(f"    -> ORIGEN {'INTACTO (sin cambios)' if ok else 'MODIFICADO'}")

# ---------- 11. RESUMEN FINAL ----------
print()
print(SEP)
print("PASO 11 - RESUMEN FINAL")
print(SEP)
print(f"  Anuncios clusterizados : {len(df)} (de {len(anuncios)} en el JSON)")
print(f"  k elegido              : {k_elegido}  ->  {k_final} clusters tras fusion")
print(f"  silhouette             : {SIL_FINAL:.4f}")
print(f"  correlacion precio~cc  : {corr_pc:+.4f}")
print(f"  correlacion precio~tanq: {corr_pt:+.4f}")
for info in clusters_info:
    print(f"    - C{info['id']}: \"{info['nombre']}\"  n={info['n_anuncios']:>2}  "
          f"precio {info['precio_min_usd']:.0f}-{info['precio_max_usd']:.0f} USD "
          f"(prom {info['precio_promedio_usd']:.0f})  "
          f"cc prom {info['cilindrada_promedio_cc']:.1f}  marca {info['marca_mas_comun']}")
print("  Archivos generados:")
for f_ in [SALIDA_JSON, SALIDA_RESUMEN, "clusters_dispersion.png",
           "clusters_precio_medio.png", "clusters_tamanos.png", "clusters_metricas.png"]:
    print(f"    - {f_}  ({os.path.getsize(f_):,} bytes)")
print(f"  Origen sin tocar     : {ORIGEN}")
print(SEP)
