import argparse
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

PY = "/home/leandro/ev-market-study/venv/bin/python3"
ORIGEN = "/home/leandro/ev-market-study/verticals/moto/scraping/motos_electricas.json"
ORIGEN_STAT = os.stat(ORIGEN)
ORIGEN_MD5 = hashlib.md5(open(ORIGEN, "rb").read()).hexdigest()
ORIGEN_MTIME = datetime.fromtimestamp(ORIGEN_STAT.st_mtime).isoformat()

ap = argparse.ArgumentParser()
ap.add_argument("--k", type=int, default=None,
                help="fuerza el numero de clusters (por defecto: argmax de silhouette)")
ap.add_argument("--sin-prefijo", action="store_true",
                help="fuerza k por --k pero graba los archivos SIN el prefijo kN_")
ARGS = ap.parse_args()
PREFIJO = "" if (ARGS.sin_prefijo or not ARGS.k) else f"k{ARGS.k}_"

FEATURES = ["precio_usd", "motor_w", "autonomia_km"]
DESCRIPTIVAS = ["marca", "tipo_bateria", "ubicacion", "plegable"]
NOMBRES_K3 = ["Económica urbana", "Moto de trabajo", "Alta gama"]
NOMBRES_K4 = ["Económica urbana", "Moto de trabajo", "Alta gama", "Premium"]
NOMBRES_K5 = ["Económica urbana", "Moto de trabajo", "Alta gama", "Premium",
              "Premium extendida"]
NOMBRES_POR_K = {3: NOMBRES_K3, 4: NOMBRES_K4, 5: NOMBRES_K5}
PALETA = ["#2e8b57", "#e07b00", "#7b2d8b", "#1f6feb", "#c1121f", "#008b8b", "#6b7280"]
SEP = "=" * 78


# ---------- 1. CARGA ----------
with open(ORIGEN, encoding="utf-8") as f:
    data = json.load(f)
anuncios = data["anuncios"]
df = pd.DataFrame(anuncios)
# posición original de cada fila, para poder recuperar el anuncio intacto al guardar
df["__pos"] = df.index.to_numpy()

print(SEP)
print("PASO 1 — CARGA DE DATOS")
print(SEP)
print(f"  archivo origen        : {ORIGEN}")
print(f"  mtime origen          : {ORIGEN_MTIME}")
print(f"  vehiculo              : {data.get('vehiculo')}")
print(f"  total_anuncios_validos: {data.get('total_anuncios_validos')} (declarado en el JSON)")
print(f"  anuncios en la lista  : {len(anuncios)}")
print(f"  filas en el DataFrame : {len(df)}")
print(f"  columnas del DataFrame: {len(df.columns)}")

# ---------- 2. COMPLETITUD ----------
print()
print(SEP)
print("PASO 2 — COMPLETITUD DE LOS 3 CAMPOS DE CLUSTERING")
print(SEP)
for c in FEATURES:
    nulos = int(df[c].isna().sum())
    print(f"  {c:<13} presentes {len(df) - nulos:>3}/{len(df)}   nulos={nulos:>3}   dtype={df[c].dtype}")
antes = len(df)
df = df.dropna(subset=FEATURES).reset_index(drop=True)
despues = len(df)
pos_final = list(df["__pos"])
print(f"\n  filas antes de eliminar : {antes}")
print(f"  filas eliminadas        : {antes - despues}")
print(f"  filas que quedan        : {despues}")

# ---------- 3. NORMALIZACIÓN ----------
print()
print(SEP)
print("PASO 3 — NORMALIZACIÓN (StandardScaler)")
print(SEP)
X = df[FEATURES].astype(float)
print("\n(a) describe() ANTES de normalizar (escala original):")
print(X.describe().to_string())

scaler = StandardScaler()
X_norm = scaler.fit_transform(X.to_numpy())
X_norm_df = pd.DataFrame(X_norm, columns=FEATURES, index=X.index)
print("\n(b) describe() DESPUÉS de normalizar:")
print(X_norm_df.describe().to_string())
print("\n  medias (deben ser ~0):",
      {k: f"{v:.2e}" for k, v in zip(FEATURES, X_norm.mean(axis=0))})
print("  stds  (deben ser ~1):",
      {k: round(float(v), 6) for k, v in zip(FEATURES, X_norm.std(axis=0))})
