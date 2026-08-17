import os, sys, time, re, shutil, unicodedata, subprocess, json
from pathlib import Path

os.environ["WDM_LOG"] = "0"
os.environ["WDM_LOCAL"] = "1"


# ===================== CHECAGEM DE DEPENDÊNCIAS =====================
def _checar_dependencias():
    faltando = []
    for mod, pkg in (("requests", "requests"),
                     ("selenium", "selenium"),
                     ("webdriver_manager", "webdriver-manager")):
        try:
            __import__(mod)
        except ImportError:
            faltando.append(pkg)
    if faltando:
        print("=" * 70)
        print(f"❌ Dependências ausentes NESTE interpretador: {', '.join(faltando)}")
        print(f"   Interpretador em uso : {sys.executable}")
        print(f"   Versão               : {sys.version.split()[0]}")
        print(f'   Corrija com          : "{sys.executable}" -m pip install {" ".join(faltando)}')
        print("=" * 70)
        raise SystemExit(7)


_checar_dependencias()

import requests

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import (
    TimeoutException, ElementClickInterceptedException, NoSuchElementException, StaleElementReferenceException
)

from webdriver_manager.chrome import ChromeDriverManager

# ===================== CONFIG Selenium =====================
USUARIO        = os.environ.get("DASS_USER", "marfinind")
SENHA          = os.environ.get("DASS_PASS", "308207")

SCRIPT_DIR     = Path(__file__).resolve().parent
PASTA_DOWNLOAD = str((SCRIPT_DIR / "downloads_dass").resolve())
DEBUG_DIR      = str((SCRIPT_DIR / "debug_dump").resolve())
HEADLESS       = (os.environ.get("DASS_HEADLESS", "0") == "1")
TIMEOUT        = 30

ARQ_ESTADO        = SCRIPT_DIR / "ultimo_count.json"
LIMITE_REGRESSAO  = 0.6
FORCAR            = (os.environ.get("DASS_FORCAR", "0") == "1")
ROW_XPATH         = "//table[contains(@class,'generic-table')]/tbody/tr"
# ===========================================================

# ===================== CONFIG Web App ======================
WEBAPP_URL    = "https://script.google.com/macros/s/AKfycbySutI0X3YulJJcMHnrcW_dLxXZwPgnxQhwO2QoZo2oy1bKrnn6v7Lf9F6GW7_sTaHM/exec"
WEBAPP_SECRET = None
# ===========================================================

os.makedirs(PASTA_DOWNLOAD, exist_ok=True)
os.makedirs(DEBUG_DIR, exist_ok=True)
HOME = Path.home()
WATCH_DIRS = [Path(PASTA_DOWNLOAD), HOME / "Downloads", HOME / "OneDrive" / "Downloads"]

chrome_options = webdriver.ChromeOptions()
chrome_options.add_experimental_option("prefs", {
    "download.default_directory": str(Path(PASTA_DOWNLOAD).resolve()),
    "download.prompt_for_download": False,
    "safebrowsing.enabled": True,
})
chrome_options.add_argument("--disable-notifications")
chrome_options.add_argument("--window-size=1920,1080")
chrome_options.add_argument("--force-device-scale-factor=1")
chrome_options.add_argument("--log-level=3")
chrome_options.add_argument("--disable-background-networking")
chrome_options.add_argument("--disable-sync")
chrome_options.add_argument("--disable-component-update")
chrome_options.add_argument("--disable-features=OptimizationHints,MediaRouter")
chrome_options.add_argument("--no-first-run")
chrome_options.add_argument("--no-default-browser-check")
chrome_options.add_experimental_option("excludeSwitches", ["enable-logging"])
if HEADLESS:
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--hide-scrollbars")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
    )


# ---------------------- Subida do Chrome ----------------------
def _novo_service(caminho_driver):
    if not caminho_driver:
        # Sem caminho explícito o Selenium Manager resolve o driver sozinho.
        return None
    try:
        return Service(caminho_driver, log_output=subprocess.DEVNULL)
    except TypeError:
        return Service(caminho_driver)


