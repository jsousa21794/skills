"""Interface gráfica (CustomTkinter, modo escuro).

A GUI corre na thread principal (requisito do Tkinter em macOS/Windows) e o
motor de trading corre na sua própria thread com um loop ``asyncio``:

- motor -> GUI: eventos no ``UIBus`` consumidos com ``after()`` (nunca bloqueia);
- GUI -> motor: ``engine.call(coroutine)`` (``run_coroutine_threadsafe``).

Layout: cabeçalho permanente com o valor da carteira e o modo de conta,
barra lateral de controlo e separadores (Visão geral, Decisões, Risco, Consola,
Configurações). Todas as opções (conta, modelo, ativos, ligação, risco, custos,
moeda de apresentação, janela) vivem no separador Configurações.
"""

from __future__ import annotations

import tkinter as tk
from datetime import datetime
from tkinter import ttk
from typing import Any, Callable, Optional

import customtkinter as ctk

from . import __version__
from .config import Settings
from .trading_engine import TradingEngine
from .ui_bus import UIBus, UIEvent

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

C = {
    "bg": "#0b0d12",
    "panel": "#12151c",
    "card": "#181c25",
    "card_alt": "#1f2430",
    "border": "#262c3a",
    "text": "#e8eaf0",
    "muted": "#8a93a6",
    "accent": "#5b8cff",
    "accent_hover": "#4a76e0",
    "green": "#22c55e",
    "green_dim": "#14532d",
    "red": "#ef4444",
    "red_dim": "#7f1d1d",
    "amber": "#f59e0b",
    "purple": "#a78bfa",
}
LOG_COLORS = {"DEBUG": C["muted"], "INFO": C["text"], "WARNING": C["amber"], "ERROR": C["red"], "CRITICAL": C["red"]}
CATEGORY_COLORS = {"ollama": C["purple"], "ordem": C["accent"]}
FONT = "Segoe UI"


CURRENCY = {"code": "USD", "rate": 1.0, "exact": True}  # moeda de apresentação e fator a partir da moeda base


def money(v: Any, suffix: Optional[str] = None) -> str:
    """Formata um montante da moeda base na moeda de apresentação escolhida em Configurações."""
    suffix = f" {CURRENCY['code']}" if suffix is None else suffix
    if isinstance(v, (int, float)):
        v = v * CURRENCY["rate"]
    return "—" if v is None else f"{v:,.2f}{suffix}".replace(",", " ")


# ---------------------------------------------------------------------------
# Widgets reutilizáveis
# ---------------------------------------------------------------------------
class Chip(ctk.CTkFrame):
    """Indicador compacto: ponto colorido + texto."""

    def __init__(self, master: Any, text: str, bg: str = C["panel"]) -> None:
        super().__init__(master, fg_color=bg, corner_radius=14, height=28)
        self.canvas = tk.Canvas(self, width=10, height=10, highlightthickness=0, bg=bg)
        self.dot = self.canvas.create_oval(1, 1, 9, 9, fill=C["muted"], outline="")
        self.canvas.pack(side="left", padx=(10, 6), pady=7)
        self.label = ctk.CTkLabel(self, text=text, text_color=C["text"], font=ctk.CTkFont(FONT, 12))
        self.label.pack(side="left", padx=(0, 12))

    def set(self, ok: Optional[bool], text: Optional[str] = None) -> None:
        self.canvas.itemconfigure(self.dot, fill=C["muted"] if ok is None else (C["green"] if ok else C["red"]))
        if text is not None:
            self.label.configure(text=text)


class Card(ctk.CTkFrame):
    """Cartão de métrica: título pequeno, valor grande, nota opcional."""

    def __init__(self, master: Any, title: str, size: int = 22) -> None:
        super().__init__(master, fg_color=C["card"], corner_radius=14, border_width=1, border_color=C["border"])
        ctk.CTkLabel(self, text=title, text_color=C["muted"], font=ctk.CTkFont(FONT, 12)).pack(anchor="w", padx=14, pady=(12, 0))
        self.value = ctk.CTkLabel(self, text="—", font=ctk.CTkFont(FONT, size, "bold"))
        self.value.pack(anchor="w", padx=14, pady=(2, 0))
        self.note = ctk.CTkLabel(self, text="", text_color=C["muted"], font=ctk.CTkFont(FONT, 11))
        self.note.pack(anchor="w", padx=14, pady=(0, 12))

    def set(self, text: str, color: Optional[str] = None, note: str = "") -> None:
        self.value.configure(text=text, text_color=color or C["text"])
        self.note.configure(text=note)


class Sparkline(tk.Canvas):
    """Gráfico de linha minimalista para o valor da carteira."""

    def __init__(self, master: Any, height: int = 140) -> None:
        super().__init__(master, height=height, bg=C["card"], highlightthickness=0)
        self.points: list[tuple[str, float]] = []
        self.bind("<Configure>", lambda _e: self.redraw())

    def set_points(self, points: list[tuple[str, float]]) -> None:
        self.points = points[-2000:]
        self.redraw()

    def append(self, ts: str, value: float) -> None:
        if value is None:
            return
        if self.points and self.points[-1][0] == ts:
            return
        self.points.append((ts, value))
        self.points = self.points[-2000:]
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        w, h = max(self.winfo_width(), 10), max(self.winfo_height(), 10)
        if len(self.points) < 2:
            self.create_text(w / 2, h / 2, text="Sem histórico de carteira ainda", fill=C["muted"], font=(FONT, 11))
            return
        values = [v for _, v in self.points]
        lo, hi = min(values), max(values)
        span = (hi - lo) or max(abs(hi) * 0.001, 1.0)
        pad_x, pad_top, pad_bot = 12, 18, 22
        n = len(values)
        coords = []
        for i, v in enumerate(values):
            x = pad_x + (w - 2 * pad_x) * i / (n - 1)
            y = pad_top + (h - pad_top - pad_bot) * (1 - (v - lo) / span)
            coords.append((x, y))
        up = values[-1] >= values[0]
        color = C["green"] if up else C["red"]
        fill = C["green_dim"] if up else C["red_dim"]
        polygon = [coords[0][0], h - pad_bot] + [c for xy in coords for c in xy] + [coords[-1][0], h - pad_bot]
        self.create_polygon(polygon, fill=fill, outline="", stipple="gray25")
        self.create_line([c for xy in coords for c in xy], fill=color, width=2, smooth=True)
        self.create_text(pad_x, h - 8, text=self.points[0][0][11:16] + " UTC", fill=C["muted"], anchor="w", font=(FONT, 9))
        self.create_text(w - pad_x, h - 8, text=self.points[-1][0][11:16] + " UTC", fill=C["muted"], anchor="e", font=(FONT, 9))
        self.create_text(pad_x, 8, text=money(hi), fill=C["muted"], anchor="w", font=(FONT, 9))
        self.create_text(w - pad_x, 8, text=money(lo), fill=C["muted"], anchor="e", font=(FONT, 9))