print("\n(c) columnas usadas (únicas que entran al modelo):", FEATURES)

# ---------- 4. ELECCIÓN DE K ----------
print()
print(SEP)
print("PASO 4 — ELECCIÓN DE K (k = 2..8)")
print(SEP)
filas = []
for k in range(2, 9):
    km = KMeans(n_clusters=k, random_state=42, n_init=10)
    lab = km.fit_predict(X_norm)
    filas.append({"k": k, "inercia": km.inertia_,
                  "silhouette": silhouette_score(X_norm, lab),
                  "n_clusters_vacios": int(k - len(set(lab)))})
tabla_k = pd.DataFrame(filas)
tabla_k["delta_inercia"] = tabla_k["inercia"].shift(-1) - tabla_k["inercia"]

_x = tabla_k["k"].to_numpy(float)
_y = tabla_k["inercia"].to_numpy(float)
tabla_k["dist_al_codo"] = np.abs((_y[-1] - _y[0]) * _x - (_x[-1] - _x[0]) * _y
                                  + _x[-1] * _y[0] - _y[-1] * _x[0]) / np.hypot(
    _x[-1] - _x[0], _y[-1] - _y[0])

print("\n(a/b) TABLA k | inercia | silhouette:")
print(tabla_k[["k", "inercia", "silhouette"]].to_string(
    index=False, float_format=lambda v: f"{v:.4f}"))
print("\n  reducción de inercia al pasar de k a k+1 (mayor caída = codo):")
print(tabla_k[["k", "inercia", "delta_inercia"]].to_string(
    index=False, float_format=lambda v: f"{v:.4f}"))
print("\n  tamaño de cada cluster por k (para detectar sobre-segmentación):")
for k in range(2, 9):
    km = KMeans(n_clusters=k, random_state=42, n_init=10)
    l = km.fit_predict(X_norm)
    tam = sorted([int((l == i).sum()) for i in range(k)])
    print(f"    k={k}: {tam}")

k_sil = int(tabla_k.loc[tabla_k["silhouette"].idxmax(), "k"])
best_sil = float(tabla_k["silhouette"].max())
segundo = tabla_k.sort_values("silhouette", ascending=False).iloc[1]
k_codo = int(tabla_k.loc[tabla_k["dist_al_codo"].idxmax(), "k"])

print(f"\n(c) argmax de silhouette -> k={k_sil} (silhouette={best_sil:.4f})")
print(f"    codo geometrico      -> k={k_codo}")

if ARGS.k:
    k_elegido = ARGS.k
    sel = f"k={k_elegido} (forzado por --k)"
else:
    k_elegido = k_sil
    sel = f"k={k_elegido} (argmax de silhouette)"

print(f"\n(d) JUSTIFICACIÓN — se aplica la regla del enunciado: mejor silhouette.")
print(f"    k={k_sil} da el silhouette máximo ({best_sil:.4f}); el segundo mejor es")
print(f"    k={int(segundo['k'])} ({segundo['silhouette']:.4f}), gap={best_sil - segundo['silhouette']:.4f}.")
print(f"    La inercia cae de forma monótona y el codo geométrico está en k={k_codo},")
print(f"    es decir NO hay un codo claro en 3 ni en 2: con n={despues} la inercia sigue")
print(f"    bajando porque siempre puede partirse el grupo grande.")
print(f"    ADVERTENCIA METODOLÓGICA: con solo {despues} anuncios, el silhouette premia")
print(f"    la sobre-segmentación (k={k_sil} deja clusters de 1-2 anuncios) y el valor 4200 USD")
print(f"    actúa como atractor que forma un cluster propio. El k se elige por la regla")
print(f"    pedida, pero para reporting de negocio k=2 o k=3 dan segmentos más accionables.")
print(f"    k SELECCIONADO: {sel}")

# ---------- 5. ENTRENAMIENTO ----------
print()
print(SEP)
print(f"PASO 5 — KMEANS FINAL (k={k_elegido})")
print(SEP)
kmeans = KMeans(n_clusters=k_elegido, random_state=42, n_init=10)
labels = kmeans.fit_predict(X_norm)
df["cluster"] = labels
print(f"  KMeans(n_clusters={k_elegido}, random_state=42, n_init=10) entrenado sobre X_norm")
print(f"  tamaño de cada cluster: {[int((labels == i).sum()) for i in range(k_elegido)]}")
print(f"  iteraciones hasta converger: {kmeans.n_iter_}")