def subir_chrome(caminho_driver, tentativas=3):
    """Abre o Chrome com repetição: a primeira sessão às vezes estoura o timeout
    na máquina do cliente (disco ocupado com OneDrive/antivírus de madrugada)."""
    try:
        from selenium.webdriver.remote.remote_connection import RemoteConnection
        RemoteConnection.set_timeout(240)
    except Exception:
        pass

    ultimo_erro = None
    for tentativa in range(1, tentativas + 1):
        service = _novo_service(caminho_driver)
        try:
            if service is None:
                return webdriver.Chrome(options=chrome_options)
            return webdriver.Chrome(service=service, options=chrome_options)
        except Exception as e:
            ultimo_erro = e
            print(f"⚠️ Chrome não subiu (tentativa {tentativa}/{tentativas}): {e}")
            try:
                service.stop()
            except Exception:
                pass
            if tentativa < tentativas:
                espera = 10 * tentativa
                print(f"   Nova tentativa em {espera}s...")
                time.sleep(espera)
    print(f"❌ Não foi possível iniciar o Chrome: {ultimo_erro}")
    return None


# ---------------------- Monitor de rede ----------------------
# O portal é uma SPA: trocar o tamanho de página dispara um XHR e a tabela só
# reflete o novo total quando a resposta chega. Sem saber disso, o robô lia a
# tabela antiga (a página padrão de 10 linhas) e a dava como boa.
JS_MONITOR_REDE = r"""
(function () {
  if (window.__dassNet) { return; }
  var s = { inflight: 0, total: 0, last: Date.now() };
  window.__dassNet = s;
  var fim = function () { s.inflight = Math.max(0, s.inflight - 1); s.last = Date.now(); };

  var open = XMLHttpRequest.prototype.open;
  var send = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function () { return open.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function () {
    var self = this;
    s.inflight++; s.total++;
    var uma_vez = function () { if (!self.__dassFim) { self.__dassFim = true; fim(); } };
    try { this.addEventListener('loadend', uma_vez); } catch (e) { }
    try { return send.apply(this, arguments); } catch (e) { uma_vez(); throw e; }
  };

  if (window.fetch) {
    var f = window.fetch;
    window.fetch = function () {
      s.inflight++; s.total++;
      return f.apply(this, arguments).then(
        function (r) { fim(); return r; },
        function (e) { fim(); throw e; }
      );
    };
  }
})();
"""


def instalar_monitor_rede(driver):
    """Instala o contador de requisições. Deve rodar antes do primeiro driver.get()."""
    ok = False
    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": JS_MONITOR_REDE})
        ok = True
    except Exception:
        pass
    try:
        driver.execute_script(JS_MONITOR_REDE)
        ok = True
    except Exception:
        pass
    if not ok:
        print("⚠️ Monitor de rede indisponível — o robô cai para espera por tempo.")
    return ok


def _estado_rede(driver):
    """[inflight, total] ou None quando o monitor não está ativo na página atual."""
    try:
        return driver.execute_script(
            "var s = window.__dassNet; return s ? [s.inflight, s.total] : null;"
        )
    except Exception:
        return None


def esperar_rede_ociosa(driver, quieto=1.5, timeout=60):
    """True quando nenhum XHR/fetch fica pendente por 'quieto' segundos seguidos."""
    if _estado_rede(driver) is None:
        time.sleep(quieto)
        return False
    t0 = time.time()
    ocioso_desde = None
    while time.time() - t0 < timeout:
        estado = _estado_rede(driver)
        if estado is None:
            time.sleep(quieto)
            return False
        if estado[0] <= 0:
            if ocioso_desde is None:
                ocioso_desde = time.time()
            elif time.time() - ocioso_desde >= quieto:
                return True
        else:
            ocioso_desde = None
        time.sleep(0.25)
    print(f"   ⚠️ Rede ainda ocupada após {timeout}s.")
    return False


def esperar_resposta_do_portal(driver, total_antes, espera_inicio=8, timeout=90):
    """Aguarda a requisição disparada por um clique começar e depois terminar."""
    if total_antes is None:
        time.sleep(2.0)
        return False
    t0 = time.time()
    while time.time() - t0 < espera_inicio:
        estado = _estado_rede(driver)
        if estado is None:
            break
        if estado[1] > total_antes:
            return esperar_rede_ociosa(driver, quieto=1.5, timeout=timeout)
        time.sleep(0.2)
    # Nenhuma requisição nova: o portal reaproveitou o resultado que já tinha.
    return esperar_rede_ociosa(driver, quieto=1.0, timeout=timeout)


