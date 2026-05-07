#!/usr/bin/env python3
"""
Monitor de perfumeriaschic.com
Detecta nuevos productos y cambios de stock/precio
Notifica por Telegram
"""

import requests
from bs4 import BeautifulSoup
import json
import os
from datetime import datetime

# ─── CONFIGURACIÓN ────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# URLs de categorías a monitorizar (añade o quita las que quieras)
URLS_A_MONITORIZAR = [
    {
        "nombre": "🧴 Perfumes",
        "url": "https://www.perfumeriaschic.com/c135344-perfumes.html"
    },
    {
        "nombre": "🔥 Ofertas Semanales",
        "url": "https://www.perfumeriaschic.com/c328441-ofertas-semanales.html"
    },
    {
        "nombre": "💰 Ofertas -10€",
        "url": "https://www.perfumeriaschic.com/c424525-ofertas-por-menos-de-10.html"
    },
    {
        "nombre": "🧪 Perfumes Tester",
        "url": "https://www.perfumeriaschic.com/c276461-perfumes-tester.html"
    },
    {
        "nombre": "💎 Perfumes Nicho",
        "url": "https://www.perfumeriaschic.com/c462392-perfumes-nicho.html"
    },
]

STATE_FILE = "estado_productos.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}
# ──────────────────────────────────────────────────────────────────


def cargar_estado():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def guardar_estado(estado):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=2)


# Crea una sesión global para que vaya súper rápido
session = requests.Session()
session.headers.update(HEADERS)

def scrape_categoria(url):
    import time
    
    productos = {}
    pagina = 1
    
    while True:
        # Añadimos el truco de pageSize=128 para descargar 128 productos de golpe
        if "?" in url:
            base_url = url.split("?")[0]
        else:
            base_url = url
            
        if pagina == 1:
            url_pag = f"{base_url}?pageSize=128"
        else:
            url_pag = f"{base_url}?Product_page={pagina}&pageSize=128"
            
        try:
            # Usamos la sesión en lugar de requests.get()
            r = session.get(url_pag, timeout=15)
            r.raise_for_status()
        except Exception as e:
            print(f"  ⚠️  Error al acceder a {url_pag}: {e}")
            break

        soup = BeautifulSoup(r.text, "html.parser")
        articulos = soup.select("figure.featured-product")
        
        if not articulos:
            break

        productos_pagina = {}

        for art in articulos:
            id_bruto = art.get('id', '')
            producto_id = id_bruto.replace('featured-product-', '')
            if not producto_id:
                continue

            link_elem = art.select_one(".featured-product-title-link")
            if not link_elem:
                continue
            
            nombre = link_elem.get_text(strip=True)
            href = link_elem.get('href', '')
            full_url = href if href.startswith("http") else "https://www.perfumeriaschic.com" + href

            precio_elem = art.select_one(".featured-product-final-price")
            precio = precio_elem.get_text(strip=True).replace('\xa0', ' ') if precio_elem else "Sin precio"

            etiqueta_agotado = art.select_one(".featured-product-ribbon")
            en_stock = True
            if etiqueta_agotado and "Agotado" in etiqueta_agotado.get_text(strip=True):
                en_stock = False

            productos_pagina[producto_id] = {
                "nombre": nombre,
                "precio": precio,
                "url": full_url,
                "en_stock": en_stock
            }

        productos.update(productos_pagina)
        print(f"    página {pagina}: {len(productos_pagina)} productos extraídos (Total acumulado: {len(productos)})")
        
        pagina += 1
        time.sleep(1)  # Bajamos la pausa a 1 segundo. Al cargar 128 de golpe, hacemos muchas menos peticiones.
        
        if pagina > 50: 
            break

    return productos


def comparar_y_notificar(nombre_cat, productos_nuevos, productos_anteriores):
    mensajes = []

    # 1. Productos NUEVOS
    nuevos = {k: v for k, v in productos_nuevos.items() if k not in productos_anteriores}
    if nuevos:
        lista = "\n".join(
            f"  • <a href='{p['url']}'>{p['nombre']}</a> — {p['precio']}"
            for p in list(nuevos.values())[:10]
        )
        extra = f"\n  <i>...y {len(nuevos)-10} más</i>" if len(nuevos) > 10 else ""
        mensajes.append(f"🆕 <b>Nuevos productos en {nombre_cat}</b>\n{lista}{extra}")

    # 2. Productos DESAPARECIDOS (ya no existen en la web)
    eliminados = {k: v for k, v in productos_anteriores.items() if k not in productos_nuevos}
    if eliminados and len(eliminados) < 20: 
        lista = "\n".join(f"  • {p['nombre']}" for p in list(eliminados.values())[:5])
        extra = f"\n  <i>...y {len(eliminados)-5} más</i>" if len(eliminados) > 5 else ""
        mensajes.append(f"❌ <b>Eliminados de la web en {nombre_cat}</b>\n{lista}{extra}")

    # 3. Cambios de PRECIO y STOCK
    cambios = []
    for k, prod_nuevo in productos_nuevos.items():
        if k in productos_anteriores:
            prod_ant = productos_anteriores[k]
            
            # Comprobar Stock
            if not prod_ant.get("en_stock", True) and prod_nuevo["en_stock"]:
                cambios.append(f"  🟢 <b>¡VUELVE A HABER STOCK!</b>\n  <a href='{prod_nuevo['url']}'>{prod_nuevo['nombre']}</a>")
            elif prod_ant.get("en_stock", True) and not prod_nuevo["en_stock"]:
                cambios.append(f"  🔴 <b>AGOTADO:</b>\n  <a href='{prod_nuevo['url']}'>{prod_nuevo['nombre']}</a>")
                
            # Comprobar Precio
            precio_ant = prod_ant.get("precio", "")
            precio_nue = prod_nuevo.get("precio", "")
            if precio_ant and precio_nue and precio_ant != precio_nue:
                cambios.append(f"  💸 <b>CAMBIO PRECIO:</b>\n  <a href='{prod_nuevo['url']}'>{prod_nuevo['nombre']}</a>\n  {precio_ant} → <b>{precio_nue}</b>")

    if cambios:
        lista = "\n\n".join(cambios[:10])
        extra = f"\n\n  <i>...y {len(cambios)-10} cambios más</i>" if len(cambios) > 10 else ""
        mensajes.append(f"⚡ <b>Actualizaciones en {nombre_cat}</b>\n\n{lista}{extra}")

    return mensajes

