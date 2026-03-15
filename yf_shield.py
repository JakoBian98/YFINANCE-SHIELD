

__version__ = "1.0.0"
__author__ = "Graphia Team"

import time
import random
import threading
import logging
import re
import requests
import yfinance as yf
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from collections import deque
from http.cookiejar import CookieJar

# ══════════════════════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════════════════════

logger = logging.getLogger("yf_shield")

# ══════════════════════════════════════════════════════════════
# DEFAULT CONFIG
# ══════════════════════════════════════════════════════════════

DEFAULT_CONFIG = {
    "min_delay": 1.5,           # Minimum istek arası bekleme (saniye)
    "max_delay": 3.5,           # Maksimum istek arası bekleme (saniye)
    "max_retries": 5,           # 429/503 için maksimum yeniden deneme
    "backoff_base": 3,          # Exponential backoff çarpanı
    "max_rpm": 25,              # Maksimum istek/dakika (rate limit)
    "burst_limit": 5,           # Art arda kaç istek atılabilir (burst)
    "burst_cooldown": 10,       # Burst sonrası soğuma süresi (saniye)
    "crumb_refresh_interval": 1800,  # Crumb yenileme aralığı (saniye, 30 dk)
    "cookie_refresh_interval": 3600, # Cookie yenileme aralığı (saniye, 60 dk)
    "verbose": True,            # Detaylı log
    "proxies": None,            # Proxy listesi (opsiyonel)
}

# ══════════════════════════════════════════════════════════════
# USER AGENTS
# ══════════════════════════════════════════════════════════════

USER_AGENTS = [
    # ── Chrome (Windows / Mac / Linux) ──
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",

    # ── Firefox ──
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:135.0) Gecko/20100101 Firefox/135.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:135.0) Gecko/20100101 Firefox/135.0",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:135.0) Gecko/20100101 Firefox/135.0",

    # ── Safari ──
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3 Safari/605.1.15",

    # ── Mobil ──
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPad; CPU OS 18_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Mobile Safari/537.36",

    # ── Edge & Brave ──
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36 Edg/134.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36 Edg/134.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36 Brave/134",
]


# ══════════════════════════════════════════════════════════════
# TOKEN BUCKET RATE LIMITER
# ══════════════════════════════════════════════════════════════

class TokenBucketRateLimiter:
    """
    Token bucket algoritması ile akıllı rate limiting.
    - max_rpm: Dakikada maksimum istek
    - burst_limit: Art arda atılabilecek maksimum istek
    - burst_cooldown: Burst sonrası soğuma süresi
    """

    def __init__(self, max_rpm=25, burst_limit=5, burst_cooldown=10):
        self._lock = threading.Lock()
        self._timestamps = deque()  # Son 60 saniyedeki istek zamanları
        self._burst_count = 0
        self._last_burst_reset = time.time()
        self.max_rpm = max_rpm
        self.burst_limit = burst_limit
        self.burst_cooldown = burst_cooldown

    def acquire(self):
        """İstek öncesi çağrılır. Gerekirse bekler."""
        with self._lock:
            now = time.time()

            # Son 60 saniye dışındaki kayıtları temizle
            while self._timestamps and self._timestamps[0] < now - 60:
                self._timestamps.popleft()

            # RPM kontrolü
            if len(self._timestamps) >= self.max_rpm:
                wait_until = self._timestamps[0] + 60
                wait = wait_until - now
                if wait > 0:
                    logger.info(f"⏳ RPM limiti ({self.max_rpm}/dk) — {wait:.1f}s bekleniyor")
                    time.sleep(wait)
                    now = time.time()
                    # Tekrar temizle
                    while self._timestamps and self._timestamps[0] < now - 60:
                        self._timestamps.popleft()

            # Burst kontrolü
            if now - self._last_burst_reset > self.burst_cooldown:
                self._burst_count = 0
                self._last_burst_reset = now

            if self._burst_count >= self.burst_limit:
                wait = self.burst_cooldown - (now - self._last_burst_reset)
                if wait > 0:
                    logger.info(f"⏳ Burst limiti ({self.burst_limit}) — {wait:.1f}s soğuma")
                    time.sleep(wait)
                    self._burst_count = 0
                    self._last_burst_reset = time.time()

            self._timestamps.append(time.time())
            self._burst_count += 1

    @property
    def current_rpm(self):
        now = time.time()
        return sum(1 for t in self._timestamps if t > now - 60)


