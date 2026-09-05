"""Hub page: How it's built.

A plain-language overview of what Uzumaki is made of -- frontend, backend,
packaging, and the external systems it talks to -- for people who want to
understand the effort and shape of the thing, not its source code. Nothing
here should ever need to change when the code does at a file/function
level; it's kept intentionally conceptual.
"""
import streamlit as st

from _pages.theme import page_header, footer

page_header(
    "🧩", "How it's built",
    "A quick look at what's under the hood — the technologies, the pieces, "
    "and how they fit together. No code, just the shape of it.",
    badges=["Local-first", "Self-updating desktop app", "One codebase, one .exe"],
)

st.markdown('<div class="sa-section">Frontend</div>', unsafe_allow_html=True)
col1, col2 = st.columns(2)
with col1:
    st.markdown(
        """
        <div class="sa-card">
            <div class="sa-card-icon">🖥️</div>
            <div class="sa-card-title">Streamlit</div>
            <div class="sa-card-desc">Every tool's screen — forms, tables, charts, file
            uploads/downloads — is built with Streamlit, a Python framework for turning
            scripts into interactive web apps without writing separate HTML/JS/CSS by hand.</div>
            <div class="sa-card-tag">Python</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
with col2:
    st.markdown(
        """
        <div class="sa-card">
            <div class="sa-card-icon">🪟</div>
            <div class="sa-card-title">Native desktop window</div>
            <div class="sa-card-desc">The .exe opens in its own window — not a browser tab —
            using a lightweight native-window wrapper (pywebview) on top of Windows' built-in
            WebView2 engine, the same rendering engine behind Edge. No separate browser needed.</div>
            <div class="sa-card-tag">pywebview + WebView2</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.markdown('<div class="sa-section">Backend & Processing</div>', unsafe_allow_html=True)
col1, col2, col3 = st.columns(3)
with col1:
    st.markdown(
        """
        <div class="sa-card">
            <div class="sa-card-icon">🐍</div>
            <div class="sa-card-title">Python, in-process</div>
            <div class="sa-card-desc">Most tools run entirely in-process — no separate server
            to start or manage. Upload a file, it's processed on your machine, you download
            the result. Nothing leaves the session.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
with col2:
    st.markdown(
        """
        <div class="sa-card">
            <div class="sa-card-icon">⚡</div>
            <div class="sa-card-title">FastAPI + a real database</div>
            <div class="sa-card-desc">Firm RMS is different from the other tools — it's a
            genuine multi-user system with its own login, roles, and data that persists
            between sessions, backed by a proper API server and database running quietly
            alongside the rest of the app.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
with col3:
    st.markdown(
        """
        <div class="sa-card">
            <div class="sa-card-icon">📊</div>
            <div class="sa-card-title">DuckDB for analytics</div>
            <div class="sa-card-desc">Large-scale journal entry / GL analysis (duplicate
            detection, Benford's Law, exception testing) runs on DuckDB, an embedded
            analytics engine built for exactly this kind of heavy, one-off data crunching.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.markdown('<div class="sa-section">Document & File Handling</div>', unsafe_allow_html=True)
col1, col2 = st.columns(2)
with col1:
    st.markdown(
        """
        <div class="sa-card">
            <div class="sa-card-icon">📄</div>
            <div class="sa-card-title">PDF engines</div>
            <div class="sa-card-desc">PDF merging, splitting, page editing, true redaction,
            and find-and-replace text all run on dedicated PDF libraries that read and rewrite
            the actual document structure — not screenshots or overlays.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
with col2:
    st.markdown(
        """
        <div class="sa-card">
            <div class="sa-card-icon">📁</div>
            <div class="sa-card-title">Office formats + OCR</div>
            <div class="sa-card-desc">Excel/CSV/Parquet conversion, Word document generation,
            and OCR for scanned images/PDFs round out the file-format coverage across the
            different tools.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.markdown('<div class="sa-section">Connectors & Integrations</div>', unsafe_allow_html=True)
col1, col2 = st.columns(2)
with col1:
    st.markdown(
        """
        <div class="sa-card">
            <div class="sa-card-icon">📒</div>
            <div class="sa-card-title">Tally</div>
            <div class="sa-card-desc">The Tally extraction tool can either read a Tally
            export file directly, or connect live to a running TallyPrime instance over
            its own XML/network interface — pulling ledgers, vouchers, and registers without
            a manual export step.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
with col2:
    st.markdown(
        """
        <div class="sa-card">
            <div class="sa-card-icon">🔄</div>
            <div class="sa-card-title">GitHub (self-updates)</div>
            <div class="sa-card-desc">Every code change is automatically built into a fresh
            .exe and published to GitHub. The app checks for a newer build at launch — and
            on demand from the sidebar — downloads it, and restarts itself with the update
            already installed.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.markdown('<div class="sa-section">Packaging & Delivery</div>', unsafe_allow_html=True)
st.markdown(
    """
    <div class="sa-card">
        <div class="sa-card-icon">📦</div>
        <div class="sa-card-title">One codebase, one .exe</div>
        <div class="sa-card-desc">
        Every tool above — despite using different libraries and, in Firm RMS's case, a
        different architecture entirely — is packaged into a single self-contained Windows
        application. There's nothing else to install: no Python, no separate runtime, no
        internet connection required for day-to-day use (only for checking updates). Every
        push to the project automatically triggers a full rebuild and test pass before a new
        version is published.
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown('<div class="sa-section">A note on privacy</div>', unsafe_allow_html=True)
st.markdown(
    """
    <div class="sa-card">
        <div class="sa-card-icon">🔒</div>
        <div class="sa-card-title">Local by default</div>
        <div class="sa-card-desc">With the exception of the Tally live-connect (which talks
        only to Tally on your own machine or network) and the update check (which only
        contacts GitHub to see if a newer version exists), everything runs locally. Your
        files are processed on your machine and never uploaded anywhere.</div>
    </div>
    """,
    unsafe_allow_html=True,
)

footer()
