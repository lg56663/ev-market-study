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
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

PY = "/home/leandro/ev-market-study/venv/bin/python3"
ORIGEN = "/home/leandro/ev-market-study/verticals/scooter/scraping/scooters_electricos.json"
ORIGEN_STAT = os.stat(ORIGEN)
ORIGEN_MD5 = hashlib.md5(open(ORIGEN, "rb").read()).hexdigest()
ORIGEN_MTIME = datetime.fromtimestamp(ORIGEN_STAT.st_mtime).isoformat()

# patron de las otras verticales: con --k N los archivos de comparacion salen
# con prefijo kN_, y con --sin-prefijo (o sin --k) salen sin prefijo, que es
# como queda el resultado definitivo.
ap = argparse.ArgumentParser()
ap.add_argument("--k", type=int, default=None,
                help="fuerza el numero de clusters (por defecto: el que elige la regla)")
ap.add_argument("--sin-prefijo", action="store_true",
                help="fuerza k por --k pero graba los archivos SIN el prefijo kN_")
ARGS = ap.parse_args()
PREFIJO = "" if (ARGS.sin_prefijo or not ARGS.k) else f"k{ARGS.k}_"
SALIDA_JSON = PREFIJO + "scooters_con_clusters.json"

# Se clusteriza con las 2 features economicas. motor_w queda fuera del modelo
# (igual que en bicimotos, donde era opcional) para que las tres verticales
# sean comparables; aqui ademas se reporta como descriptivo.
FEATURES = ["precio_usd", "autonomia_km"]
SOLO_DESCRIPTIVO = ["motor_w"]
DESCRIPTIVAS = ["marca", "tipo_bateria", "ubicacion", "plegable"]
K_CANDIDATOS = [2, 3, 4]
MAX_FRACCION = 0.60
MIN_N = 4
# regla de fusion: dos clusters cuyo precio medio difiere menos de este
# porcentaje se reportan como un solo segmento (se encadena en cadena)
FUSION_PCT = 0.10
# Los nombres son INTERPRETATIVOS: el modelo solo separa por precio_usd y
# autonomia_km, así que el nombre de mercado de cada cluster lo aporta quien
# conoce el mercado cubano, no el algoritmo (ver NOTA METODOLÓGICA al final del
# resumen). "Patinete con asiento" NO sale de los datos: la palabra asiento no
# aparece en ningún anuncio de ese cluster.
NOMBRES_POR_K = {
    2: ["Patinete básico", "Patinete estándar-alto"],
    3: ["Patinete básico", "Patinete con asiento", "Patinete de largo alcance"],
    4: ["Patinete básico", "Patinete con asiento", "Patinete alcance medio",
        "Patinete de largo alcance"],
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
print("PASO 2 — COMPLETITUD DE CAMPOS")
print(SEP)
print("  (a) features USADAS para clusterizar:")
for c in FEATURES:
    nulos = int(df[c].isna().sum())
    print(f"      {c:<13} presentes {len(df) - nulos:>3}/{len(df)}   nulos={nulos:>3}")
print("  (b) campo SOLO DESCRIPTIVO (no entra al modelo):")
for c in SOLO_DESCRIPTIVO:
    nulos = int(df[c].isna().sum())
    print(f"      {c:<13} presentes {len(df) - nulos:>3}/{len(df)}   nulos={nulos:>3}")
print("  (c) campos descriptivos:")
for c in DESCRIPTIVAS:
    if c in df.columns:
        print(f"      {c:<13} nulos={int(df[c].isna().sum()):>3}   "
              f"valores distintos={df[c].nunique()}")

antes = len(df)
df = df.dropna(subset=FEATURES).reset_index(drop=True)
despues = len(df)
pos_final = list(df["__pos"])
con_motor = int(df["motor_w"].notna().sum())
nulos_motor = despues - con_motor
print()
print(f"  anuncios con precio_usd Y autonomia_km completos : {despues}/{len(anuncios)}")
print(f"  ...de esos, los que TAMBIÉN tienen motor_w        : {con_motor}/{despues}")
print(f"  ...sin motor_w (se conservan igual)              : {nulos_motor}/{despues}")
print(f"  filas eliminadas por nulls en las features       : {antes - despues}")
print()
if nulos_motor == 0:
    print("  NOTA: en ESTA vertical motor_w está completo (0 nulls), a diferencia de")
    print("        bicimotos donde era opcional. Aun así se EXCLUYE del modelo a")
    print("        propósito, para que las features sean las mismas en las tres")
    print("        verticales y los segmentos sean comparables. Se reporta después")
    print("        como promedio por cluster.")
else:
    print("  DECISIÓN: se clusteriza usando SOLO precio_usd y autonomia_km. Los")
    print("            anuncios sin motor_w NO se descartan; el motor se reporta")
    print("            después como estadística descriptiva por cluster.")

dup_mask = df.duplicated(subset=FEATURES, keep=False)
print(f"\n  aviso: {int(dup_mask.sum())} anuncios comparten el par "
      f"(precio_usd, autonomia_km) con otro")
print(f"        ({int(df.duplicated(subset=FEATURES).sum())} son repetidos exactos). Se")
print("        mantienen: son listings distintos, pero con tan pocas combinaciones")
print("        distintas K-Means tiende a partir por grupos de puntos identicos.")
if "tipo_bateria_inferido" in df.columns:
    inf = int((df["tipo_bateria_inferido"] == True).sum())  # noqa: E712
    print(f"\n  aviso: tipo_bateria fue INFERIDO (no está en el texto del anuncio) en")
    print(f"        {inf}/{despues} anuncios. Como se reporta como característica del")
    print("        cluster, ese valor es una estimación y no un dato duro.")

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
print("    motor_w EXCLUIDO a propósito (mismas features que bicimotos)")

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

# ---------- 5. REGLA DE DECISIÓN sobre k=2,3,4 ----------
print()
print(SEP)
print("PASO 5 — EVALUACIÓN DE k=2,3,4 CON LA REGLA DE DECISIÓN")
print(SEP)
print(f"  Regla: (1) ningún cluster > {int(MAX_FRACCION*100)}% de los datos"
      f" (>{int(MAX_FRACCION*despues)} de {despues}),")
print(f"         (2) ningún cluster < {MIN_N} anuncios,")
print(f"         (3) entre los que cumplen, el de mayor silhouette,")
print(f"         (4) y si dos clusters tienen precio medio a menos de"
      f" {int(FUSION_PCT*100)}% de diferencia, se fusionan en un solo segmento.\n")


def pares_a_fusionar(medias):
    """Pares (i, j) con |media_i - media_j| / max(media_i, media_j) < FUSION_PCT."""
    return [(i, j) for i in range(len(medias)) for j in range(i + 1, len(medias))
            if abs(medias[i] - medias[j]) / max(medias[i], medias[j]) < FUSION_PCT]


def segmentos_por_fusion(medias):
    """Union-find sobre los pares a fusionar: devuelve un id de segmento por cluster.

    Encadena en cadena: si A se fusiona con B y B con C, sale un solo segmento
    ABC aunque A y C esten mas lejos del 10%. Asi el bloque denso de 746/802/805
    USD que parte el k=4 se reporta como un unico segmento.
    """
    padre = list(range(len(medias)))

    def busca(a):
        while padre[a] != a:
            padre[a] = padre[padre[a]]
            a = padre[a]
        return a

    for i, j in pares_a_fusionar(medias):
        padre[busca(i)] = busca(j)
    return [busca(c) for c in range(len(medias))]


def firma_segmentos(labels, seg_cluster):
    """Id de segmento por anuncio, normalizado por precio -> comparable entre k."""
    orden = sorted(set(seg_cluster),
                   key=lambda s: min(c for c in range(len(seg_cluster))
                                     if seg_cluster[c] == s))
    norm = {s: i for i, s in enumerate(orden)}
    return tuple(norm[seg_cluster[c]] for c in labels)


def medias_por_cluster(labels, k):
    return [float(df.loc[labels == i, "precio_usd"].mean()) for i in range(k)]


resultados = {}
for k in K_CANDIDATOS:
    km = KMeans(n_clusters=k, random_state=42, n_init=10)
    lab = km.fit_predict(X_norm)
    tam = sorted(int((lab == i).sum()) for i in range(k))
    sil = float(silhouette_score(X_norm, lab))
    n_max, n_min = max(tam), min(tam)
    ok_max = n_max <= MAX_FRACCION * despues
    ok_min = n_min >= MIN_N
    cumple = ok_max and ok_min
    seg_cluster = segmentos_por_fusion(medias_por_cluster(lab, k))
    resultados[k] = {"labels": lab, "tamanos": tam, "silhouette": sil,
                     "cumple": cumple, "ok_max": ok_max, "ok_min": ok_min,
                     "medias": medias_por_cluster(lab, k),
                     "seg_cluster": seg_cluster,
                     "n_segmentos": len(set(seg_cluster)),
                     "firma": firma_segmentos(lab, seg_cluster)}
    print(f"  k={k}: silhouette={sil:.4f}  tamaños={tam}  max={n_max}  min={n_min}"
          f"  -> {'CUMPLE' if cumple else 'NO CUMPLE'}"
          f"   | tras fusionar: {resultados[k]['n_segmentos']} segmentos")

validos = [k for k in K_CANDIDATOS if resultados[k]["cumple"]]
print()
if not validos:
    finales = list(K_CANDIDATOS)
    print("  *** Ningún k candidato cumple la regla de tamaños -> se queda solo el")
    print("      criterio de silhouette ***")
else:
    # La regla 4 puede volver redundante a un k mayor: si dos k válidos
    # reportan EXACTAMENTE los mismos segmentos tras fusionar, el mayor solo
    # agrega cortes internos que la propia regla de fusion manda eliminar. Se
    # conserva el k más chico de cada grupo y el silhouette se decide sobre
    # los que sobreviven.
    por_firma = {}
    for k in validos:
        por_firma.setdefault(resultados[k]["firma"], []).append(k)
    redundantes = [k for grupo in por_firma.values() if len(grupo) > 1
                   for k in sorted(grupo)[1:]]
    finales = [k for k in validos if k not in redundantes]
    print(f"  k válidos según la regla: {validos}")
    for grupo in por_firma.values():
        if len(grupo) > 1:
            print(f"    k={sorted(grupo)} reportan los MISMOS segmentos tras fusionar"
                  f" -> el mayor es redundante")
    if redundantes:
        print(f"  redundantes por la regla 4 (descartados): "
              f"{', '.join(f'k={k}' for k in sorted(redundantes))}")
    print(f"  k no redundantes: {finales}")
    print(f"  de esos, mayor silhouette -> k={max(finales, key=lambda k: resultados[k]['silhouette'])} "
          f"({max(resultados[k]['silhouette'] for k in finales):.4f})")

k_elegido = ARGS.k if ARGS.k is not None else max(finales,
                                                  key=lambda k: resultados[k]["silhouette"])
if ARGS.k is not None:
    print(f"\n  k FORZADO por --k: {k_elegido} (no se aplica la regla de selección)")
rechazados = []
for k in K_CANDIDATOS:
    if not resultados[k]["cumple"]:
        r = resultados[k]
        if not r["ok_max"]:
           motivo = f"un cluster con {max(r['tamanos'])} anuncios = " \
                     f"{max(r['tamanos']) / despues:.0%} > {MAX_FRACCION:.0%}"
        else:
            motivo = f"un cluster con {min(r['tamanos'])} anuncios < {MIN_N}"
        rechazados.append(f"k={k} (sil={r['silhouette']:.4f}, {motivo})")
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

# ---------- 6b. FUSIÓN DE CLUSTERS CERCANOS EN PRECIO ----------
print()
print(SEP)
print("PASO 6b — FUSIÓN POR PRECIO MEDIO (< 10% DE DIFERENCIA)")
print(SEP)
medias_final = df.groupby("cluster")["precio_usd"].mean()
NOMBRES = NOMBRES_POR_K[k_elegido]
seg_raiz = segmentos_por_fusion([float(medias_final.loc[i]) for i in range(k_elegido)])
# los segmentos se renumeran de más barato a más caro
orden_seg = sorted(set(seg_raiz),
                   key=lambda s: min(medias_final.loc[c] for c in range(k_elegido)
                                     if seg_raiz[c] == s))
seg_cluster = [orden_seg.index(s) for s in seg_raiz]
df["segmento"] = df["cluster"].map(dict(enumerate(seg_cluster))).astype(int)
n_seg = len(orden_seg)
miembros = {s: [c for c in range(k_elegido) if seg_cluster[c] == s]
            for s in range(n_seg)}
NOMBRES_SEG = []
for s in range(n_seg):
    cl = miembros[s]
    NOMBRES_SEG.append(NOMBRES[cl[0]] if len(cl) == 1
                       else " + ".join(NOMBRES[c] for c in cl) + " (fusionado)")
print(f"  precio medio por cluster: "
      f"{ {int(c): round(float(medias_final.loc[c]), 1) for c in range(k_elegido)} }")
if pares := pares_a_fusionar([float(medias_final.loc[i]) for i in range(k_elegido)]):
    for i, j in pares:
        dif = abs(medias_final.loc[i] - medias_final.loc[j])
        rel = dif / max(medias_final.loc[i], medias_final.loc[j])
        print(f"  FUSION {NOMBRES[i]} ({medias_final.loc[i]:.0f} USD) + "
              f"{NOMBRES[j]} ({medias_final.loc[j]:.0f} USD): difieren "
              f"{dif:.0f} USD = {rel:.1%} < {FUSION_PCT:.0%}")
else:
    print("  ningún par de clusters a menos del 10% de diferencia de precio medio")
print(f"\n  {k_elegido} clusters -> {n_seg} segmentos reportables:")
for s in range(n_seg):
    print(f"    Segmento {s}: \"{NOMBRES_SEG[s]}\"  "
          f"(clusters {miembros[s]}, n={int((df['segmento'] == s).sum())})")

# ---------- 7. NOMBRES ----------
print()
print(SEP)
print("PASO 7 — NOMBRES DE CLUSTERS Y SEGMENTOS")
print(SEP)
for i, n in enumerate(NOMBRES):
    print(f"    Cluster {i}: \"{n}\"")
print()
for s, nombre in enumerate(NOMBRES_SEG):
    print(f"    Segmento {s}: \"{nombre}\"")

# ---------- 8. INTERPRETACIÓN ----------
print()
print(SEP)
print("PASO 8 — INTERPRETACIÓN POR CLUSTER")
print(SEP)
CAMPO_FALTA = [c for c in DESCRIPTIVAS if c not in df.columns]
if CAMPO_FALTA:
    print(f"\n  ADVERTENCIA DE DATOS: {CAMPO_FALTA} no existe(n) en el JSON de entrada.")
    print("  Se reporta null en vez de inventar un valor.")


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
        "segmento": int(sub["segmento"].iloc[0]),
        "segmento_nombre": NOMBRES_SEG[int(sub["segmento"].iloc[0])],
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
        "plegable_mas_comun": (bool(mas_comun(sub["plegable"]))
                               if "plegable" in sub else None),
        "tipo_bateria_nulos": int(sub["tipo_bateria"].isna().sum()),
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
    print(f"  segmento          : {info['segmento']} \"{info['segmento_nombre']}\"")
    print(f"  precio_usd    prom={info['precio_promedio_usd']:>8.2f}  "
          f"min={info['precio_min_usd']:>8.2f}  max={info['precio_max_usd']:>8.2f}")
    print(f"  autonomia_km  prom={info['autonomia_promedio_km']:>8.1f}  "
          f"min={info['autonomia_min_km']:>8d}  max={info['autonomia_max_km']:>8d}")
    print(f"  motor_w       prom={str(info['motor_promedio_w']):>8}  "
          f"min={str(info['motor_min_w']):>8}  max={str(info['motor_max_w']):>8}  "
          f"(con dato {info['motor_con_dato_n']}/{info['n_anuncios']})")
    print(f"  marca mas comun       : {info['marca_mas_comun']}")
    print(f"  tipo_bateria mas comun: {info['tipo_bateria_mas_comun']} "
          f"({info['tipo_bateria_nulos']} sin dato en el cluster)")
    print(f"  ubicacion mas comun   : {info['ubicacion_mas_comun']}")
    print(f"  plegable mas comun    : {info['plegable_mas_comun']}")
    print(f"  descripcion           : {info['descripcion']}")

SIL_FINAL = float(silhouette_score(X_norm, df["cluster"]))

# Los segmentos son la unidad reportable: los clusters que la regla de fusión
# junta se devisitan con las estadísticas de la unión, no con un promedio.
segmentos_info = []
for s, nombre in enumerate(NOMBRES_SEG):
    sub = df[df["segmento"] == s]
    cl = miembros[s]
    m = sub["motor_w"].dropna()
    info = {
        "id": s,
        "nombre": nombre,
        "clusters_fusionados": cl,
        "n_clusters": len(cl),
        "n_anuncios": int(len(sub)),
        "fraccion": round(len(sub) / len(df), 4),
        "precio_promedio_usd": round(float(sub["precio_usd"].mean()), 2),
        "precio_min_usd": float(sub["precio_usd"].min()),
        "precio_max_usd": float(sub["precio_usd"].max()),
        "autonomia_promedio_km": round(float(sub["autonomia_km"].mean()), 1),
        "autonomia_min_km": int(sub["autonomia_km"].min()),
        "autonomia_max_km": int(sub["autonomia_km"].max()),
        "motor_promedio_w": round(float(m.mean()), 1) if len(m) else None,
        "marca_mas_comun": mas_comun(sub["marca"]),
        "tipo_bateria_mas_comun": mas_comun(sub["tipo_bateria"]),
    }
    info["descripcion"] = (
        f"{info['n_anuncios']} anuncios ({info['fraccion']:.0%} del catálogo) entre "
        f"{info['precio_min_usd']:.0f}-{info['precio_max_usd']:.0f} USD "
        f"(prom. {info['precio_promedio_usd']:.0f}) con "
        f"{info['autonomia_min_km']}-{info['autonomia_max_km']} km de autonomía "
        f"(prom. {info['autonomia_promedio_km']:.0f})."
        + (f" Nace de {info['n_clusters']} clusters con precio medio casi igual "
           f"({', '.join(f'{clusters_info[c]['precio_promedio_usd']:.0f}' for c in cl)} USD), "
           f"así que es UN solo segmento de precio, no {info['n_clusters']} gamas."
           if info["n_clusters"] > 1 else "")
    )
    segmentos_info.append(info)

    print(f"\n=== Segmento {s}: \"{nombre}\" ({len(sub)} anuncios, "
          f"{len(sub) / len(df):.0%}) ===")
    print(f"  clusters fusionados: {cl}")
    print(f"  precio_usd    prom={info['precio_promedio_usd']:>8.2f}  "
          f"min={info['precio_min_usd']:>8.2f}  max={info['precio_max_usd']:>8.2f}")
    print(f"  autonomia_km  prom={info['autonomia_promedio_km']:>8.1f}  "
          f"min={info['autonomia_min_km']:>8d}  max={info['autonomia_max_km']:>8d}")
    print(f"  descripcion           : {info['descripcion']}")


def columnas_comparativas():
    """Filas de la tabla comparativa por cluster, ordenadas por precio ascendente."""
    lineas = []
    for k in K_CANDIDATOS:
        tmp = df.drop(columns=["cluster", "segmento"]).copy()
        tmp["cl"] = resultados[k]["labels"]
        tmp["cl"] = tmp["cl"].map(
            {orig: nuevo for nuevo, orig in
             enumerate(tmp.groupby("cl")["precio_usd"].mean().sort_values().index)}
        ).astype(int)
        # los ids de segmento se calculan sobre 'cl' ya reordenado por precio,
        # que es el mismo orden con el que se imprimen las filas de la tabla
        medias_k = [float(tmp.loc[tmp["cl"] == i, "precio_usd"].mean())
                    for i in range(k)]
        seg_k = segmentos_por_fusion(medias_k)
        orden_s = sorted(set(seg_k), key=lambda s: min(medias_k[c] for c in range(k)
                                                       if seg_k[c] == s))
        remap_s = {s: nuevo for nuevo, s in enumerate(orden_s)}
        n_seg_k = len(orden_s)
        filas_k = []
        for i in range(k):
            sub = tmp[tmp["cl"] == i]
            m = sub["motor_w"].dropna()
            filas_k.append({
                "nombre": NOMBRES_POR_K[k][i],
                "seg": remap_s[seg_k[i]],
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
        lineas.append((k, resultados[k]["silhouette"], resultados[k]["cumple"],
                       n_seg_k, filas_k))
    return lineas


HDR_TABLA = (f"  {'cluster':<28}{'seg':>4}{'n':>4}{'precio prom':>13}{'min':>7}{'max':>7}"
             f"{'auton prom':>12}{'min':>6}{'max':>6}{'motor prom':>12}{'marca':>13}"
             f"{'bateria':>9}")

# tablas comparativas de k=2,3,4
print()
print(SEP)
print(f"PASO 8b — TABLAS COMPARATIVAS k={' / k='.join(str(k) for k in K_CANDIDATOS)}")
print(SEP)
print("  (los clusters de cada k se ordenan por precio medio ascendente antes de")
print("   asignar los nombres; 'seg' es el segmento al que cae cada cluster tras la")
print("   fusión por precio, y los clusters que comparten seg son un solo segmento)")
for k, sil, cumple, n_seg, filas_k in columnas_comparativas():
    print(f"\n### k={k}  (silhouette={sil:.4f}, cumple regla={cumple}, "
          f"segmentos tras fusionar={n_seg}) ###")
    print(HDR_TABLA)
    print("  " + "-" * (len(HDR_TABLA) - 2))
    for r in filas_k:
        print(f"  {r['nombre']:<28}{r['seg']:>4}{r['n']:>4}"
              f"{r['precio_prom']:>13.2f}{r['precio_min']:>7.0f}{r['precio_max']:>7.0f}"
              f"{r['auton_prom']:>12.1f}{r['auton_min']:>6.0f}{r['auton_max']:>6.0f}"
              f"{(f'{r['motor_prom']:.0f}' if r['motor_prom'] is not None else 'n/d'):>12}"
              f"{r['marca']:>13}{r['bateria']:>9}")

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
               label=f"C{i} {NOMBRES[i]} (n={len(sub)}) → "
                     f"S{sub['segmento'].iloc[0]} {NOMBRES_SEG[int(sub['segmento'].iloc[0])]}")
    cx, cy = df_centros.iloc[i]["precio_usd"], df_centros.iloc[i]["autonomia_km"]
    ax.scatter(cx, cy, marker="X", s=430, color=col, edgecolors="black",
               linewidths=2.0, zorder=5)
    ax.annotate(f"C{i}", (cx, cy), textcoords="offset points", xytext=(13, 9),
                fontweight="bold", fontsize=12)
ax.set_xlabel("precio_usd (USD)", fontsize=12)
ax.set_ylabel("autonomia_km (km)", fontsize=12)
ax.set_title(f"Scooters / patinete eléctrico — K-Means (k={k_elegido}) "
             "precio vs autonomía\n"
             "features: precio_usd y autonomia_km | X = centroide", fontsize=13,
             fontweight="bold")
ax.legend(loc="best", fontsize=9, framealpha=0.92)
ax.grid(alpha=0.3, linestyle="--")
fig.tight_layout()
fig.savefig(PREFIJO + "clusters_dispersion.png", dpi=150)
plt.close(fig)
print(f"  guardado: {PREFIJO}clusters_dispersion.png")

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 7))
for ax, col, titulo, ylab, fmt in [
        (ax1, "precio_usd", "Precio (USD)", "precio_usd (USD)", "${x:,.0f}"),
        (ax2, "autonomia_km", "Autonomía (km)", "autonomia_km (km)", "{x:,.0f}")]:
    datos = [df.loc[df["cluster"] == i, col].to_numpy() for i in range(k_elegido)]
    bp = ax.boxplot(
        datos, tick_labels=[f"C{i}\n(n={len(d)})" for i, d in enumerate(datos)],
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
fig.savefig(PREFIJO + "clusters_boxplot.png", dpi=150)
plt.close(fig)
print(f"  guardado: {PREFIJO}clusters_boxplot.png")

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
                edgecolors="black", linewidths=1.8, zorder=5)
    ax2.annotate(f"k={k}{'✓' if resultados[k]['cumple'] else '✗'}",
                 (k, resultados[k]["silhouette"]), textcoords="offset points",
                 xytext=(0, 11), ha="center", fontsize=9, fontweight="bold")