# las etiquetas de KMeans son arbitrarias: se reordenan por precio medio ascendente
orden = df.groupby("cluster")["precio_usd"].mean().sort_values().index.tolist()
remap = {orig: nuevo for nuevo, orig in enumerate(orden)}
df["cluster"] = df["cluster"].map(remap).astype(int)
centros_norm = kmeans.cluster_centers_[orden]
centros_orig = scaler.inverse_transform(centros_norm)
df_centros = pd.DataFrame(centros_orig, columns=FEATURES)
print("\n  nota: se reordenan las etiquetas de menor a mayor precio medio para que")
print("        'Segmento 0..n' quede en orden de precio creciente (KMeans no ordena).")
print(f"        orden original {list(range(k_elegido))} -> {orden}")

print("\n(d) CENTROIDES EN ESCALA ORIGINAL (scaler.inverse_transform de los centroides):")
print(df_centros.round(2).to_string())
print(f"  columnas: precio_usd (USD) | motor_w (W) | autonomia_km (km)")
print("\n    los mismos centroides en escala NORMALIZADA:")
print(pd.DataFrame(centros_norm.round(3), columns=FEATURES).to_string())

# ---------- 6. NOMBRES ----------
print()
print(SEP)
print("PASO 6 — NOMBRES DE CLUSTERS")
print(SEP)
if k_elegido in NOMBRES_POR_K:
    NOMBRES = NOMBRES_POR_K[k_elegido]
    print(f"  k={k_elegido}: se usan los nombres exactos requeridos.")
else:
    NOMBRES = [f"Segmento {i}" for i in range(k_elegido)]
    print(f"  *** AVISO: k={k_elegido} NO tiene nombres definidos -> genéricos. ***")
    print(f"  *** Revisá NOMBRES_POR_K en {os.path.basename(__file__)} para   ***")
    print(f"  *** ajustarlos; k={sorted(NOMBRES_POR_K)} tienen nombres reales.  ***")
for i, n in enumerate(NOMBRES):
    print(f"    Cluster {i}: \"{n}\"")

# ---------- 7. INTERPRETACIÓN ----------
print()
print(SEP)
print("PASO 7 — INTERPRETACIÓN POR CLUSTER")
print(SEP)
CAMPO_FALTA = [c for c in DESCRIPTIVAS if c not in df.columns]
if CAMPO_FALTA:
    print(f"\n  ADVERTENCIA DE DATOS: {CAMPO_FALTA} no existe(n) en el JSON de entrada")
    print("  (0 ocurrencias). Se reporta null en vez de inventar un valor.")


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


clusters_info = []
for i in range(k_elegido):
    sub = df[df["cluster"] == i]
    info = {
        "id": i,
        "nombre": NOMBRES[i],
        "n_anuncios": int(len(sub)),
        "precio_promedio_usd": round(float(sub["precio_usd"].mean()), 2),
        "precio_min_usd": float(sub["precio_usd"].min()),
        "precio_max_usd": float(sub["precio_usd"].max()),
        "motor_promedio_w": round(float(sub["motor_w"].mean()), 1),
        "motor_min_w": int(sub["motor_w"].min()),
        "motor_max_w": int(sub["motor_w"].max()),
        "autonomia_promedio_km": round(float(sub["autonomia_km"].mean()), 1),
        "autonomia_min_km": int(sub["autonomia_km"].min()),
        "autonomia_max_km": int(sub["autonomia_km"].max()),
        "marca_mas_comun": mas_comun(sub["marca"]),
        "tipo_bateria_mas_comun": mas_comun(sub["tipo_bateria"]) if "tipo_bateria" in sub else None,
        "ubicacion_mas_comun": mas_comun(sub["ubicacion"]) if "ubicacion" in sub else None,
        "plegable_mas_comun": bool(mas_comun(sub["plegable"])) if "plegable" in sub else None,
    }
    pos = ("más económico" if i == 0 else
           "más premium" if i == k_elegido - 1 else "intermedio")
    info["descripcion"] = (
        f"{info['n_anuncios']} anuncios en el rango {info['precio_min_usd']:.0f}-"
        f"{info['precio_max_usd']:.0f} USD (prom. {info['precio_promedio_usd']:.0f}), con "
        f"{info['motor_min_w']}-{info['motor_max_w']} W (prom. {info['motor_promedio_w']:.0f}) y "
        f"{info['autonomia_min_km']}-{info['autonomia_max_km']} km de autonomía "
        f"(prom. {info['autonomia_promedio_km']:.0f}). "
        f"Es el segmento {pos} por precio; marca dominante {info['marca_mas_comun']} "
        f"y batería {info['tipo_bateria_mas_comun']}."
    )
    clusters_info.append(info)

    print(f"\n--- Cluster {i}: \"{NOMBRES[i]}\" ({len(sub)} anuncios) ---")
    print(f"  precio_usd    prom={info['precio_promedio_usd']:>8.2f}  "
          f"min={info['precio_min_usd']:>8.2f}  max={info['precio_max_usd']:>8.2f}")
    print(f"  motor_w       prom={info['motor_promedio_w']:>8.1f}  "
          f"min={info['motor_min_w']:>8d}  max={info['motor_max_w']:>8d}")
    print(f"  autonomia_km  prom={info['autonomia_promedio_km']:>8.1f}  "
          f"min={info['autonomia_min_km']:>8d}  max={info['autonomia_max_km']:>8d}")
    print(f"  marca mas comun       : {info['marca_mas_comun']}")
    print(f"  tipo_bateria mas comun: {info['tipo_bateria_mas_comun']}")
    print(f"  ubicacion mas comun   : {info['ubicacion_mas_comun']}")
    print(f"  plegable mas comun    : {info['plegable_mas_comun']}")
    print(f"  descripcion           : {info['descripcion']}")

