"""Interface gráfica (CustomTkinter, modo escuro).

A GUI corre na thread principal (requisito do Tkinter em macOS/Windows) e o
motor de trading corre na sua própria thread com um loop ``asyncio``. A
comunicação é unidirecional em cada sentido:

- motor -> GUI: eventos no ``UIBus`` consumidos com ``after()`` (nunca bloqueia).
- GUI -> motor: ``engine.call(coroutine)`` (``run_coroutine_threadsafe``).
"""

from __future__ import annotations

import tkinter as tk
from datetime import datetime
from tkinter import ttk
from typing import Any

import customtkinter as ctk

from . import __version__
from .config import Settings
from .trading_engine import TradingEngine
from .ui_bus import UIBus, UIEvent

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

COLORS = {
    "bg": "#0f1115",
    "panel": "#171a21",
    "panel_alt": "#1e222b",
    "border": "#2a2f3a",
    "text": "#e6e6e6",
    "muted": "#8b93a7",
    "green": "#2ecc71",
    "red": "#ff5c5c",
    "amber": "#f5b041",
    "blue": "#4ea1ff",
    "purple": "#b388ff",
}
LOG_COLORS = {
    "DEBUG": COLORS["muted"],
    "INFO": COLORS["text"],
    "WARNING": COLORS["amber"],
    "ERROR": COLORS["red"],
    "CRITICAL": COLORS["red"],
}
CATEGORY_COLORS = {"ollama": COLORS["purple"], "ordem": COLORS["blue"]}


class StatusDot(ctk.CTkFrame):
    """Indicador circular + etiqueta (ligado/desligado)."""

    def __init__(self, master: Any, label: str) -> None:
        super().__init__(master, fg_color="transparent")
        self.canvas = tk.Canvas(self, width=12, height=12, highlightthickness=0, bg=COLORS["panel"])
        self.dot = self.canvas.create_oval(2, 2, 10, 10, fill=COLORS["muted"], outline="")
        self.canvas.grid(row=0, column=0, padx=(0, 6))
        self.label = ctk.CTkLabel(self, text=label, text_color=COLORS["muted"], anchor="w")
        self.label.grid(row=0, column=1, sticky="w")

    def set(self, ok: bool | None, text: str) -> None:
        color = COLORS["muted"] if ok is None else (COLORS["green"] if ok else COLORS["red"])
        self.canvas.itemconfigure(self.dot, fill=color)
        self.label.configure(text=text)


class Metric(ctk.CTkFrame):
    """Cartão de métrica (título pequeno + valor grande)."""

    def __init__(self, master: Any, title: str) -> None:
        super().__init__(master, fg_color=COLORS["panel_alt"], corner_radius=10)
        self.title = ctk.CTkLabel(self, text=title, text_color=COLORS["muted"], font=ctk.CTkFont(size=12))
        self.title.pack(anchor="w", padx=12, pady=(10, 0))
        self.value = ctk.CTkLabel(self, text="—", font=ctk.CTkFont(size=22, weight="bold"))
        self.value.pack(anchor="w", padx=12, pady=(0, 10))

    def set(self, text: str, color: str | None = None) -> None:
        self.value.configure(text=text, text_color=color or COLORS["text"])