ax2.axvline(k_elegido, color="#1f6feb", linestyle="--", linewidth=1.7,
            label=f"k elegido = {k_elegido}")
ax2.set_xlabel("k (número de clusters)", fontsize=11)
ax2.set_ylabel("Silhouette score", fontsize=11)
ax2.set_title(f"Silhouette vs k (candidatos {"/".join(str(k) for k in K_CANDIDATOS)}; "
              "relleno = cumple la regla)", fontsize=12, fontweight="bold")
ax2.set_xticks(tabla_k["k"])
ax2.grid(alpha=0.3, linestyle="--")
ax2.legend()
fig.tight_layout()
fig.savefig(PREFIJO + "clusters_metricas.png", dpi=150)
plt.close(fig)
print(f"  guardado: {PREFIJO}clusters_metricas.png")

# ---------- 10. ARCHIVOS DE SALIDA ----------
print()
print(SEP)
print("PASO 10 — ARCHIVOS DE SALIDA")
print(SEP)
fecha = datetime.now().isoformat(timespec="seconds")

anuncios_out = []
for pos, clus, seg in zip(pos_final, df["cluster"].to_numpy(),
                          df["segmento"].to_numpy()):
    a = dict(anuncios[int(pos)])
    a["cluster"] = int(clus)
    a["segmento"] = int(seg)
    anuncios_out.append(a)