# ══════════════════════════════════════════════════════════════
# CRUMB & COOKIE MANAGER
# ══════════════════════════════════════════════════════════════

class YahooCrumbManager:
    """
    Yahoo Finance'ın crumb/cookie tabanlı kimlik doğrulamasını yönetir.
    Crumb süresi dolduğunda otomatik yeniler.
    """

    CONSENT_URL = "https://consent.yahoo.com/v2/collectConsent"
    CRUMB_URL = "https://query2.finance.yahoo.com/v1/test/getcrumb"
    FINANCE_URL = "https://finance.yahoo.com"

    def __init__(self, session, refresh_interval=1800):
        self._session = session
        self._crumb = None
        self._crumb_time = 0
        self._cookie_time = 0
        self._refresh_interval = refresh_interval
        self._lock = threading.Lock()

    def get_crumb(self):
        """Geçerli crumb döner, süresi dolmuşsa yeniler."""
        with self._lock:
            now = time.time()
            if self._crumb and (now - self._crumb_time) < self._refresh_interval:
                return self._crumb

            logger.info("🔑 Yahoo crumb yenileniyor...")
            try:
                self._refresh_cookies()
                self._fetch_crumb()
            except Exception as e:
                logger.warning(f"⚠️ Crumb yenileme hatası: {e}")

            return self._crumb

    def _refresh_cookies(self):
        """Yahoo'dan taze cookie al."""
        try:
            # Ana sayfaya git — cookie'leri topla
            resp = self._session.get(
                self.FINANCE_URL,
                headers={"User-Agent": random.choice(USER_AGENTS)},
                timeout=10,
                allow_redirects=True,
            )

            # EU consent redirect kontrolü
            if "consent.yahoo.com" in resp.url or resp.status_code == 302:
                self._handle_consent(resp)

            self._cookie_time = time.time()
            cookie_count = len(self._session.cookies)
            logger.info(f"🍪 Cookie yenilendi ({cookie_count} cookie)")

        except Exception as e:
            logger.warning(f"⚠️ Cookie yenileme hatası: {e}")

    def _handle_consent(self, initial_response):
        """EU GDPR consent sayfasını otomatik geç."""
        try:
            # Consent formundaki hidden field'ları bul
            text = initial_response.text
            csrf_match = re.search(r'name="csrfToken"\s+value="([^"]+)"', text)
            session_match = re.search(r'name="sessionId"\s+value="([^"]+)"', text)

            if csrf_match and session_match:
                consent_data = {
                    "csrfToken": csrf_match.group(1),
                    "sessionId": session_match.group(1),
                    "originalDoneUrl": "https://finance.yahoo.com/",
                    "namespace": "yahoo",
                    "agree": "agree",
                }
                self._session.post(
                    self.CONSENT_URL,
                    data=consent_data,
                    headers={"User-Agent": random.choice(USER_AGENTS)},
                    timeout=10,
                )
                logger.info("✅ EU consent otomatik onaylandı")
        except Exception as e:
            logger.debug(f"Consent işlemi atlandı: {e}")

    def _fetch_crumb(self):
        """Crumb token'ı çek."""
        try:
            resp = self._session.get(
                self.CRUMB_URL,
                headers={"User-Agent": random.choice(USER_AGENTS)},
                timeout=10,
            )
            if resp.status_code == 200 and len(resp.text) < 50:
                self._crumb = resp.text.strip()
                self._crumb_time = time.time()
                logger.info(f"🔑 Crumb alındı: {self._crumb[:8]}...")
            else:
                logger.warning(f"⚠️ Crumb alınamadı (status: {resp.status_code})")
        except Exception as e:
            logger.warning(f"⚠️ Crumb fetch hatası: {e}")

    @property
    def is_valid(self):
        return self._crumb is not None and (time.time() - self._crumb_time) < self._refresh_interval


# ══════════════════════════════════════════════════════════════
# PROXY ROTATOR
# ══════════════════════════════════════════════════════════════