def enviar_telegram(mensaje):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️  No hay credenciales de Telegram configuradas")
        print("─" * 50)
        print(mensaje)
        print("─" * 50)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mensaje,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    print(f"  📤 Enviando a Telegram chat_id={TELEGRAM_CHAT_ID[:4]}...")

    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        print("  ✅ Notificación enviada por Telegram")
    except Exception as e:
        print(f"  ❌ Error enviando Telegram: {e}")


def comparar_y_notificar(nombre_cat, productos_nuevos, productos_anteriores):
    mensajes = []

    # Productos nuevos (no existían antes)
    nuevos = {k: v for k, v in productos_nuevos.items() if k not in productos_anteriores}
    if nuevos:
        lista = "\n".join(
            f"  • <a href='{p['url']}'>{p['nombre']}</a>{' — ' + p['precio'] if p['precio'] else ''}"
            for p in list(nuevos.values())[:10]  # máximo 10 por mensaje
        )
        extra = f"\n  <i>...y {len(nuevos)-10} más</i>" if len(nuevos) > 10 else ""
        mensajes.append(
            f"🆕 <b>Nuevos productos en {nombre_cat}</b>\n{lista}{extra}"
        )

    # Productos eliminados (ya no están)
    eliminados = {k: v for k, v in productos_anteriores.items() if k not in productos_nuevos}
    if eliminados and len(eliminados) < 20:  # evitar spam si desaparece toda la categoría
        lista = "\n".join(
            f"  • {p['nombre']}"
            for p in list(eliminados.values())[:5]
        )
        extra = f"\n  <i>...y {len(eliminados)-5} más</i>" if len(eliminados) > 5 else ""
        mensajes.append(
            f"❌ <b>Productos agotados/eliminados en {nombre_cat}</b>\n{lista}{extra}"
        )

    # Cambios de precio
    cambios_precio = []
    for k, prod_nuevo in productos_nuevos.items():
        if k in productos_anteriores:
            precio_ant = productos_anteriores[k].get("precio", "")
            precio_nue = prod_nuevo.get("precio", "")
            if precio_ant and precio_nue and precio_ant != precio_nue:
                cambios_precio.append(
                    f"  • <a href='{prod_nuevo['url']}'>{prod_nuevo['nombre']}</a>: {precio_ant} → <b>{precio_nue}</b>"
                )

    if cambios_precio:
        lista = "\n".join(cambios_precio[:10])
        extra = f"\n  <i>...y {len(cambios_precio)-10} más</i>" if len(cambios_precio) > 10 else ""
        mensajes.append(
            f"💸 <b>Cambios de precio en {nombre_cat}</b>\n{lista}{extra}"
        )

    return mensajes


def main():
    print(f"\n🕐 Ejecutando monitor — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    estado_anterior = cargar_estado()
    estado_nuevo = {}
    todos_los_mensajes = []

    for categoria in URLS_A_MONITORIZAR:
        nombre = categoria["nombre"]
        url = categoria["url"]
        print(f"\n📦 Scrapeando {nombre}...")

        productos = scrape_categoria(url)
        print(f"  → {len(productos)} productos encontrados")

        estado_nuevo[url] = productos
        anteriores = estado_anterior.get(url, {})

        if anteriores:  # Solo comparar si ya teníamos datos previos
            mensajes = comparar_y_notificar(nombre, productos, anteriores)
            todos_los_mensajes.extend(mensajes)
        else:
            print(f"  ℹ️  Primera ejecución para esta categoría, guardando estado inicial")

    # Enviar notificaciones
    if todos_los_mensajes:
        print(f"\n📣 {len(todos_los_mensajes)} notificaciones a enviar")
        for msg in todos_los_mensajes:
            enviar_telegram(msg)
    else:
        print("\n✅ Sin cambios detectados")

    guardar_estado(estado_nuevo)
    print("\n💾 Estado guardado\n")


if __name__ == "__main__":
    main()