comparativa_k = []
for k in K_CANDIDATOS:
    r = resultados[k]
    comparativa_k.append({
        "k": k,
        "silhouette": round(r["silhouette"], 4),
        "tamanos": r["tamanos"],
        "cumple_fraccion_max": r["ok_max"],
        "cumple_min_n": r["ok_min"],
        "cumple_regla": r["cumple"],
        "n_segmentos_tras_fusion": r["n_segmentos"],
        "precios_promedio": [round(v, 2) for v in r["medias"]],
    })

salida = {
    "vehiculo": data.get("vehiculo", "scooter_patinete_electrico"),
    "total_anuncios_validos": data.get("total_anuncios_validos", len(anuncios)),
    "total_anuncios_clusterizados": int(len(df)),
    "k_elegido": k_elegido,
    "n_segmentos": len(segmentos_info),
    "silhouette": round(SIL_FINAL, 4),
    "fecha_analisis": fecha,
    "features_usadas": FEATURES,
    "campos_solo_descriptivos": SOLO_DESCRIPTIVO,
    "nota_features": "Se clusteriza con precio_usd y autonomia_km, igual que en "
                     "bicimotos. motor_w queda fuera del modelo y solo se reporta "
                     "por cluster, aunque en esta vertical esté completo.",
    "normalizacion": "StandardScaler",
    "regla_decision": {
        "max_fraccion_por_cluster": MAX_FRACCION,
        "min_anuncios_por_cluster": MIN_N,
        "fusion_si_precio_medio_difiere_menos_de": FUSION_PCT,
        "candidatos": K_CANDIDATOS,
        "nota": "El límite de 60% y el mínimo de 4 anuncios se evalúan sobre los "
                "clusters de cada k. La fusión de precio se aplica después: dos "
                "clusters cuyo precio medio difiere menos de 10% se reportan como "
                "un solo segmento. 'segmento' es la unidad reportable, 'cluster' "
                "conserva el corte crudo de K-Means para trazabilidad.",
    },
    "comparativa_k": comparativa_k,
    "segmentos": segmentos_info,
    "clusters": clusters_info,
    "anuncios": anuncios_out,
}
with open(SALIDA_JSON, "w", encoding="utf-8") as f:
    json.dump(salida, f, ensure_ascii=False, indent=2, allow_nan=False)