# ---------------------- Utils Selenium ----------------------
def dump_debug(driver, tag):
    try:
        ts = time.strftime("%Y%m%d_%H%M%S")
        driver.save_screenshot(str(Path(DEBUG_DIR) / f"{ts}_{tag}.png"))
        with open(Path(DEBUG_DIR) / f"{ts}_{tag}.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        print(f"🧷 Debug salvo: {ts}_{tag}.png / .html")
    except Exception:
        pass


def safe_click(el, driver):
    try:
        el.click()
    except (ElementClickInterceptedException, StaleElementReferenceException):
        driver.execute_script("arguments[0].click();", el)


def norm(txt):
    if txt is None:
        return ""
    return unicodedata.normalize("NFKD", txt).encode("ascii", "ignore").decode().lower().strip()


def is_dropdown_open(container):
    try:
        wrapper = container.find_element(By.CSS_SELECTOR, ".multiselect__content-wrapper")
    except NoSuchElementException:
        return False
    style = (wrapper.get_attribute("style") or "").lower().replace(" ", "")
    return "display:none" not in style


def open_multiselect_by_placeholder(placeholder_substr, wait, driver):
    xp = (
        f"(//div[contains(@class,'multiselect')][.//span[contains(@class,'multiselect__placeholder') "
        f"and contains(normalize-space(), '{placeholder_substr}')]]"
        f" | "
        f"//div[contains(@class,'multiselect')][.//input[contains(@class,'multiselect__input') "
        f"and contains(@placeholder,'{placeholder_substr}')]])[1]"
    )
    cont = wait.until(EC.presence_of_element_located((By.XPATH, xp)))
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", cont)
    time.sleep(0.3)

    alvos = cont.find_elements(By.CSS_SELECTOR, ".multiselect__select")
    if not alvos:
        alvos = cont.find_elements(By.CSS_SELECTOR, ".multiselect__tags")
    alvo = alvos[0] if alvos else cont

    for tentativa in range(3):
        try:
            if tentativa == 0:
                alvo.click()
            elif tentativa == 1:
                driver.execute_script("arguments[0].click();", alvo)
            else:
                driver.execute_script("arguments[0].click();", cont)
        except Exception:
            driver.execute_script("arguments[0].click();", cont)
        try:
            WebDriverWait(driver, 4).until(lambda d: is_dropdown_open(cont))
            return cont
        except TimeoutException:
            time.sleep(0.4)

    dump_debug(driver, "dropdown_nao_abriu")
    raise TimeoutException(f"Dropdown '{placeholder_substr}' não abriu após 3 tentativas.")


def select_option_in_multiselect(container, text_exact, wait, driver):
    xp = (".//li[.//span[contains(@class,'multiselect__option')]//div[normalize-space()='%s']]"
          "//span[contains(@class,'multiselect__option')]") % text_exact
    option = wait.until(EC.presence_of_element_located((By.XPATH, xp)))
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", option)
    driver.execute_script("arguments[0].click();", option)
    try:
        WebDriverWait(driver, 5).until(lambda d: not is_dropdown_open(container))
    except TimeoutException:
        driver.find_element(By.TAG_NAME, "body").click()


def read_selected_from_container(cont):
    try:
        single = cont.find_element(By.CSS_SELECTOR, ".multiselect__tags .multiselect__single")
        return (single.text or "").strip()
    except NoSuchElementException:
        try:
            return (cont.find_element(By.CSS_SELECTOR, ".multiselect__tags").text or "").strip()
        except Exception:
            return ""


def get_selected_count(driver):
    try:
        el = driver.find_element(By.CSS_SELECTOR, ".selection-counter")
        m = re.search(r"(\d+)", el.text or "")
        if m:
            return int(m.group(1))
    except Exception:
        pass
    try:
        el = driver.find_element(By.XPATH, "//*[contains(normalize-space(), 'Selecionados:')]")
        m = re.search(r"Selecionados:\s*(\d+)", el.text)
        return int(m.group(1)) if m else 0
    except Exception:
        return 0


def _listar_candidatos(dirs, exts=(".txt", ".TXT")):
    files = []
    for d in dirs:
        if not d.exists():
            continue
        for p in d.iterdir():
            if p.is_file() and not p.name.endswith(".crdownload"):
                if exts is None or p.suffix in exts:
                    try:
                        files.append((p.stat().st_mtime, p))
                    except Exception:
                        pass
    return files


def esperar_arquivo_baixado_robusto(start_ts, timeout=120, exts=(".txt", ".TXT")):
    t0 = time.time()
    print("🔎 Monitorando pastas para download:")
    for d in WATCH_DIRS:
        print("   -", d)
    while time.time() - t0 < timeout:
        cand = _listar_candidatos(WATCH_DIRS, exts)
        recentes = [(mt, p) for (mt, p) in cand if mt >= start_ts - 0.5]
        if recentes:
            mt, path = max(recentes, key=lambda x: x[0])
            try:
                tam1 = path.stat().st_size
            except Exception:
                continue
            time.sleep(1.5)
            try:
                tam2 = path.stat().st_size
            except Exception:
                continue
            if tam1 != tam2 or tam2 == 0:
                continue
            print(f"📥 Arquivo novo estabilizado: {path} ({tam2} bytes)")
            return str(path)
        time.sleep(0.5)
    print("❌ Nenhum arquivo .txt NOVO apareceu dentro do timeout. Sem fallback: nada será enviado.")
    return None


def contar_linhas(driver):
    return len(driver.find_elements(By.XPATH, ROW_XPATH))


def ler_total_paginacao(driver):
    """Lê o total real de registros informado pela paginação. None se indisponível."""
    tentativas = [
        (By.CSS_SELECTOR, ".el-pagination__total"),
        (By.XPATH, "//*[contains(@class,'el-pagination')]//*[contains(translate(text(),'TOTAL','total'),'total')]"),
    ]
    for by, sel in tentativas:
        try:
            for el in driver.find_elements(by, sel):
                txt = (el.text or "").replace(".", "").replace(" ", "")
                m = re.search(r"(\d+)", txt)
                if m:
                    return int(m.group(1))
        except Exception:
            continue
    return None


def proxima_pagina_habilitada(driver):
    """True se o botão de próxima página existir e estiver ativo (há mais registros)."""
    try:
        btn = driver.find_element(By.CSS_SELECTOR, ".el-pagination button.btn-next")
        desab = btn.get_attribute("disabled") or "is-disabled" in (btn.get_attribute("class") or "")
        return not desab
    except Exception:
        return False


def aplicar_tamanho_pagina(valor, wait, driver):
    """Abre o seletor e escolhe o tamanho de página. Retorna o tamanho aplicado ou None."""
    try:
        pag = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".el-pagination")))
    except TimeoutException:
        print("ℹ️ Sem .el-pagination — seguindo.")
        return None

    driver.execute_script("arguments[0].scrollIntoView({block:'end'});", pag)
    time.sleep(0.3)
    try:
        opener = pag.find_element(By.CSS_SELECTOR, ".el-pagination__sizes .el-select .el-input__suffix")
    except NoSuchElementException:
        opener = pag.find_element(By.CSS_SELECTOR, ".el-pagination__sizes .el-select .el-input")
    safe_click(opener, driver)

    try:
        dropdown = wait.until(EC.visibility_of_element_located((
            By.XPATH, "//div[contains(@class,'el-select-dropdown') and not(contains(@style,'display: none'))]"
        )))
    except TimeoutException:
        print("⚠️ Dropdown de itens por página não abriu.")
        return None

    itens = dropdown.find_elements(By.CSS_SELECTOR, "li.el-select-dropdown__item")
    if not itens:
        print("⚠️ Nenhuma opção de itens por página encontrada.")
        return None

    # textContent em vez de .text: item fora da área visível do dropdown vem vazio.
    try:
        textos = driver.execute_script(
            "return arguments[0].map(function (e) { return (e.textContent || '').trim(); });", itens
        )
    except Exception:
        textos = [(it.text or "") for it in itens]

    # Casa pelo número exato: procurar "100" como substring acharia "1000/página".
    opcoes = []
    for it, txt in zip(itens, textos):
        m = re.search(r"(\d+)", (txt or "").replace(".", ""))
        if m:
            opcoes.append((int(m.group(1)), it))

    alvo, tamanho = None, None
    for numero, it in opcoes:
        if numero == valor:
            alvo, tamanho = it, numero
            break
    if alvo is None and opcoes:
        tamanho, alvo = max(opcoes, key=lambda par: par[0])
    if alvo is None:
        alvo, tamanho = itens[-1], None

    total_antes = (_estado_rede(driver) or [0, None])[1]
    driver.execute_script("arguments[0].click();", alvo)
    try:
        wait.until(EC.invisibility_of_element(dropdown))
    except TimeoutException:
        pass
    esperar_resposta_do_portal(driver, total_antes)
    print(f"   📐 Tamanho de página aplicado: {tamanho}")
    return tamanho


