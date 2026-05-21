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
import time
from datetime import datetime
from difflib import SequenceMatcher

# ─── CONFIGURACIÓN ────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_THREAD_ID = os.environ.get("TELEGRAM_THREAD_ID", "")

# URLs de categorías a monitorizar
URLS_A_MONITORIZAR = [
    {
        "nombre": "🔥 Ofertas Semanales",
        "url": "https://www.perfumeriaschic.com/c328441-ofertas-semanales.html"
    },
    {
        "nombre": "🧪 Perfumes Tester",
        "url": "http://www.perfumeriaschic.com/c276461-perfumes-tester.html"
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
NOMBRES_NOTIFICADOS_KEY = "nombres_ya_notificados"  # clave en el estado para persistir nombres ya notificados
# ──────────────────────────────────────────────────────────────────


def nombre_ya_notificado(nombre, nombres_set, umbral=0.90):
    """Comprueba si un nombre ya fue notificado, usando coincidencia exacta
    o fuzzy matching (para variaciones menores como espacios, tildes, etc.)."""
    nombre_norm = nombre.strip().upper()
    if nombre_norm in nombres_set:
        return True
    for n in nombres_set:
        if SequenceMatcher(None, nombre_norm, n).ratio() >= umbral:
            return True
    return False

# Crea una sesión global para que vaya súper rápido
session = requests.Session()
session.headers.update(HEADERS)

def cargar_estado():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def guardar_estado(estado):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=2)

def enviar_telegram(mensaje):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️  No hay credenciales de Telegram configuradas")
        return
        
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    
    # Límite seguro de Telegram
    limite_caracteres = 4000
    mensajes_cortados = []
    
    # Lógica para dividir mensajes largos sin romper HTML
    if len(mensaje) <= limite_caracteres:
        mensajes_cortados.append(mensaje)
    else:
        lineas = mensaje.split('\n')
        bloque_actual = ""
        for linea in lineas:
            if len(bloque_actual) + len(linea) + 1 > limite_caracteres:
                mensajes_cortados.append(bloque_actual.strip())
                bloque_actual = linea + "\n"
            else:
                bloque_actual += linea + "\n"
        if bloque_actual:
            mensajes_cortados.append(bloque_actual.strip())

    # Enviar cada bloque
    for i, msg in enumerate(mensajes_cortados):
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg,
            "parse_mode": "HTML",
            "disable_web_page_preview": False
        }
        
        if TELEGRAM_THREAD_ID:
            payload["message_thread_id"] = int(TELEGRAM_THREAD_ID)
            
        print(f"  📤 Enviando bloque {i+1}/{len(mensajes_cortados)} a Telegram...")
        try:
            r = requests.post(url, json=payload, timeout=10)
            r.raise_for_status()
            print("  ✅ Notificación enviada")
        except Exception as e:
            print(f"  ❌ Error enviando Telegram: {e}")
        
        # Pausa de 1 segundo entre bloques para que Telegram no nos bloquee por spam
        time.sleep(1)

def scrape_categoria(url):
    productos = {}
    pagina = 1
    
    # Limpiamos la URL por si acaso viene con parámetros de antes
    base_url = url.split("?")[0]
    
    while True:
        # Añadimos un timestamp para evitar la caché de la web (el truco que comentamos)
        timestamp = int(time.time())
        url_pag = f"{base_url}?t={timestamp}" if pagina == 1 else f"{base_url}?Product_page={pagina}&t={timestamp}"
        
        try:
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

            if producto_id in productos_pagina or producto_id in productos:
                continue

            productos_pagina[producto_id] = {
                "nombre": nombre,
                "precio": precio,
                "url": full_url,
                "en_stock": en_stock
            }

        if not productos_pagina:
            print("  ⚠️ La web no devuelve productos nuevos. Fin de la categoría.")
            break

        productos.update(productos_pagina)
        print(f"    página {pagina}: {len(productos_pagina)} productos extraídos (Total acumulado: {len(productos)})")
        
        pagina += 1
        time.sleep(1)
        
        if pagina > 10000:
            break

    return productos