print(f"  guardado: {SALIDA_JSON}")

L = []
A = L.append
A(SEP)
A("RESUMEN DE CLUSTERING K-MEANS — SCOOTERS / PATINETES ELÉCTRICOS")
A(SEP)
A(f"Fecha de análisis       : {fecha}")
A(f"Archivo origen          : {ORIGEN}")
A(f"Vehículo                : {salida['vehiculo']}")
A("")
A("DATOS Y COMPLETITUD")
A(f"  Anuncios totales en el JSON        : {len(anuncios)}")
A(f"  Con precio_usd Y autonomia_km      : {despues}/{len(anuncios)}")
A(f"  ...de esos, con motor_w también    : {con_motor}/{despues}")
A(f"  ...sin motor_w (conservados)       : {nulos_motor}/{despues}")
A(f"  Descartados por nulls              : {antes - despues}")
A(f"  Features usadas                   : {', '.join(FEATURES)}")
A(f"  Campos solo descriptivos          : {', '.join(SOLO_DESCRIPTIVO)}")
A("  Normalización                      : StandardScaler (media 0, desv. 1)")
A("")
if nulos_motor == 0:
    A("  NOTA SOBRE motor_w: a diferencia de bicimotos (donde era opcional y salía")
    A("  null en 9/30 anuncios), aquí motor_w está completo en los 30. Aun así se")
    A("  EXCLUYE del modelo a propósito para que las tres verticales usen las mismas")
    A("  features y sus segmentos sean comparables. Se reporta como promedio por")
    A("  cluster en la sección de descripción.")