class TraderApp(ctk.CTk):
    def __init__(self, engine: TradingEngine, bus: UIBus, settings: Settings) -> None:
        super().__init__()
        self.engine = engine
        self.bus = bus
        self.settings = settings
        self._log_lines = 0
        self._models: list[str] = []

        self.title(f"Ollama × IBKR Trader v{__version__} — Paper Trading")
        self.geometry("1280x800")
        self.minsize(1024, 640)
        self.configure(fg_color=COLORS["bg"])
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.grid_columnconfigure(0, weight=0, minsize=280)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=3)
        self.grid_rowconfigure(1, weight=2)

        self._build_control_panel()
        self._build_portfolio_panel()
        self._build_log_console()
        self.after(self.settings.ui_poll_ms, self._poll_bus)

    # ------------------------------------------------------- construção UI
    def _build_control_panel(self) -> None:
        panel = ctk.CTkFrame(self, fg_color=COLORS["panel"], corner_radius=12)
        panel.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(12, 6), pady=12)
        panel.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(panel, text="Painel de Controlo", font=ctk.CTkFont(size=18, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=16, pady=(16, 8))

        self.btn_start = ctk.CTkButton(panel, text="▶  Iniciar ciclo", fg_color=COLORS["green"],
                                       hover_color="#27ae60", text_color="#0b0f0c",
                                       font=ctk.CTkFont(size=14, weight="bold"), height=40,
                                       command=self._start)
        self.btn_start.grid(row=1, column=0, sticky="ew", padx=16, pady=(4, 6))
        self.btn_stop = ctk.CTkButton(panel, text="■  Parar ciclo", fg_color=COLORS["red"],
                                      hover_color="#e04848", text_color="#0b0f0c",
                                      font=ctk.CTkFont(size=14, weight="bold"), height=40,
                                      command=self._stop, state="disabled")
        self.btn_stop.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 14))

        ctk.CTkLabel(panel, text="Modelo Ollama", text_color=COLORS["muted"]).grid(
            row=3, column=0, sticky="w", padx=16)
        model_row = ctk.CTkFrame(panel, fg_color="transparent")
        model_row.grid(row=4, column=0, sticky="ew", padx=16, pady=(2, 10))
        model_row.grid_columnconfigure(0, weight=1)
        self.model_var = tk.StringVar(value=self.settings.ollama_model)
        self.model_menu = ctk.CTkOptionMenu(model_row, variable=self.model_var,
                                            values=[self.settings.ollama_model],
                                            command=self._on_model_change)
        self.model_menu.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(model_row, text="↻", width=36, command=self._refresh_models).grid(
            row=0, column=1, padx=(6, 0))

        ctk.CTkLabel(panel, text="Ativos (separados por vírgula)", text_color=COLORS["muted"]).grid(
            row=5, column=0, sticky="w", padx=16)
        sym_row = ctk.CTkFrame(panel, fg_color="transparent")
        sym_row.grid(row=6, column=0, sticky="ew", padx=16, pady=(2, 14))
        sym_row.grid_columnconfigure(0, weight=1)
        self.symbols_var = tk.StringVar(value=", ".join(self.settings.symbols))
        ctk.CTkEntry(sym_row, textvariable=self.symbols_var).grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(sym_row, text="Aplicar", width=70, command=self._apply_symbols).grid(
            row=0, column=1, padx=(6, 0))

        ctk.CTkButton(panel, text="🧠  Retrospetiva agora", fg_color=COLORS["panel_alt"],
                      hover_color=COLORS["border"], command=self._retro).grid(
            row=7, column=0, sticky="ew", padx=16, pady=(0, 14))

        status = ctk.CTkFrame(panel, fg_color="transparent")
        status.grid(row=8, column=0, sticky="ew", padx=16)
        self.dot_ibkr = StatusDot(status, "IBKR: desligado")
        self.dot_ibkr.pack(anchor="w", pady=2)
        self.dot_ollama = StatusDot(status, "Ollama: a verificar…")
        self.dot_ollama.pack(anchor="w", pady=2)
        self.dot_cycle = StatusDot(status, "Ciclo: parado")
        self.dot_cycle.pack(anchor="w", pady=2)
        self.dot_kill = StatusDot(status, "Kill-switch: inativo")
        self.dot_kill.pack(anchor="w", pady=2)

        self.lbl_meta = ctk.CTkLabel(panel, text="", text_color=COLORS["muted"], justify="left",
                                     font=ctk.CTkFont(size=11), anchor="w")
        self.lbl_meta.grid(row=9, column=0, sticky="ew", padx=16, pady=(12, 0))

        risk = (f"Risco: SL {self.settings.stop_loss_pct:.0%} · TP {self.settings.take_profit_pct:.0%} · "
                f"{self.settings.risk_fraction_per_trade:.0%} NetLiq/entrada\n"
                f"Kill-switch diário: -{self.settings.daily_loss_limit_pct:.0%}\n"
                f"IBKR {self.settings.ib_host}:{self.settings.ib_port} (clientId {self.settings.ib_client_id})")
        ctk.CTkLabel(panel, text=risk, text_color=COLORS["muted"], justify="left",
                     font=ctk.CTkFont(size=11), anchor="w").grid(row=10, column=0, sticky="ew", padx=16, pady=(8, 16))
        panel.grid_rowconfigure(11, weight=1)

    def _build_portfolio_panel(self) -> None:
        panel = ctk.CTkFrame(self, fg_color=COLORS["panel"], corner_radius=12)
        panel.grid(row=0, column=1, sticky="nsew", padx=(6, 12), pady=(12, 6))
        panel.grid_columnconfigure((0, 1, 2, 3), weight=1)
        panel.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(panel, text="Monitor de Portefólio", font=ctk.CTkFont(size=18, weight="bold")).grid(
            row=0, column=0, columnspan=4, sticky="w", padx=16, pady=(16, 8))

        self.m_netliq = Metric(panel, "Net Liquidation")
        self.m_cash = Metric(panel, "Cash")
        self.m_unreal = Metric(panel, "P&L não realizado")
        self.m_day = Metric(panel, "P&L do dia")
        for i, m in enumerate((self.m_netliq, self.m_cash, self.m_unreal, self.m_day)):
            m.grid(row=1, column=i, sticky="ew", padx=(16 if i == 0 else 6, 16 if i == 3 else 6), pady=(0, 10))

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Dark.Treeview", background=COLORS["panel_alt"], fieldbackground=COLORS["panel_alt"],
                        foreground=COLORS["text"], rowheight=26, borderwidth=0, font=("Segoe UI", 10))
        style.configure("Dark.Treeview.Heading", background=COLORS["border"], foreground=COLORS["text"],
                        relief="flat", font=("Segoe UI", 10, "bold"))
        style.map("Dark.Treeview", background=[("selected", COLORS["border"])])

        columns = ("symbol", "qty", "avg", "price", "value", "upnl")
        self.tree = ttk.Treeview(panel, columns=columns, show="headings", style="Dark.Treeview", height=8)
        headers = {"symbol": "Ativo", "qty": "Qtd", "avg": "Preço médio", "price": "Preço",
                   "value": "Valor", "upnl": "P&L não real."}
        for col in columns:
            self.tree.heading(col, text=headers[col])
            self.tree.column(col, anchor="e" if col != "symbol" else "w", width=110, stretch=True)
        self.tree.tag_configure("pos", foreground=COLORS["green"])
        self.tree.tag_configure("neg", foreground=COLORS["red"])
        self.tree.grid(row=2, column=0, columnspan=4, sticky="nsew", padx=16, pady=(0, 8))

        self.lbl_last_decision = ctk.CTkLabel(panel, text="Última decisão: —", text_color=COLORS["muted"],
                                              anchor="w", font=ctk.CTkFont(size=12))
        self.lbl_last_decision.grid(row=3, column=0, columnspan=4, sticky="ew", padx=16, pady=(0, 12))

    def _build_log_console(self) -> None:
        panel = ctk.CTkFrame(self, fg_color=COLORS["panel"], corner_radius=12)
        panel.grid(row=1, column=1, sticky="nsew", padx=(6, 12), pady=(6, 12))
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(1, weight=1)
        header = ctk.CTkFrame(panel, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 4))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text="Consola", font=ctk.CTkFont(size=18, weight="bold")).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(header, text="Limpar", width=70, fg_color=COLORS["panel_alt"],
                      hover_color=COLORS["border"], command=self._clear_log).grid(row=0, column=1)
        self.console = ctk.CTkTextbox(panel, fg_color=COLORS["bg"], text_color=COLORS["text"],
                                      font=ctk.CTkFont(family="Consolas", size=12), wrap="word",
                                      activate_scrollbars=True)
        self.console.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 12))
        for level, color in LOG_COLORS.items():
            self.console.tag_config(level, foreground=color)
        for cat, color in CATEGORY_COLORS.items():
            self.console.tag_config(cat, foreground=color)
        self.console.tag_config("time", foreground=COLORS["muted"])
        self.console.configure(state="disabled")

    # ----------------------------------------------------------- comandos
    def _start(self) -> None:
        self.engine.call(self.engine.start_trading())
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")

    def _stop(self) -> None:
        self.engine.call(self.engine.stop_trading())
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")

    def _retro(self) -> None:
        self.engine.call(self.engine.run_retrospective())

    def _refresh_models(self) -> None:
        self.engine.call(self.engine.refresh_models())

    def _on_model_change(self, model: str) -> None:
        self.engine.call(self.engine.set_model(model))

    def _apply_symbols(self) -> None:
        symbols = [s for s in self.symbols_var.get().replace(";", ",").split(",")]
        self.engine.call(self.engine.set_symbols(symbols))

    def _clear_log(self) -> None:
        self.console.configure(state="normal")
        self.console.delete("1.0", "end")
        self.console.configure(state="disabled")
        self._log_lines = 0

    def _on_close(self) -> None:
        self.append_log("INFO", "A encerrar… (posições e Brackets mantêm-se na IBKR)")
        self.update_idletasks()
        self.engine.shutdown()
        self.destroy()

    # ----------------------------------------------------- consumo de eventos
    def _poll_bus(self) -> None:
        try:
            for event in self.bus.drain():
                self._handle(event)
        finally:
            self.after(self.settings.ui_poll_ms, self._poll_bus)

    def _handle(self, event: UIEvent) -> None:
        p = event.payload
        if event.kind == "log":
            self.append_log(p["level"], p["message"], p.get("category", ""))
        elif event.kind == "portfolio":
            self._update_portfolio(p)
        elif event.kind == "status":
            self._update_status(p)
        elif event.kind == "models":
            self._update_models(p["models"], p["current"])
        elif event.kind == "decision":
            d, s = p["decision"], p["snapshot"]
            self.lbl_last_decision.configure(
                text=f"Última decisão: {p['symbol']} → {d['acao']} (conf {d['confianca']:.2f}) "
                     f"@ {s['price']:.2f} · {d['razao'][:120]}")
        elif event.kind == "retrospective":
            report = p["report"]
            if report.get("skipped"):
                self.append_log("INFO", f"Retrospetiva ignorada: {report.get('reason')}")
            else:
                for lesson in report.get("new_lessons", []):
                    self.append_log("WARNING", f"LIÇÃO: {lesson}", "ollama")

    def append_log(self, level: str, message: str, category: str = "") -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.console.configure(state="normal")
        self.console.insert("end", f"{stamp} ", "time")
        tag = category if category in CATEGORY_COLORS and level == "INFO" else level
        self.console.insert("end", f"{message}\n", tag)
        self._log_lines += 1
        if self._log_lines > self.settings.log_max_lines:
            self.console.delete("1.0", f"{self._log_lines - self.settings.log_max_lines + 1}.0")
            self._log_lines = self.settings.log_max_lines
        self.console.see("end")
        self.console.configure(state="disabled")

    def _update_portfolio(self, p: dict[str, Any]) -> None:
        def money(v: Any) -> str:
            return "—" if v is None else f"{v:,.2f} USD"

        def signed(v: Any, suffix: str = " USD") -> tuple[str, str | None]:
            if v is None:
                return "—", None
            return f"{v:+,.2f}{suffix}", COLORS["green"] if v >= 0 else COLORS["red"]

        self.m_netliq.set(money(p.get("net_liq")))
        self.m_cash.set(money(p.get("cash")))
        self.m_unreal.set(*signed(p.get("unrealized")))
        self.m_day.set(*signed(p.get("day_pnl_pct"), "%"))

        existing = {self.tree.set(i, "symbol"): i for i in self.tree.get_children()}
        seen = set()
        for pos in p.get("positions", []):
            values = (pos["symbol"], f"{pos['qty']:g}", f"{pos['avg_cost']:.2f}", f"{pos['market_price']:.2f}",
                      f"{pos['market_value']:,.2f}", f"{pos['unrealized_pnl']:+,.2f}")
            tag = "pos" if pos["unrealized_pnl"] >= 0 else "neg"
            if pos["symbol"] in existing:
                self.tree.item(existing[pos["symbol"]], values=values, tags=(tag,))
            else:
                self.tree.insert("", "end", values=values, tags=(tag,))
            seen.add(pos["symbol"])
        for symbol, item in existing.items():
            if symbol not in seen:
                self.tree.delete(item)

    def _update_status(self, p: dict[str, Any]) -> None:
        self.dot_ibkr.set(p["ibkr_connected"], "IBKR: ligado (Paper)" if p["ibkr_connected"] else "IBKR: desligado")
        self.dot_ollama.set(p["ollama_ok"], f"Ollama: {p['model']}" if p["ollama_ok"] else "Ollama: indisponível")
        self.dot_cycle.set(p["trading_enabled"] or None, "Ciclo: ativo" if p["trading_enabled"] else "Ciclo: parado")
        self.dot_kill.set(not p["halted"] if p["halted"] else None,
                          "Kill-switch: ATIVADO (sem novas entradas hoje)" if p["halted"] else "Kill-switch: inativo")
        self.lbl_meta.configure(
            text=f"Prompt v{p['prompt_version']} · {p['lessons']} lições ativas\n"
                 f"Limiar de confiança: {p['min_confidence']:.2f}\nAtivos: {', '.join(p['symbols'])}")
        if p["trading_enabled"]:
            self.btn_start.configure(state="disabled")
            self.btn_stop.configure(state="normal")
        else:
            self.btn_start.configure(state="normal")
            self.btn_stop.configure(state="disabled")

    def _update_models(self, models: list[str], current: str) -> None:
        self._models = models
        values = models or [current]
        self.model_menu.configure(values=values)
        if current in values:
            self.model_var.set(current)
