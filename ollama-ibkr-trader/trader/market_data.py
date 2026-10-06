"""Dados de eventos externos: datas de resultados e VIX.

Fonte por defeito: ``yfinance`` (opcional). Tudo é *fail-soft*: sem rede ou
sem a biblioteca instalada, devolve ``None`` e o gate de risco decide com
``event_data_fail_closed``. Os resultados são guardados na tabela
``event_cache`` para não martelar a fonte (resultados: 12 h; VIX: 15 min).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from .database import Database

log = logging.getLogger("trader.events")


class EventData:
    def __init__(self, db: Database, finnhub_key: str = "", source: str = "yfinance") -> None:
        self.db = db
        self.finnhub_key = finnhub_key
        self.source = source

    def prefetch(self, symbols: list[str], news_hours: int = 0) -> None:
        """Aquece a cache (resultados, VIX, notícias). Correr num executor, nunca no loop."""
        for symbol in symbols:
            try:
                self.next_earnings_date(symbol)
                if news_hours:
                    self.recent_news(symbol, news_hours)
            except Exception as exc:  # noqa: BLE001
                log.debug("prefetch %s: %s", symbol, exc)
        try:
            self.vix()
        except Exception as exc:  # noqa: BLE001
            log.debug("prefetch vix: %s", exc)

    # ------------------------------------------------------------ earnings
    def next_earnings_date(self, symbol: str, cached_only: bool = False) -> Optional[date]:
        """Próxima data de resultados (ou a mais recente, se já passou há pouco)."""
        cached = self.db.event_cache_get(f"earnings:{symbol}", max_age_hours=12)
        if cached is not None:
            return date.fromisoformat(cached) if cached else None
        if cached_only:
            return None
        value = self._fetch_earnings(symbol)
        self.db.event_cache_put(f"earnings:{symbol}", value.isoformat() if value else "")
        return value

    def _fetch_earnings(self, symbol: str) -> Optional[date]:
        if self.source == "finnhub" and self.finnhub_key:
            return self._fetch_earnings_finnhub(symbol)
        try:
            import yfinance as yf  # type: ignore
        except ImportError:
            log.debug("yfinance não instalado; blackout de resultados indisponível.")
            return None
        try:
            ticker = yf.Ticker(symbol)
            dates: list[date] = []
            try:
                cal = ticker.calendar
                raw = cal.get("Earnings Date") if isinstance(cal, dict) else None
                for item in (raw or []):
                    dates.append(item if isinstance(item, date) else datetime.fromisoformat(str(item)).date())
            except Exception:  # noqa: BLE001
                pass
            if not dates:
                try:
                    frame = ticker.earnings_dates
                    if frame is not None:
                        for idx in list(frame.index)[:8]:
                            dates.append(idx.date() if hasattr(idx, "date") else datetime.fromisoformat(str(idx)).date())
                except Exception:  # noqa: BLE001
                    pass
            today = datetime.now(timezone.utc).date()
            future = sorted(d for d in dates if d >= today - timedelta(days=1))
            return future[0] if future else None
        except Exception as exc:  # noqa: BLE001
            log.warning("Falha a obter resultados de %s via yfinance: %s", symbol, exc)
            return None

    def _fetch_earnings_finnhub(self, symbol: str) -> Optional[date]:
        try:
            import requests

            today = datetime.now(timezone.utc).date()
            resp = requests.get(
                "https://finnhub.io/api/v1/calendar/earnings",
                params={"symbol": symbol, "from": (today - timedelta(days=1)).isoformat(),
                        "to": (today + timedelta(days=60)).isoformat(), "token": self.finnhub_key},
                timeout=10,
            )
            resp.raise_for_status()
            items = resp.json().get("earningsCalendar", [])
            dates = sorted(date.fromisoformat(i["date"]) for i in items if i.get("date"))
            return dates[0] if dates else None
        except Exception as exc:  # noqa: BLE001
            log.warning("Falha a obter resultados de %s via finnhub: %s", symbol, exc)
            return None

    def in_earnings_blackout(self, symbol: str, days_before: int, days_after: int,
                             today: Optional[date] = None, cached_only: bool = True) -> Optional[bool]:
        """True/False, ou None quando não há dados. Por defeito só lê a cache (sem rede no loop)."""
        nxt = self.next_earnings_date(symbol, cached_only=cached_only)
        if nxt is None:
            return None
        today = today or datetime.now(timezone.utc).date()
        return nxt - timedelta(days=days_before) <= today <= nxt + timedelta(days=days_after)

    # ----------------------------------------------------------------- VIX
    def vix(self, cached_only: bool = False) -> Optional[float]:
        cached = self.db.event_cache_get("vix", max_age_hours=0.25)
        if cached is not None:
            return float(cached) if cached else None
        if cached_only:
            return None
        value = self._fetch_vix()
        self.db.event_cache_put("vix", value if value is not None else "")
        return value

    def _fetch_vix(self) -> Optional[float]:
        try:
            import yfinance as yf  # type: ignore

            hist = yf.Ticker("^VIX").history(period="5d", interval="1d")
            if hist is None or hist.empty:
                return None
            return float(hist["Close"].iloc[-1])
        except ImportError:
            return None
        except Exception as exc:  # noqa: BLE001
            log.warning("Falha a obter VIX: %s", exc)
            return None

    # ---------------------------------------------------------------- news
    def recent_news(self, symbol: str, hours: int, cached_only: bool = False) -> list[dict[str, Any]]:
        """Manchetes recentes [{ts, title}] para o módulo de sentimento.

        ``cached_only=True`` (o que o loop do motor usa) nunca faz rede: devolve a cache ainda que
        expirada, ou lista vazia se nunca foi preenchida (N15/F36). A rede corre no ``prefetch``.
        """
        cached = self.db.event_cache_get(f"news:{symbol}", max_age_hours=0.25)
        if cached is not None:
            items = cached
        elif cached_only:
            stale = self.db.event_cache_get(f"news:{symbol}", max_age_hours=1e9)
            items = stale if stale is not None else []
        else:
            items = self._fetch_news(symbol)
            self.db.event_cache_put(f"news:{symbol}", items)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        out = []
        for item in items:
            try:
                ts = datetime.fromisoformat(item["ts"])
            except (KeyError, ValueError):
                continue
            if ts >= cutoff:
                out.append(item)
        return out

    def _fetch_news(self, symbol: str) -> list[dict[str, Any]]:
        if self.source == "finnhub" and self.finnhub_key:
            try:
                import requests

                today = datetime.now(timezone.utc).date()
                resp = requests.get(
                    "https://finnhub.io/api/v1/company-news",
                    params={"symbol": symbol, "from": (today - timedelta(days=2)).isoformat(),
                            "to": today.isoformat(), "token": self.finnhub_key}, timeout=10,
                )
                resp.raise_for_status()
                return [{"ts": datetime.fromtimestamp(i["datetime"], tz=timezone.utc).isoformat(),
                         "title": i.get("headline", "")} for i in resp.json() if i.get("datetime")]
            except Exception as exc:  # noqa: BLE001
                log.warning("Notícias finnhub falharam para %s: %s", symbol, exc)
                return []
        try:
            import yfinance as yf  # type: ignore

            items = yf.Ticker(symbol).news or []
            out = []
            for i in items:
                content = i.get("content", i)
                ts_raw = content.get("pubDate") or i.get("providerPublishTime")
                title = content.get("title") or i.get("title") or ""
                if isinstance(ts_raw, (int, float)):
                    ts = datetime.fromtimestamp(ts_raw, tz=timezone.utc)
                else:
                    ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                out.append({"ts": ts.isoformat(), "title": title})
            return out
        except ImportError:
            return []
        except Exception as exc:  # noqa: BLE001
            log.warning("Notícias yfinance falharam para %s: %s", symbol, exc)
            return []