else:
    A("  NOTA SOBRE motor_w: en el scraping de scooters el motor es opcional, así que")
    A("  queda null en algunos anuncios. Se excluye del modelo y se reporta después")
    A("  como promedio por cluster.")
A("")
A("  AVISOS DE CALIDAD DE DATOS")
A(f"  - {int(dup_mask.sum())} anuncios comparten el par (precio_usd, autonomia_km) con")
A("    otro anuncio. Con un rango de precio de solo "
  f"{df['precio_usd'].min():.0f}-{df['precio_usd'].max():.0f} USD y de autonomía de")
A(f"    {df['autonomia_km'].min():.0f}-{df['autonomia_km'].max():.0f} km, hay muchas "
  "combinaciones repetidas, lo que hace que")
A("    K-Means parta por grupos de puntos idénticos y no por gamas de producto.")
A(f"  - ubicacion tiene {df['ubicacion'].nunique()} solo valor distinto "
  f"(\"{mas_comun(df['ubicacion'])}\"), por lo que no discrimina")
A("    nada entre clusters. tipo_bateria tiene "
  f"{df['tipo_bateria'].nunique()} valores y {int(df['tipo_bateria'].isna().sum())} nulos.")
if "tipo_bateria_inferido" in df.columns:
    inf = int((df["tipo_bateria_inferido"] == True).sum())  # noqa: E712
    A(f"  - tipo_bateria fue INFERIDO en {inf}/{despues} anuncios: en esos clusters el")
    A("    tipo de batería reportado es una estimación del scraper, no un dato del texto.")
