"""
Shared design system for the Uzumaki hub.

Injected once from Home.py — since all pages run inside the same Streamlit
session/DOM, the CSS cascades to every page without each one re-declaring it.
Also exposes small reusable layout helpers (page_header, footer) that the
lighter "launcher" pages use for a consistent look.
"""

from __future__ import annotations

import streamlit as st

NAVY = "#1a1a2e"
NAVY_2 = "#16213e"
BLUE = "#2d6a9f"
TEAL = "#1a9e8f"
PURPLE = "#764ba2"
INDIGO = "#667eea"
INK = "#1e293b"
MUTED = "#64748b"
BORDER = "#e5e9f0"
BG = "#f5f7fb"

_ACCENT_GRADIENT = f"linear-gradient(135deg, {NAVY} 0%, {NAVY_2} 35%, {BLUE} 70%, {TEAL} 100%)"


def inject_css() -> None:
    st.markdown(
        f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

        html, body, [class*="css"] {{ font-family: 'Inter', sans-serif; }}
        .stApp {{ background: {BG}; }}

        /* ── sidebar ── */
        [data-testid="stSidebar"] {{
            background: {_ACCENT_GRADIENT};
        }}
        [data-testid="stSidebar"] * {{ color: #eef1fb !important; }}
        [data-testid="stSidebar"] [data-testid="stPageLink"] p,
        [data-testid="stSidebar"] a {{ font-weight: 500; }}
        [data-testid="stSidebarNav"] ul {{ padding-top: .25rem; }}

        /* ── hero banner (used by Home + page_header) ── */
        .sa-hero {{
            background: {_ACCENT_GRADIENT};
            padding: 2.2rem 2.4rem;
            border-radius: 18px;
            color: white;
            margin-bottom: 1.6rem;
            box-shadow: 0 8px 30px rgba(22,33,62,.25);
        }}
        .sa-hero h1 {{ font-size: 1.9rem; font-weight: 800; margin: 0; letter-spacing: -.02em; }}
        .sa-hero p  {{ font-size: .98rem; opacity: .85; margin: .45rem 0 0; max-width: 62ch; }}
        .sa-hero .sa-badges {{ margin-top: .9rem; }}
        .sa-badge {{
            display: inline-block; background: rgba(255,255,255,.14);
            border: 1px solid rgba(255,255,255,.28); border-radius: 999px;
            padding: .22rem .85rem; font-size: .76rem; margin-right: .4rem;
            font-weight: 500;
        }}

        /* ── section labels ── */
        .sa-section {{
            font-size: .82rem; font-weight: 700; color: {MUTED};
            text-transform: uppercase; letter-spacing: .08em;
            margin: 1.6rem 0 .7rem; display: flex; align-items: center; gap: .5rem;
        }}
        .sa-section::after {{ content: ""; flex: 1; height: 1px; background: {BORDER}; }}

        /* ── tool cards ── */
        .sa-card {{
            background: white; border: 1px solid {BORDER}; border-radius: 16px;
            padding: 1.3rem 1.4rem; margin-bottom: 1rem; height: 100%;
            box-shadow: 0 1px 3px rgba(15,23,42,.04);
            transition: transform .15s ease, box-shadow .15s ease, border-color .15s ease;
        }}
        .sa-card:hover {{
            transform: translateY(-3px);
            box-shadow: 0 12px 28px rgba(15,23,42,.10);
            border-color: #d6ddf0;
        }}
        .sa-card-icon {{
            width: 44px; height: 44px; border-radius: 12px;
            display: flex; align-items: center; justify-content: center;
            font-size: 1.35rem; margin-bottom: .8rem;
            background: linear-gradient(135deg, #eef1ff, #e6f7f5);
        }}
        .sa-card-title {{ font-weight: 700; font-size: 1.02rem; color: {INK}; margin-bottom: .3rem; }}
        .sa-card-desc  {{ color: {MUTED}; font-size: .86rem; line-height: 1.45; }}
        .sa-card-tag {{
            display: inline-block; margin-top: .7rem; font-size: .72rem; font-weight: 600;
            color: {BLUE}; background: #eaf2fb; border-radius: 6px; padding: .15rem .5rem;
        }}

        /* ── KPI / status chips ── */
        .sa-kpi-row {{ display: flex; gap: .7rem; flex-wrap: wrap; margin-bottom: .3rem; }}
        .sa-kpi {{
            background: white; border: 1px solid {BORDER}; border-radius: 12px;
            padding: .75rem 1.1rem; flex: 1; min-width: 140px;
        }}
        .sa-kpi-val {{ font-size: 1.4rem; font-weight: 800; color: {INK}; }}
        .sa-kpi-lbl {{ font-size: .74rem; color: {MUTED}; text-transform: uppercase; letter-spacing: .05em; }}

        .sa-status {{
            display: inline-flex; align-items: center; gap: .4rem;
            font-size: .78rem; font-weight: 600; padding: .25rem .65rem;
            border-radius: 999px; margin: .15rem .3rem .15rem 0;
        }}
        .sa-status.ok   {{ background: #dcfce7; color: #15803d; }}
        .sa-status.warn {{ background: #fef3c7; color: #92400e; }}
        .sa-status .dot {{ width: 7px; height: 7px; border-radius: 50%; background: currentColor; }}

        /* ── footer ── */
        .sa-footer {{
            margin-top: 2.5rem; padding-top: 1.2rem; border-top: 1px solid {BORDER};
            color: {MUTED}; font-size: .8rem; display: flex; justify-content: space-between;
            flex-wrap: wrap; gap: .5rem;
        }}

        div[data-testid="stExpander"] {{
            background: white; border-radius: 12px; border: 1px solid {BORDER};
        }}

        /* ── sidebar expander (e.g. "Check for Updates") ──
        Overrides the white-background rule above: without this, the
        sidebar's forced-light-text rule (`[data-testid="stSidebar"] * {{
        color: #eef1fb !important; }}`) combined with that expander's
        default white background made this content nearly invisible --
        pale text on a near-white box sitting on the dark sidebar. */
        [data-testid="stSidebar"] div[data-testid="stExpander"] {{
            background: rgba(255,255,255,.08);
            border: 1px solid rgba(255,255,255,.18);
        }}
        [data-testid="stSidebar"] div[data-testid="stExpander"] button {{
            background: rgba(255,255,255,.12) !important;
            border: 1px solid rgba(255,255,255,.25) !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def page_header(icon: str, title: str, subtitle: str, badges: list[str] | None = None) -> None:
    """Consistent hero header for lighter/launcher pages."""
    badge_html = "".join(f'<span class="sa-badge">{b}</span>' for b in (badges or []))
    st.markdown(
        f"""
        <div class="sa-hero">
            <h1>{icon} {title}</h1>
            <p>{subtitle}</p>
            <div class="sa-badges">{badge_html}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def footer() -> None:
    st.markdown(
        """
        <div class="sa-footer">
            <div>Uzumaki · Tools Hub</div>
            <div>All processing runs locally — no data leaves this session.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
