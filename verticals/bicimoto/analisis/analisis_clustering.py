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
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

PY = "/home/leandro/ev-market-study/venv/bin/python3"
ORIGEN = "/home/leandro/ev-market-study/verticals/bicimoto/scraping/bicis_electricas.json"
ORIGEN_STAT = os.stat(ORIGEN)
ORIGEN_MD5 = hashlib.md5(open(ORIGEN, "rb").read()).hexdigest()
ORIGEN_MTIME = datetime.fromtimestamp(ORIGEN_STAT.st_mtime).isoformat()

# motor_w es OPCIONAL en el scraper de bicimotos: NO entra al modelo.
FEATURES = ["precio_usd", "autonomia_km"]
SOLO_DESCRIPTIVO = ["motor_w"]
DESCRIPTIVAS = ["marca", "tipo_bateria", "ubicacion"]
K_CANDIDATOS = [3, 4, 5]
MAX_FRACCION = 0.50
MIN_N = 4
NOMBRES_POR_K = {
    3: ["Económica urbana", "Bicimoto de trabajo", "Alta gama"],
    4: ["Económica urbana", "Bicimoto de trabajo", "Alta gama", "Premium"],
    5: ["Económica urbana", "Bicimoto de trabajo", "Alta gama", "Premium",
        "Premium extendida"],
}
PALETA = ["#2e8b57", "#e07b00", "#7b2d8b", "#1f6feb", "#c1121f"]
SEP = "=" * 78


# ---------- 1. CARGA ----------
with open(ORIGEN, encoding="utf-8") as f:
    data = json.load(f)
anuncios = data["anuncios"]
df = pd.DataFrame(anuncios)
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

# ---------- 2. COMPLETITUD ----------
print()
print(SEP)
print("PASO 2 — COMPLETITUD (motor_w es OPCIONAL en este scraper)")
print(SEP)
print("  (a) features USADAS para clusterizar:")
for c in FEATURES:
    nulos = int(df[c].isna().sum())
    print(f"      {c:<13} presentes {len(df) - nulos:>3}/{len(df)}   nulos={nulos:>3}")
print("  (b) campo SOLO DESCRIPTIVO (no entra al modelo):")
for c in SOLO_DESCRIPTIVO:
    nulos = int(df[c].isna().sum())
    print(f"      {c:<13} presentes {len(df) - nulos:>3}/{len(df)}   nulos={nulos:>3}")

antes = len(df)
df = df.dropna(subset=FEATURES).reset_index(drop=True)
despues = len(df)
pos_final = list(df["__pos"])
con_motor = int(df["motor_w"].notna().sum())
print()
print(f"  anuncios con precio_usd Y autonomia_km completos : {despues}/{len(anuncios)}")
print(f"  ...de esos, los que TAMBIÉN tienen motor_w        : {con_motor}/{despues}")
print(f"  ...sin motor_w (se conservan igual)              : {despues - con_motor}/{despues}")
print(f"  filas eliminadas por nulls en las features       : {antes - despues}")
print()
print("  DECISIÓN: se clusteriza con las 30 filas usando SOLO precio_usd y")
print("            autonomia_km. Los 9 anuncios sin motor_w NO se descartan; el")
print("            motor se reporta después como estadística descriptiva por cluster.")

dup = df.duplicated(subset=FEATURES).sum()
print(f"\n  aviso: hay {dup} anuncios con par (precio_usd, autonomia_km) repetido;")
print("        se mantienen (son listings distintos) pero el motor NO puede")
print("        discriminar entre ellos.")

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
print("\n(c) columnas que entran al modelo:", FEATURES)
print("    motor_w EXCLUIDO a propósito (opcional en el scraper, 9/30 null)")

# ---------- 4. k = 2..8 (métricas) ----------
print()
print(SEP)
print("PASO 4 — MÉTRICAS POR k (k = 2..8, para diagnóstico)")
print(SEP)
filas = []
for k in range(2, 9):
    km = KMeans(n_clusters=k, random_state=42, n_init=10)
    lab = km.fit_predict(X_norm)
    filas.append({"k": k, "inercia": km.inertia_,
                  "silhouette": silhouette_score(X_norm, lab)})