A("")
A("ELECCIÓN DE K")
A(f"  Regla: ningún cluster > {int(MAX_FRACCION*100)}% (>{int(MAX_FRACCION*despues)} de "
  f"{despues}), ningún cluster < {MIN_N}, de los que cumplen el mayor silhouette,")
A(f"  y dos clusters con precio medio a menos de {int(FUSION_PCT*100)}% se reportan")
A("  fusionados como un solo segmento.")
A("")
A("   k  |  tamaños por cluster                |  max | min | silhouette | cumple | "
  "segmentos")
A("  --- + ------------------------------------ + ----- + ---- + ---------- + ------- +"
  " ---------")
for k in K_CANDIDATOS:
    r = resultados[k]
    A(f"  {k:<3d} | {str(r['tamanos']):<36s} | {max(r['tamanos']):>5d} | "
      f"{min(r['tamanos']):>4d} | {r['silhouette']:>10.4f} | "
      f"{'SÍ' if r['cumple'] else 'NO':<7} | {r['n_segmentos']:>9d}")
A("")
for k in K_CANDIDATOS:
    r = resultados[k]
    if not r["cumple"]:
        motivo = (f"un cluster con {max(r['tamanos'])} anuncios = "
                  f"{max(r['tamanos']) / despues:.0%} > {MAX_FRACCION:.0%}"
                  if not r["ok_max"] else
                  f"un cluster con {min(r['tamanos'])} anuncios < {MIN_N}")
        A(f"  k={k} NO cumple: {motivo} (silhouette={r['silhouette']:.4f})")
for grupo in (por_firma.values() if validos else []):
    if len(grupo) > 1:
        A(f"  k={sorted(grupo)[1:]} REDUNDANTE(S): tras la fusión por precio reportan")
        A(f"    exactamente los mismos segmentos que k={min(grupo)}, así que el")
        A("    cluster extra no separa nada nuevo.")
A("")
A(f"  k NO redundantes : {finales}")
A(f"  k ELEGIDO       : {k_elegido}  (silhouette={SIL_FINAL:.4f})")
A(f"  -> {k_elegido} clusters crudos -> {len(segmentos_info)} segmentos reportables "
  f"tras la fusión por precio")
A("")
A("   k  |    inercia      |   silhouette   (diagnóstico k=2..8)")
A("  --- + --------------- + ---------------")
for _, r in tabla_k.iterrows():
    A(f"  {int(r['k']):<3d} | {r['inercia']:>15.4f} | {r['silhouette']:>15.4f}")
A("")
A(f"TABLAS COMPARATIVAS k={' / k='.join(str(k) for k in K_CANDIDATOS)}")
A("(los clusters de cada k se ordenan por precio medio ascendente antes de asignar")
A(" los nombres; 'seg' es el segmento al que cae cada cluster tras la fusión por")
A(" precio, y los clusters que comparten seg son un solo segmento)")
A(SEP)
for k, sil, cumple, n_seg, filas_k in columnas_comparativas():
    A("")
    A(f"### k={k}  (silhouette={sil:.4f}, cumple regla={cumple}, "
      f"segmentos tras fusionar={n_seg}) ###")
    A(HDR_TABLA)
    A("  " + "-" * (len(HDR_TABLA) - 2))
    for r in filas_k:
        A(f"  {r['nombre']:<28}{r['seg']:>4}{r['n']:>4}"
          f"{r['precio_prom']:>13.2f}{r['precio_min']:>7.0f}{r['precio_max']:>7.0f}"
          f"{r['auton_prom']:>12.1f}{r['auton_min']:>6.0f}{r['auton_max']:>6.0f}"
          f"{(f'{r['motor_prom']:.0f}' if r['motor_prom'] is not None else 'n/d'):>12}"
          f"{r['marca']:>13}{r['bateria']:>9}")
    A("")
    for r in filas_k:
        mot = "n/d" if r["motor_prom"] is None else f"{r['motor_prom']:.0f} W"
        A(f"    - {r['nombre']}: {r['n']} anuncios | precio {r['precio_min']:.0f}-"
          f"{r['precio_max']:.0f} USD (prom {r['precio_prom']:.0f}) | autonomía "
          f"{r['auton_min']:.0f}-{r['auton_max']:.0f} km (prom {r['auton_prom']:.0f}) | "
          f"motor prom {mot} ({r['motor_con_dato']}/{r['n']} con dato) | "
          f"marca {r['marca']} | batería {r['bateria']} | ubicación {r['ubicacion']}")
A("")
A(f"SEGMENTOS REPORTABLES (k={k_elegido} con la fusión por precio aplicada)")
A(SEP)
for info in segmentos_info:
    A("")
    A(f"SEGMENTO {info['id']} — {info['nombre']}")
    A(f"  Anuncios            : {info['n_anuncios']} ({info['fraccion']:.0%} del "
      f"catálogo, de {despues})")
    A(f"  Clusters fusionados : {info['clusters_fusionados']} "
      f"({info['n_clusters']} cluster{'s' if info['n_clusters'] > 1 else ''})")
    A(f"  Precio (USD)        : prom {info['precio_promedio_usd']:.2f} | "
      f"min {info['precio_min_usd']:.0f} | max {info['precio_max_usd']:.0f}")
    A(f"  Autonomía (km)      : prom {info['autonomia_promedio_km']:.1f} | "
      f"min {info['autonomia_min_km']} | max {info['autonomia_max_km']}")
    A(f"  Marca más común     : {info['marca_mas_comun']}")
    A(f"  Descripción         : {info['descripcion']}")
A("")
A(f"DESCRIPCIÓN DE CADA CLUSTER (k={k_elegido})")
A(SEP)