def comparar_y_notificar(nombre_cat, productos_nuevos, productos_anteriores, ya_notificados=None, nombres_persistentes=None):
    mensajes = []
    if ya_notificados is None:
        ya_notificados = set()
    if nombres_persistentes is None:
        nombres_persistentes = set()

    # Límite para agrupar notificaciones (evita spam masivo en Telegram)
    LIMITE_DETALLE = 20

    # 1. Productos NUEVOS (filtrando los que ya se notificaron en otra categoría
    #    O que ya se notificaron en ejecuciones anteriores por nombre)
    nuevos = {}
    for k, v in productos_nuevos.items():
        if k in productos_anteriores:
            continue  # mismo ID, no es nuevo
        if v['nombre'] in ya_notificados:
            continue  # ya notificado en esta ejecución
        if nombre_ya_notificado(v['nombre'], nombres_persistentes):
            continue  # ya notificado en una ejecución anterior (mismo nombre o muy similar)
        nuevos[k] = v

    if nuevos:
        # Registrar como ya notificados para las siguientes categorías
        for p in nuevos.values():
            ya_notificados.add(p['nombre'])
            nombres_persistentes.add(p['nombre'].strip().upper())

        if len(nuevos) <= LIMITE_DETALLE:
            lista = "\n".join(
                f"  • <a href='{p['url']}'>{p['nombre']}</a> — {p['precio']}"
                for p in nuevos.values()
            )
            mensajes.append(f"🆕 <b>Nuevos productos en {nombre_cat}</b>\n{lista}")
        else:
            # Demasiados → resumen compacto (probablemente la web se recuperó de un fallo)
            muestra = list(nuevos.values())[:5]
            lista_muestra = "\n".join(
                f"  • <a href='{p['url']}'>{p['nombre']}</a> — {p['precio']}"
                for p in muestra
            )
            mensajes.append(
                f"🆕 <b>{len(nuevos)} nuevos productos en {nombre_cat}</b>\n"
                f"(Mostrando 5 de {len(nuevos)}):\n{lista_muestra}\n"
                f"  ...y {len(nuevos) - 5} más"
            )


    # 3. Cambios de PRECIO y STOCK (filtrando ya notificados en otra categoría)
    cambios = []
    for k, prod_nuevo in productos_nuevos.items():
        if k in productos_anteriores:
            # Si ya se notificó este producto en otra categoría, saltar
            if prod_nuevo['nombre'] in ya_notificados:
                continue

            prod_ant = productos_anteriores[k]
            producto_tiene_cambio = False

            # Comprobar Stock
            if not prod_ant.get("en_stock", True) and prod_nuevo["en_stock"]:
                cambios.append(f"  🟢 <b>¡VUELVE A HABER STOCK!</b>\n  <a href='{prod_nuevo['url']}'>{prod_nuevo['nombre']}</a>")
                producto_tiene_cambio = True

            # Comprobar Precio
            precio_ant = prod_ant.get("precio", "")
            precio_nue = prod_nuevo.get("precio", "")
            if precio_ant and precio_nue and precio_ant != precio_nue:
                cambios.append(f"  💸 <b>CAMBIO PRECIO:</b>\n  <a href='{prod_nuevo['url']}'>{prod_nuevo['nombre']}</a>\n  {precio_ant} → <b>{precio_nue}</b>")
                producto_tiene_cambio = True

            # Marcar como notificado para que no se repita en otra categoría
            if producto_tiene_cambio:
                ya_notificados.add(prod_nuevo['nombre'])

    if cambios:
        if len(cambios) <= LIMITE_DETALLE:
            lista = "\n\n".join(cambios)
            mensajes.append(f"⚡ <b>Actualizaciones en {nombre_cat}</b>\n\n{lista}")
        else:
            lista = "\n\n".join(cambios[:10])
            mensajes.append(
                f"⚡ <b>{len(cambios)} actualizaciones en {nombre_cat}</b>\n"
                f"(Mostrando 10 de {len(cambios)}):\n\n{lista}\n\n"
                f"  ...y {len(cambios) - 10} más"
            )

    return mensajes