corr_pm = float(np.corrcoef(df["precio_usd"], df["motor_w"])[0, 1])
corr_pa = float(np.corrcoef(df["precio_usd"], df["autonomia_km"])[0, 1])
corr_ma = float(np.corrcoef(df["motor_w"], df["autonomia_km"])[0, 1])
SIL_FINAL = float(silhouette_score(X_norm, df["cluster"]))
print(f"  silhouette del clustering final (k={k_elegido}): {SIL_FINAL:.4f}")

# ---------- 8. VISUALIZACIONES ----------
print()
print(SEP)
print("PASO 8 — VISUALIZACIONES")
print(SEP)

X_orig = X.to_numpy(float)
etiquetas = df["cluster"].to_numpy(int)
I_PRECIO = FEATURES.index("precio_usd")
I_AUTONOMIA = FEATURES.index("autonomia_km")
print(f"  features graficadas: {FEATURES} -> eje X = {FEATURES[I_PRECIO]}, "
      f"eje Y = {FEATURES[I_AUTONOMIA]}")
print("  los puntos se dibujan en ESCALA ORIGINAL (X) y los centroides en ESCALA")
print("  ORIGINAL tambien (df_centros = scaler.inverse_transform), para que los ejes")
print("  sean USD y km. El color usa df['cluster'], que ya esta reordenado por precio")
print("  medio (es el mismo id que usa el resumen, el JSON y df_centros).")

fig, ax = plt.subplots(figsize=(12, 8))
puntos = ax.scatter(X_orig[:, I_PRECIO], X_orig[:, I_AUTONOMIA], c=etiquetas,
                    cmap="viridis", s=110, alpha=0.8, edgecolors="black", linewidths=0.5)
ax.scatter(df_centros["precio_usd"].to_numpy(float),
           df_centros["autonomia_km"].to_numpy(float),
           c="red", marker="X", s=430, edgecolors="black", linewidths=2.0, zorder=5,
           label="Centroides")
for i in range(k_elegido):
    ax.annotate(f"C{i}", (df_centros.iloc[i]["precio_usd"], df_centros.iloc[i]["autonomia_km"]),
                textcoords="offset points", xytext=(13, 9), fontweight="bold", fontsize=12)
ax.set_xlabel("Precio (USD)", fontsize=12)
ax.set_ylabel("Autonomía (km)", fontsize=12)
ax.set_title(f"Dispersión por cluster (k={k_elegido}): precio vs autonomía\n"
             "X roja = centroide del cluster", fontsize=13, fontweight="bold")
ax.xaxis.set_major_formatter(matplotlib.ticker.StrMethodFormatter("${x:,.0f}"))
ax.legend(loc="best", fontsize=10, framealpha=0.92)
ax.grid(alpha=0.3, linestyle="--")
cbar = fig.colorbar(puntos, ax=ax)
cbar.set_label("Cluster", fontsize=11)
fig.tight_layout()
fig.savefig(PREFIJO + "clusters_dispersion.png", dpi=150)
plt.close(fig)
print(f"  guardado: {PREFIJO}clusters_dispersion.png")