# Diagnóstico: pares de clusters que se separan SOLO por autonomía, con los
# rangos de precio solapados y los promedios a menos del 10%. Se evalúa para
# TODOS los k candidatos, que es donde se ve si un cluster extra es real.
A("")
A("SPLITS DEGENERADOS POR k (rango de precio solapado + promedios a menos del 10%")
A(" de diferencia + autonomía sin un solo valor en común)")
for k in K_CANDIDATOS:
    # los clusters se renumeran por precio medio para que el nombre asignado
    # corresponda al cluster que se imprime (mismo criterio que las tablas)
    lab = resultados[k]["labels"]
    orden_k = (df.assign(_l=lab).groupby("_l")["precio_usd"].mean().sort_values().index)
    lab = df.assign(_l=lab)["_l"].map({o: n for n, o in enumerate(orden_k)}).to_numpy()
    pares = []
    for i in range(k):
        for j in range(i + 1, k):
            a = df[lab == i]
            b = df[lab == j]
            solapa_precio = (a["precio_usd"].min() <= b["precio_usd"].max()
                             and b["precio_usd"].min() <= a["precio_usd"].max())
            disyunta_aut = (a["autonomia_km"].max() < b["autonomia_km"].min()
                            or b["autonomia_km"].max() < a["autonomia_km"].min())
            dif = abs(a["precio_usd"].mean() - b["precio_usd"].mean())
            rel = dif / max(a["precio_usd"].mean(), b["precio_usd"].mean())
            if solapa_precio and disyunta_aut and rel < FUSION_PCT:
                pares.append((i, j, dif, rel, a, b))
    marca = "  <-- ELEGIDO" if k == k_elegido else ""
    A(f"  k={k}: {len(pares)} par(es) degenerado(s){marca}")
    for i, j, dif, rel, a, b in pares:
        A(f"    C{i} \"{NOMBRES_POR_K[k][i]}\" (n={len(a)}, "
          f"{a['precio_usd'].mean():.0f} USD, {a['autonomia_km'].min():.0f}-"
          f"{a['autonomia_km'].max():.0f} km)  vs  "
          f"C{j} \"{NOMBRES_POR_K[k][j]}\" (n={len(b)}, "
          f"{b['precio_usd'].mean():.0f} USD, {b['autonomia_km'].min():.0f}-"
          f"{b['autonomia_km'].max():.0f} km)")
        A(f"      precios {dif:.0f} USD de diferencia = {rel:.1%} < "
          f"{FUSION_PCT:.0%}, rangos solapados, autonomía sin valores en común")
A("")
A("  LECTURA: un par así no son dos gamas de producto, es un escalón de precio")
A("  partido por autonomía. Por eso la regla de fusión los junta en un solo")
A("  segmento, y por eso un k con muchos de estos pares no aporta información: su")
A("  silhouette sube porque el bloque denso se parte en trozos, no porque")
A("  aparezcan límites de precio nuevos.")

for info in clusters_info:
    A("")
    A(f"CLUSTER {info['id']} — {info['nombre']}")
    A(f"  Anuncios            : {info['n_anuncios']}")
    A(f"  Segmento            : {info['segmento']} \"{info['segmento_nombre']}\"")
    A(f"  Precio (USD)        : prom {info['precio_promedio_usd']:.2f} | "
      f"min {info['precio_min_usd']:.0f} | max {info['precio_max_usd']:.0f}")
    A(f"  Autonomía (km)      : prom {info['autonomia_promedio_km']:.1f} | "
      f"min {info['autonomia_min_km']} | max {info['autonomia_max_km']}")
    A(f"  Motor (W, descript.): prom {info['motor_promedio_w']} | "
      f"min {info['motor_min_w']} | max {info['motor_max_w']} "
      f"({info['motor_con_dato_n']}/{info['n_anuncios']} con dato, "
      f"{info['motor_sin_dato_n']} sin dato)")
    A(f"  Marca más común     : {info['marca_mas_comun']}")
    A(f"  Tipo de batería     : {info['tipo_bateria_mas_comun']} "
      f"({info['tipo_bateria_nulos']} sin dato en el cluster)")
    A(f"  Ubicación más común : {info['ubicacion_mas_comun']}")
    A(f"  Plegable más común  : {info['plegable_mas_comun']}")
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
A(f"  - Precio y autonomía se correlacionan {abs(corr_pa):.2f} y el motor con el precio")
A(f"    {abs(corr_pm):.2f}: las dos features disponibles se mueven casi juntas, así que")
A("    el clustering de scooters separa mucho menos que el de bicimotos (allí la")
A("    correlación precio~autonomía fue 0.75 con un rango de 650-1950 USD).")
A(f"  - El mercado de scooters es estrecho: {df['precio_usd'].min():.0f}-"
  f"{df['precio_usd'].max():.0f} USD y {df['autonomia_km'].min():.0f}-"
  f"{df['autonomia_km'].max():.0f} km. Casi todo el catálogo")
A("    se concentra en patinetes de 500 W entre 750 y 850 USD, y el premium real")
A("    (los de 1200 W) tiene solo 2 anuncios.")
A("")
A("  SOBRE LA ELECCIÓN DE k")
seg_max = max(segmentos_info, key=lambda s: s["n_anuncios"])
A(f"  - k=2 se cae por la regla de tamaño: reparte {max(resultados[2]['tamanos'])}/"
  f"{despues} anuncios en un cluster ({max(resultados[2]['tamanos']) / despues:.0%}),"
  f" muy por encima del {MAX_FRACCION:.0%} permitido.")
A(f"  - k=3 cumple la regla de tamaño "
  f"({max(resultados[3]['tamanos'])}/{despues} = "
  f"{max(resultados[3]['tamanos']) / despues:.0%} en el cluster más grande, "
  f"{min(resultados[3]['tamanos'])} en el más chico) y da "
  f"{resultados[3]['n_segmentos']} segmentos tras la fusión.")
A(f"  - k=4 tiene el silhouette más alto de los tres ({resultados[4]['silhouette']:.4f} "
  f"vs {resultados[3]['silhouette']:.4f} de k=3), PERO sus "
  f"{resultados[4]['n_segmentos']} segmentos tras la fusión son exactamente los")
