"""
Scraper de Renfe usando Playwright
Instalar con: pip install playwright && playwright install
"""

from playwright.sync_api import sync_playwright
import time
from datetime import datetime
from difflib import SequenceMatcher
import re
import asyncio

class RenfeScraperPlaywright:
    """Scraper de Renfe con Playwright (versión ordenada, MISMA lógica/flujo).

    Nota: Se han reagrupado utilidades, constantes y manejo de selectores
    para mejorar legibilidad, sin alterar el comportamiento observable.
    """

    # --------------------------- Constantes/Selectores --------------------------- #
    _VIEWPORT = {"width": 1920, "height": 1080}

    _SELECTORS_COOKIES = (
        "button:has-text('Aceptar')",
        "button:has-text('Aceptar todas')",
        "#onetrust-accept-btn-handler",
        "button.accept-cookies",
    )

    _SELECTORS_SOLO_IDA = (
        "input[value='ONE_WAY']",
        "label:has-text('Ida'):not(:has-text('vuelta'))",
        "text='Ida' >> input[type='radio']",
    )

    _SELECTORS_INPUT_ORIGEN = (
        "input[id*='origin']",
        "input[placeholder*='Origen']",
        "input[aria-label*='Origen']",
    )

    _SELECTORS_INPUT_DESTINO = (
        "input[id*='destination']",
        "input[placeholder*='Destino']",
        "input[aria-label*='Destino']",
    )

    _SELECTORS_INPUT_FECHA = (
        "input[id*='date']",
        "input[placeholder*='Fecha']",
        "input[type='text'][placeholder*='Salida']",
    )

    _SELECTORS_SUGERENCIAS = (
        "[id*='origin'][id*='options'] li",
        "[id*='destination'][id*='options'] li",
        "[role='listbox'] [role='option']",
        ".mat-autocomplete-panel [role='option']",
        "ul[role='listbox'] li",
    )

    _SELECTORS_BUSCAR = (
        "button[type='submit']",
        "button:has-text('Buscar')",
        "button:has-text('BUSCAR')",
    )

    _MESES = {
        "enero": 1,
        "febrero": 2,
        "marzo": 3,
        "abril": 4,
        "mayo": 5,
        "junio": 6,
        "julio": 7,
        "agosto": 8,
        "septiembre": 9,
        "setiembre": 9,
        "octubre": 10,
        "noviembre": 11,
        "diciembre": 12,
    }

    # ------------------------------- Inicialización ------------------------------ #
    def __init__(self, headless: bool = True):
        self.headless = headless
        self.playwright = None
        self.browser = None
        self.page = None

    def __enter__(self):
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=self.headless, args=["--lang=es", "--headless=new"]) 
        self.page = self.browser.new_page()
        self.page.set_viewport_size(self._VIEWPORT)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()

    # ------------------------------- Utilidades -------------------------------- #
    def similitud_texto(self, a: str, b: str) -> float:
        """Calcula la similitud entre dos textos (case-insensitive)."""
        return SequenceMatcher(None, a.lower(), b.lower()).ratio()

    def _month_diff(self, src_m: int, src_y: int, dst_m: int, dst_y: int) -> int:
        return (dst_y - src_y) * 12 + (dst_m - src_m)

    def _min_diff(self, hhmm_a: str, hhmm_b: str) -> int:
        """Diferencia absoluta en minutos entre 'HH:MM' y 'HH:MM'."""
        try:
            ha, ma = map(int, hhmm_a.split(":"))
            hb, mb = map(int, hhmm_b.split(":"))
            return abs((ha * 60 + ma) - (hb * 60 + mb))
        except Exception:
            return 9999

    # ------------------------------ Cookies/Modo ida ----------------------------- #
    def aceptar_cookies(self):
        """Acepta cookies si aparecen (varios selectores tolerantes)."""
        try:
            for selector in self._SELECTORS_COOKIES:
                try:
                    boton = self.page.locator(selector).first
                    if boton.is_visible(timeout=3000):
                        boton.click()
                        print("✓ Cookies aceptadas")
                        time.sleep(1)
                        return
                except Exception:
                    continue
            print("⚠ No se encontró banner de cookies")
        except Exception as e:
            print(f"⚠ Error con cookies: {e}")

    def seleccionar_solo_ida(self):
        """Selecciona solo ida (si no está ya por defecto)."""
        try:
            for selector in self._SELECTORS_SOLO_IDA:
                try:
                    elemento = self.page.locator(selector).first
                    if elemento.is_visible(timeout=2000):
                        elemento.click(force=True)
                        print("✓ Seleccionado viaje de IDA")
                        time.sleep(0.5)
                        return
                except Exception:
                    continue
            print("⚠ No se pudo seleccionar 'Ida' - puede estar por defecto")
        except Exception as e:
            print(f"⚠ Error al seleccionar ida: {e}")

    # ------------------------------- Autocompletado ------------------------------ #
    def buscar_estacion_aproximada(self, estacion_deseada, opciones):
        """Encuentra la estación más similar entre opciones de autocompletar."""
        mejor_opcion, mejor_score = None, 0.0
        for opcion in opciones:
            try:
                texto = opcion.inner_text().strip()
            except Exception:
                continue
            score = self.similitud_texto(estacion_deseada, texto)
            if score > mejor_score:
                mejor_score, mejor_opcion = score, opcion
        return mejor_opcion, mejor_score

    def _resolver_input_y_sugerencias(self, tipo: str, etiqueta: str):
        """Retorna (input_locator, lista_opciones) con selectores tolerantes."""
        input_selectors = self._SELECTORS_INPUT_ORIGEN if tipo == "origin" else self._SELECTORS_INPUT_DESTINO

        input_box = None
        for sel in input_selectors:
            try:
                cand = self.page.locator(sel).first
                if cand.is_visible(timeout=2000):
                    input_box = cand
                    break
            except Exception:
                continue
        if not input_box:
            print(f"✗ No se encontró el campo de {etiqueta}")
            return None, []

        input_box.click()
        input_box.fill("")
        time.sleep(0.3)
        return input_box, []

    def rellenar_estacion(self, tipo: str, estacion: str):
        """Rellena origen/destino con heurística de autocompletado tolerante."""
        try:
            etiqueta = "Origen" if tipo == "origin" else "Destino"
            print(f"  Buscando campo de {etiqueta}...")

            input_box, _ = self._resolver_input_y_sugerencias(tipo, etiqueta)
            if not input_box:
                return None, 0.0

            input_box.type(estacion, delay=100)
            time.sleep(0.3)
            print(f"  Buscando sugerencias para '{estacion}'...")
            self.page.wait_for_timeout(1500)

            opciones = []
            for sel in self._SELECTORS_SUGERENCIAS:
                try:
                    opciones = self.page.locator(sel).all()
                    if len(opciones) > 0:
                        break
                except Exception:
                    continue

            if not opciones:
                print("⚠ No se encontraron sugerencias")
                input_box.press("Enter")
                return estacion, 0.5

            mejor_opcion, score = self.buscar_estacion_aproximada(estacion, opciones)
            if mejor_opcion:
                texto = mejor_opcion.inner_text().strip()
                print(f"  Mejor coincidencia: '{texto}' (similitud: {score:.2%})")
                mejor_opcion.click()
                print(f"✓ {etiqueta} seleccionado: {texto}")
                time.sleep(0.5)
                return texto, score

            return None, 0.0

        except Exception as e:
            print(f"✗ Error al rellenar {tipo}: {e}")
            import traceback
            traceback.print_exc()
            return None, 0.0

    # -------------------------------- Calendarios -------------------------------- #
    def seleccionar_fecha(self, fecha_str: str):
        """Selecciona la fecha por calendario genérico (fallback)."""
        try:
            fecha = datetime.strptime(fecha_str, "%d/%m/%Y")
            input_fecha = None
            for selector in self._SELECTORS_INPUT_FECHA:
                try:
                    cand = self.page.locator(selector).first
                    if cand.is_visible(timeout=2000):
                        input_fecha = cand
                        break
                except Exception:
                    continue
            if not input_fecha:
                print("✗ No se encontró el campo de fecha")
                return

            input_fecha.click()
            time.sleep(0.5)

            dia = fecha.day
            for selector in (
                f"button:has-text('{dia}')",
                f"[role='gridcell']:has-text('{dia}')",
                f".mat-calendar-body-cell-content:has-text('{dia}')",
            ):
                try:
                    dias = self.page.locator(selector).all()
                    for d in dias:
                        if d.is_visible() and not d.is_disabled():
                            if d.inner_text().strip() == str(dia):
                                d.click()
                                print(f"✓ Fecha seleccionada: {fecha_str}")
                                time.sleep(0.5)
                                return
                except Exception:
                    continue
            print(f"⚠ No se pudo seleccionar el día {dia}")
        except Exception as e:
            print(f"✗ Error al seleccionar fecha: {e}")

    def seleccionar_ida_y_fecha(self, fecha_str: str) -> bool:
        """Versión robusta para calendario Lightpick (solo ida).
        Mantiene la misma lógica de navegación/selección.
        """
        def _parse_fecha(s: str) -> datetime:
            return datetime.strptime(s.strip(), "%d/%m/%Y")

        def _leer_mes_y_anyo():
            # 1) <select> (aunque estén disabled)
            try:
                opt_mes = self.page.locator(".lightpick__select-months option[selected]").first
                opt_any = self.page.locator(".lightpick__select-years option[selected]").first
                if opt_mes.is_visible() and opt_any.is_visible():
                    mes_txt = opt_mes.inner_text().strip().lower()
                    anyo_txt = opt_any.inner_text().strip()
                    if mes_txt in self._MESES and anyo_txt.isdigit():
                        return self._MESES[mes_txt], int(anyo_txt)
            except Exception:
                pass
            # 2) Etiqueta alternativa
            try:
                alt = self.page.locator(".rf-daterange-picker-alternative__month-label").first
                if alt.is_visible():
                    mes_txt = alt.locator("span").nth(0).inner_text().strip().lower()
                    anyo_txt = alt.locator("span").nth(1).inner_text().strip()
                    if mes_txt in self._MESES and anyo_txt.isdigit():
                        return self._MESES[mes_txt], int(anyo_txt)
            except Exception:
                pass
            return None

        def _click_next(n=1):
            for _ in range(max(0, n)):
                try:
                    self.page.locator("button.lightpick__next-action").first.click()
                    self.page.wait_for_timeout(120)
                except Exception:
                    return False
            return True

        def _click_prev(n=1):
            for _ in range(max(0, n)):
                try:
                    self.page.locator("button.lightpick__previous-action").first.click()
                    self.page.wait_for_timeout(120)
                except Exception:
                    return False
            return True

        try:
            fecha = _parse_fecha(fecha_str)
            dia = str(fecha.day)
            mes_obj, anyo_obj = fecha.month, fecha.year

            # 1) Abrir FECHA IDA (selectores tolerantes)
            openers = (
                "button:has-text('FECHA IDA')",
                "label:has-text('FECHA IDA')",
                "input[placeholder*='Fecha'][placeholder*='ida']",
                "input[id*='date'][id*='out']",
                "input[type='text'][placeholder*='Salida']",
            )
            opener = None
            for sel in openers:
                try:
                    cand = self.page.locator(sel).first
                    if cand and cand.is_visible():
                        opener = cand
                        break
                except Exception:
                    continue
            if not opener:
                print("✗ No se encontró el control para abrir FECHA IDA")
                return False

            opener.click()
            self.page.locator(".lightpick").first.wait_for(state="visible", timeout=3000)
            self.page.locator(".lightpick__days").first.wait_for(state="visible", timeout=3000)

            # 2) Marcar "Viaje solo ida"
            try:
                self.page.locator("#trip-option label:has-text('Viaje solo ida')").first.click()
                self.page.wait_for_timeout(120)
            except Exception:
                pass

            # 3) Navegar al mes/año objetivo
            visible = _leer_mes_y_anyo() or (self.page.wait_for_timeout(250) or _leer_mes_y_anyo())
            if not visible:
                hoy = datetime.today()
                diff = self._month_diff(hoy.month, hoy.year, mes_obj, anyo_obj)
                if diff > 0:
                    _click_next(diff)
                elif diff < 0:
                    _click_prev(-diff)
            else:
                mes_vis, anyo_vis = visible
                diff = self._month_diff(mes_vis, anyo_vis, mes_obj, anyo_obj)
                if diff > 0:
                    _click_next(min(diff, 60))
                elif diff < 0:
                    _click_prev(min(-diff, 60))
                self.page.wait_for_timeout(120)

            # 4) Seleccionar el día (tres estrategias)
            seleccionado = False
            try:
                self.page.locator(
                    f".lightpick__day.is-available:not(.is-previous-month):not(.is-next-month):has-text('{dia}')"
                ).first.click()
                seleccionado = True
            except Exception:
                seleccionado = False

            if not seleccionado:
                try:
                    did = self.page.evaluate(
                        f"""() => {{
                        const t = new Date({anyo_obj}, {mes_obj-1}, {int(dia)}).setHours(0,0,0,0);
                        const el = document.querySelector(".lightpick .lightpick__day[data-time='"+t+"']");
                        if (el && !el.classList.contains('is-previous-month') && !el.classList.contains('is-next-month')) {{
                            el.click();
                            return true;
                        }}
                        return false;
                    }}"""
                    )
                    if did:
                        seleccionado = True
                except Exception:
                    pass

            if not seleccionado:
                try:
                    dias_loc = self.page.locator(
                        ".lightpick__day.is-available:not(.is-previous-month):not(.is-next-month)"
                    )
                    count = dias_loc.count()
                    for i in range(count):
                        dloc = dias_loc.nth(i)
                        if not dloc.is_visible():
                            continue
                        try:
                            txt = dloc.inner_text().strip()
                        except Exception:
                            continue
                        if txt == dia:
                            dloc.click(force=True)
                            seleccionado = True
                            break
                except Exception:
                    pass

            if not seleccionado:
                print(f"⚠ No se pudo seleccionar el día {dia}")
                return False

            # 5) Pulsar 'Aceptar' si está
            try:
                btn_ok = self.page.locator("button.lightpick__apply-action-sub").first
                if btn_ok and btn_ok.is_visible():
                    btn_ok.click()
            except Exception:
                pass

            print(f"✓ Fecha (solo ida) seleccionada: {fecha_str}")
            self.page.wait_for_timeout(150)
            return True

        except Exception as e:
            print(f"✗ Error en seleccionar_ida_y_fecha (Lightpick): {e}")
            return False

    # --------------------------------- Resultados -------------------------------- #
    def _extraer_trayectos_ida(self):
        """Devuelve lista de dicts con 'salida', 'llegada' y 'estado' (OK/LLENO/DESCONOCIDO)."""
        viajes = []
        try:
            filas = self.page.locator("#listaTrenesTBodyIda .row.selectedTren")
            n = filas.count()
            for i in range(n):
                fila = filas.nth(i)

                horas = fila.locator(".col-md-8.trenes h5")
                if horas.count() < 2:
                    continue
                txt_salida = horas.nth(0).inner_text().strip()
                txt_llegada = horas.nth(horas.count() - 1).inner_text().strip()
                m1 = re.search(r"\b\d{2}:\d{2}\b", txt_salida)
                m2 = re.search(r"\b\d{2}:\d{2}\b", txt_llegada)
                if not (m1 and m2):
                    continue

                has_precio = fila.locator(".precio-final").count() > 0
                has_plaza_h = fila.locator(f".plazas-h, #ahorro_tren_i_{i+1} .accessiblechair").count() > 0

                if has_precio:
                    estado = "OK"
                elif has_plaza_h:
                    estado = "LLENO"
                else:
                    estado = "DESCONOCIDO"

                viajes.append({
                    "salida": m1.group(0),
                    "llegada": m2.group(0),
                    "estado": estado,
                })
            return viajes
        except Exception as e:
            print(f"⚠ Error extrayendo trayectos: {e}")
            return []

    def imprimir_trayectos(self, origen: str, destino: str, viajes):
        """Imprime lista numerada de trayectos (IDA)."""
        if not viajes:
            print("⚠ No se encontraron trayectos para mostrar.")
            return

        print("\n🧾 LISTADO DE TRAYECTOS (IDA)")
        for idx, v in enumerate(viajes, start=1):
            if v.get("estado") == "OK":
                estado_str = "TREN OK ✓"
            elif v.get("estado") == "LLENO":
                estado_str = "TREN LLENO ✗"
            else:
                estado_str = "ESTADO DESCONOCIDO ?"
            print(f"{idx}. {origen} → {destino} | salida {v['salida']} | llegada {v['llegada']} | {estado_str}")

    # ------------------------------- Búsquedas/Checks ---------------------------- #
    def buscar_billetes(self, origen: str, destino: str, fecha: str):
        """Ejecuta la búsqueda completa y devuelve dict con metadatos/resultados."""
        resultado = {
            "ok": False,
            "origen_seleccionado": None,
            "destino_seleccionado": None,
            "fecha": fecha,
            "url": None,
        }
        try:
            print("\n" + "=" * 50)
            print("BÚSQUEDA DE BILLETES RENFE - PLAYWRIGHT")
            print("=" * 50)

            # 1) Navegar
            print("\n1. Navegando a Renfe...")
            self.page.goto("https://www.renfe.com/es/es", wait_until="networkidle")
            time.sleep(0.3)

            # 2) Cookies
            print("\n2. Gestionando cookies...")
            self.aceptar_cookies()

            # 3) Origen
            print(f"\n3. Rellenando ORIGEN: {origen}")
            origen_sel, score_origen = self.rellenar_estacion("origin", origen)
            resultado["origen_seleccionado"] = (origen_sel, score_origen)

            # 4) Destino
            print(f"\n4. Rellenando DESTINO: {destino}")
            destino_sel, score_destino = self.rellenar_estacion("destination", destino)
            resultado["destino_seleccionado"] = (destino_sel, score_destino)

            # 5) Solo ida + Fecha (orden original)
            print(f"\n5. Seleccionando SOLO IDA y FECHA: {fecha}")
            ok_fecha = self.seleccionar_ida_y_fecha(fecha)
            if not ok_fecha:
                print("⚠ No se pudo terminar la selección de fecha en modo solo ida")

            # 7) Buscar
            print("\n7. Buscando billetes...")
            for selector in self._SELECTORS_BUSCAR:
                try:
                    boton = self.page.locator(selector).first
                    if boton.is_visible(timeout=2000):
                        boton.click()
                        print("✓ Búsqueda iniciada")
                        break
                except Exception:
                    continue

            print("⏳ Esperando resultados...")
            self.page.wait_for_selector("#listaTrenesTBodyIda .row.selectedTren", timeout=20000)

            resultado["url"] = self.page.url
            resultado["ok"] = True

            viajes = self._extraer_trayectos_ida()
            origen_print = (resultado["origen_seleccionado"][0] or f"{origen}")
            destino_print = (resultado["destino_seleccionado"][0] or f"{destino}")
            self.imprimir_trayectos(origen_print, destino_print, viajes)

        except Exception as e:
            print(f"\n✗ Error: {e}")
            import traceback
            traceback.print_exc()

        return resultado

    def esta_lleno_en_hora(
        self,
        origen: str,
        destino: str,
        fecha: str,
        hora_objetivo: str,
        tolerancia_min: int = 0,
        imprimir: bool = True,
    ):
        """Comprueba si un tren concreto está lleno en la fecha/hora dadas.

        Retorna dict con: ok, estado ('OK'|'LLENO'|'DESCONOCIDO'|'NO_ENCONTRADO'),
        salida, llegada, url.
        """
        resultado = {
            "ok": False,
            "estado": "NO_ENCONTRADO",
            "salida": None,
            "llegada": None,
            "url": None,
        }
        try:
            busc = self.buscar_billetes(origen=origen, destino=destino, fecha=fecha)
            resultado["url"] = busc.get("url")
            if not busc.get("ok"):
                if imprimir:
                    print("✗ No se pudo completar la búsqueda previa.")
                return resultado

            viajes = self._extraer_trayectos_ida()

            mejor, mejor_diff = None, 10**9
            for v in viajes:
                if "salida" not in v:
                    continue
                diff = self._min_diff(v["salida"], hora_objetivo)
                if diff <= tolerancia_min and diff < mejor_diff:
                    mejor, mejor_diff = v, diff

            if mejor is None:
                for v in viajes:
                    if v.get("salida") == hora_objetivo:
                        mejor = v
                        break

            if mejor is None:
                if imprimir:
                    print(f"⚠ No se encontró tren con salida {hora_objetivo} (±{tolerancia_min} min).")
                return resultado

            resultado.update(
                {
                    "ok": True,
                    "estado": mejor.get("estado", "DESCONOCIDO"),
                    "salida": mejor.get("salida"),
                    "llegada": mejor.get("llegada"),
                }
            )

            if imprimir:
                if resultado["estado"] == "OK":
                    print(
                        f"✓ {origen} → {destino} | {fecha} {resultado['salida']}–{resultado['llegada']} · TREN OK"
                    )
                elif resultado["estado"] == "LLENO":
                    print(
                        f"✗ {origen} → {destino} | {fecha} {resultado['salida']}–{resultado['llegada']} · TREN LLENO"
                    )
                else:
                    print(
                        f"? {origen} → {destino} | {fecha} {resultado['salida']}–{resultado['llegada']} · ESTADO DESCONOCIDO"
                    )

            return resultado

        except Exception as e:
            if imprimir:
                print(f"✗ Error en esta_lleno_en_hora: {e}")
            return resultado


if __name__ == "__main__":
    with RenfeScraperPlaywright(headless=True) as scraper:
        # 1) Buscar todos los trenes de un día
        resultado = scraper.buscar_billetes(
            origen="Vigo U",
            destino="A Coruña",
            fecha="06/10/2025",
        )

        print("\n📊 RESULTADO BÚSQUEDA COMPLETA:")
        print(f"  Estado: {'✓ OK' if resultado['ok'] else '✗ Error'}")
        print(f"  Origen: {resultado['origen_seleccionado']}")
        print(f"  Destino: {resultado['destino_seleccionado']}")
        print(f"  URL: {resultado['url']}")

        # 2) Consultar si un tren concreto está lleno o no
        check = scraper.esta_lleno_en_hora(
            origen="Vigo U",
            destino="A Coruña",
            fecha="06/10/2025",  # dd/mm/YYYY
            hora_objetivo="08:00",  # Hora exacta a comprobar
            tolerancia_min=2,  # margen de 2 minutos
            imprimir=True,
        )

        print("\n📌 RESULTADO CONSULTA PUNTUAL:")
        print(check)

        input("\n⏸ Presiona Enter para cerrar...")