class ProxyRotator:
    """
    Proxy listesini round-robin şeklinde döndürür.
    Başarısız proxy'leri geçici olarak devre dışı bırakır.
    """

    def __init__(self, proxies=None):
        self._proxies = proxies or []
        self._index = 0
        self._failures = {}  # proxy -> (fail_count, last_fail_time)
        self._lock = threading.Lock()
        self._cooldown = 300  # 5 dk ban süresi
        self._max_fails = 3   # 3 başarısızlıktan sonra devre dışı

    def get_proxy(self):
        """Sıradaki sağlıklı proxy'yi döner. Yoksa None."""
        if not self._proxies:
            return None

        with self._lock:
            now = time.time()
            attempts = 0

            while attempts < len(self._proxies):
                proxy = self._proxies[self._index % len(self._proxies)]
                self._index += 1
                attempts += 1

                # Bu proxy ban'lı mı?
                if proxy in self._failures:
                    fails, last_time = self._failures[proxy]
                    if fails >= self._max_fails and (now - last_time) < self._cooldown:
                        continue  # Hâlâ soğuma süresinde, atla
                    elif (now - last_time) >= self._cooldown:
                        del self._failures[proxy]  # Süre doldu, sıfırla

                return {"http": proxy, "https": proxy}

            # Tüm proxy'ler ban'lı — direkt bağlan
            logger.warning("⚠️ Tüm proxy'ler devre dışı — direkt bağlantı")
            return None

    def report_failure(self, proxy_url):
        """Başarısız proxy'yi raporla."""
        with self._lock:
            if proxy_url in self._failures:
                fails, _ = self._failures[proxy_url]
                self._failures[proxy_url] = (fails + 1, time.time())
            else:
                self._failures[proxy_url] = (1, time.time())

    def report_success(self, proxy_url):
        """Başarılı proxy'nin sayacını sıfırla."""
        with self._lock:
            if proxy_url in self._failures:
                del self._failures[proxy_url]

    @property
    def available_count(self):
        now = time.time()
        active = 0
        for p in self._proxies:
            if p not in self._failures:
                active += 1
            else:
                fails, last_time = self._failures[p]
                if fails < self._max_fails or (now - last_time) >= self._cooldown:
                    active += 1
        return active


# ══════════════════════════════════════════════════════════════
# SHIELD SESSION (Ana Sınıf)
# ══════════════════════════════════════════════════════════════