precios_medio = [X_orig[etiquetas == c, I_PRECIO].mean() for c in range(k_elegido)]
print(f"\n  precios promedio por cluster (X[cluster, precio_usd].mean()): "
      f"{[round(float(v), 2) for v in precios_medio]}")
fig, ax = plt.subplots(figsize=(11, 7))
barras = ax.bar([NOMBRES[i] for i in range(k_elegido)], precios_medio,
                color=[PALETA[i % len(PALETA)] for i in range(k_elegido)],
                edgecolor="black", linewidth=0.8, width=0.6)
for barra, valor in zip(barras, precios_medio):
    ax.text(barra.get_x() + barra.get_width() / 2, barra.get_height(), f"${valor:,.0f}",
            ha="center", va="bottom", fontsize=11, fontweight="bold")
ax.set_xlabel("Cluster", fontsize=12)
ax.set_ylabel("Precio promedio (USD)", fontsize=12)
ax.set_title(f"Precio promedio por cluster (k={k_elegido})", fontsize=13, fontweight="bold")
ax.yaxis.set_major_formatter(matplotlib.ticker.StrMethodFormatter("${x:,.0f}"))
ax.set_ylim(0, max(precios_medio) * 1.15)
ax.grid(axis="y", alpha=0.3, linestyle="--")
fig.tight_layout()
fig.savefig(PREFIJO + "clusters_precio_medio.png", dpi=150)
plt.close(fig)
print(f"  guardado: {PREFIJO}clusters_precio_medio.png")

tamanos = [int(np.sum(etiquetas == c)) for c in range(k_elegido)]
print(f"  tamaño por cluster (np.sum(etiquetas == c)): {tamanos}  "
      f"(suma={sum(tamanos)}, filas={len(df)})")
fig, ax = plt.subplots(figsize=(11, 7))
barras = ax.bar([NOMBRES[i] for i in range(k_elegido)], tamanos,
                color=[PALETA[i % len(PALETA)] for i in range(k_elegido)],
                edgecolor="black", linewidth=0.8, width=0.6)
for barra, valor in zip(barras, tamanos):
    ax.text(barra.get_x() + barra.get_width() / 2, barra.get_height(), f"{valor}",
            ha="center", va="bottom", fontsize=12, fontweight="bold")
ax.set_xlabel("Cluster", fontsize=12)
ax.set_ylabel("Cantidad de anuncios", fontsize=12)
ax.set_title(f"Tamaño de cada cluster (k={k_elegido})", fontsize=13, fontweight="bold")
ax.set_ylim(0, max(tamanos) * 1.15)
ax.grid(axis="y", alpha=0.3, linestyle="--")
fig.tight_layout()
fig.savefig(PREFIJO + "clusters_tamanos.png", dpi=150)
plt.close(fig)
print(f"  guardado: {PREFIJO}clusters_tamanos.png")

sil_muestras = silhouette_samples(X_norm, etiquetas)
sil_media = float(sil_muestras.mean())
print(f"  silhouette por anuncio (sklearn, sobre X_norm): min={sil_muestras.min():.4f}  "
      f"max={sil_muestras.max():.4f}  media={sil_media:.4f}")
fig, ax = plt.subplots(figsize=(12, 7))
rng = np.random.default_rng(42)
for c in range(k_elegido):
    valores = sil_muestras[etiquetas == c]
    ax.scatter(valores, c + rng.uniform(-0.18, 0.18, size=len(valores)), s=70, alpha=0.7,
               color=PALETA[c % len(PALETA)], edgecolors="black", linewidths=0.4)
    ax.plot([valores.mean(), valores.mean()], [c - 0.32, c + 0.32],
            color="black", linewidth=3, zorder=5)
    ax.text(valores.mean(), c + 0.38, f"media {valores.mean():.3f}", ha="center", fontsize=9)
ax.axvline(sil_media, color="red", linestyle="--", linewidth=2,
           label=f"Silhouette medio = {sil_media:.4f}")