tabla_k = pd.DataFrame(filas)
tabla_k["delta_inercia"] = tabla_k["inercia"].shift(-1) - tabla_k["inercia"]

_x = tabla_k["k"].to_numpy(float)
_y = tabla_k["inercia"].to_numpy(float)
tabla_k["dist_al_codo"] = np.abs((_y[-1] - _y[0]) * _x - (_x[-1] - _x[0]) * _y
                                  + _x[-1] * _y[0] - _y[-1] * _x[0]) / np.hypot(
    _x[-1] - _x[0], _y[-1] - _y[0])
k_codo = int(tabla_k.loc[tabla_k["dist_al_codo"].idxmax(), "k"])

print(tabla_k[["k", "inercia", "silhouette"]].to_string(
    index=False, float_format=lambda v: f"{v:.4f}"))
print(f"\n  codo geométrico de la inercia: k={k_codo}")

# ---------- 5. REGLA DE DECISIÓN sobre k=3,4,5 ----------
print()
print(SEP)
print("PASO 5 — EVALUACIÓN DE k=3,4,5 CON LA REGLA DE DECISIÓN")
print(SEP)
print(f"  Regla: (1) ningún cluster > {int(MAX_FRACCION*100)}% de los datos"
      f" (>{int(MAX_FRACCION*despues)} de {despues}),")
print(f"         (2) ningún cluster < {MIN_N} anuncios,")
print(f"         (3) entre los que cumplen, el de mayor silhouette.\n")

resultados = {}
fila_eval = []
for k in K_CANDIDATOS:
    km = KMeans(n_clusters=k, random_state=42, n_init=10)
    lab = km.fit_predict(X_norm)
    tam = sorted(int((lab == i).sum()) for i in range(k))
    sil = float(silhouette_score(X_norm, lab))
    n_max, n_min = max(tam), min(tam)
    ok_max = n_max <= MAX_FRACCION * despues
    ok_min = n_min >= MIN_N
    cumple = ok_max and ok_min
    resultados[k] = {"labels": lab, "tamanos": tam, "silhouette": sil,
                     "cumple": cumple, "ok_max": ok_max, "ok_min": ok_min}
    fila_eval.append({"k": k, "silhouette": sil, "tamanos": tam,
                      "max": n_max, "min": n_min,
                      "ok_max_50pct": ok_max, "ok_min_4": ok_min, "cumple": cumple})
    print(f"  k={k}: silhouette={sil:.4f}  tamaños={tam}  max={n_max}  min={n_min}"
          f"  -> {'CUMPLE' if cumple else 'NO CUMPLE'}")

validos = [k for k in K_CANDIDATOS if resultados[k]["cumple"]]
print()
if not validos:
    k_elegido = max(K_CANDIDATOS, key=lambda k: resultados[k]["silhouette"])
    print(f"  *** Ningún k candidato cumple la regla -> se usa argmax silhouette k={k_elegido} ***")
else:
    k_elegido = max(validos, key=lambda k: resultados[k]["silhouette"])
    print(f"  k válidos según la regla: {validos}")
    print(f"  de esos, mayor silhouette -> k={k_elegido} "
          f"({resultados[k_elegido]['silhouette']:.4f})")
rechazados = [f"k={k} (sil={resultados[k]['silhouette']:.4f})"
              for k in K_CANDIDATOS if not resultados[k]["cumple"]]
if rechazados:
    print(f"  descartados por la regla: {', '.join(rechazados)}")
print(f"\n  k SELECCIONADO: {k_elegido}")

# ---------- 6. ENTRENAMIENTO FINAL ----------
print()
print(SEP)
print(f"PASO 6 — KMEANS FINAL (k={k_elegido})")
print(SEP)
kmeans = KMeans(n_clusters=k_elegido, random_state=42, n_init=10)
labels = kmeans.fit_predict(X_norm)
df["cluster"] = labels
print(f"  KMeans(n_clusters={k_elegido}, random_state=42, n_init=10) sobre X_norm")
print(f"  iteraciones hasta converger: {kmeans.n_iter_}")