class ShieldSession(requests.Session):
    """
    Yahoo Finance için korumalı HTTP oturumu.
    Tüm koruma katmanlarını birleştirir.
    """

    def __init__(self, config=None):
        super().__init__()
        self.config = {**DEFAULT_CONFIG, **(config or {})}

        # Koruma katmanları
        self.rate_limiter = TokenBucketRateLimiter(
            max_rpm=self.config["max_rpm"],
            burst_limit=self.config["burst_limit"],
            burst_cooldown=self.config["burst_cooldown"],
        )
        self.crumb_manager = YahooCrumbManager(
            self,
            refresh_interval=self.config["crumb_refresh_interval"],
        )
        self.proxy_rotator = ProxyRotator(self.config["proxies"])

        # İstatistikler
        self._stats_lock = threading.Lock()
        self._stats = {
            "total_requests": 0,
            "successful": 0,
            "retried": 0,
            "failed": 0,
            "rate_limited": 0,
            "proxy_rotations": 0,
            "crumb_refreshes": 0,
            "start_time": time.time(),
        }

        # Throttle state
        self._throttle_lock = threading.Lock()
        self._last_request_time = 0

    def get(self, url, **kwargs):
        return self._shielded_request(super().get, url, **kwargs)

    def post(self, url, **kwargs):
        return self._shielded_request(super().post, url, **kwargs)

    def _shielded_request(self, method, url, **kwargs):
        """Tüm koruma katmanlarını uygulayan merkezi istek metodu."""

        # 1. Rate limiting
        self.rate_limiter.acquire()

        # 2. Throttle (minimum bekleme)
        self._throttle()

        # 3. User-Agent rotasyonu
        self.headers.update({"User-Agent": random.choice(USER_AGENTS)})

        # 4. Proxy rotasyonu
        proxy = self.proxy_rotator.get_proxy()
        if proxy:
            kwargs["proxies"] = proxy
            current_proxy = list(proxy.values())[0]
        else:
            current_proxy = None

        # 5. Timeout ayarla (yoksa default)
        if "timeout" not in kwargs:
            kwargs["timeout"] = 15

        # 6. Retry döngüsü
        max_retries = self.config["max_retries"]
        last_response = None

        for attempt in range(max_retries + 1):
            try:
                self._inc_stat("total_requests")
                response = method(url, **kwargs)
                last_response = response

                # ── Başarılı ──
                if response.status_code == 200:
                    self._inc_stat("successful")
                    if current_proxy:
                        self.proxy_rotator.report_success(current_proxy)
                    return response

                # ── 429 Too Many Requests ──
                if response.status_code == 429:
                    self._inc_stat("rate_limited")
                    wait = self._calculate_backoff(attempt)
                    logger.warning(
                        f"⚠️ 429 Rate Limited (deneme {attempt + 1}/{max_retries}) "
                        f"— {wait:.1f}s bekleniyor [{self._short_url(url)}]"
                    )
                    time.sleep(wait)
                    self.headers.update({"User-Agent": random.choice(USER_AGENTS)})

                    # Proxy değiştir
                    if self.proxy_rotator._proxies:
                        if current_proxy:
                            self.proxy_rotator.report_failure(current_proxy)
                        proxy = self.proxy_rotator.get_proxy()
                        if proxy:
                            kwargs["proxies"] = proxy
                            current_proxy = list(proxy.values())[0]
                            self._inc_stat("proxy_rotations")

                    self._inc_stat("retried")
                    continue

                # ── 403 Forbidden (crumb/cookie sorunu) ──
                if response.status_code == 403:
                    logger.warning(f"⚠️ 403 Forbidden — Crumb/cookie yenileniyor [{self._short_url(url)}]")
                    self.crumb_manager._refresh_cookies()
                    self.crumb_manager._fetch_crumb()
                    self._inc_stat("crumb_refreshes")
                    self._inc_stat("retried")
                    time.sleep(2)
                    continue

                # ── 503/502 Server Error ──
                if response.status_code in (502, 503, 504):
                    wait = self._calculate_backoff(attempt, base=2)
                    logger.warning(
                        f"⚠️ {response.status_code} Server Error "
                        f"(deneme {attempt + 1}/{max_retries}) — {wait:.1f}s [{self._short_url(url)}]"
                    )
                    time.sleep(wait)
                    self._inc_stat("retried")
                    continue

                # ── Diğer status kodları ──
                return response

            except requests.exceptions.ProxyError as e:
                logger.warning(f"🔄 Proxy hatası: {e} — proxy değiştiriliyor")
                if current_proxy:
                    self.proxy_rotator.report_failure(current_proxy)
                proxy = self.proxy_rotator.get_proxy()
                if proxy:
                    kwargs["proxies"] = proxy
                    current_proxy = list(proxy.values())[0]
                else:
                    kwargs.pop("proxies", None)
                    current_proxy = None
                self._inc_stat("proxy_rotations")
                time.sleep(2)
                continue

            except (requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout,
                    requests.exceptions.ChunkedEncodingError) as e:
                wait = 2 * (attempt + 1) + random.uniform(0, 1)
                logger.warning(
                    f"🔌 Bağlantı hatası (deneme {attempt + 1}/{max_retries}): "
                    f"{type(e).__name__} — {wait:.1f}s [{self._short_url(url)}]"
                )
                time.sleep(wait)
                self._inc_stat("retried")
                continue

            except Exception as e:
                logger.error(f"❌ Beklenmeyen hata: {type(e).__name__}: {e}")
                self._inc_stat("failed")
                if attempt < max_retries:
                    time.sleep(3)
                    continue
                raise

        # Tüm denemeler tükendi
        self._inc_stat("failed")
        logger.error(f"❌ Tüm denemeler başarısız ({max_retries + 1}x): {self._short_url(url)}")

        if last_response is not None:
            return last_response
        # Son çare: bir kez daha dene (retry olmadan)
        return method(url, **kwargs)

    def _throttle(self):
        """Minimum istek arası bekleme."""
        with self._throttle_lock:
            elapsed = time.time() - self._last_request_time
            delay = random.uniform(self.config["min_delay"], self.config["max_delay"])
            if elapsed < delay:
                time.sleep(delay - elapsed)
            self._last_request_time = time.time()

    def _calculate_backoff(self, attempt, base=None):
        """Exponential backoff + jitter."""
        b = base or self.config["backoff_base"]
        return b * (2 ** attempt) + random.uniform(0, 2)

    def _inc_stat(self, key):
        with self._stats_lock:
            self._stats[key] = self._stats.get(key, 0) + 1

    def _short_url(self, url):
        return url[:70] + "..." if len(url) > 70 else url

    @property
    def stats(self):
        """İstek istatistiklerini döner."""
        with self._stats_lock:
            s = dict(self._stats)
            s["uptime_seconds"] = round(time.time() - s.pop("start_time", time.time()), 1)
            s["current_rpm"] = self.rate_limiter.current_rpm
            s["active_proxies"] = self.proxy_rotator.available_count
            s["crumb_valid"] = self.crumb_manager.is_valid
            return s