def contagem_estavel(driver, janela=4, timeout=90):
    """Conta as linhas depois que a rede fica ociosa e a contagem para de mudar."""
    esperar_rede_ociosa(driver, quieto=1.5, timeout=min(timeout, 60))
    t0 = time.time()
    atual = contar_linhas(driver)
    estavel_desde = time.time()
    while time.time() - t0 < timeout:
        time.sleep(1.0)
        n = contar_linhas(driver)
        if n != atual:
            atual = n
            estavel_desde = time.time()
            continue
        if n > 0 and (time.time() - estavel_desde) >= janela:
            return n
    print(f"⚠️ Contagem não ficou estável por {janela}s em {timeout}s (última: {atual}).")
    return None


def carregar_tabela_confiavel(wait, driver, tentativas=5):
    """Recarrega a tabela até obter duas leituras iguais e coerentes.

    O portal às vezes responde com a página padrão (10 linhas) mesmo com o maior
    tamanho de página selecionado. Uma leitura assim é descartada em vez de
    derrubar a execução: só o maior valor confirmado duas vezes é aceito.
    Aplicar 100 e depois o máximo garante que a última resposta a chegar seja a
    da requisição mais recente, e não um payload antigo da carga inicial.
    """
    anterior, data_anterior = ler_ultimo_count()
    leituras = []
    tamanho_por_leitura = {}

    for i in range(1, tentativas + 1):
        print(f"🔁 Carga {i}/{tentativas} da tabela...")
        aplicar_tamanho_pagina(100, wait, driver)
        tamanho = aplicar_tamanho_pagina(1000, wait, driver)
        n = contagem_estavel(driver, janela=4, timeout=90)
        if n is None:
            continue
        prox = proxima_pagina_habilitada(driver)
        print(f"   Leitura {i}: {n} linhas | página seguinte: {'sim' if prox else 'não'}")
        if prox:
            print("   Ainda há páginas — o tamanho aplicado não cobre o total.")
            continue

        leituras.append(n)
        tamanho_por_leitura[n] = tamanho
        maior = max(leituras)

        if n < maior:
            print(f"   ⚠️ Resposta parcial do portal ({n} contra {maior} já lidas) — descartada.")
            continue
        if anterior and n < anterior * LIMITE_REGRESSAO and not FORCAR:
            print(f"   ⚠️ {n} linhas destoam do último volume conhecido ({anterior} em {data_anterior}) — descartada.")
            continue
        if leituras.count(n) >= 2:
            print(f"✅ Duas leituras independentes concordam: {n} linhas.")
            return n, tamanho_por_leitura[n]

    print(f"❌ Leituras não convergiram: {leituras}")
    if leituras:
        print(f"   Maior leitura observada: {max(leituras)}. Se o volume real caiu de verdade, rode com DASS_FORCAR=1.")
    return None, None