# KMeans no ordena etiquetas: se reordenan por precio medio ascendente
orden = df.groupby("cluster")["precio_usd"].mean().sort_values().index.tolist()
remap = {orig: nuevo for nuevo, orig in enumerate(orden)}
df["cluster"] = df["cluster"].map(remap).astype(int)
centros_norm = kmeans.cluster_centers_[orden]
centros_orig = scaler.inverse_transform(centros_norm)
df_centros = pd.DataFrame(centros_orig, columns=FEATURES)
print(f"  orden original {list(range(k_elegido))} -> {orden} (por precio medio ascendente)")
print("\n  CENTROIDES EN ESCALA ORIGINAL:")
print(df_centros.round(2).to_string())

# ---------- 7. NOMBRES ----------
print()
print(SEP)
print("PASO 7 — NOMBRES DE CLUSTERS")
print(SEP)
NOMBRES = NOMBRES_POR_K[k_elegido]
for i, n in enumerate(NOMBRES):
    print(f"    Cluster {i}: \"{n}\"")

# ---------- 8. INTERPRETACIÓN ----------
print()
print(SEP)
print("PASO 8 — INTERPRETACIÓN POR CLUSTER")
print(SEP)


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
    m = sub["motor_w"].dropna()
    info = {
        "id": i,
        "nombre": NOMBRES[i],
        "n_anuncios": int(len(sub)),
        "precio_promedio_usd": round(float(sub["precio_usd"].mean()), 2),
        "precio_min_usd": float(sub["precio_usd"].min()),
        "precio_max_usd": float(sub["precio_usd"].max()),
        "autonomia_promedio_km": round(float(sub["autonomia_km"].mean()), 1),
        "autonomia_min_km": int(sub["autonomia_km"].min()),
        "autonomia_max_km": int(sub["autonomia_km"].max()),
        "motor_promedio_w": round(float(m.mean()), 1) if len(m) else None,
        "motor_min_w": int(m.min()) if len(m) else None,
        "motor_max_w": int(m.max()) if len(m) else None,
        "motor_con_dato_n": int(len(m)),
        "motor_sin_dato_n": int(sub["motor_w"].isna().sum()),
        "marca_mas_comun": mas_comun(sub["marca"]),
        "tipo_bateria_mas_comun": mas_comun(sub["tipo_bateria"]),
        "ubicacion_mas_comun": mas_comun(sub["ubicacion"]),
    }
    pos = ("más económico" if i == 0 else
           "más premium" if i == k_elegido - 1 else "intermedio")
    info["descripcion"] = (
        f"{info['n_anuncios']} anuncios en el rango {info['precio_min_usd']:.0f}-"
        f"{info['precio_max_usd']:.0f} USD (prom. {info['precio_promedio_usd']:.0f}) con "
        f"{info['autonomia_min_km']}-{info['autonomia_max_km']} km de autonomía "
        f"(prom. {info['autonomia_promedio_km']:.0f}). "
        f"Motor (descriptivo, {info['motor_con_dato_n']}/{info['n_anuncios']} con dato) "
        f"prom. {info['motor_promedio_w']} W. "
        f"Es el segmento {pos} por precio; marca dominante {info['marca_mas_comun']} "
        f"y batería {info['tipo_bateria_mas_comun']}."
    )
    clusters_info.append(info)

    print(f"\n--- Cluster {i}: \"{NOMBRES[i]}\" ({len(sub)} anuncios) ---")
    print(f"  precio_usd    prom={info['precio_promedio_usd']:>8.2f}  "
          f"min={info['precio_min_usd']:>8.2f}  max={info['precio_max_usd']:>8.2f}")
    print(f"  autonomia_km  prom={info['autonomia_promedio_km']:>8.1f}  "
          f"min={info['autonomia_min_km']:>8d}  max={info['autonomia_max_km']:>8d}")
    print(f"  motor_w       prom={str(info['motor_promedio_w']):>8}  "
          f"min={str(info['motor_min_w']):>8}  max={str(info['motor_max_w']):>8}  "
          f"(con dato {info['motor_con_dato_n']}/{info['n_anuncios']}, "
          f"sin dato {info['motor_sin_dato_n']})")
    print(f"  marca mas comun       : {info['marca_mas_comun']}")
    print(f"  tipo_bateria mas comun: {info['tipo_bateria_mas_comun']}")
    print(f"  ubicacion mas comun   : {info['ubicacion_mas_comun']}")
    print(f"  descripcion           : {info['descripcion']}")