class ConfirmLiveDialog(ctk.CTkToplevel):
    """Confirmação explícita do modo real: o utilizador escreve REAL."""

    def __init__(self, master: Any, port: int, on_confirm: Callable[[], None], on_cancel: Callable[[], None]) -> None:
        super().__init__(master)
        self.title("Confirmar conta real")
        self.geometry("520x300")
        self.configure(fg_color=C["panel"])
        self.resizable(False, False)
        self.grab_set()
        self._on_confirm, self._on_cancel = on_confirm, on_cancel
        ctk.CTkLabel(self, text="Ativar trading com DINHEIRO REAL", font=ctk.CTkFont(FONT, 18, "bold"),
                     text_color=C["red"]).pack(pady=(22, 6))
        ctk.CTkLabel(self, text=(f"O bot vai ligar-se à porta {port} (TWS/Gateway em conta real) e colocar ordens\n"
                                 "reais de forma autónoma. A camada de risco (kill-switch diário, StoplossGuard,\n"
                                 "sizing por ATR, brackets) mantém-se ativa, mas o capital é real.\n"
                                 "Esta confirmação é pedida uma única vez e fica guardada.\n\n"
                                 "Escreve REAL para confirmar:"),
                     justify="center", text_color=C["text"], font=ctk.CTkFont(FONT, 12)).pack(pady=(0, 10))
        self.entry = ctk.CTkEntry(self, width=200, justify="center", font=ctk.CTkFont(FONT, 14, "bold"))
        self.entry.pack()
        self.entry.focus_set()
        self.entry.bind("<Return>", lambda _e: self._confirm())
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(pady=18)
        ctk.CTkButton(row, text="Cancelar", fg_color=C["card_alt"], hover_color=C["border"], width=120,
                      command=self._cancel).pack(side="left", padx=8)
        ctk.CTkButton(row, text="Ativar conta real", fg_color=C["red"], hover_color="#dc2626", width=160,
                      text_color="#fff", command=self._confirm).pack(side="left", padx=8)
        self.protocol("WM_DELETE_WINDOW", self._cancel)

    def _confirm(self) -> None:
        if self.entry.get().strip().upper() == "REAL":
            self.destroy()
            self._on_confirm()
        else:
            self.entry.configure(border_color=C["red"])

    def _cancel(self) -> None:
        self.destroy()
        self._on_cancel()