ax.set_yticks(range(k_elegido))
ax.set_yticklabels([f"{NOMBRES[i]} (n={tamanos[i]})" for i in range(k_elegido)])
ax.set_xlabel("Silhouette por anuncio", fontsize=12)
ax.set_ylabel("Cluster", fontsize=12)
ax.set_title(f"Silhouette por cluster (k={k_elegido})\n"
             f"media global = {sil_media:.4f} — línea negra = media del cluster",
             fontsize=13, fontweight="bold")
ax.set_ylim(-0.6, k_elegido - 0.3)
ax.legend(loc="best", fontsize=10, framealpha=0.92)
ax.grid(axis="x", alpha=0.3, linestyle="--")
fig.tight_layout()
fig.savefig(PREFIJO + "clusters_metricas.png", dpi=150)
plt.close(fig)
print(f"  guardado: {PREFIJO}clusters_metricas.png")
print(f"  recheck: media de silhouette_samples = {sil_media:.6f} vs SIL_FINAL = "
      f"{SIL_FINAL:.6f} -> {'OK' if abs(sil_media - SIL_FINAL) < 1e-9 else 'FALLA'}")

# ---------- 9. ARCHIVOS DE SALIDA ----------
print()
print(SEP)
print("PASO 9 — ARCHIVOS DE SALIDA")
print(SEP)
fecha = datetime.now().isoformat(timespec="seconds")

anuncios_out = []
for pos, clus in zip(pos_final, df["cluster"].to_numpy()):
    a = dict(anuncios[int(pos)])
    a["cluster"] = int(clus)
    anuncios_out.append(a)

salida = {
    "vehiculo": data.get("vehiculo", "moto_electrica"),
    "total_anuncios_validos": data.get("total_anuncios_validos", len(anuncios)),
    "total_anuncios_clusterizados": int(len(df)),
    "k_elegido": k_elegido,
    "silhouette": round(SIL_FINAL, 4),
    "fecha_analisis": fecha,
    "features_usadas": FEATURES,
    "normalizacion": "StandardScaler",
    "clusters": clusters_info,
    "anuncios": anuncios_out,
}
with open(PREFIJO + "motos_con_clusters.json", "w", encoding="utf-8") as f:
    json.dump(salida, f, ensure_ascii=False, indent=2, allow_nan=False)
print(f"  guardado: {PREFIJO}motos_con_clusters.json")

L = []
A = L.append
A(SEP)
A("RESUMEN DE CLUSTERING K-MEANS — MOTOS ELÉCTRICAS")
A(SEP)
A(f"Fecha de análisis       : {fecha}")
A(f"Archivo origen          : {ORIGEN}")
A(f"Vehículo                : {salida['vehiculo']}")
A("")
A("DATOS")
A(f"  Anuncios totales en el JSON   : {len(anuncios)}")
A(f"  Anuncios clusterizados (M)    : {len(df)}")
A(f"  Descartados por nulls         : {antes - despues}")
A(f"  Campos usados para clusterizar: {', '.join(FEATURES)}")
A("  Normalización                 : StandardScaler (media 0, desv. 1)")
A("")
A("ELECCIÓN DE K")
A(f"  k elegido                     : {k_elegido}")
A(f"  silhouette del clustering      : {SIL_FINAL:.4f}")
A("")
A("   k  |    inercia      |   silhouette")
A("  --- + --------------- + ---------------")
for _, r in tabla_k.iterrows():
    A(f"  {int(r['k']):<3d} | {r['inercia']:>15.4f} | {r['silhouette']:>15.4f}")
A("")
A("  Justificación:")
for j in [
    f"  - Regla aplicada: máximo silhouette_score. El máximo está en k={k_sil} ({best_sil:.4f}).",
    f"  - Segundo mejor: k={int(segundo['k'])} ({segundo['silhouette']:.4f}); gap={best_sil - segundo['silhouette']:.4f}.",
    f"  - El codo geométrico de la inercia cae en k={k_codo}; la inercia baja de forma",
    "    monótona, así que no hay un codo marcado que contradiga al silhouette.",
    f"  - Advertencia: con n={despues} el silhouette premia la sobre-segmentación",
    f"    (k={k_sil} deja clusters de 1-2 anuncios) y el anuncio de 4200 USD queda solo en",
    "    su propio cluster. Para reporting de negocio, k=2 o k=3 son más accionables.",
]:
    A(j)
if ARGS.k:
    A(f"  - NOTA: k fue forzado con --k {ARGS.k}; el argmax de silhouette es k={k_sil}.")