SIL_FINAL = float(silhouette_score(X_norm, df["cluster"]))


def columnas_comparativas():
    """Filas de la tabla comparativa por cluster, ordenadas por precio ascendente."""
    lineas = []
    for k in K_CANDIDATOS:
        tmp = df.drop(columns=["cluster"]).copy()
        tmp["cl"] = resultados[k]["labels"]
        tmp["cl"] = tmp["cl"].map(
            {orig: nuevo for nuevo, orig in
             enumerate(tmp.groupby("cl")["precio_usd"].mean().sort_values().index)}
        ).astype(int)
        filas_k = []
        for i in range(k):
            sub = tmp[tmp["cl"] == i]
            m = sub["motor_w"].dropna()
            filas_k.append({
                "nombre": NOMBRES_POR_K[k][i],
                "n": int(len(sub)),
                "precio_prom": float(sub["precio_usd"].mean()),
                "precio_min": float(sub["precio_usd"].min()),
                "precio_max": float(sub["precio_usd"].max()),
                "auton_prom": float(sub["autonomia_km"].mean()),
                "auton_min": float(sub["autonomia_km"].min()),
                "auton_max": float(sub["autonomia_km"].max()),
                "motor_prom": float(m.mean()) if len(m) else None,
                "motor_con_dato": int(len(m)),
                "marca": mas_comun(sub["marca"]),
                "bateria": mas_comun(sub["tipo_bateria"]),
                "ubicacion": mas_comun(sub["ubicacion"]),
            })
        lineas.append((k, resultados[k]["silhouette"], resultados[k]["cumple"], filas_k))
    return lineas


HDR_TABLA = (f"  {'cluster':<22}{'n':>4}{'precio prom':>13}{'min':>8}{'max':>8}"
             f"{'auton prom':>12}{'min':>6}{'max':>6}{'motor prom':>12}{'marca':>14}")

# tablas comparativas de k=3,4,5
print()
print(SEP)
print("PASO 8b — TABLAS COMPARATIVAS k=3 / k=4 / k=5")
print(SEP)
print("  (los clusters de cada k se ordenan por precio medio ascendente antes de")
print("   asignar los nombres, para que 'Económica urbana' sea siempre el más barato)")
for k, sil, cumple, filas_k in columnas_comparativas():
    print(f"\n### k={k}  (silhouette={sil:.4f}, cumple regla={cumple}) ###")
    print(HDR_TABLA)
    print("  " + "-" * (len(HDR_TABLA) - 2))
    for r in filas_k:
        print(f"  {r['nombre']:<22}{r['n']:>4}"
              f"{r['precio_prom']:>13.2f}{r['precio_min']:>8.0f}{r['precio_max']:>8.0f}"
              f"{r['auton_prom']:>12.1f}{r['auton_min']:>6.0f}{r['auton_max']:>6.0f}"
              f"{(f'{r['motor_prom']:.0f}' if r['motor_prom'] is not None else 'n/d'):>12}"
              f"{r['marca']:>14}")

# ---------- 9. VISUALIZACIONES ----------
print()
print(SEP)
print("PASO 9 — VISUALIZACIONES")
print(SEP)

fig, ax = plt.subplots(figsize=(12, 8))
for i in range(k_elegido):
    sub = df[df["cluster"] == i]
    col = PALETA[i % len(PALETA)]
    ax.scatter(sub["precio_usd"], sub["autonomia_km"], s=120, alpha=0.78, color=col,
               edgecolors="black", linewidths=0.6,
               label=f"{NOMBRES[i]} (n={len(sub)})")
    cx, cy = df_centros.iloc[i]["precio_usd"], df_centros.iloc[i]["autonomia_km"]
    ax.scatter(cx, cy, marker="X", s=430, color=col, edgecolors="black",
               linewidths=2.0, zorder=5)
    ax.annotate(f"C{i}", (cx, cy), textcoords="offset points", xytext=(13, 9),
                fontweight="bold", fontsize=12)
ax.set_xlabel("precio_usd (USD)", fontsize=12)
ax.set_ylabel("autonomia_km (km)", fontsize=12)
ax.set_title(f"Bicimotos — K-Means (k={k_elegido}) precio vs autonomía\n"
             "features: precio_usd y autonomia_km | X = centroide", fontsize=13,
             fontweight="bold")