def marcar_todas_as_linhas_generic_table(wait, driver):
    wait.until(EC.presence_of_element_located((By.XPATH, ROW_XPATH)))
    linhas_antes = contar_linhas(driver)
    head_xps = [
        "//table[contains(@class,'generic-table')]/thead//span[contains(@class,'el-checkbox__inner')]",
        "//table[contains(@class,'generic-table')]/thead//label[contains(@class,'el-checkbox')]",
        "//thead[contains(@class,'generic-table-thead')]//span[contains(@class,'el-checkbox__inner')]",
        "//thead[contains(@class,'generic-table-head')]//span[contains(@class,'el-checkbox__inner')]",
        "//thead[contains(@class,'generic-table-head')]//label[contains(@class,'el-checkbox')]",
        "//thead//span[contains(@class,'el-checkbox__inner')]",
        "//thead//label[contains(@class,'el-checkbox')]",
    ]
    head_el = None
    for xp in head_xps:
        try:
            head_el = driver.find_element(By.XPATH, xp)
            break
        except NoSuchElementException:
            continue
    if head_el is None:
        dump_debug(driver, "sem_checkbox_header")
        raise TimeoutException("Header checkbox não encontrado.")
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", head_el)
    safe_click(head_el, driver)
    # O contador do rodapé leva alguns segundos para acompanhar tabelas grandes:
    # esperar só por "> 0" já dava a marcação como parcial e abortava a execução.
    try:
        WebDriverWait(driver, 20).until(lambda d: get_selected_count(d) >= linhas_antes)
    except TimeoutException:
        pass
    if get_selected_count(driver) == 0:
        rows = driver.find_elements(By.XPATH, ROW_XPATH)
        for r in rows:
            try:
                cb = r.find_element(By.XPATH, ".//span[contains(@class,'el-checkbox__inner')]")
                safe_click(cb, driver)
                time.sleep(0.02)
            except Exception:
                pass

    linhas_depois = contar_linhas(driver)
    total = get_selected_count(driver)
    print(f"✅ Selecionados agora: {total} | linhas antes={linhas_antes}, depois={linhas_depois}")

    if linhas_depois != linhas_antes:
        dump_debug(driver, "tabela_mudou_na_marcacao")
        raise TimeoutException(
            f"A tabela foi reescrita durante a marcação ({linhas_antes} → {linhas_depois}). "
            "Resposta atrasada do portal sobrescreveu os dados."
        )
    if total == 0:
        dump_debug(driver, "selecao_zerada")
        raise TimeoutException("Nenhuma linha ficou selecionada.")
    return total