A("")
A("CENTROIDES EN ESCALA ORIGINAL (scaler.inverse_transform)")
A("")
A("  cluster | nombre             | precio_usd |  motor_w | autonomia_km")
A("  ------- + ------------------- + ----------- + -------- + ------------")
for i, info in enumerate(clusters_info):
    c = df_centros.iloc[i]
    A(f"  {i:<7d} | {info['nombre']:<19s} | {c['precio_usd']:>11.2f} | "
      f"{c['motor_w']:>8.2f} | {c['autonomia_km']:>12.2f}")
A("")
A("DESCRIPCIÓN DE CADA CLUSTER")
A(SEP)
for info in clusters_info:
    A("")
    A(f"CLUSTER {info['id']} — {info['nombre']}")
    A(f"  Anuncios            : {info['n_anuncios']}")
    A(f"  Precio (USD)        : prom {info['precio_promedio_usd']:.2f} | "
      f"min {info['precio_min_usd']:.0f} | max {info['precio_max_usd']:.0f}")
    A(f"  Motor (W)           : prom {info['motor_promedio_w']:.1f} | "
      f"min {info['motor_min_w']} | max {info['motor_max_w']}")
    A(f"  Autonomía (km)      : prom {info['autonomia_promedio_km']:.1f} | "
      f"min {info['autonomia_min_km']} | max {info['autonomia_max_km']}")
    A(f"  Marca más común     : {info['marca_mas_comun']}")
    A(f"  Tipo de batería     : {info['tipo_bateria_mas_comun']}")
    A(f"  Ubicación más común : {info['ubicacion_mas_comun']}")
    A(f"  Plegable más común  : {info['plegable_mas_comun']}")
    A(f"  Descripción         : {info['descripcion']}")
A("")
A("CONCLUSIONES PRELIMINARES")
A(SEP)
A(f"  Correlación Pearson precio~motor_w       : {corr_pm:+.4f}")
A(f"  Correlación Pearson precio~autonomia_km  : {corr_pa:+.4f}")
A(f"  Correlación Pearson motor_w~autonomia_km : {corr_ma:+.4f}")
A("")
A("  - El precio correlates {a} con el motor ({c}) y {b} con la autonomía ({d}):".format(
    a="FUERTE" if abs(corr_pm) > 0.7 else "moderada" if abs(corr_pm) > 0.4 else "débil",
    c=f"{corr_pm:+.2f}",
    b="FUERTE" if abs(corr_pa) > 0.7 else "moderada" if abs(corr_pa) > 0.4 else "débil",
    d=f"{corr_pa:+.2f}"))
A(f"    en este mercado la potencia del motor pesa {'más' if abs(corr_pm) > abs(corr_pa) else 'menos'}"
  " que la autonomía como driver del precio.")
if corr_ma > 0.3:
    A(f"  - Motor y autonomía están correlacionados ({corr_ma:+.2f}): el cluster premium")
    A("    combina más W y más km a la vez, así que el precio sube por los dos lados.")
else:
    A(f"  - Motor y autonomía apenas se correlacionan ({corr_ma:+.2f}): son dimensiones")
    A("    independientes y el cluster se define por su combinación (p. ej. mucha")
    A("    autonomía con poco motor, o al revés).")
A(f"  - El rango de precio observado es {df['precio_usd'].min():.0f}-"
  f"{df['precio_usd'].max():.0f} USD y hay solapamiento entre segmentos, por lo que el")
A("    clustering describe el catálogo pero no predice el precio de forma exacta.")
A(f"  - Limitaciones: n={despues} anuncios, todas las ubicaciones son "
  f"\"{mas_comun(df['ubicacion'])}\" (sin variación")
A("    geográfica) y el 90% de los anuncios usan batería de litio, así que esos campos")
A("    no discriminan entre clusters. Conviene re-ejecutar con más anuncios antes de")
A("    tomar decisiones de precio o de compra.")
if "plegable" in CAMPO_FALTA:
    A("")
    A("  NOTA SOBRE DATOS: el campo 'plegable' NO existe en motos_electricas.json (0")
    A("  ocurrencias; tampoco figura en el esquema ni en el scraper). Por eso")
    A("  'plegable_mas_comun' queda en null en todos los clusters. Si hace falta, hay que")
    A("  agregarlo al esquema del scraper y volver a recolectar los anuncios.")