ax.legend(loc="best", fontsize=9, framealpha=0.92)
ax.grid(alpha=0.3, linestyle="--")
fig.tight_layout()
fig.savefig("clusters_dispersion.png", dpi=150)
plt.close(fig)
print("  guardado: clusters_dispersion.png")

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 7))
etq = [f"{NOMBRES[i]}\n(n={len(datos)})" for i, datos in enumerate(
    [df.loc[df["cluster"] == i, "precio_usd"].to_numpy() for i in range(k_elegido)])]
for ax, col, titulo, ylab, fmt in [
        (ax1, "precio_usd", "Precio (USD)", "precio_usd (USD)", "${x:,.0f}"),
        (ax2, "autonomia_km", "Autonomía (km)", "autonomia_km (km)", "{x:,.0f}")]:
    datos = [df.loc[df["cluster"] == i, col].to_numpy() for i in range(k_elegido)]
    bp = ax.boxplot(
        datos, tick_labels=[f"C{i}\n{n}" for i, n in enumerate(
            [str(len(d)) for d in datos])],
        patch_artist=True, widths=0.55, showmeans=True,
        meanprops=dict(marker="D", markerfacecolor="white", markeredgecolor="black",
                       markersize=9),
        medianprops=dict(color="black", linewidth=2))
    for patch, i in zip(bp["boxes"], range(k_elegido)):
        patch.set_facecolor(PALETA[i % len(PALETA)])
        patch.set_alpha(0.7)
    for flier in bp["fliers"]:
        flier.set(marker="o", markersize=6, alpha=0.6)
    ax.set_ylabel(ylab, fontsize=12)
    ax.set_title(titulo, fontsize=12, fontweight="bold")
    ax.yaxis.set_major_formatter(matplotlib.ticker.StrMethodFormatter(fmt))
    ax.grid(axis="y", alpha=0.3, linestyle="--")
fig.suptitle(f"Distribución de las features por cluster (k={k_elegido}) — "
             "rombo blanco = media", fontsize=13, fontweight="bold")
fig.tight_layout()
fig.savefig("clusters_boxplot.png", dpi=150)
plt.close(fig)
print("  guardado: clusters_boxplot.png")

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5.5))
ax1.plot(tabla_k["k"], tabla_k["inercia"], "o-", color="#c1121f", linewidth=2, markersize=7)
ax1.axvline(k_elegido, color="#1f6feb", linestyle="--", linewidth=1.7,
            label=f"k elegido = {k_elegido}")
ax1.axvline(k_codo, color="#888888", linestyle=":", linewidth=1.7,
            label=f"codo ≈ k={k_codo}")
ax1.set_xlabel("k (número de clusters)", fontsize=11)
ax1.set_ylabel("Inercia", fontsize=11)
ax1.set_title("Inercia vs k", fontsize=12, fontweight="bold")
ax1.set_xticks(tabla_k["k"])
ax1.grid(alpha=0.3, linestyle="--")
ax1.legend()

ax2.plot(tabla_k["k"], tabla_k["silhouette"], "s-", color="#2e8b57", linewidth=2,
         markersize=7)
for k in K_CANDIDATOS:
    ax2.scatter([k], [resultados[k]["silhouette"]], s=150,
                facecolors="#2e8b57" if resultados[k]["cumple"] else "white",
                edgecolors="black" if not resultados[k]["cumple"] else "black",
                linewidths=1.8, zorder=5)
    ax2.annotate(f"k={k}{'✓' if resultados[k]['cumple'] else '✗'}",
                 (k, resultados[k]["silhouette"]), textcoords="offset points",
                 xytext=(0, 11), ha="center", fontsize=9, fontweight="bold")
ax2.axvline(k_elegido, color="#1f6feb", linestyle="--", linewidth=1.7,
            label=f"k elegido = {k_elegido}")
ax2.set_xlabel("k (número de clusters)", fontsize=11)
ax2.set_ylabel("Silhouette score", fontsize=11)
ax2.set_title("Silhouette vs k (candidatos 3/4/5; ✓ cumple la regla)", fontsize=12,
              fontweight="bold")