def find_action_button_by_tooltip(keywords, wait, driver, actions):
    group = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".action-group")))
    buttons = group.find_elements(By.CSS_SELECTOR, "button.action-button")
    if not buttons:
        raise TimeoutException("Nenhum button.action-button encontrado na action-group.")
    for btn in buttons:
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
            actions.move_to_element(btn).perform()
            time.sleep(0.4)
            tips = driver.find_elements(By.CSS_SELECTOR, ".el-tooltip__popper")
            for tip in tips:
                if tip.is_displayed():
                    t = norm(tip.text)
                    if any(norm(k) in t for k in keywords):
                        return btn
        except Exception:
            continue
    return None


def baixar_via_action_group(wait, driver, actions):
    keywords = ["arquivo de ordem", "arquivo", "txt", "baixar"]
    btn = find_action_button_by_tooltip(keywords, wait, driver, actions)
    if btn is None:
        group = driver.find_element(By.CSS_SELECTOR, ".action-group")
        btns = group.find_elements(By.CSS_SELECTOR, "button.action-button")
        if not btns:
            dump_debug(driver, "sem_action_button")
            raise TimeoutException("Nenhum botão de ação disponível para download.")
        btn = btns[0]
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
    safe_click(btn, driver)
    time.sleep(0.5)
    try:
        modal = WebDriverWait(driver, 5).until(
            EC.visibility_of_element_located((By.CSS_SELECTOR, ".el-dialog__wrapper"))
        )
        candidatos = modal.find_elements(By.XPATH, ".//*[self::button or self::a or self::span or self::div]")
        for c in candidatos:
            try:
                if not c.is_displayed():
                    continue
                tx = norm(c.text)
                if any(k in tx for k in ["txt", "baixar", "gerar", "arquivo da ordem"]):
                    safe_click(c, driver)
                    break
            except Exception:
                continue
    except TimeoutException:
        pass


def limpar_downloads_antigos():
    """Remove só os TXT de nome fixo (ambíguos). As cópias datadas são preservadas."""
    removidos = 0
    for p in list(Path(PASTA_DOWNLOAD).glob("*.txt")) + list(Path(PASTA_DOWNLOAD).glob("*.TXT")):
        if re.search(r"_\d{8}_\d{6}\.txt$", p.name, re.IGNORECASE):
            continue
        try:
            p.unlink()
            removidos += 1
        except Exception:
            pass
    if removidos:
        print(f"🧹 {removidos} TXT de nome fixo removido(s) de downloads_dass.")


def arquivar_txt(caminho, manter=60):
    """Guarda uma cópia datada do TXT baixado e mantém apenas as N mais recentes."""
    try:
        origem = Path(caminho)
        destino = Path(PASTA_DOWNLOAD) / f"oc_{time.strftime('%Y%m%d_%H%M%S')}.txt"
        shutil.copy2(origem, destino)
        print(f"🗄️ Cópia arquivada: {destino.name} ({destino.stat().st_size} bytes)")
        copias = sorted(Path(PASTA_DOWNLOAD).glob("oc_*_*.txt"),
                        key=lambda p: p.stat().st_mtime, reverse=True)
        for velho in copias[manter:]:
            try:
                velho.unlink()
            except Exception:
                pass
        return str(destino)
    except Exception as e:
        print(f"⚠️ Não foi possível arquivar cópia do TXT: {e}")
        return None


def ler_ultimo_count():
    try:
        with open(ARQ_ESTADO, "r", encoding="utf-8") as f:
            d = json.load(f)
        return int(d.get("count", 0)), d.get("data", "?")
    except Exception:
        return 0, None


def salvar_ultimo_count(n):
    try:
        with open(ARQ_ESTADO, "w", encoding="utf-8") as f:
            json.dump({"count": int(n), "data": time.strftime("%Y-%m-%d %H:%M:%S")}, f)
    except Exception as e:
        print(f"⚠️ Não foi possível gravar {ARQ_ESTADO.name}: {e}")


def extrair_ocs_do_txt(caminho_txt):
    padrao = re.compile(r"^\s*\d{14}\s+(\d{10})", re.MULTILINE)
    vistos = set()
    ocs = []
    with open(caminho_txt, "r", encoding="utf-8", errors="ignore") as f:
        texto = f.read()
    for m in padrao.finditer(texto):
        oc = m.group(1)
        if oc not in vistos:
            vistos.add(oc)
            ocs.append(oc)
    return ocs


