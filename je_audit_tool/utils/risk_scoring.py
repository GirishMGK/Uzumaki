"""
Risk Scoring — aggregates exception scores per transaction and ranks users.
Uses pandas groupby — avoids DuckDB OOM on large exception datasets.
"""
from __future__ import annotations
import pandas as pd

RISK_SCORES: dict = {
    "Low":      1,
    "Medium":   3,
    "High":     5,
    "Critical": 8,
}

RISK_COLORS: dict = {
    "Critical": "#FF3B3B",
    "High":     "#FF8C00",
    "Medium":   "#F5A623",
    "Low":      "#4CAF50",
}


def calculate_transaction_risk(exceptions_df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate risk scores per transaction (original_index).
    Returns a sorted DataFrame with: original_index, Total_Risk_Score,
    Exception_Count, Exception_Types, Categories_Hit, Risk_Band.
    """
    if exceptions_df.empty:
        return pd.DataFrame(
            columns=["original_index", "Total_Risk_Score",
                     "Exception_Count", "Exception_Types",
                     "Categories_Hit", "Risk_Band"]
        )

    grp = exceptions_df.groupby("original_index", sort=False)

    agg = pd.DataFrame({
        "Total_Risk_Score": grp["Risk Score"].sum(),
        "Exception_Count":  grp["Exception Type"].count(),
        "Exception_Types":  grp["Exception Type"].apply(
            lambda s: " | ".join(sorted(s.unique()))
        ),
        "Categories_Hit":   grp["Exception Category"].nunique(),
    }).reset_index()

    agg.sort_values("Total_Risk_Score", ascending=False, inplace=True)

    agg["Risk_Band"] = pd.cut(
        agg["Total_Risk_Score"],
        bins=[-1, 2, 7, 20, float("inf")],
        labels=["Low", "Medium", "High", "Critical"],
    )

    return agg.reset_index(drop=True)


def rank_users(
    exceptions_df: pd.DataFrame,
    df: pd.DataFrame,
    user_col: str,
) -> pd.DataFrame:
    """
    Rank users by total exception count and risk score.
    Returns DataFrame sorted by Total_Risk_Score descending.
    """
    if exceptions_df.empty or not user_col or user_col not in df.columns:
        return pd.DataFrame()

    # Join user column onto exceptions — this merge is on the small exceptions_df
    exc_with_user = exceptions_df.merge(
        df[[user_col]].reset_index().rename(columns={"index": "original_index"}),
        on="original_index",
        how="left",
    )

    u_grp = exc_with_user.groupby(user_col, sort=False)
    base = pd.DataFrame({
        "Total_Exceptions":    u_grp["Exception Type"].count(),
        "Unique_Transactions": u_grp["original_index"].nunique(),
        "Total_Risk_Score":    u_grp["Risk Score"].sum(),
    }).reset_index()

    # Top_Categories: computed in pandas on the already-small exc_with_user
    top_cats = (
        exc_with_user
        .groupby(user_col)["Exception Category"]
        .apply(lambda x: ", ".join(list(x.value_counts().head(3).index)))
        .reset_index()
        .rename(columns={"Exception Category": "Top_Categories"})
    )
    user_risk = base.merge(top_cats, on=user_col, how="left")

    user_risk["Risk_Band"] = pd.cut(
        user_risk["Total_Risk_Score"],
        bins=[-1, 5, 15, 40, float("inf")],
        labels=["Low", "Medium", "High", "Critical"],
    )

    return user_risk.sort_values("Total_Risk_Score", ascending=False).reset_index(drop=True)