ax2.set_xticks(tabla_k["k"])
ax2.grid(alpha=0.3, linestyle="--")
ax2.legend()
fig.tight_layout()
fig.savefig("clusters_metricas.png", dpi=150)
plt.close(fig)
print("  guardado: clusters_metricas.png")

# ---------- 10. ARCHIVOS DE SALIDA ----------
print()
print(SEP)
print("PASO 10 — ARCHIVOS DE SALIDA")
print(SEP)
fecha = datetime.now().isoformat(timespec="seconds")

anuncios_out = []
for pos, clus in zip(pos_final, df["cluster"].to_numpy()):
    a = dict(anuncios[int(pos)])
    a["cluster"] = int(clus)
    anuncios_out.append(a)

salida = {
    "vehiculo": data.get("vehiculo", "bicicleta_electrica_bicimoto"),
    "total_anuncios_validos": data.get("total_anuncios_validos", len(anuncios)),
    "total_anuncios_clusterizados": int(len(df)),
    "k_elegido": k_elegido,
    "silhouette": round(SIL_FINAL, 4),
    "fecha_analisis": fecha,
    "features_usadas": FEATURES,
    "campos_solo_descriptivos": SOLO_DESCRIPTIVO,
    "nota_features": "motor_w es opcional en el scraper de bicimotos (9/30 null) y por "
                     "eso NO se usa como feature; solo se reporta por cluster.",
    "normalizacion": "StandardScaler",
    "clusters": clusters_info,
    "anuncios": anuncios_out,
}
with open("bicis_con_clusters.json", "w", encoding="utf-8") as f:
    json.dump(salida, f, ensure_ascii=False, indent=2, allow_nan=False)
print("  guardado: bicis_con_clusters.json")

L = []
A = L.append
A(SEP)
A("RESUMEN DE CLUSTERING K-MEANS — BICIMOTOS / BICICLETAS ELÉCTRICAS")
A(SEP)
A(f"Fecha de análisis       : {fecha}")
A(f"Archivo origen          : {ORIGEN}")
A(f"Vehículo                : {salida['vehiculo']}")
A("")
A("DATOS Y COMPLETITUD")
A(f"  Anuncios totales en el JSON        : {len(anuncios)}")
A(f"  Con precio_usd Y autonomia_km      : {despues}/{len(anuncios)}")
A(f"  ...de esos, con motor_w también    : {con_motor}/{despues}")
A(f"  ...sin motor_w (conservados)       : {despues - con_motor}/{despues}")
A(f"  Descartados por nulls              : {antes - despues}")
A(f"  Features usadas                   : {', '.join(FEATURES)}")
A(f"  Campos solo descriptivos          : {', '.join(SOLO_DESCRIPTIVO)}")
A("  Normalización                      : StandardScaler (media 0, desv. 1)")
A("")
A("  NOTA SOBRE motor_w: en el scraping de bicimotos el motor es OPCIONAL, así que")
A("  queda null en 9 de 30 anuncios. Clusterizar también con motor_w habría")
A("  descartado el 30% del catálogo; por eso se excluye del modelo y se reporta")
A("  después como promedio por cluster.")
A("")
A("ELECCIÓN DE K")
A(f"  Regla: ningún cluster > {int(MAX_FRACCION*100)}% (>{int(MAX_FRACCION*despues)} de "
  f"{despues}), ningún cluster < {MIN_N}, y de los que cumplen el mayor silhouette.")
A("")
A("   k  |  tamaños por cluster                |  max | min | silhouette | cumple")
A("  --- + ------------------------------------ + ----- + ---- + ---------- + -------")
for k in K_CANDIDATOS:
    r = resultados[k]
    A(f"  {k:<3d} | {str(r['tamanos']):<36s} | {max(r['tamanos']):>5d} | "
      f"{min(r['tamanos']):>4d} | {r['silhouette']:>10.4f} | "
      f"{'SÍ' if r['cumple'] else 'NO'}")
A("")
A(f"  k ELEGIDO : {k_elegido}  (silhouette={SIL_FINAL:.4f})")
if rechazados:
    A(f"  Descartados por la regla: {', '.join(rechazados)}")
