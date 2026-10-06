import json
from pathlib import Path
import plotly.graph_objects as go

# --- Configuración de rutas ---
BASE = Path("/home/leandro/ev-market-study/verticals/analisis_mercado_electrico")
JSON_IN = BASE / "marcas_mercado_electrico.json"
HTML_OUT = BASE / "grafico_marcas_interactivo.html"

# --- Cargar datos ---
with open(JSON_IN, encoding="utf-8") as f:
    data = json.load(f)

# Tomamos las 15 marcas principales
top_marcas = data["todas_las_marcas"][:15]
marcas = [x["marca"] for x in top_marcas][::-1]  # Invertimos para que Topmaq quede arriba
n_motos = [x["n_motos"] for x in top_marcas][::-1]
n_bicis = [x["n_bicimotos"] for x in top_marcas][::-1]

# --- Crear la figura ---
fig = go.Figure()

# Traza para Motos (valores negativos para que se dibujen a la izquierda)
fig.add_trace(go.Bar(
    y=marcas,
    x=[-v for v in n_motos],
    name='Motos Eléctricas',
    orientation='h',
    marker=dict(color='#1F4E79'),
    customdata=n_motos,
    hovertemplate='<b>%{y}</b><br>Motos: %{customdata}<extra></extra>'
))

# Traza para Bicimotos (valores positivos para la derecha)
fig.add_trace(go.Bar(
    y=marcas,
    x=n_bicis,
    name='Bicimotos Eléctricas',
    orientation='h',
    marker=dict(color='#2E8B57'),
    hovertemplate='<b>%{y}</b><br>Bicimotos: %{x}<extra></extra>'
))

# --- Personalizar el layout ---
fig.update_layout(
    title=dict(
        text='<b>Análisis de Marcas del Mercado Eléctrico Cubano</b><br>' +
             '<sub>Distribución entre motos y bicimotos — 240 anuncios (Revolico + Telegram, 2026)</sub>',
        x=0.5,
        xanchor='center',
        font=dict(size=20, family='Arial', color='#2C3E50')
    ),
    barmode='relative',
    bargap=0.3,
    height=800,
    width=1100,
    paper_bgcolor='#FFFFFF',
    plot_bgcolor='#FFFFFF',
    legend=dict(
        orientation='h',
        yanchor='bottom',
        y=1.02,
        xanchor='right',
        x=1
    ),
    xaxis=dict(
        showgrid=False,
        zeroline=True,
        zerolinecolor='#BDC3C7',
        zerolinewidth=1.5,
        showticklabels=False,
        range=[-max(n_motos)*1.2, max(n_bicis)*1.2]
    ),
    yaxis=dict(
        showgrid=False,
        tickfont=dict(size=13, color='#2C3E50')
    ),
    font=dict(family='Arial', size=12, color='#2C3E50')
)

# --- Guardar como HTML interactivo ---
fig.write_html(HTML_OUT, include_plotlyjs='cdn')

print(f"✅ Gráfico interactivo guardado en: {HTML_OUT}")