def reduzir_ocs_para_8_digitos(ocs):
    vistos = set()
    saida = []
    for oc in ocs:
        oc8 = str(oc)[:8]
        if len(oc8) != 8 or not oc8.isdigit():
            continue
        if oc8 not in vistos:
            vistos.add(oc8)
            saida.append(oc8)
    return saida


def enviar_ocs_para_planilha_via_webapp(ocs, url=WEBAPP_URL, secret=WEBAPP_SECRET):
    if not ocs:
        print("⛔ Lista de OCs vazia — envio abortado para não limpar a planilha.")
        return False
    payload = {"ocs": ocs}
    if secret:
        payload["secret"] = secret
    if FORCAR:
        payload["forcar"] = True
    ultimo_erro = None
    for tentativa in range(1, 4):
        try:
            r = requests.post(url, json=payload, timeout=60)
            r.raise_for_status()
            print("✅ Resposta do WebApp (bruta):", r.text[:500])
            try:
                data = r.json()
            except Exception:
                print("⚠️ Resposta não é JSON — tratando como falha.")
                return False
            if not data.get("ok"):
                print(f"⛔ WebApp RECUSOU a gravação: {data.get('motivo')} | {data.get('detalhe')}")
                print("   A planilha NÃO foi alterada. Use DASS_FORCAR=1 se a queda for legítima.")
                return False
            print(f"📄 Planilha: {data.get('docName')} | Aba: {data.get('tabName')} | "
                  f"Início: B{data.get('startRow')} | Gravadas: {data.get('count')} | Amostra: {data.get('sample')}")
            print(f"🔗 URL: {data.get('url')}")
            return True
        except Exception as e:
            ultimo_erro = e
            print(f"⚠️ Falha no envio (tentativa {tentativa}/3): {e}")
            time.sleep(5 * tentativa)
    print(f"❌ Envio ao WebApp falhou definitivamente: {ultimo_erro}")
    return False


