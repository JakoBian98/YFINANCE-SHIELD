# 🛡️ yf-shield

**Stop getting banned by Yahoo Finance.** Drop-in protection layer for `yfinance` that eliminates 429/403/ban errors.

yf-shield wraps every HTTP request made by yfinance with intelligent rate limiting, automatic retry, user-agent rotation, cookie/crumb management, and optional proxy support — all in **one line of code**.

---

## 🇹🇷 Türkçe | 🇬🇧 English

> Scroll down for English documentation.

---

# 🇹🇷 Türkçe Dokümantasyon

## Problem

yfinance kullanırken şu hatalarla karşılaşıyorsunuz:

```
HTTPError: 429 Too Many Requests
HTTPError: 403 Forbidden  
ConnectionError: Max retries exceeded
JSONDecodeError: Expecting value
```

Yahoo Finance IP'nizi tespit edip engelliyor. Özellikle:
- Kısa sürede çok fazla istek atınca (rate limiting)
- Aynı User-Agent ile sürekli istek gelince
- Cookie/crumb token süresi dolunca
- Aynı IP'den tekrarlayan trafik görünce

## Çözüm

```python
from yf_shield import install
install()  # Bu kadar. Tek satır.

# Artık tüm yfinance çağrıları korumalı:
import yfinance as yf
df = yf.download("AAPL", period="1mo")  # ✅ 429 yok, ban yok
ticker = yf.Ticker("TSLA")
info = ticker.info                        # ✅ Otomatik retry
hist = ticker.history(period="1y")        # ✅ Akıllı bekleme
```

## Kurulum

```bash
# Dosyayı projenize kopyalayın
cp yf_shield.py your_project/

# veya doğrudan import edin
```

**Gereksinimler:**
```
yfinance>=0.2.0
requests>=2.28.0
urllib3>=1.26.0
```

## Özellikler

### 1. 🚦 Akıllı Rate Limiting (Token Bucket)

Dakikada maksimum istek sayısını kontrol eder. Burst (ani yoğunluk) algılar ve soğuma süresi uygular.

```python
install(
    max_rpm=20,          # Dakikada max 20 istek
    burst_limit=5,       # Art arda max 5 istek
    burst_cooldown=10,   # Burst sonrası 10s bekle
)
```

### 2. 🔄 Exponential Backoff Retry

429/403/503 hatalarında akıllıca bekleyip tekrar dener:
- 1. deneme: 3s
- 2. deneme: 6s + jitter
- 3. deneme: 12s + jitter
- 4. deneme: 24s + jitter
- 5. deneme: 48s + jitter

### 3. 🕵️ User-Agent Rotasyonu

Her istekte 15+ farklı tarayıcı kimliği arasından rastgele seçim:
- Chrome (Windows/Mac/Linux)
- Firefox
- Safari (Desktop/Mobile)
- Edge, Brave
- Android/iOS mobil tarayıcılar

### 4. 🍪 Cookie & Crumb Yönetimi

Yahoo Finance'ın kimlik doğrulama sistemiyle uyumlu:
- Otomatik cookie toplama
- EU GDPR consent sayfasını otomatik geçme
- Crumb token'ı 30 dakikada bir yenileme
- 403 hatası alınca otomatik crumb/cookie yenileme

### 5. 🌐 Proxy Rotasyonu (Opsiyonel)

IP ban'ından korunmak için proxy desteği:

```python
install(proxies=[
    "http://user:pass@proxy1.example.com:8080",
    "http://user:pass@proxy2.example.com:8080",
    "socks5://proxy3.example.com:1080",
])
```

- Round-robin proxy rotasyonu
- Başarısız proxy'leri otomatik devre dışı bırakma
- 5 dakika soğuma süresi sonrası tekrar aktifleştirme
- Tüm proxy'ler başarısız olursa direkt bağlantıya geçiş

### 6. 📊 İstatistikler

```python
from yf_shield import get_stats

stats = get_stats()
print(stats)
# {
#   'total_requests': 150,
#   'successful': 145,
#   'retried': 8,
#   'failed': 0,
#   'rate_limited': 3,
#   'proxy_rotations': 2,
#   'crumb_refreshes': 1,
#   'uptime_seconds': 3600.0,
#   'current_rpm': 12,
#   'active_proxies': 3,
#   'crumb_valid': True,
# }
```

## Gelişmiş Kullanım

### Flask/Django Uygulaması

```python
# app.py
from yf_shield import install

# Uygulama başlangıcında bir kere çağır
install(
    min_delay=2.0,
    max_delay=4.0,
    max_rpm=15,        # Web app için daha düşük limit
    verbose=True,
)

# Geri kalan kodda hiçbir değişiklik gerekmez
import yfinance as yf
df = yf.download("AAPL", period="1mo")
```

### Yoğun Veri Çekimi (Screener/Scanner)

```python
install(
    min_delay=3.0,     # Daha yavaş ama güvenli
    max_delay=6.0,
    max_rpm=10,        # Dakikada max 10
    burst_limit=3,     # Max 3 ardışık
    max_retries=7,     # Daha fazla deneme
)

# 500 hisseyi tarayabilirsiniz — ban yemeden
symbols = ["AAPL", "MSFT", "GOOGL", ...]
for sym in symbols:
    data = yf.download(sym, period="6mo")
    # yf-shield otomatik olarak bekler, retry yapar
```

