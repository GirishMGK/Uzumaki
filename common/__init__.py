"""Small shared utilities used by more than one tool in the hub.

Kept deliberately tiny — this is not a general "utils" dumping ground, just
the few helpers that would otherwise be copy-pasted (or, worse, imported
from a Streamlit script that runs st.set_page_config() as a side effect of
import) across independent tools.
"""