# ---------------------- Fluxo principal ----------------------
def fluxo_principal():
    print("=" * 70)
    print(f"🚀 Iniciando o robô em {time.strftime('%Y-%m-%d %H:%M:%S')} | HEADLESS={HEADLESS}")
    print(f"🐍 Interpretador : {sys.executable}")
    print(f"📁 Pasta script  : {SCRIPT_DIR}")
    print(f"👤 Home          : {HOME}")

    print("🔧 Verificando/Atualizando driver do Chrome...")
    caminho_driver = os.environ.get("DASS_CHROMEDRIVER") or None
    if caminho_driver:
        print(f"   Usando chromedriver de DASS_CHROMEDRIVER: {caminho_driver}")
    else:
        try:
            caminho_driver = ChromeDriverManager().install()
        except Exception as e:
            print(f"⚠️ webdriver-manager falhou ({e}). Tentando o Selenium Manager.")
            caminho_driver = None

    driver = subir_chrome(caminho_driver)
    if driver is None:
        return 3

    try:
        try:
            driver.set_window_size(1920, 1080)
        except Exception:
            pass
        try:
            driver.execute_cdp_cmd("Page.setDownloadBehavior",
                                   {"behavior": "allow", "downloadPath": str(Path(PASTA_DOWNLOAD).resolve())})
        except Exception:
            pass

        wait = WebDriverWait(driver, TIMEOUT)
        actions = ActionChains(driver)
        instalar_monitor_rede(driver)

        driver.get("https://dsc.grupodass.com.br/#/login")
        print("✅ Página de login acessada.")
        wait.until(EC.presence_of_element_located((By.NAME, "user_useruser"))).send_keys(USUARIO)
        driver.find_element(By.NAME, "user_usersenha").send_keys(SENHA)
        safe_click(wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Entrar')]"))), driver)
        print("✅ Login enviado.")
        time.sleep(3.0)

        print("... Navegando para a área de pedidos...")
        safe_click(wait.until(EC.element_to_be_clickable((By.XPATH, "//img[contains(@src, 'orders')]"))), driver)
        print("✅ Área de pedidos acessada.")
        instalar_monitor_rede(driver)   # no-op se o CDP já injetou
        time.sleep(1.5)

        print("... Abrindo filtro 'Situação de Or'...")
        cont_sit = open_multiselect_by_placeholder("Situação de Or", wait, driver)
        print("... Selecionando 'Aberta'...")
        select_option_in_multiselect(cont_sit, "Aberta", wait, driver)
        sel_txt = read_selected_from_container(cont_sit)
        if "aberta" not in norm(sel_txt):
            driver.find_element(By.TAG_NAME, "body").click()
            time.sleep(0.3)
            sel_txt = read_selected_from_container(cont_sit)
            if "aberta" not in norm(sel_txt):
                dump_debug(driver, "filtro_aberta_falhou")
                raise TimeoutException("Filtro 'Aberta' não ficou selecionado.")
        print("✅ Filtro 'Aberta' confirmado.")

        print("... Carregando a tabela completa (dupla leitura)...")
        total = ler_total_paginacao(driver)
        if total is not None:
            print(f"📊 Total informado pela paginação: {total}")

        linhas, tamanho_pag = carregar_tabela_confiavel(wait, driver, tentativas=5)
        if linhas is None:
            print("❌ Não foi possível obter duas leituras concordantes da tabela. Abortando.")
            dump_debug(driver, "leituras_divergentes")
            return 11

        total = ler_total_paginacao(driver) or total
        if total and tamanho_pag and total > tamanho_pag:
            print(f"❌ {total} ordens abertas excedem o máximo de {tamanho_pag} por página.")
            dump_debug(driver, "paginacao_excedida")
            return 13
        if total and linhas < total:
            print(f"❌ Carregadas {linhas} linhas de {total} declaradas. Abortando.")
            dump_debug(driver, "tabela_incompleta")
            return 11

        print("... Marcando todos os itens (header checkbox)...")
        total_sel = marcar_todas_as_linhas_generic_table(wait, driver)

        if total_sel != linhas:
            print(f"❌ Selecionados: {total_sel}, mas há {linhas} linhas na tela. Abortando.")
            dump_debug(driver, "selecao_parcial")
            return 11

        if contar_linhas(driver) != linhas:
            print("❌ A tabela mudou depois da marcação. Abortando.")
            dump_debug(driver, "tabela_mudou_pos_marcacao")
            return 11

        print("⏳ Aguardando o site processar a seleção...")
        time.sleep(8)

        limpar_downloads_antigos()
        start_ts = time.time()
        print("... Iniciando download pelo botão da action-group...")
        baixar_via_action_group(wait, driver, actions)

        print("... Aguardando arquivo novo (até 120s)...")
        new_file = esperar_arquivo_baixado_robusto(start_ts, timeout=120, exts=(".txt", ".TXT"))
        if not new_file:
            dump_debug(driver, "sem_download")
            return 4

        print(f"🎉 Arquivo obtido: {new_file}")
        arquivar_txt(new_file)
        ocs10 = extrair_ocs_do_txt(new_file)
        ocs8 = reduzir_ocs_para_8_digitos(ocs10)
        print(f"🔎 OCs (10 dígitos): {len(ocs10)} | Após cortar (8 únicos): {len(ocs8)} | Linhas selecionadas: {total_sel}")
        print(f"🧪 Amostra pós-corte: {ocs8[:5]}{' ...' if len(ocs8) > 5 else ''}")

        if not ocs8:
            print("❌ TXT baixado não produziu nenhuma OC válida — envio abortado.")
            return 5

        if len(ocs8) != total_sel:
            print(f"⚠️ Divergência: {len(ocs8)} OCs únicas contra {total_sel} linhas selecionadas.")
            if not FORCAR:
                print("   Abortando. Se a divergência for esperada, rode com DASS_FORCAR=1.")
                return 12
            print("   DASS_FORCAR=1 ativo — seguindo mesmo assim.")

        anterior, data_anterior = ler_ultimo_count()
        if anterior and len(ocs8) < anterior * LIMITE_REGRESSAO:
            print(f"⚠️ Queda de volume: {len(ocs8)} agora contra {anterior} em {data_anterior}.")
            if not FORCAR:
                print("   Abortando para não apagar a planilha. Se a queda for real, rode com DASS_FORCAR=1.")
                return 10
            print("   DASS_FORCAR=1 ativo — seguindo mesmo assim.")

        if not enviar_ocs_para_planilha_via_webapp(ocs8):
            return 6

        salvar_ultimo_count(len(ocs8))
        return 0

    except Exception as e:
        print("\n❌ OCORREU UM ERRO DURANTE A AUTOMAÇÃO:\n", repr(e))
        try:
            dump_debug(driver, "erro_geral")
        except Exception:
            pass
        try:
            with open(Path(DEBUG_DIR) / "erro_robodass.log", "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {repr(e)}\n")
        except Exception:
            pass
        return 1
    finally:
        print(f"🤖 Finalizando o robô em {time.strftime('%Y-%m-%d %H:%M:%S')}.")
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(fluxo_principal())