A("")
A("   k  |    inercia      |   silhouette   (diagnóstico k=2..8)")
A("  --- + --------------- + ---------------")
for _, r in tabla_k.iterrows():
    A(f"  {int(r['k']):<3d} | {r['inercia']:>15.4f} | {r['silhouette']:>15.4f}")
A("")
A("TABLAS COMPARATIVAS k=3 / k=4 / k=5")
A("(los clusters de cada k se ordenan por precio medio ascendente antes de asignar")
A(" los nombres, para que 'Económica urbana' sea siempre el más barato)")
A(SEP)
for k, sil, cumple, filas_k in columnas_comparativas():
    A("")
    A(f"### k={k}  (silhouette={sil:.4f}, cumple regla={cumple}) ###")
    A(HDR_TABLA)
    A("  " + "-" * (len(HDR_TABLA) - 2))
    for r in filas_k:
        A(f"  {r['nombre']:<22}{r['n']:>4}"
          f"{r['precio_prom']:>13.2f}{r['precio_min']:>8.0f}{r['precio_max']:>8.0f}"
          f"{r['auton_prom']:>12.1f}{r['auton_min']:>6.0f}{r['auton_max']:>6.0f}"
          f"{(f'{r['motor_prom']:.0f}' if r['motor_prom'] is not None else 'n/d'):>12}"
          f"{r['marca']:>14}")
    A("")
    for r in filas_k:
        mot = "n/d" if r["motor_prom"] is None else f"{r['motor_prom']:.0f} W"
        A(f"    - {r['nombre']}: {r['n']} anuncios | precio {r['precio_min']:.0f}-"
          f"{r['precio_max']:.0f} USD (prom {r['precio_prom']:.0f}) | autonomía "
          f"{r['auton_min']:.0f}-{r['auton_max']:.0f} km (prom {r['auton_prom']:.0f}) | "
          f"motor prom {mot} ({r['motor_con_dato']}/{r['n']} con dato) | "
          f"marca {r['marca']} | batería {r['bateria']} | ubicación {r['ubicacion']}")
A("")
A(f"DESCRIPCIÓN DE CADA CLUSTER (k={k_elegido})")
A(SEP)
for info in clusters_info:
    A("")
    A(f"CLUSTER {info['id']} — {info['nombre']}")
    A(f"  Anuncios            : {info['n_anuncios']}")
    A(f"  Precio (USD)        : prom {info['precio_promedio_usd']:.2f} | "
      f"min {info['precio_min_usd']:.0f} | max {info['precio_max_usd']:.0f}")
    A(f"  Autonomía (km)      : prom {info['autonomia_promedio_km']:.1f} | "
      f"min {info['autonomia_min_km']} | max {info['autonomia_max_km']}")
    A(f"  Motor (W, descript.): prom {info['motor_promedio_w']} | "
      f"min {info['motor_min_w']} | max {info['motor_max_w']} "
      f"({info['motor_con_dato_n']}/{info['n_anuncios']} con dato, "
      f"{info['motor_sin_dato_n']} sin dato)")
    A(f"  Marca más común     : {info['marca_mas_comun']}")
    A(f"  Tipo de batería     : {info['tipo_bateria_mas_comun']}")
    A(f"  Ubicación más común : {info['ubicacion_mas_comun']}")
    A(f"  Descripción         : {info['descripcion']}")
A("")
A("CONCLUSIONES")
A(SEP)
corr_pa = float(np.corrcoef(df["precio_usd"], df["autonomia_km"])[0, 1])
sub_m = df.dropna(subset=["motor_w"])
corr_pm = float(np.corrcoef(sub_m["precio_usd"], sub_m["motor_w"])[0, 1])
corr_ma = float(np.corrcoef(sub_m["motor_w"], sub_m["autonomia_km"])[0, 1])
A(f"  Correlación Pearson precio~autonomia_km (n={despues})        : {corr_pa:+.4f}")
A(f"  Correlación Pearson precio~motor_w      (n={len(sub_m)})      : {corr_pm:+.4f}")
A(f"  Correlación Pearson motor_w~autonomia_km(n={len(sub_m)})      : {corr_ma:+.4f}")
A("")
A(f"  - Precio y autonomía se correlacionan {abs(corr_pa):.2f}: el precio sube con la")
A("    autonomía, que es la dimensión que mejor separa los segmentos del catálogo.")
A(f"  - El rango de precio es {df['precio_usd'].min():.0f}-{df['precio_usd'].max():.0f} USD "
  f"y el de autonomía {df['autonomia_km'].min():.0f}-{df['autonomia_km'].max():.0f} km; "
  "el clustering describe el catálogo,")