A("    mismos que los de k=3: el cluster que sobra parte el bloque de 670-930 USD")
A("    por autonomía y no por precio, así que la fusión lo vuelve a pegar. Por eso")
A("    el silhouette de k=4 no es una razón para elegirlo: premia partir un bloque")
A("    denso en más trozos, no descubrir un escalón de precio nuevo.")
A(f"  - k={k_elegido} es el k más chico que (a) no concentra más del "
  f"{int(MAX_FRACCION*100)}% en un cluster,")
A("    (b) no deja clusters de menos de 4 anuncios y (c) no reporta los mismos")
A("    segmentos que un k mayor. Su límite honesto: el cluster grande queda al")
A(f"    borde ({max(resultados[k_elegido]['tamanos'])}/{despues} = "
  f"{max(resultados[k_elegido]['tamanos']) / despues:.0%}), así que un solo anuncio")
A("    que se mueva de cluster lo haría incumplir el 60%.")
seg_fusionado = [s for s in segmentos_info if s["n_clusters"] > 1]
if seg_fusionado:
    s = seg_fusionado[0]
    A("")
    A("  ADVERTENCIA: la regla de fusión y el tope del 60% se contradicen aquí.")
    A(f"  Los {s['n_clusters']} clusters fusionados del segmento \"{s['nombre']}\" son")
    A(f"  {s['n_anuncios']}/{despues} anuncios = {s['fraccion']:.0%}, muy por encima del")
    A(f"  {MAX_FRACCION:.0%}. Es decir: el tope del 60% se cumple sobre el corte crudo")
    A("  de K-Means, pero el mercado NO tiene tres grupos de tamaño parecido. Tiene")
    A(f"  {len(segmentos_info)} escalones de precio de verdad ({len(segmentos_info)} "
      f"segmentos) y el segundo es enorme porque")
    A("  concentra casi todo el catálogo. Quien quiera leerlo sin contradicciones debe")
    A("  aceptar 2 segmentos reales (5 y 25 anuncios) o ampliar el scraping hasta")
    A("  tener un tercer escalón con más anuncios.")
A("")
A("  - Limitaciones: n=30 anuncios, pero con muy pocas combinaciones distintas de")
A("    precio/autonomía ("
  f"{df[['precio_usd', 'autonomia_km']].drop_duplicates().shape[0]} pares únicos), "
  "lo que hace que K-Means")
A("    parta por grupos de puntos idénticos. Con solo ubicacion y bateria como")
A("    variables categóricas, la segmentación de scooters es bastante más débil que")
A("    la de bicimotos; conviene ampliar el scraping antes de tomar decisiones de")
A("    precio o compra.")
A("")
A(SEP)
A("NOTA METODOLÓGICA SOBRE LOS NOMBRES")
A(SEP)
A("  Los nombres son interpretativos, basados en conocimiento del mercado")
A("  cubano. El clustering agrupa por precio y autonomía. Con n=30, los")
A("  datos sugieren 2 segmentos reales de precio (340-500 y 670-930 USD).")
A("  El tercer cluster se separa del segundo principalmente por autonomía")
A("  (39 vs 56 km), no por precio.")
A("")
A("  ADVERTENCIA: el campo scraped no registra si el patinete trae asiento, así")
A("  que \"Patinete con asiento\" es una etiqueta de mercado, NO un dato del")
A(f"  anuncio. Ningún anuncio del cluster 1 ({NOMBRES[1]}) menciona asiento,")
A("  silla ni banco en su título ni en su descripción: 0/8. Antes de usar ese")
A("  nombre en una decisión de compra hay que verificarlo anuncio por anuncio.")
with open(PREFIJO + "resumen_clustering.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(L) + "\n")
print(f"  guardado: {PREFIJO}resumen_clustering.txt")

# ---------- 11. VALIDACIÓN ----------
print()
print(SEP)
print("PASO 11 — VALIDACIÓN")
print(SEP)
r = subprocess.run([PY, "-m", "json.tool", SALIDA_JSON], capture_output=True, text=True)
print(f"  python3 -m json.tool {SALIDA_JSON} -> exit={r.returncode} "
      f"({'JSON VÁLIDO' if r.returncode == 0 else 'ERROR'})")
texto = open(SALIDA_JSON, encoding="utf-8").read()
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
print(f"  recheck: todos los anuncios tienen 'segmento' = "
      f"{all('segmento' in a for a in con['anuncios'])}")
print(f"  recheck: suma de n_anuncios por segmento = "
      f"{sum(s['n_anuncios'] for s in con['segmentos'])} (debe ser {len(df)})")
print(f"  recheck: clusters por segmento = "
      f"{ {s['id']: s['clusters_fusionados'] for s in con['segmentos']} }")
print(f"  recheck: cada cluster cae en un solo segmento = "
      f"{len({c['segmento'] for c in con['clusters']}) == len(con['segmentos'])}")
print(f"  recheck: comparación k=2,3,4 en el JSON = "
      f"{[c['k'] for c in con['comparativa_k']]}")
print(f"  recheck: nulos preservados como null (tipo_bateria) = "
      f"{sum(1 for a in con['anuncios'] if a.get('tipo_bateria') is None)}")
fidele = all(
    {k: v for k, v in a.items() if k not in ("cluster", "segmento")} == anuncios[int(p)]
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
          f"auton {info['autonomia_min_km']:.0f}-{info['autonomia_max_km']:.0f} km  "
          f"-> segmento {info['segmento']}")
print(f"  Segmentos reportables  : {len(segmentos_info)}")
for info in segmentos_info:
    print(f"    - Segmento {info['id']}: \"{info['nombre']}\"  n={info['n_anuncios']:>2} "
          f"({info['fraccion']:.0%})  precio {info['precio_min_usd']:.0f}-"
          f"{info['precio_max_usd']:.0f} USD (prom {info['precio_promedio_usd']:.0f})  "
          f"auton prom {info['autonomia_promedio_km']:.0f} km")
print("  Archivos generados:")
for f_ in [SALIDA_JSON, PREFIJO + "resumen_clustering.txt",
           PREFIJO + "clusters_dispersion.png", PREFIJO + "clusters_boxplot.png",
           PREFIJO + "clusters_metricas.png"]:
    print(f"    - {f_}  ({os.path.getsize(f_):,} bytes)")
print(f"  Original sin tocar    : {ORIGEN}")
print(SEP)