# ---------------------------------------------------------------------------
# Aplicação
# ---------------------------------------------------------------------------
class TraderApp(ctk.CTk):
    def __init__(self, engine: TradingEngine, bus: UIBus, settings: Settings) -> None:
        super().__init__()
        self.engine = engine
        self.bus = bus
        self.settings = settings
        self._log_lines = 0
        self._last_value: Optional[float] = None
        self._last_value_ts: Optional[str] = None
        self._connected = False
        self._decision_rows: dict[int, str] = {}

        self.title(f"Ollama × IBKR Trader v{__version__}")
        self.geometry("1360x860")
        self.minsize(1100, 700)
        self.configure(fg_color=C["bg"])
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        if settings.ui_always_on_top:
            self.attributes("-topmost", True)

        self.grid_columnconfigure(0, weight=0, minsize=270)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=1)

        self._style_treeview()
        self._build_header()
        self._build_sidebar()
        self._build_tabs()
        self.after(self.settings.ui_poll_ms, self._poll_bus)

    # ------------------------------------------------------------- estilos
    def _style_treeview(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Dark.Treeview", background=C["card"], fieldbackground=C["card"], foreground=C["text"],
                        rowheight=28, borderwidth=0, font=(FONT, 10))
        style.configure("Dark.Treeview.Heading", background=C["card_alt"], foreground=C["muted"], relief="flat",
                        font=(FONT, 10, "bold"), padding=6)
        style.map("Dark.Treeview", background=[("selected", C["border"])])
        style.layout("Dark.Treeview", [("Dark.Treeview.treearea", {"sticky": "nswe"})])

    def _tree(self, master: Any, columns: dict[str, tuple[str, int, str]], height: int = 8) -> ttk.Treeview:
        tree = ttk.Treeview(master, columns=list(columns), show="headings", style="Dark.Treeview", height=height)
        for key, (label, width, anchor) in columns.items():
            tree.heading(key, text=label)
            tree.column(key, width=width, anchor=anchor, stretch=True)
        tree.tag_configure("pos", foreground=C["green"])
        tree.tag_configure("neg", foreground=C["red"])
        tree.tag_configure("muted", foreground=C["muted"])
        tree.tag_configure("buy", foreground=C["green"])
        tree.tag_configure("sell", foreground=C["red"])
        tree.tag_configure("review", foreground=C["amber"])
        return tree

    # ------------------------------------------------------------ cabeçalho
    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, fg_color=C["panel"], corner_radius=0, height=96)
        header.grid(row=0, column=0, columnspan=2, sticky="ew")
        header.grid_columnconfigure(1, weight=1)

        brand = ctk.CTkFrame(header, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="w", padx=20, pady=16)
        ctk.CTkLabel(brand, text="◆  Ollama × IBKR", font=ctk.CTkFont(FONT, 18, "bold")).pack(anchor="w")
        live0 = self.settings.is_live
        self.mode_badge = ctk.CTkLabel(brand, text="CONTA REAL" if live0 else "PAPER",
                                       fg_color=C["red_dim"] if live0 else C["green_dim"],
                                       text_color=C["red"] if live0 else C["green"],
                                       corner_radius=8, font=ctk.CTkFont(FONT, 11, "bold"), width=90, height=24)
        self.mode_badge.pack(anchor="w", pady=(6, 0))

        value_box = ctk.CTkFrame(header, fg_color="transparent")
        value_box.grid(row=0, column=1, sticky="w", padx=10)
        ctk.CTkLabel(value_box, text="VALOR DA CARTEIRA", text_color=C["muted"],
                     font=ctk.CTkFont(FONT, 11, "bold")).pack(anchor="w")
        row = ctk.CTkFrame(value_box, fg_color="transparent")
        row.pack(anchor="w")
        self.lbl_value = ctk.CTkLabel(row, text="—", font=ctk.CTkFont(FONT, 32, "bold"))
        self.lbl_value.pack(side="left")
        self.lbl_day = ctk.CTkLabel(row, text="", font=ctk.CTkFont(FONT, 16, "bold"), text_color=C["muted"])
        self.lbl_day.pack(side="left", padx=(14, 0), pady=(8, 0))
        self.lbl_value_note = ctk.CTkLabel(value_box, text="sem dados", text_color=C["muted"], font=ctk.CTkFont(FONT, 11))
        self.lbl_value_note.pack(anchor="w")

        chips = ctk.CTkFrame(header, fg_color="transparent")
        chips.grid(row=0, column=2, sticky="e", padx=20)
        self.chip_ibkr = Chip(chips, "IBKR: desligado")
        self.chip_ibkr.pack(side="left", padx=4)
        self.chip_ollama = Chip(chips, "Ollama: a verificar…")
        self.chip_ollama.pack(side="left", padx=4)
        self.chip_cycle = Chip(chips, "Ciclo: parado")
        self.chip_cycle.pack(side="left", padx=4)
        self.chip_data = Chip(chips, "Dados: —")
        self.chip_data.pack(side="left", padx=4)

    # -------------------------------------------------------------- sidebar
    def _build_sidebar(self) -> None:
        side = ctk.CTkFrame(self, fg_color=C["panel"], corner_radius=0)
        side.grid(row=1, column=0, sticky="nsew")
        side.grid_columnconfigure(0, weight=1)
        pad = {"padx": 18}

        ctk.CTkLabel(side, text="CONTROLO", text_color=C["muted"], font=ctk.CTkFont(FONT, 11, "bold")).grid(
            row=0, column=0, sticky="w", pady=(18, 6), **pad)
        self.btn_start = ctk.CTkButton(side, text="▶   Iniciar trading", fg_color=C["green"], hover_color="#16a34a",
                                       text_color="#06130a", font=ctk.CTkFont(FONT, 14, "bold"), height=44,
                                       corner_radius=12, command=self._start)
        self.btn_start.grid(row=1, column=0, sticky="ew", pady=(0, 8), **pad)
        self.btn_stop = ctk.CTkButton(side, text="■   Parar", fg_color=C["card_alt"], hover_color=C["border"],
                                      font=ctk.CTkFont(FONT, 14, "bold"), height=40, corner_radius=12,
                                      command=self._stop, state="disabled")
        self.btn_stop.grid(row=2, column=0, sticky="ew", pady=(0, 16), **pad)

        ctk.CTkLabel(side, text="LIGAÇÃO", text_color=C["muted"], font=ctk.CTkFont(FONT, 11, "bold")).grid(
            row=3, column=0, sticky="w", pady=(0, 6), **pad)
        self.lbl_mode_note = ctk.CTkLabel(side, text=f"{'REAL' if self.settings.is_live else 'PAPER'} · porta {self.settings.ib_port}",
                                          text_color=C["red"] if self.settings.is_live else C["muted"],
                                          font=ctk.CTkFont(FONT, 11), justify="left", anchor="w", wraplength=230)
        self.lbl_mode_note.grid(row=4, column=0, sticky="ew", pady=(0, 16), **pad)

        ctk.CTkLabel(side, text="ANÁLISE", text_color=C["muted"], font=ctk.CTkFont(FONT, 11, "bold")).grid(
            row=5, column=0, sticky="w", pady=(0, 6), **pad)
        ctk.CTkButton(side, text="🧠   Retrospetiva agora", fg_color=C["card_alt"], hover_color=C["border"],
                      anchor="w", height=38, corner_radius=10, command=self._retro).grid(row=6, column=0, sticky="ew", pady=(0, 6), **pad)
        ctk.CTkButton(side, text="📊   Relatório estatístico", fg_color=C["card_alt"], hover_color=C["border"],
                      anchor="w", height=38, corner_radius=10, command=self._report).grid(row=7, column=0, sticky="ew", pady=(0, 6), **pad)
        ctk.CTkButton(side, text="⚙   Configurações", fg_color=C["card_alt"], hover_color=C["border"],
                      anchor="w", height=38, corner_radius=10,
                      command=lambda: self.tabs.set("Configurações")).grid(row=8, column=0, sticky="ew", pady=(0, 6), **pad)
        self.btn_resume = ctk.CTkButton(side, text="▶   Retomar novas entradas", fg_color=C["card_alt"], hover_color=C["border"],
                                        anchor="w", height=38, corner_radius=10, state="disabled",
                                        command=lambda: self.engine.call(self.engine.resume_entries(actor="gui")))
        self.btn_resume.grid(row=9, column=0, sticky="ew", pady=(0, 16), **pad)

        side.grid_rowconfigure(10, weight=1)
        self.lbl_meta = ctk.CTkLabel(side, text="", text_color=C["muted"], justify="left", anchor="w",
                                     font=ctk.CTkFont(FONT, 11), wraplength=230)
        self.lbl_meta.grid(row=11, column=0, sticky="ew", pady=(0, 16), **pad)

    # ----------------------------------------------------------------- tabs
    def _build_tabs(self) -> None:
        self.tabs = ctk.CTkTabview(self, fg_color=C["bg"], segmented_button_fg_color=C["panel"],
                                   segmented_button_selected_color=C["accent"],
                                   segmented_button_selected_hover_color=C["accent_hover"],
                                   segmented_button_unselected_color=C["panel"],
                                   segmented_button_unselected_hover_color=C["border"], corner_radius=12)
        self.tabs.grid(row=1, column=1, sticky="nsew", padx=(10, 14), pady=(10, 14))
        for name in ("Visão geral", "Decisões", "Risco", "Consola", "Configurações"):
            self.tabs.add(name)
        self._build_overview(self.tabs.tab("Visão geral"))
        self._build_decisions(self.tabs.tab("Decisões"))
        self._build_risk(self.tabs.tab("Risco"))
        self._build_console(self.tabs.tab("Consola"))
        self._build_settings(self.tabs.tab("Configurações"))

    def _build_overview(self, tab: Any) -> None:
        tab.grid_columnconfigure((0, 1, 2, 3), weight=1)
        tab.grid_rowconfigure(2, weight=1)
        self.c_cash = Card(tab, "Cash disponível")
        self.c_unreal = Card(tab, "P&L não realizado")
        self.c_real = Card(tab, "P&L realizado (sessão)")
        self.c_pos = Card(tab, "Posições abertas")
        for i, card in enumerate((self.c_cash, self.c_unreal, self.c_real, self.c_pos)):
            card.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 6, 0 if i == 3 else 6), pady=(4, 10))
        chart = ctk.CTkFrame(tab, fg_color=C["card"], corner_radius=14, border_width=1, border_color=C["border"])
        chart.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(0, 10))
        ctk.CTkLabel(chart, text="Valor da carteira (últimas 48 h)", text_color=C["muted"],
                     font=ctk.CTkFont(FONT, 12)).pack(anchor="w", padx=14, pady=(10, 0))
        self.spark = Sparkline(chart, height=150)
        self.spark.pack(fill="x", padx=10, pady=(4, 10))
        table = ctk.CTkFrame(tab, fg_color=C["card"], corner_radius=14, border_width=1, border_color=C["border"])
        table.grid(row=2, column=0, columnspan=4, sticky="nsew")
        table.grid_columnconfigure(0, weight=1)
        table.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(table, text="Posições", text_color=C["muted"], font=ctk.CTkFont(FONT, 12)).grid(
            row=0, column=0, sticky="w", padx=14, pady=(10, 4))
        self.tree = self._tree(table, {
            "symbol": ("Ativo", 90, "w"), "qty": ("Qtd", 70, "e"), "avg": ("Preço médio", 110, "e"),
            "price": ("Preço", 100, "e"), "value": ("Valor", 120, "e"), "upnl": ("P&L não real.", 120, "e"),
            "stop": ("Stop", 90, "e"), "tp": ("Take profit", 100, "e")}, height=7)
        self.tree.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        self.lbl_last_decision = ctk.CTkLabel(table, text="Última proposta do LLM: —", text_color=C["muted"],
                                              anchor="w", font=ctk.CTkFont(FONT, 12))
        self.lbl_last_decision.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 10))

    def _build_decisions(self, tab: Any) -> None:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(tab, text="Propostas do LLM e decisão da camada de risco (mais recente primeiro)",
                     text_color=C["muted"], font=ctk.CTkFont(FONT, 12)).grid(row=0, column=0, sticky="w", pady=(4, 6))
        box = ctk.CTkFrame(tab, fg_color=C["card"], corner_radius=14, border_width=1, border_color=C["border"])
        box.grid(row=1, column=0, sticky="nsew")
        box.grid_columnconfigure(0, weight=1)
        box.grid_rowconfigure(0, weight=1)
        self.dtree = self._tree(box, {
            "time": ("Hora", 70, "w"), "symbol": ("Ativo", 70, "w"), "model": ("Modelo", 130, "w"),
            "action": ("Ação", 70, "w"), "verbal": ("Verbal", 80, "e"), "agree": ("Acordo", 80, "e"),
            "p": ("p calibrada", 110, "e"), "price": ("Preço", 90, "e"), "result": ("Resultado", 340, "w")}, height=20)
        self.dtree.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)

    def _build_risk(self, tab: Any) -> None:
        tab.grid_columnconfigure((0, 1), weight=1)
        tab.grid_rowconfigure((0, 1), weight=1)

        def section(title: str, r: int, c: int) -> ctk.CTkLabel:
            frame = ctk.CTkFrame(tab, fg_color=C["card"], corner_radius=14, border_width=1, border_color=C["border"])
            frame.grid(row=r, column=c, sticky="nsew", padx=(0 if c == 0 else 6, 0 if c == 1 else 6), pady=(4, 6))
            ctk.CTkLabel(frame, text=title, text_color=C["muted"], font=ctk.CTkFont(FONT, 12, "bold")).pack(anchor="w", padx=14, pady=(10, 2))
            body = ctk.CTkLabel(frame, text="—", justify="left", anchor="nw", font=ctk.CTkFont(FONT, 12), wraplength=480)
            body.pack(anchor="nw", fill="both", expand=True, padx=14, pady=(0, 12))
            return body

        self.risk_protections = section("Proteções e kill-switch", 0, 0)
        self.risk_gates = section("Gates estatísticos (controlam o risco por trade)", 0, 1)
        self.risk_calib = section("Calibração e parâmetros", 1, 0)
        self.risk_lessons = section("Lições ativas (calculadas em código)", 1, 1)

    def _build_console(self, tab: Any) -> None:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(tab, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", pady=(4, 6))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="Atividade do sistema, decisões do Ollama e erros", text_color=C["muted"],
                     font=ctk.CTkFont(FONT, 12)).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(head, text="Limpar", width=80, fg_color=C["card_alt"], hover_color=C["border"],
                      command=self._clear_log).grid(row=0, column=1)
        self.console = ctk.CTkTextbox(tab, fg_color=C["card"], text_color=C["text"], border_width=1,
                                      border_color=C["border"], corner_radius=14,
                                      font=ctk.CTkFont("Consolas", 12), wrap="word", activate_scrollbars=True)
        self.console.grid(row=1, column=0, sticky="nsew")
        for level, color in LOG_COLORS.items():
            self.console.tag_config(level, foreground=color)
        for cat, color in CATEGORY_COLORS.items():
            self.console.tag_config(cat, foreground=color)
        self.console.tag_config("time", foreground=C["muted"])
        self.console.configure(state="disabled")

    # --------------------------------------------------------- configurações
    CURRENCIES = ["Conta (auto)", "EUR", "USD", "GBP", "CHF", "CAD", "JPY", "AUD"]
    MARKET_DATA = {"Tempo real (subscrição)": 1, "Atrasados 15 min (sem subscrição)": 3}
    # (campo, rótulo, tipo) — tipo: str | int | float | pct (apresentado em %, guardado em fração) | bool
    SETTINGS_FORM: dict[str, list[tuple[str, str, str]]] = {
        "Ligação à IBKR": [
            ("ib_host", "Host da TWS / Gateway", "str"), ("ib_port_live", "Porta conta REAL", "int"),
            ("ib_port_paper", "Porta conta Paper", "int"), ("ib_client_id", "Client ID da API", "int"),
            ("ib_account", "Conta (vazio = primeira conta gerida)", "str"),
            ("manage_external_positions", "Adotar posições não abertas pelo bot (proteger e gerir)", "bool"),
            ("cancel_external_exits_on_close", "Ao fechar por sinal, cancelar stops/limits MANUAIS (senão o fecho é bloqueado)", "bool"),
        ],
        "Ollama": [
            ("ollama_url", "URL do Ollama", "str"), ("llm_interval_minutes", "Consultar o modelo a cada (min)", "int"),
            ("llm_samples", "Amostras por decisão", "int"), ("llm_min_agreement", "Acordo mínimo entre amostras (%)", "pct"),
            ("llm_two_stage", "Duas etapas (raciocínio livre → JSON)", "bool"),
            ("anonymize_prompt", "Ocultar ticker e níveis de preço ao modelo", "bool"),
        ],
        "Trading e risco": [
            ("max_open_positions", "Máximo de posições abertas", "int"), ("risk_per_trade_pct", "Risco por operação (% do equity)", "pct"),
            ("daily_loss_limit_pct", "Kill-switch: perda diária máxima (%)", "pct"),
            ("relax_limits_when_in_profit", "Entradas ilimitadas enquanto o dia está em lucro", "bool"),
            ("allow_short", "Permitir vendas a descoberto (short)", "bool"),
            ("max_entry_slippage_pct", "Slippage máximo na entrada (%; 0 = limit ao preço de referência)", "pct"),
            ("atr_stop_multiple", "Stop = k × ATR (k)", "float"), ("reward_risk_ratio", "Take-profit = R × distância do stop (R)", "float"),
            ("signal_persistence_cycles", "Sinal tem de repetir-se N consultas seguidas", "int"),
        ],
        "Integração ChatGPT (MCP)": [
            ("mcp_enabled", "Ativar servidor MCP local (aplica-se ao reiniciar o programa)", "bool"),
            ("mcp_host", "Endereço de escuta (127.0.0.1 recomendado)", "str"),
            ("mcp_port", "Porta", "int"),
            ("mcp_public_url", "URL pública do túnel HTTPS (cloudflared/ngrok), só para compor o endereço", "str"),
        ],
        "Custos": [
            ("commission_per_share", "Comissão por ação (USD)", "float"), ("commission_min", "Comissão mínima por ordem (USD)", "float"),
            ("max_cost_fraction_of_tp", "Custo ida+volta máximo (% do ganho no TP)", "pct"),
            ("min_net_gain_multiple", "Ganho líquido no TP ≥ N × custo", "float"),
        ],
    }

    def _build_settings(self, tab: Any) -> None:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(tab, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", pady=(4, 6))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="Todas as opções do programa. As alterações aplicam-se ao gravar; conta e modelo aplicam-se de imediato.",
                     text_color=C["muted"], font=ctk.CTkFont(FONT, 12), anchor="w").grid(row=0, column=0, sticky="w")
        ctk.CTkButton(head, text="💾  Guardar e aplicar", fg_color=C["accent"], hover_color=C["accent_hover"], height=36,
                      font=ctk.CTkFont(FONT, 13, "bold"), command=self._save_settings).grid(row=0, column=1, padx=(8, 0))
        self.lbl_settings_status = ctk.CTkLabel(head, text="", text_color=C["muted"], font=ctk.CTkFont(FONT, 11), anchor="w",
                                                wraplength=900, justify="left")
        self.lbl_settings_status.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

        body = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_columnconfigure((0, 1), weight=1, uniform="cols")
        self._setting_vars: dict[str, tuple[tk.Variable, str]] = {}

        def section(title: str, r: int, c: int, colspan: int = 1) -> ctk.CTkFrame:
            frame = ctk.CTkFrame(body, fg_color=C["card"], corner_radius=14, border_width=1, border_color=C["border"])
            frame.grid(row=r, column=c, columnspan=colspan, sticky="nsew", padx=(0 if c == 0 else 6, 0 if c + colspan == 2 else 6), pady=(0, 10))
            frame.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(frame, text=title.upper(), text_color=C["muted"], font=ctk.CTkFont(FONT, 11, "bold")).grid(
                row=0, column=0, columnspan=2, sticky="w", padx=14, pady=(10, 4))
            return frame

        def row(frame: ctk.CTkFrame, r: int, label: str, widget: Any) -> None:
            ctk.CTkLabel(frame, text=label, font=ctk.CTkFont(FONT, 12), anchor="w", wraplength=330, justify="left").grid(
                row=r, column=0, sticky="w", padx=14, pady=4)
            widget.grid(row=r, column=1, sticky="e", padx=14, pady=4)

        def field(frame: ctk.CTkFrame, r: int, name: str, label: str, kind: str) -> None:
            value = getattr(self.settings, name)
            if kind == "bool":
                var: tk.Variable = tk.BooleanVar(value=bool(value))
                widget: Any = ctk.CTkSwitch(frame, text="", variable=var, progress_color=C["accent"], width=48)
            else:
                text = f"{value * 100:g}" if kind == "pct" else (", ".join(value) if isinstance(value, list) else str(value))
                var = tk.StringVar(value=text)
                widget = ctk.CTkEntry(frame, textvariable=var, width=170, fg_color=C["card_alt"], border_color=C["border"], justify="right")
            self._setting_vars[name] = (var, kind)
            row(frame, r, label, widget)

        # --- Conta e modelo (aplicação imediata) ---
        acc = section("Conta e modelo", 0, 0, colspan=2)
        acc.grid_columnconfigure(1, weight=0)
        self.mode_var = tk.StringVar(value="Real" if self.settings.is_live else "Paper")
        self.mode_seg = ctk.CTkSegmentedButton(acc, values=["Paper", "Real"], variable=self.mode_var,
                                               command=self._on_mode_change, selected_color=C["accent"],
                                               selected_hover_color=C["accent_hover"], fg_color=C["card_alt"],
                                               unselected_color=C["card_alt"], unselected_hover_color=C["border"],
                                               font=ctk.CTkFont(FONT, 13, "bold"), height=34, width=220)
        row(acc, 1, f"Conta (Real = porta {self.settings.ib_port_live}, Paper = porta {self.settings.ib_port_paper}); a mudança é imediata e pede confirmação", self.mode_seg)
        model_row = ctk.CTkFrame(acc, fg_color="transparent")
        self.model_var = tk.StringVar(value=self.settings.ollama_model)
        self.model_menu = ctk.CTkOptionMenu(model_row, variable=self.model_var, values=[self.settings.ollama_model],
                                            command=self._on_model_change, fg_color=C["card_alt"], width=220,
                                            button_color=C["accent"], button_hover_color=C["accent_hover"], height=34)
        self.model_menu.grid(row=0, column=0)
        ctk.CTkButton(model_row, text="↻", width=34, height=34, fg_color=C["card_alt"], hover_color=C["border"],
                      command=self._refresh_models).grid(row=0, column=1, padx=(6, 0))
        row(acc, 2, "Modelo Ollama (lista de /api/tags; a mudança é imediata e reinicia a calibração desse modelo)", model_row)
        sym_row = ctk.CTkFrame(acc, fg_color="transparent")
        self.symbols_var = tk.StringVar(value=", ".join(self.settings.symbols))
        ctk.CTkEntry(sym_row, textvariable=self.symbols_var, width=220, fg_color=C["card_alt"], border_color=C["border"],
                     height=34).grid(row=0, column=0)
        ctk.CTkButton(sym_row, text="OK", width=40, height=34, fg_color=C["accent"], hover_color=C["accent_hover"],
                      command=self._apply_symbols).grid(row=0, column=1, padx=(6, 0))
        row(acc, 3, "Ativos negociados (separados por vírgula)", sym_row)

        # --- Secções do formulário ---
        r, c = 1, 0
        for title, items in self.SETTINGS_FORM.items():
            frame = section(title, r, c)
            for i, (name, label, kind) in enumerate(items, start=1):
                field(frame, i, name, label, kind)
            c += 1
            if c == 2:
                c, r = 0, r + 1

        # --- Apresentação ---
        pres = section("Apresentação", r, c)
        cur = self.settings.display_currency
        self.currency_var = tk.StringVar(value="Conta (auto)" if cur in ("auto", "") else cur)
        row(pres, 1, "Moeda em que os valores são mostrados (taxa de câmbio da IBKR)",
            ctk.CTkOptionMenu(pres, variable=self.currency_var, values=self.CURRENCIES, fg_color=C["card_alt"], width=170,
                              button_color=C["accent"], button_hover_color=C["accent_hover"], height=32))
        mdt = {v: k for k, v in self.MARKET_DATA.items()}
        self.market_data_var = tk.StringVar(value=mdt.get(self.settings.market_data_type, next(iter(self.MARKET_DATA))))
        row(pres, 2, "Dados de mercado", ctk.CTkOptionMenu(pres, variable=self.market_data_var, values=list(self.MARKET_DATA),
                                                           fg_color=C["card_alt"], width=170, button_color=C["accent"],
                                                           button_hover_color=C["accent_hover"], height=32))
        self.top_var = tk.BooleanVar(value=self.settings.ui_always_on_top)
        row(pres, 3, "Janela sempre visível (always-on-top)",
            ctk.CTkSwitch(pres, text="", variable=self.top_var, command=self._toggle_top, progress_color=C["accent"], width=48))
        ctk.CTkLabel(pres, text=f"Ficheiro: {self.settings.config_path()}", text_color=C["muted"], font=ctk.CTkFont(FONT, 10),
                     anchor="w", wraplength=330, justify="left").grid(row=4, column=0, columnspan=2, sticky="w", padx=14, pady=(2, 10))

        # --- Endereço do conector MCP (token no caminho) ---
        mcp_box = section("Ligação do ChatGPT", r + 1, 0, colspan=2)
        token = self.settings.mcp_token or "(gerado ao ativar)"
        base = (self.settings.mcp_public_url or f"http://{self.settings.mcp_host}:{self.settings.mcp_port}").rstrip("/")
        self.lbl_mcp_url = ctk.CTkLabel(mcp_box, text=f"Conector: {base}/t/{token}/mcp", font=ctk.CTkFont("Consolas", 11),
                                        anchor="w", wraplength=900, justify="left")
        self.lbl_mcp_url.grid(row=1, column=0, columnspan=2, sticky="w", padx=14, pady=(2, 2))
        ctk.CTkLabel(mcp_box, text="No ChatGPT: Definições → Conectores → Criar; URL do servidor MCP = endereço acima; autenticação: nenhuma "
                                   "(o token vai no caminho). O servidor só expõe consultas, diagnósticos e a pausa de novas entradas. "
                                   "Exige um túnel HTTPS para o ChatGPT chegar ao teu PC.",
                     text_color=C["muted"], font=ctk.CTkFont(FONT, 11), anchor="w", wraplength=900, justify="left").grid(
            row=2, column=0, columnspan=2, sticky="w", padx=14, pady=(0, 10))

    def _save_settings(self) -> None:
        """Lê o formulário, valida com ``Settings.apply`` (coerção + limites), grava e aplica no motor."""
        data: dict[str, Any] = {}
        for name, (var, kind) in self._setting_vars.items():
            raw = var.get()
            if kind == "bool":
                data[name] = bool(raw)
            elif kind == "pct":
                try:
                    data[name] = float(str(raw).strip().replace(",", ".").rstrip("%")) / 100.0
                except ValueError:
                    data[name] = raw  # apply() reporta o valor inválido
            else:
                data[name] = raw
        cur = self.currency_var.get()
        data["display_currency"] = "auto" if cur.startswith("Conta") else cur
        data["market_data_type"] = self.MARKET_DATA.get(self.market_data_var.get(), 3)
        before = {k: getattr(self.settings, k) for k in ("ib_host", "ib_port_live", "ib_port_paper", "ib_client_id",
                                                           "ib_account", "market_data_type", "symbols", "ollama_url")}
        warnings = self.settings.apply(data)
        self.settings.save()
        self.settings.load_warnings.clear()
        # Repor o formulário com os valores efetivamente aceites.
        for name, (var, kind) in self._setting_vars.items():
            value = getattr(self.settings, name)
            var.set(bool(value) if kind == "bool" else (f"{value * 100:g}" if kind == "pct" else str(value)))
        connection_changed = any(getattr(self.settings, k) != before[k]
                                 for k in ("ib_host", "ib_port_live", "ib_port_paper", "ib_client_id", "ib_account", "market_data_type"))
        self.engine.call(self.engine.settings_changed())
        if connection_changed:
            self.engine.call(self.engine.reconnect())
        status = "Configurações guardadas e aplicadas." + (" Ligação à IBKR reiniciada." if connection_changed else "")
        if warnings:
            status += "  Rejeitado: " + "; ".join(warnings)
        self.lbl_settings_status.configure(text=status, text_color=C["amber"] if warnings else C["green"])
        self.append_log("WARNING" if warnings else "INFO", status)

    # ------------------------------------------------------------- comandos
    def _start(self) -> None:
        if self.settings.is_live and not self.settings.live_confirmed:
            ConfirmLiveDialog(self, self.settings.ib_port_live, on_confirm=self._confirm_live_and_start,
                              on_cancel=lambda: None)
            return
        self.engine.call(self.engine.start_trading())
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")

    def _confirm_live_and_start(self) -> None:
        self.settings.live_confirmed = True
        self.settings.save()
        self.engine.call(self.engine.set_mode("live", confirmed=True))
        self.engine.call(self.engine.start_trading())
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")

    def _stop(self) -> None:
        self.engine.call(self.engine.stop_trading())
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")

    def _retro(self) -> None:
        self.engine.call(self.engine.run_retrospective())

    def _report(self) -> None:
        self.engine.call(self.engine.run_statistical_report())
        self.tabs.set("Consola")

    def _refresh_models(self) -> None:
        self.engine.call(self.engine.refresh_models())

    def _on_model_change(self, model: str) -> None:
        self.engine.call(self.engine.set_model(model))

    def _apply_symbols(self) -> None:
        self.engine.call(self.engine.set_symbols(self.symbols_var.get().replace(";", ",").split(",")))

    def _toggle_top(self) -> None:
        self.settings.ui_always_on_top = bool(self.top_var.get())
        self.settings.save()
        self.attributes("-topmost", self.settings.ui_always_on_top)

    def _on_mode_change(self, value: str) -> None:
        if value == "Real":
            if self.settings.is_live and self.settings.live_confirmed:
                return
            ConfirmLiveDialog(self, self.settings.ib_port_live,
                              on_confirm=lambda: self.engine.call(self.engine.set_mode("live", confirmed=True)),
                              on_cancel=lambda: self.mode_var.set("Paper"))
        else:
            self.engine.call(self.engine.set_mode("paper"))

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
        kind = event.kind
        if kind == "log":
            self.append_log(p["level"], p["message"], p.get("category", ""))
        elif kind == "portfolio":
            self._update_portfolio(p)
        elif kind == "status":
            self._update_status(p)
        elif kind == "models":
            self._update_models(p["models"], p["current"])
        elif kind == "equity_history":
            self.spark.set_points(p["points"])
            if p["points"]:
                ts, value = p["points"][-1]
                self._set_value(value, ts, live=False)
        elif kind == "decision":
            self._add_decision(p)
        elif kind == "decision_result":
            self._update_decision_result(p["decision_id"], p["executed"], p["reason"])
        elif kind == "retrospective":
            report = p["report"]
            for lesson in report.get("new_lessons", []):
                self.append_log("WARNING", f"LIÇÃO: {lesson}", "ollama")
            if not report.get("new_lessons"):
                self.append_log("INFO", "Retrospetiva: sem padrões de falha com suporte suficiente.")
        elif kind == "report":
            for line in p["markdown"].splitlines()[:8]:
                if line.strip():
                    self.append_log("INFO", line, "ollama")
            self._render_gates(p["report"].get("gates"))

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

    # ------------------------------------------------------------- carteira
    def _set_value(self, value: Optional[float], ts: Optional[str], live: bool) -> None:
        if value is None:
            return
        self._last_value, self._last_value_ts = value, ts
        self.lbl_value.configure(text=money(value))
        when = (ts or "")[11:16]
        note = f"em tempo real · {datetime.now().strftime('%H:%M:%S')}" if live else \
            f"último valor conhecido · {(ts or '')[:10]} {when} UTC · sem ligação"
        if not CURRENCY.get("exact", True):
            note += f" · sem taxa para {self.settings.display_currency}: valores na moeda da conta"
        self.lbl_value_note.configure(text=note, text_color=C["muted"] if live else C["amber"])
        self.title(f"{money(value)}  ·  Ollama × IBKR Trader {'[REAL]' if self.settings.is_live else '[Paper]'}")

    def _update_portfolio(self, p: dict[str, Any]) -> None:
        self._connected = bool(p.get("connected"))
        if p.get("display_currency"):
            CURRENCY["code"], CURRENCY["rate"] = p["display_currency"], float(p.get("display_rate") or 1.0)
            CURRENCY["exact"] = bool(p.get("display_exact", True))
        elif p.get("currency"):
            CURRENCY["code"], CURRENCY["rate"] = p["currency"], 1.0
        if self._connected and p.get("net_liq") is not None:
            now_iso = datetime.utcnow().isoformat()
            self._set_value(p["net_liq"], now_iso, live=True)
            self.spark.append(now_iso[:16], p["net_liq"])
        elif self._last_value is not None:
            self._set_value(self._last_value, self._last_value_ts, live=False)
        day = p.get("day_pnl_pct")
        if day is None:
            self.lbl_day.configure(text="", text_color=C["muted"])
        else:
            self.lbl_day.configure(text=f"{'▲' if day >= 0 else '▼'} {day:+.2f}% hoje",
                                   text_color=C["green"] if day >= 0 else C["red"])

        def signed(v: Any, suffix: Optional[str] = None) -> tuple[str, Optional[str]]:
            if v is None:
                return "—", None
            suffix = f" {CURRENCY['code']}" if suffix is None else suffix
            return f"{v * CURRENCY['rate']:+,.2f}{suffix}".replace(",", " "), C["green"] if v >= 0 else C["red"]

        self.c_cash.set(money(p.get("cash")))
        self.c_unreal.set(*signed(p.get("unrealized")))
        realized_text, realized_color = signed(p.get("realized"))
        comm = p.get("commissions_today")
        self.c_real.set(realized_text, realized_color,
                        note=f"comissões hoje: {money(comm)}" if comm is not None else "")
        positions = p.get("positions", [])
        self.c_pos.set(f"{len(positions)} / {self.settings.max_open_positions}",
                       note="máximo configurado")

        existing = {self.tree.set(i, "symbol"): i for i in self.tree.get_children()}
        seen = set()
        for pos in positions:
            covered = pos.get("covered")
            stop_txt = (f"{pos['stop']:.2f}" if pos.get("stop") else "—") + ("" if covered in (None, True) else " ⚠ sem cobertura")
            values = (pos["symbol"], f"{pos['qty']:g}", f"{pos['avg_cost']:.2f}", f"{pos['market_price']:.2f}",
                      money(pos["market_value"], ""), f"{pos['unrealized_pnl']:+,.2f}",
                      stop_txt, f"{pos['tp']:.2f}" if pos.get("tp") else "—")
            tag = "review" if covered is False else ("pos" if pos["unrealized_pnl"] >= 0 else "neg")
            if pos["symbol"] in existing:
                self.tree.item(existing[pos["symbol"]], values=values, tags=(tag,))
            else:
                self.tree.insert("", "end", values=values, tags=(tag,))
            seen.add(pos["symbol"])
        for symbol, item in existing.items():
            if symbol not in seen:
                self.tree.delete(item)

    # --------------------------------------------------------------- estado
    def _update_status(self, p: dict[str, Any]) -> None:
        live = p.get("mode") == "live"
        self.mode_badge.configure(text="CONTA REAL" if live else "PAPER",
                                  fg_color=C["red_dim"] if live else C["green_dim"],
                                  text_color=C["red"] if live else C["green"])
        self.mode_var.set("Real" if live else "Paper")
        accounts = ", ".join(p.get("accounts") or []) or "—"
        self.lbl_mode_note.configure(text=f"{'REAL' if live else 'PAPER'} · porta {p.get('port')} · conta {accounts} · "
                                          f"moeda da conta {p.get('currency', 'USD')} · modelo {p.get('model', '—')}",
                                     text_color=C["red"] if live else C["muted"])
        self.chip_ibkr.set(p["ibkr_connected"], "IBKR: ligado" if p["ibkr_connected"] else "IBKR: desligado")
        self.chip_ollama.set(p["ollama_ok"], f"Ollama: {p['model']}" if p["ollama_ok"] else "Ollama: indisponível")
        self.chip_cycle.set(p["trading_enabled"] or None, "Ciclo: ativo" if p["trading_enabled"] else "Ciclo: parado")
        delayed = p.get("data_delayed")
        self.chip_data.set(None if delayed is None else (not delayed),
                           "Dados: —" if delayed is None else ("Dados: atrasados 15 min" if delayed else "Dados: tempo real"))
        if p["trading_enabled"]:
            self.btn_start.configure(state="disabled")
            self.btn_stop.configure(state="normal")
        else:
            self.btn_start.configure(state="normal")
            self.btn_stop.configure(state="disabled")

        pauses = p.get("pauses") or {}
        prot = ["🛑 KILL-SWITCH DIÁRIO ATIVO: sem novas entradas hoje" if p.get("halted") else "✅ Kill-switch diário: inativo"]
        prot += [f"⏸ {k} em pausa até {v}" for k, v in pauses.items()] or ["✅ Sem pausas de proteção ativas"]
        if p.get("pending_entries"):
            prot.append(f"⏳ {p['pending_entries']} entrada(s) pendente(s) na corretora")
        if p.get("pending_closes"):
            prot.append("⏳ fecho pendente: " + ", ".join(p["pending_closes"]))
        if p.get("ibkr_connected") and not p.get("reconciled", True):
            prot.append("⏳ a reconciliar o estado da corretora (sem decisões até terminar)")
        if p.get("external_positions"):
            prot.append("⚠ posições NÃO abertas pelo bot (não geridas): " + ", ".join(p["external_positions"]))
        if p.get("bound_account"):
            prot.append(f"Base de dados da conta {p['bound_account']} · experiência {p.get('experiment', '—')}")
        if p.get("discrepancies"):
            prot.append("⚠ discrepância posição/saídas em reconciliação (decisões bloqueadas): " + ", ".join(p["discrepancies"]))
        if p.get("entries_paused"):
            prot.append(f"⏸ NOVAS ENTRADAS EM PAUSA: {p.get('entries_paused_reason') or '—'} (retomar na barra lateral)")
        self.btn_resume.configure(state="normal" if p.get("entries_paused") else "disabled")
        rs = p.get("risk_summary") or {}
        prot.append(f"Kill-switch {rs.get('daily_loss', 0):.0%}/dia (para o ciclo) · StoplossGuard {rs.get('stoploss_guard')} stops e "
                    f"cooldown {rs.get('cooldown')} min só em perda · entradas ilimitadas em lucro (regra PDT e fundos disponíveis mandam)")
        self.risk_protections.configure(text="\n".join(prot))

        self._render_gates(p.get("gates_detail"), risk_pct=p.get("risk_pct"))
        calib = (f"{'Platt ajustado' if p.get('calibrated') else 'Heurística (sem ajuste ainda)'} · "
                 f"{p.get('calibration_n', 0)} decisões settled\n"
                 f"Piso de confiança {p['min_confidence']:.2f} · LLM a cada {p.get('llm_interval')} min · "
                 f"{p.get('llm_samples')} amostras\nStop {rs.get('atr_mult')}×ATR · R:R {rs.get('rr')} · "
                 f"risco/trade {p.get('risk_pct', 0):.2%}\nPrompt v{p['prompt_version']} · "
                 f"N experiências {p.get('n_trials', 1)}")
        self.risk_calib.configure(text=calib)
        lessons = p.get("lesson_texts") or []
        self.risk_lessons.configure(text="\n\n".join(f"• {t}" for t in lessons) if lessons else "Ainda sem lições com suporte suficiente.")
        self.lbl_meta.configure(text=f"Prompt v{p['prompt_version']} · {p['lessons']} lições · "
                                     f"risco/trade {p.get('risk_pct', 0):.2%}\nAtivos: {', '.join(p['symbols'])}")
        if self._last_value is not None:
            self.title(f"{money(self._last_value)}  ·  Ollama × IBKR Trader {'[REAL]' if live else '[Paper]'}")

    def _render_gates(self, gates: Optional[dict[str, Any]], risk_pct: Optional[float] = None) -> None:
        if not gates:
            self.risk_gates.configure(text="Sem relatório ainda. Usa «Relatório estatístico» na barra lateral.\n"
                                           "Enquanto os gates não passam, o risco por trade fica no valor de aprendizagem.")
            return
        lines = [f"{'✅' if v else '❌'} {k.replace('_', ' ')}" for k, v in gates.get("checks", {}).items()]
        head = "TODOS PASSAM" if gates.get("all_passed") else "NÃO PASSAM"
        lines.insert(0, f"{head} → risco/trade {gates.get('risk_per_trade', risk_pct or 0):.2%}")
        self.risk_gates.configure(text="\n".join(lines))

    def _update_models(self, models: list[str], current: str) -> None:
        values = models or [current]
        self.model_menu.configure(values=values)
        if current in values:
            self.model_var.set(current)

    # ------------------------------------------------------------- decisões
    def _add_decision(self, p: dict[str, Any]) -> None:
        d, s = p["decision"], p["snapshot"]
        cal = p.get("calibrated")
        action = d["acao"]
        tag = "buy" if action == "BUY" else "sell" if action == "SELL" else "review" if action == "REVIEW" else "muted"
        iid = self.dtree.insert("", 0, values=(
            datetime.now().strftime("%H:%M"), p["symbol"], p.get("model", ""), action, f"{d['confianca']:.2f}",
            f"{p.get('agree_frac', 0) * 100:.0f}%", f"{cal:.2f}" if cal is not None else "—", f"{s['price']:.2f}",
            d["razao"][:120]), tags=(tag,))
        self._decision_rows[int(p["decision_id"])] = iid
        for old in self.dtree.get_children()[300:]:
            self.dtree.delete(old)
        self.lbl_last_decision.configure(
            text=f"Última proposta do LLM: {p['symbol']} → {action} (verbal {d['confianca']:.2f} · acordo "
                 f"{p.get('agree_frac', 0) * 100:.0f}%{' · p=%.2f' % cal if cal is not None else ''}) · {d['razao'][:100]}")

    def _update_decision_result(self, decision_id: int, executed: bool, reason: str) -> None:
        iid = self._decision_rows.get(int(decision_id))
        if not iid or not self.dtree.exists(iid):
            return
        self.dtree.set(iid, "result", ("EXECUTADA · " if executed else "não executada · ") + reason)