A("    no predice el precio de forma exacta (hay solapamiento entre segmentos).")
A(f"  - Limitaciones: n={despues} anuncios; ubicacion solo tiene "
  f"{df['ubicacion'].nunique()} valores distintos y tipo_bateria {df['tipo_bateria'].nunique()}, "
  "así que")
A("    esos campos no discriminan entre clusters. Además 9/30 anuncios no tienen")
A("    motor, por lo que su motor_promedio_w se calcula sobre una submuestra.")
A("    Conviene re-ejecutar con más anuncios antes de decisiones de precio o compra.")
with open("resumen_clustering.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(L) + "\n")
print("  guardado: resumen_clustering.txt")

# ---------- 11. VALIDACIÓN ----------
print()
print(SEP)
print("PASO 11 — VALIDACIÓN")
print(SEP)
r = subprocess.run([PY, "-m", "json.tool", "bicis_con_clusters.json"],
                   capture_output=True, text=True)
print(f"  python3 -m json.tool bicis_con_clusters.json -> exit={r.returncode} "
      f"({'JSON VÁLIDO' if r.returncode == 0 else 'ERROR'})")
texto = open("bicis_con_clusters.json", encoding="utf-8").read()
malos = [t for t in ("NaN", "Infinity") if t in texto]
print(f"  recheck: sin literales NaN/Infinity -> {'OK' if not malos else 'FALLA: ' + str(malos)}")
con = json.loads(texto, parse_constant=lambda c: (_ for _ in ()).throw(
    ValueError(f"constante JSON no válida: {c}")))
print("  recheck: parseo estricto (parse_constant estricto) -> OK")
print(f"  recheck: k_elegido={con['k_elegido']}, clusters={len(con['clusters'])}, "
      f"anuncios={len(con['anuncios'])}, fecha={con['fecha_analisis']}")
print(f"  recheck: suma de n_anuncios = {sum(c['n_anuncios'] for c in con['clusters'])} "
      f"(debe ser {len(df)})")
print(f"  recheck: todos los anuncios tienen 'cluster' = "
      f"{all('cluster' in a for a in con['anuncios'])}")
print(f"  recheck: los 9 anuncios sin motor siguen en la salida y con motor=null = "
      f"{sum(1 for a in con['anuncios'] if a['motor_w'] is None)}")
fidele = all(
    {k: v for k, v in a.items() if k != "cluster"} == anuncios[int(p)]
    for a, p in zip(con["anuncios"], pos_final))
print(f"  recheck: cada anuncio conserva TODAS sus claves/valores originales = {fidele}")

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

# ---------- 12. RESUMEN FINAL ----------
print()
print(SEP)
print("PASO 12 — RESUMEN FINAL")
print(SEP)
print(f"  Anuncios clusterizados : {len(df)} (de {len(anuncios)} en el JSON)")
print(f"  Features               : {', '.join(FEATURES)}  (motor_w solo descriptivo)")
print(f"  Clusters               : {k_elegido}  (silhouette={SIL_FINAL:.4f})")
for info in clusters_info:
    print(f"    - Cluster {info['id']}: \"{info['nombre']}\"  n={info['n_anuncios']:>2}  "
          f"precio {info['precio_min_usd']:.0f}-{info['precio_max_usd']:.0f} USD "
          f"(prom {info['precio_promedio_usd']:.0f})  "
          f"auton {info['autonomia_min_km']:.0f}-{info['autonomia_max_km']:.0f} km")
print("  Archivos generados:")
for f_ in ["bicis_con_clusters.json", "resumen_clustering.txt",
           "clusters_dispersion.png", "clusters_boxplot.png",
           "clusters_metricas.png"]:
    print(f"    - {f_}  ({os.path.getsize(f_):,} bytes)")
print(f"  Original sin tocar    : {ORIGEN}")
print(SEP)