def main():
    print(f"\n🕐 Ejecutando monitor — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    estado_anterior = cargar_estado()
    estado_nuevo = {}
    todos_los_mensajes = []
    ya_notificados = set()  # Evita notificar el mismo producto en varias categorías

    # ── Cargar nombres persistentes (historial de productos ya notificados) ──
    nombres_persistentes = set(estado_anterior.get(NOMBRES_NOTIFICADOS_KEY, []))
    print(f"  📋 {len(nombres_persistentes)} nombres de productos en historial de notificaciones")

    for categoria in URLS_A_MONITORIZAR:
        nombre = categoria["nombre"]
        url = categoria["url"]
        print(f"\n📦 Scrapeando {nombre}...")

        productos = scrape_categoria(url)
        print(f"  → {len(productos)} productos encontrados")

        anteriores = estado_anterior.get(url, {})

        # ── PROTECCIÓN ANTI-SCRAPING-FALLIDO ──────────────────────
        # Si la categoría antes tenía productos y ahora devuelve muchos menos
        # (menos del 80%), probablemente la web falló o nos bloqueó.
        # En ese caso, MANTENEMOS el estado anterior para no generar
        # falsas notificaciones de "eliminados" y luego "nuevos".
        if anteriores and len(productos) < len(anteriores) * 0.8:
            print(f"  ⚠️  PROTECCIÓN: Se esperaban ~{len(anteriores)} productos pero solo se obtuvieron {len(productos)}.")
            print(f"  ⚠️  Esto indica un fallo de la web, NO un cambio real. Se mantiene el estado anterior.")
            estado_nuevo[url] = anteriores  # Mantener estado anterior
            continue

        # ── PROTECCIÓN ANTI-RECUPERACIÓN ──────────────────────────
        # Si de repente aparecen muchos productos "nuevos" (más de 30),
        # es probable que el scrape ANTERIOR fue parcial y ahora se
        # recuperó. Actualizamos el estado SIN notificar.
        if anteriores:
            nuevos_detectados = set(productos.keys()) - set(anteriores.keys())
            if len(nuevos_detectados) > 30:
                print(f"  ⚠️  PROTECCIÓN ANTI-RECUPERACIÓN: Se detectaron {len(nuevos_detectados)} productos 'nuevos'.")
                print(f"  ⚠️  Probablemente el scrape anterior fue parcial. Se actualiza estado SIN notificar.")
                # Registrar los nombres en el historial persistente para no notificarlos después
                for pid in nuevos_detectados:
                    nombres_persistentes.add(productos[pid]['nombre'].strip().upper())
                estado_nuevo[url] = productos
                continue

        estado_nuevo[url] = productos

        if anteriores:
            mensajes = comparar_y_notificar(nombre, productos, anteriores, ya_notificados, nombres_persistentes)
            todos_los_mensajes.extend(mensajes)
        else:
            print(f"  ℹ️  Primera ejecución para esta categoría, guardando estado inicial")
            # En primera ejecución, registrar todos los nombres actuales como ya conocidos
            for p in productos.values():
                nombres_persistentes.add(p['nombre'].strip().upper())

    # Enviar notificaciones
    if todos_los_mensajes:
        print(f"\n📣 {len(todos_los_mensajes)} notificaciones a enviar")
        for msg in todos_los_mensajes:
            enviar_telegram(msg)
    else:
        print("\n✅ Sin cambios detectados")

    # ── Guardar estado + historial de nombres persistentes ────────
    estado_nuevo[NOMBRES_NOTIFICADOS_KEY] = sorted(nombres_persistentes)
    guardar_estado(estado_nuevo)
    print(f"  📋 {len(nombres_persistentes)} nombres en historial persistente")
    print("\n💾 Estado guardado\n")

if __name__ == "__main__":
    main()