### Debug Modu

```python
install(log_level="DEBUG")

# Her istek detaylı loglanır:
# 14:23:01 [yf-shield] ⏳ RPM limiti (25/dk) — 3.2s bekleniyor
# 14:23:04 [yf-shield] 🔑 Yahoo crumb yenileniyor...
# 14:23:05 [yf-shield] 🍪 Cookie yenilendi (4 cookie)
# 14:23:05 [yf-shield] 🔑 Crumb alındı: aHR0cHM...
```

## Nasıl Çalışır?

```
┌─────────────────────────────────────────────────┐
│                  Kullanıcı Kodu                  │
│         yf.download("AAPL", period="1mo")        │
└────────────────────┬────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────┐
│               🛡️ yf-shield                       │
│                                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐      │
│  │ Rate     │  │ Throttle │  │ UA       │      │
│  │ Limiter  │→ │ (delay)  │→ │ Rotasyon │      │
│  └──────────┘  └──────────┘  └──────────┘      │
│       │                            │             │
│       ▼                            ▼             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐      │
│  │ Cookie   │  │ Crumb    │  │ Proxy    │      │
│  │ Manager  │→ │ Manager  │→ │ Rotator  │      │
│  └──────────┘  └──────────┘  └──────────┘      │
│       │                            │             │
│       ▼                            ▼             │
│  ┌──────────────────────────────────────┐       │
│  │     Exponential Backoff Retry        │       │
│  │   429 → bekle → UA değiş → tekrar   │       │
│  │   403 → crumb yenile → tekrar       │       │
│  │   503 → bekle → proxy değiş         │       │
│  └──────────────────────────────────────┘       │
└────────────────────┬────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────┐
│              Yahoo Finance API                   │
└─────────────────────────────────────────────────┘
```

## Karşılaştırma

| Özellik | Düz yfinance | yf-shield |
|---------|:----------:|:---------:|
| 429 Koruması | ❌ | ✅ 5 kademeli retry |
| Rate Limiting | ❌ | ✅ Token bucket |
| User-Agent Rotasyonu | ❌ | ✅ 15+ tarayıcı |
| Cookie Yönetimi | ❌ | ✅ Otomatik |
| Crumb Yenileme | ❌ | ✅ 30dk aralık |
| Proxy Desteği | ❌ | ✅ Round-robin |
| Burst Kontrolü | ❌ | ✅ Akıllı soğuma |
| EU Consent | ❌ | ✅ Otomatik bypass |
| Thread-Safe | ❌ | ✅ Lock tabanlı |

---

# 🇬🇧 English Documentation

## The Problem

When using yfinance, you encounter these errors:

```
HTTPError: 429 Too Many Requests
HTTPError: 403 Forbidden
ConnectionError: Max retries exceeded
```

Yahoo Finance detects and blocks your IP — especially with rapid requests, same User-Agent, or expired cookies.

## The Solution

```python
from yf_shield import install
install()  # That's it. One line.

import yfinance as yf
df = yf.download("AAPL", period="1mo")  # ✅ No more 429s
```

## Features

- **Smart Rate Limiting** — Token bucket algorithm with burst control
- **Exponential Backoff** — Intelligent retry with jitter for 429/403/503
- **User-Agent Rotation** — 15+ browser fingerprints rotated per request
- **Cookie Management** — Automatic Yahoo session handling + EU consent bypass
- **Crumb Auto-Refresh** — Yahoo auth token renewed every 30 minutes
- **Proxy Support** — Optional round-robin proxy rotation with failure detection
- **Thread-Safe** — Lock-based design for concurrent applications
- **Zero Code Changes** — Drop-in monkey-patch, no API changes needed
- **Statistics** — Real-time request metrics via `get_stats()`

## Quick Start

```python
from yf_shield import install

# Basic
install()

# Advanced
install(
    min_delay=2.0,        # Min delay between requests
    max_delay=4.0,        # Max delay between requests
    max_rpm=20,           # Max requests per minute
    burst_limit=5,        # Max consecutive requests
    max_retries=5,        # Retry count for 429/503
    proxies=[             # Optional proxy list
        "http://proxy1:8080",
        "socks5://proxy2:1080",
    ],
    verbose=True,         # Print startup banner
    log_level="INFO",     # Logging level
)
```

## How It Works

yf-shield monkey-patches yfinance's HTTP session with a `ShieldSession` that intercepts every request and applies 6 protection layers before it reaches Yahoo's servers:

1. **Rate Limiter** checks RPM and burst limits
2. **Throttle** enforces minimum delay between requests
3. **User-Agent** is rotated to a random browser fingerprint
4. **Proxy** is selected from the pool (if configured)
5. **Cookie/Crumb** validity is checked and refreshed if needed
6. **Retry Loop** handles 429/403/503 with exponential backoff

## License

MIT License — Use freely in personal and commercial projects.

## Contributing

Pull requests welcome! Areas of interest:
- Additional User-Agent strings
- SOCKS5 proxy testing
- yfinance version compatibility testing
- Performance benchmarks