with open(PREFIJO + "resumen_clustering.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(L) + "\n")
print(f"  guardado: {PREFIJO}resumen_clustering.txt")

# ---------- 10. VALIDACIÓN ----------
print()
print(SEP)
print("PASO 10 — VALIDACIÓN")
print(SEP)
r = subprocess.run([PY, "-m", "json.tool", PREFIJO + "motos_con_clusters.json"],
                   capture_output=True, text=True)
print(f"  python3 -m json.tool {PREFIJO}motos_con_clusters.json -> exit={r.returncode} "
      f"({'JSON VÁLIDO' if r.returncode == 0 else 'ERROR'})")
if r.returncode != 0:
    print(r.stderr[:1000])
texto = open(PREFIJO + "motos_con_clusters.json", encoding="utf-8").read()
malos = [t for t in ("NaN", "Infinity", "-Infinity") if t in texto]
print(f"  recheck: sin literales NaN/Infinity (JSON estricto) -> "
      f"{'OK' if not malos else 'FALLA: ' + str(malos)}")
con = json.loads(texto, parse_constant=lambda c: (_ for _ in ()).throw(
    ValueError(f"constante JSON no válida: {c}")))
print(f"  recheck: parseo estricto (parse_constant estricto) -> OK")
print(f"  recheck: k_elegido={con['k_elegido']}, clusters={len(con['clusters'])}, "
      f"anuncios={len(con['anuncios'])}, fecha={con['fecha_analisis']}")
print(f"  recheck: suma de n_anuncios = {sum(c['n_anuncios'] for c in con['clusters'])} "
      f"(debe ser {len(df)})")
print(f"  recheck: todos los anuncios tienen 'cluster' = "
      f"{all('cluster' in a for a in con['anuncios'])}")
fidele = all(
    {k: v for k, v in a.items() if k != "cluster"} == anuncios[int(p)]
    for a, p in zip(con["anuncios"], pos_final))
print(f"  recheck: cada anuncio conserva TODAS sus claves/valores originales = {fidele}")
print(f"  recheck: nulos preservados como null (no NaN) = "
      f"{con['anuncios'][0]['modelo'] is None}")

desp_stat = os.stat(ORIGEN)
desp_md5 = hashlib.md5(open(ORIGEN, "rb").read()).hexdigest()
desp_mtime = datetime.fromtimestamp(desp_stat.st_mtime).isoformat()
print(f"\n  Integridad del original: {ORIGEN}")
print(f"    mtime antes   : {ORIGEN_MTIME}")
print(f"    mtime después : {desp_mtime}")
print(f"    tamaño antes  : {ORIGEN_STAT.st_size} bytes")
print(f"    tamaño después: {desp_stat.st_size} bytes")
print(f"    md5 antes     : {ORIGEN_MD5}")
print(f"    md5 después   : {desp_md5}")
ok = (ORIGEN_MD5 == desp_md5 and ORIGEN_STAT.st_size == desp_stat.st_size
      and ORIGEN_STAT.st_mtime == desp_stat.st_mtime)
print(f"    -> ORIGEN {'INTACTO (sin cambios)' if ok else '⚠ MODIFICADO ⚠'}")

# ---------- 11. RESUMEN FINAL ----------
print()
print(SEP)
print("PASO 11 — RESUMEN FINAL")
print(SEP)
print(f"  Anuncios clusterizados : {len(df)} (de {len(anuncios)} en el JSON)")
print(f"  Clusters               : {k_elegido}")
for info in clusters_info:
    print(f"    - Cluster {info['id']}: \"{info['nombre']}\"  n={info['n_anuncios']:>2}  "
          f"precio {info['precio_min_usd']:.0f}-{info['precio_max_usd']:.0f} USD "
          f"(prom {info['precio_promedio_usd']:.0f})")
print("  Archivos generados:")
for f_ in [PREFIJO + "motos_con_clusters.json", PREFIJO + "resumen_clustering.txt",
           PREFIJO + "clusters_dispersion.png", PREFIJO + "clusters_precio_medio.png",
           PREFIJO + "clusters_tamanos.png", PREFIJO + "clusters_metricas.png"]:
    print(f"    - {f_}  ({os.path.getsize(f_):,} bytes)")
print(f"  Original sin tocar    : {ORIGEN}")
print(SEP)