# ══════════════════════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════════════════════

_active_session = None

def install(
    min_delay=1.5,
    max_delay=3.5,
    max_rpm=25,
    burst_limit=5,
    burst_cooldown=10,
    max_retries=5,
    proxies=None,
    verbose=True,
    log_level=None,
):
    """
    Yahoo Finance koruma katmanını aktifleştirir.
    Tek satırda çağır — geri kalan her şey otomatik.

    Args:
        min_delay: Minimum istek arası bekleme (saniye)
        max_delay: Maksimum istek arası bekleme (saniye)
        max_rpm: Dakikada maksimum istek sayısı
        burst_limit: Art arda atılabilecek istek sayısı
        burst_cooldown: Burst sonrası soğuma süresi (saniye)
        max_retries: 429/503 için retry sayısı
        proxies: Proxy URL listesi (opsiyonel)
                 Örn: ["http://user:pass@proxy1:8080", "socks5://proxy2:1080"]
        verbose: Başlangıç bilgilerini yazdır
        log_level: Logging seviyesi (None=WARNING, "DEBUG", "INFO")

    Returns:
        ShieldSession instance
    """
    global _active_session

    # Logging ayarla
    if log_level:
        level = getattr(logging, log_level.upper(), logging.WARNING)
        logging.basicConfig(
            level=level,
            format="%(asctime)s [yf-shield] %(message)s",
            datefmt="%H:%M:%S",
        )
        logger.setLevel(level)
    else:
        logger.setLevel(logging.WARNING)
        if verbose:
            logger.setLevel(logging.INFO)
            if not logger.handlers:
                handler = logging.StreamHandler()
                handler.setFormatter(logging.Formatter("%(asctime)s [yf-shield] %(message)s", "%H:%M:%S"))
                logger.addHandler(handler)

    config = {
        "min_delay": min_delay,
        "max_delay": max_delay,
        "max_rpm": max_rpm,
        "burst_limit": burst_limit,
        "burst_cooldown": burst_cooldown,
        "max_retries": max_retries,
        "proxies": proxies,
        "verbose": verbose,
    }

    session = ShieldSession(config=config)

    # Retry stratejisi (500 serisi için ek koruma)
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "OPTIONS", "POST"],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=10, pool_maxsize=10)
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    # Standart header'lar
    session.headers.update({
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Cache-Control": "max-age=0",
    })

    # yfinance session monkey-patch
    yf.utils.requests = requests
    yf.utils.requests.Session = lambda: session

    try:
        yf.shared._session = session
    except Exception:
        pass

    try:
        yf.shared.set_tz_cache_location = lambda x: None
    except Exception:
        pass

    _active_session = session

    # İlk crumb/cookie al
    #try:
        #session.crumb_manager.get_crumb()
    #except Exception:
        #pass

    if verbose:
        proxy_info = f"{len(proxies)} proxy aktif" if proxies else "Direkt bağlantı"
        print()
        print("  ╔══════════════════════════════════════════════════╗")
        print("  ║         🛡️  yf-shield v{} AKTİF            ║".format(__version__))
        print("  ╠══════════════════════════════════════════════════╣")
        print(f"  ║  Throttle    : {min_delay}-{max_delay}s arası bekleme          ║")
        print(f"  ║  Rate Limit  : {max_rpm} istek/dakika                ║")
        print(f"  ║  Burst       : {burst_limit} ardışık, {burst_cooldown}s soğuma          ║")
        print(f"  ║  Retry       : {max_retries}x exponential backoff          ║")
        print(f"  ║  User-Agent  : {len(USER_AGENTS)} farklı tarayıcı           ║")
        print(f"  ║  Proxy       : {proxy_info:<30} ║")
        print(f"  ║  Crumb       : Otomatik yenileme (30dk)         ║")
        print(f"  ║  Cookie      : Yahoo oturum yönetimi aktif      ║")
        print("  ╚══════════════════════════════════════════════════╝")
        print()

    return session


def get_stats():
    """Aktif oturumun istatistiklerini döner."""
    if _active_session:
        return _active_session.stats
    return {"error": "yf-shield henüz kurulmadı. install() çağırın."}


def get_session():
    """Aktif ShieldSession objesini döner."""
    return _active_session

def yf_pipisini_kur(**kwargs):
    return install(**kwargs)
