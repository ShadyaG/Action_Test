# qa_dashboard.py
# ─────────────────────────────────────────────────────────────────────────────
# To add a new chart:
#   1. Write a function that takes stats_df and returns a plotly Figure
#   2. Decorate it with @register(section, page_label, icon)
#   3. Done — if multiple functions share a page_label, they group together automatically
# ─────────────────────────────────────────────────────────────────────────────

import re
from datetime import datetime

import geopandas as gpd
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ── Config ────────────────────────────────────────────────────────────────────
THEME = "plotly_white"
DB_MASTER = "ltvt_master"
DB_PROD = "ltvt_prod"
COLOR_MAP = {DB_MASTER: "#729ECC", DB_PROD: "#AB965C"}
DIFF_COLORS = {"added": "#CCE2FF", "removed": "#D7CDB2", "unchanged": "#888780"}
# ERR_COLOR = "#e24b4a"
ERR_COLOR = "#fed519"
WARN_COLOR = "#fed519"

# ── Registry ──────────────────────────────────────────────────────────────────
PAGES = []
def register(section, page_label, icon):
    """Decorator — registers a figure-builder to a specific dashboard page."""

    def decorator(fn):
        PAGES.append({
            "section": section,
            "page": page_label,
            "icon": icon,
            "builder": fn,
        })
        return fn

    return decorator


# ── Generic helpers ───────────────────────────────────────────────────────────
def _symlog(s):
    """Signed log10 that handles zero and negatives."""
    return np.sign(s) * np.log10(np.abs(s) + 1)


def _diff_direction(s):
    return s.apply(lambda x: "added" if x > 0 else ("removed" if x < 0 else "unchanged"))


def _pivot_diff(df, index_col, value_col):
    """
    Pivot master / prod on index_col for value_col.
    Returns DataFrame with columns: index_col, master, prod, diff, diff_symlog, direction.
    """
    pivot = df.pivot_table(
        index=index_col, columns="database", values=value_col, aggfunc="sum"
    ).reset_index()
    pivot.columns.name = None
    master = pivot.get(DB_MASTER, pd.Series(0, index=pivot.index))
    prod = pivot.get(DB_PROD, pd.Series(0, index=pivot.index))
    pivot["diff"] = master - prod  # master − prod
    pivot["diff_symlog"] = _symlog(pivot["diff"])
    pivot["direction"] = _diff_direction(pivot["diff"])

    pct = np.where(prod == 0, np.nan, (pivot["diff"] / prod) * 100)
    pivot["diff_pct"] = pct
    pivot["diff_pct_label"] = np.where(
        prod == 0,
        np.where(master == 0, "0%", "No data in prod"),
        np.char.mod("%+.1f%%", pct)
    )
    return pivot


def _add_scale_toggle(fig):
    """
    Add a stable Linear / Log y-axis toggle to any figure.
    Anchored just above the plot top-left — never drifts on resize.
    """
    fig.update_layout(
        margin=dict(t=80),
        updatemenus=[dict(
            type="buttons", direction="right",
            x=0.0, y=0.99, xanchor="left", yanchor="bottom",
            showactive=True, pad=dict(b=4),
            buttons=[
                dict(label="Log", method="relayout",
                     args=[{"yaxis.type": "log"}]),
                dict(label="Linear", method="relayout",
                     args=[{"yaxis.type": "linear"}]),
            ],
            bgcolor="#f5f5f3", bordercolor="#e5e5e3", font=dict(size=11),
        )],
    )
    return fig


def _dropdown_figure(tables, traces_fn, title, left_subtitle="", right_subtitle=None):
    """
    Build a figure with a per-table dropdown.

    traces_fn(table) -> (left_traces, right_traces) where each is list[go.Bar].
    If right_traces is always empty, a single-panel figure is produced.

    IMPORTANT: traces_fn must return the SAME number of traces for every table.
    Use empty x/y lists rather than skipping a trace when data is absent.
    """
    use_two_panels = right_subtitle is not None
    fig = make_subplots(
        rows=1, cols=2 if use_two_panels else 1,
        horizontal_spacing=0.08,
    )

    all_left, all_right = [], []
    for i, table in enumerate(tables):
        left_traces, right_traces = traces_fn(table)
        show = (i == 0)
        for tr in left_traces:
            tr.visible = show
            fig.add_trace(tr, row=1, col=1)
            all_left.append(tr)
        for tr in right_traces:
            tr.visible = show
            fig.add_trace(tr, row=1, col=2 if use_two_panels else 1)
            all_right.append(tr)

    n_left = len(all_left) // len(tables)
    n_right = len(all_right) // len(tables)
    n_total = n_left + n_right

    buttons = []
    for i, table in enumerate(tables):
        vis = [False] * (len(tables) * n_total)
        for j in range(n_total):
            vis[i * n_total + j] = True
        buttons.append(dict(
            label=table, method="update",
            args=[{"visible": vis},
                  {"title.text": f"{title} - {table}"}],
        ))

    fig.update_layout(
        template=THEME,
        barmode="group",
        title=f"{title} — {tables[0]}",
        legend_title_text="",
        xaxis_tickangle=-45,
        updatemenus=[dict(
            buttons=buttons, direction="down", showactive=True,
            x=1.01, y=1.22, xanchor="left", yanchor="top",
        )],
    )
    if use_two_panels:
        fig.update_layout(xaxis2_tickangle=-45)
    return fig


def _dropdown_grouped_stacked_figure(tables, traces_fn, title,):
    fig = go.Figure()
    all_traces = []

    for i, table in enumerate(tables):
        traces = traces_fn(table)
        for tr in traces:
            tr.visible = (i == 0)
            fig.add_trace(tr)
        all_traces.extend(traces)

    n_traces_per_table = len(all_traces) // len(tables)
    buttons = []

    for i, table in enumerate(tables):
        vis = [False] * len(all_traces)
        start = i * n_traces_per_table
        end = start + n_traces_per_table
        for j in range(start, end):
            vis[j] = True
        buttons.append(
            dict(
                label=table,
                method="update",
                args=[
                    {"visible": vis},
                    {"title": f"{title} — {table}"}
                ],
            )
        )

    fig.update_layout(
        template=THEME,
        barmode="group",
        title=f"{title} — {tables[0]}",
        xaxis_tickangle=-45,
        legend_title_text="",
        updatemenus=[
            dict(
                buttons=buttons,
                direction="down",
                showactive=True,
                x=1.01,
                y=1.22,
                xanchor="left",
                yanchor="top",
            )
        ],
    )
    return fig

def _class_or_subclass_data(class_df, subclass_df, table):
    """Combine subclass-level rows with class-level rows for classes that
    have no subclass breakdown, so no class is dropped entirely."""
    cls = class_df[class_df["table"] == table].copy()
    sub = subclass_df[subclass_df["table"] == table].copy() if not subclass_df.empty else pd.DataFrame()

    if sub.empty:
        cls["x_label"] = cls["class"]
        cls["level"] = "Class"
        return cls.sort_values("x_label")

    sub = sub.copy()
    sub["x_label"] = sub["class"]
    mask = sub["subclass"].notna()
    sub.loc[mask, "x_label"] += " › " + sub.loc[mask, "subclass"]
    sub["level"] = "Subclass"

    classes_with_sub = set(sub["class"].unique())
    leftover = cls[~cls["class"].isin(classes_with_sub)].copy()
    leftover["x_label"] = leftover["class"]
    leftover["level"] = "Class"

    combined = pd.concat([sub, leftover], ignore_index=True)
    return combined.sort_values("x_label")

def _hydrate_subclass_totals(df, api_subclass):
    """api_id_count_subclass lacks total_features — pull it from subclass_feature_count."""
    if api_subclass.empty:
        return api_subclass
    fc_sub = (
        df[df["stat"] == "subclass_feature_count"]
        [["table", "class", "subclass", "database", "feature_count"]]
        .rename(columns={"feature_count": "total_features"})
    )
    api_subclass = api_subclass.drop(columns=["total_features"], errors="ignore")
    api_subclass = api_subclass.merge(
        fc_sub, on=["table", "class", "subclass", "database"], how="left"
    )
    return api_subclass
# ── Comparison · Feature count ────────────────────────────────────────────────

@register("Feature count", "FC · Overview", "ti-chart-bar")
def fc_overview(df):
    """All tables — master vs prod grouped bar, log scale."""
    fc = df[df["stat"] == "table_feature_count"]
    fc = fc.sort_values(by='table')
    fig = px.bar(
        fc, x="table", y="feature_count", color="database",
        barmode="group",
        color_discrete_map=COLOR_MAP, template=THEME,
        labels={"feature_count": "Count", "table": "Table", "database": "Database"},
        log_y=True,
    )
    fig.update_layout(xaxis_tickangle=-45, legend_title_text="", title="Feature Count - Overview")
    _add_scale_toggle(fig)
    return fig


@register("Feature count", "FC · Overview", "ti-chart-bar")
def fc_overview_diff(df):
    """master − prod per table, symlog, color-coded."""
    fc = df[df["stat"] == "table_feature_count"]
    pivot = _pivot_diff(fc, "table", "feature_count")
    fig = px.bar(
        pivot, x="table", y="diff_symlog", color="direction",
        text="diff", template=THEME,
        color_discrete_map=DIFF_COLORS,
        labels={"table": "Table", "diff": "Difference"},
        custom_data=["diff_pct_label"],
    )
    fig.update_xaxes(categoryorder="category ascending")
    fig.add_hline(y=0, line_dash="dash", line_color="#888780", line_width=1)
    fig.update_layout(
        xaxis_tickangle=-45, legend_title_text="", title="Feature Count - Difference (master − prod)",
        yaxis_title="Δ Features (symlog)",
    )
    fig.update_traces(
        texttemplate="%{text:,.0f}",
        hovertemplate=
        "Table: %{x}<br>"
        "Difference: %{text:.3s}<br>"
        "% Change: %{customdata[0]}<br>"
        "<extra></extra>"
    )
    return fig


@register("Feature count", "FC · by Table", "ti-tag")
def fc_by_table(df):
    """Dropdown per table — master vs prod grouped bar per class (subclass if available)."""
    fc_class = df[df["stat"] == "class_feature_count"].copy().sort_values('table')
    fc_subclass = df[df["stat"] == "subclass_feature_count"].copy().sort_values('table')
    tables = sorted(fc_class["table"].unique())

    def traces_fn(table):
        sub = fc_subclass[fc_subclass["table"] == table]
        if not sub.empty:
            sub = sub.copy()
            sub["x_label"] = sub["class"]
            mask = sub["subclass"].notna()
            sub.loc[mask, "x_label"] += " › " + sub.loc[mask, "subclass"]
            x_col, data = "x_label", sub
            level = "Subclass"
        else:
            data = fc_class[fc_class["table"] == table].copy()
            x_col = "class"
            level = "Class"
        data = data.sort_values(by=x_col)
        left = [
            go.Bar(
                x=data[data["database"] == db][x_col].tolist(),
                y=data[data["database"] == db]["feature_count"].tolist(),
                name=db, marker_color=COLOR_MAP[db],
                showlegend=(table == tables[0]),
                hovertemplate=f"Database: {db}<br>{level}: %{{x}}<br>Count: %{{y:s}}<extra></extra>",
            )
            for db in [DB_MASTER, DB_PROD]
        ]
        return left, []

    fig = _dropdown_figure(
        tables, traces_fn,
        title="Feature Count by Table",
        left_subtitle="Master vs Prod",
    )
    fig.update_layout(yaxis_type="log", yaxis_title="Count",xaxis_tickangle=-45)
    fig.update_yaxes(type="log", dtick=1, tickformat="s")
    return fig


@register("Feature count", "FC · by Table", "ti-tag")
def fc_by_class_diff(df):
    """Dropdown per table — master − prod per class (subclass if available), symlog."""
    fc_class = df[df["stat"] == "class_feature_count"].copy().sort_values('table')
    fc_subclass = df[df["stat"] == "subclass_feature_count"].copy().sort_values('table')
    tables = sorted(fc_class["table"].unique())

    def traces_fn(table):
        sub = fc_subclass[fc_subclass["table"] == table]
        if not sub.empty:
            sub = sub.copy()
            sub["x_label"] = sub["class"]
            mask = sub["subclass"].notna()
            sub.loc[mask, "x_label"] += " › " + sub.loc[mask, "subclass"]
            pivot = _pivot_diff(sub, "x_label", "feature_count")
            x_col = "x_label"
            level = "Subclass"
        else:
            data = fc_class[fc_class["table"] == table].copy()
            pivot = _pivot_diff(data, "class", "feature_count")
            x_col = "class"
            level = "Class"
        colors = pivot["direction"].map(DIFF_COLORS)
        left = [go.Bar(
            x=pivot[x_col].tolist(), y=pivot["diff_symlog"].tolist(),
            customdata=pivot[["diff_pct_label"]].values,
            text=pivot["diff"].tolist(), textposition="auto", texttemplate="%{text:,.0f}",
            name="master − prod", marker_color=colors.tolist(),
            showlegend=False,
            hovertemplate=f"{level}: %{{x}}<br>Difference: %{{text:.3s}}<br>% Change: %{{customdata[0]}}<br><extra></extra>",
        )]
        return left, []

    fig = _dropdown_figure(
        tables, traces_fn,
        title="Feature Count - Difference by Table")
    fig.update_layout(yaxis_title="Δ Features (symlog)",xaxis_tickangle=-45)
    return fig


# ── Comparison · API ID coverage ──────────────────────────────────────────────

@register("API ID coverage", "API IDs · Overview", "ti-check")
def api_overview(df):
    api = (
        df[df["stat"] == "api_id_count_class"]
        .groupby(["database", "table"], as_index=False)
        .agg({
            "non_null_api_ids": "sum",
            "total_features": "sum",
        }))
    api["null_api_ids"] = (api["total_features"] - api["non_null_api_ids"])

    fig = go.Figure()
    for db in [DB_MASTER, DB_PROD]:
        subset = api[api["database"] == db]
        # non-null (solid)
        fig.add_bar(
            x=subset["table"],
            y=subset["non_null_api_ids"],
            name=f"{db} - With ID",
            marker_color=COLOR_MAP[db],
            offsetgroup=db,
            customdata=subset["non_null_api_ids"],
            hovertemplate=(
                "Table: %{x}<br>"
                "Series: %{fullData.name}<br>"
                "Count: %{customdata:s}<br>"
                "<extra></extra>"
            )
        )
        # null (hatched)
        fig.add_bar(
            x=subset["table"],
            y=subset["null_api_ids"],
            name=f"{db} - Null ID",
            marker_color=COLOR_MAP[db],
            marker_pattern_shape="/",
            marker_pattern_fgcolor="white",
            offsetgroup=db,
            base=subset["non_null_api_ids"],
            customdata=subset["null_api_ids"],
            hovertemplate=(
                "Table: %{x}<br>"
                "Series: %{fullData.name}<br>"
                "Count: %{customdata:s}<br>"
                "<extra></extra>"
            )
        )

    fig.update_layout(
        barmode="group",
        template=THEME,
        xaxis_tickangle=-45, legend_title_text="",
        title="API IDs - Overview",
        xaxis={"categoryorder": "category ascending"},
        yaxis_type="log",
        yaxis_title="Count",
    )
    _add_scale_toggle(fig)
    return fig


@register("API ID coverage", "API IDs · Overview", "ti-check")
def api_overview_diff(df):
    api = (
        df[df["stat"] == "api_id_count_class"]
        .groupby(["database", "table"], as_index=False)
        .agg({
            "non_null_api_ids": "sum",
            "total_features": "sum",
        }))
    api["null_api_ids"] = (api["total_features"] - api["non_null_api_ids"])

    pivot_nonnull = _pivot_diff(api, "table", "non_null_api_ids")
    pivot_null = _pivot_diff(api, "table", "null_api_ids")
    pivot_nonnull["id_type"] = "With ID"
    pivot_null["id_type"] = "Null ID"

    combined_diffs = pd.concat([pivot_nonnull, pivot_null], ignore_index=True)

    fig = px.bar(
        combined_diffs,
        x="table",
        y="diff_symlog",
        color="direction",
        pattern_shape="id_type",
        pattern_shape_map={
            "With ID": "",
            "Null ID": "/",
        },
        text="diff",
        custom_data=["id_type","diff_pct_label"],
        barmode="group",
        template=THEME,
        color_discrete_map=DIFF_COLORS,
    )

    fig.update_traces(
        texttemplate="%{text:,.0f}",
        textposition="auto",
        marker_pattern_fgcolor="white",
        hovertemplate=
        "Table: %{x}<br>"
        "Metric: %{customdata[0]}<br>"
        "Difference: %{text:s}<br>"
        "% Change: %{customdata[1]}<br>"
        "<extra></extra>"
    )
    fig.update_xaxes(categoryorder="category ascending")
    fig.add_hline(y=0, line_dash="dash", line_color="#888780", line_width=1)
    fig.update_layout(
        xaxis_tickangle=-45,
        legend_title_text="",
        title="API ID Completeness Differences (master − prod)",
        yaxis_title="Δ Features (symlog)",
    )
    return fig


@register("API ID coverage", "API IDs · by Table", "ti-check")
def api_by_table(df):
    api_class = df[df["stat"] == "api_id_count_class"].copy()
    api_class["null_api_ids"] = api_class["total_features"] - api_class["non_null_api_ids"]

    api_subclass = df[df["stat"] == "api_id_count_subclass"].copy()
    api_subclass = _hydrate_subclass_totals(df, api_subclass)
    if not api_subclass.empty:
        api_subclass["null_api_ids"] = (api_subclass["total_features"] - api_subclass["non_null_api_ids"]).fillna(0)

    tables = sorted(api_class["table"].unique())

    def traces_fn(table):
        data = _class_or_subclass_data(api_class, api_subclass, table)
        x_col = "x_label"
        traces = []
        for db in [DB_MASTER, DB_PROD]:
            subset = data[data["database"] == db]
            traces.append(go.Bar(
                x=subset[x_col], y=subset["non_null_api_ids"],
                name=f"{db} - With ID", marker_color=COLOR_MAP[db],
                offsetgroup=db, legendgroup=f"{db}-With-ID",
                customdata=subset["non_null_api_ids"],
                showlegend=(table == tables[0]),
                hovertemplate=f"Database: {db}<br>%{{x}}<br>With ID: %{{customdata:s}}<extra></extra>",
            ))
            traces.append(go.Bar(
                x=subset[x_col], y=subset["null_api_ids"],
                name=f"{db} - Null ID", offsetgroup=db,
                customdata=subset["null_api_ids"],
                base=subset["non_null_api_ids"], legendgroup=f"{db}-Null-ID",
                marker=dict(color=COLOR_MAP[db],
                            pattern=dict(shape="/", fgcolor="white", solidity=0.3)),
                showlegend=(table == tables[0]),
                hovertemplate=f"Database: {db}<br>%{{x}}<br>Null ID: %{{customdata:s}}<extra></extra>",
            ))
        return traces

    fig = _dropdown_grouped_stacked_figure(tables, traces_fn, title="API ID Coverage")
    fig.update_layout(yaxis_type="log", xaxis_tickangle=-45)
    fig.update_yaxes(type="log", dtick=1, tickformat="s")
    return fig

@register("API ID coverage", "API IDs · by Table", "ti-check")
def api_diff_by_table(df):
    api_class = df[df["stat"] == "api_id_count_class"].copy()
    api_class["null_api_ids"] = api_class["total_features"] - api_class["non_null_api_ids"]

    api_subclass = df[df["stat"] == "api_id_count_subclass"].copy()
    api_subclass = _hydrate_subclass_totals(df, api_subclass)
    if not api_subclass.empty:
        api_subclass["null_api_ids"] = (api_subclass["total_features"] - api_subclass["non_null_api_ids"]).fillna(0)

    tables = sorted(api_class["table"].unique())

    def traces_fn(table):
        data = _class_or_subclass_data(api_class, api_subclass, table)
        x_col = "x_label"

        pivot_nonnull = _pivot_diff(data, x_col, "non_null_api_ids")
        pivot_null = _pivot_diff(data, x_col, "null_api_ids")
        pivot_nonnull["id_type"] = "With ID"
        pivot_null["id_type"] = "Null ID"

        combined = pd.concat([pivot_nonnull, pivot_null], ignore_index=True)

        traces = []
        for id_type, pattern in [("With ID", ""), ("Null ID", "/")]:
            d = combined[combined["id_type"] == id_type]
            colors = d["direction"].map(DIFF_COLORS)
            traces.append(go.Bar(
                x=d[x_col].tolist(), y=d["diff_symlog"].tolist(),
                customdata=d[["diff_pct_label"]].values,
                text=d["diff"].tolist(), textposition="auto", texttemplate="%{text:,.0f}",
                name=id_type,
                marker=dict(
                    color=colors.tolist(),
                    pattern=dict(
                        shape=pattern,
                        fgcolor="white",
                    ),
                ),
                showlegend=(table == tables[0]),
                hovertemplate=f"Metric: {id_type}<br>%{{x}}<br>Difference: %{{text:s}}<br>% Change: %{{customdata[0]}}<br><extra></extra>",
            ))
        return traces


    fig = _dropdown_grouped_stacked_figure(tables, traces_fn, title="API ID Difference by Table")
    fig.add_hline(y=0, line_dash="dash", line_color="#888780", line_width=1)
    fig.update_layout(yaxis_title="Δ Features (symlog)", legend_title_text="",
                      barmode="group", xaxis_tickangle=-45)
    return fig


def date_range_dumbbell(df):
    dr = df[df["stat"] == "date_range"].copy()
    dr["date_range_min"] = pd.to_datetime(dr["date_range_min"], errors="coerce")
    dr["date_range_max"] = pd.to_datetime(dr["date_range_max"], errors="coerce")
    dr = dr.dropna(subset=["date_range_min", "date_range_max"])
    # dr.at[291, "date_range_min"] = "2026-03-08 11:07:35.865532"
    # dr.at[291, "date_range_max"] = "2026-03-08 11:07:35.865532"

    if dr.empty:
        fig = go.Figure()
        fig.update_layout(title="Data Lifespan — no data", template=THEME)
        return fig

    # ------------------------------------------------------------------
    # Comparison table
    # ------------------------------------------------------------------
    cmp = dr.pivot(
        index="table",
        columns="database",
        values=["date_range_min", "date_range_max"]
    )
    cmp.columns = [f"{col[0]}_{col[1]}" for col in cmp.columns]
    m_min_col = f"date_range_min_{DB_MASTER}"
    p_min_col = f"date_range_min_{DB_PROD}"
    m_max_col = f"date_range_max_{DB_MASTER}"
    p_max_col = f"date_range_max_{DB_PROD}"
    cmp["diff_days"] = (
            (cmp[m_min_col] - cmp[p_min_col]).abs().dt.days.fillna(0)
            +
            (cmp[m_max_col] - cmp[p_max_col]).abs().dt.days.fillna(0)
    )

    cmp["has_diff"] = cmp["diff_days"] > 0
    tables = cmp.sort_values("table", ascending=False).index.tolist()
    y_offset = {DB_MASTER: 0.18, DB_PROD: -0.18}

    fig = go.Figure()
    # ------------------------------------------------------------------
    # Lifelines
    # ------------------------------------------------------------------
    for db in [DB_MASTER, DB_PROD]:
        sub = dr[dr["database"] == db].set_index("table").reindex(tables)

        for i, table in enumerate(tables):
            if table not in sub.index or pd.isna(sub.loc[table, "date_range_min"]):
                continue

            row = sub.loc[table]
            fig.add_trace(go.Scatter(
                x=[row["date_range_min"], row["date_range_max"]],
                y=[i+0.5 + y_offset[db], i+0.5 + y_offset[db]],
                mode="lines",
                line=dict(color=COLOR_MAP[db], width=2),
                showlegend=False,
                hoverinfo="skip",
            ))

        # Start markers
        fig.add_trace(go.Scatter(
            x=sub["date_range_min"],
            y=[i+0.5 + y_offset[db] for i in range(len(tables))],
            mode="markers",
            marker=dict(color=COLOR_MAP[db], size=9, symbol="circle"),
            name=db,
            legendgroup=db,
            customdata=sub.index,
            hovertemplate=f"Database: {db}<br>Table: %{{customdata}}<br>Start: %{{x|%Y-%m-%d}}<extra></extra>"
        ))

        # End markers
        fig.add_trace(go.Scatter(
            x=sub["date_range_max"],
            y=[i +0.5+ y_offset[db] for i in range(len(tables))],
            mode="markers",
            marker=dict(color=COLOR_MAP[db], size=9, symbol="circle-open", line=dict(width=2)),
            showlegend=False,
            legendgroup=db,
            customdata=sub.index,
            hovertemplate=f"Database: {db}<br>Table: %{{customdata}}<br>End: %{{x|%Y-%m-%d}}<extra></extra>"
        ))
        # ------------------------------------------------------------------
        # Diff overlays
        # ------------------------------------------------------------------
        diff_legend = False

        for i, table in enumerate(tables):
            if not cmp.loc[table, "has_diff"]:
                continue

            m_min = cmp.loc[table, m_min_col]
            p_min = cmp.loc[table, p_min_col]
            m_max = cmp.loc[table, m_max_col]
            p_max = cmp.loc[table, p_max_col]

            if pd.isna(m_min) or pd.isna(p_min) or pd.isna(m_max) or pd.isna(p_max):
                continue

            # 1. Start Date Mismatch segment
            if m_min != p_min:
                start_date = min(m_min, p_min)
                end_date = max(m_min, p_min)
                fig.add_trace(go.Scatter(
                    x=[start_date, end_date],
                    y=[i + 0.5, i + 0.5],
                    mode="lines+markers",
                    line=dict(color=WARN_COLOR, width=2),
                    marker=dict(
                        symbol="arrow",
                        size=14,
                        color=WARN_COLOR,
                        angleref="previous",
                        standoff=0
                    ),
                    name="Mismatch",
                    legendgroup="diff",
                    showlegend=False,
                    hovertemplate=f"<b>{table}</b><br>Start Mismatch: {int(abs((m_min - p_min).days))} days<extra></extra>",
                    opacity=0.6,
                ))
                fig.data = (fig.data[-1],) + fig.data[:-1]
                diff_legend = True

            # 2. End Date Mismatch segment
            if m_max != p_max:
                fig.add_trace(go.Scatter(
                    x=[max(m_max, p_max),min(m_max, p_max)],
                    y=[i+0.5, i+0.5],
                    mode="lines+markers",
                    line=dict(color=WARN_COLOR, width=2),
                    marker=dict(
                        symbol="arrow",
                        size=14,
                        color=WARN_COLOR,
                        angleref="previous",
                        standoff=0
                    ),
                    name="Mismatch",
                    legendgroup="diff",
                    showlegend=not diff_legend,
                    hovertemplate=f"<b>{table}</b><br>End Mismatch: {int(abs((m_max - p_max).days))} days<extra></extra>",
                    opacity=0.6,
                ))
                diff_legend = True
                fig.data = (fig.data[-1],) + fig.data[:-1]
    # ------------------------------------------------------------------
    # labels for affected tables
    # ------------------------------------------------------------------
    ticktext = [
        f"<span style='color:{WARN_COLOR}'><b>{t}</b></span>" if cmp.loc[t, "has_diff"] else t
        for t in tables
    ]

    for i in range(len(tables)):
        if i % 2 == 1:
            fig.add_hrect(
                y0=i+0.5 - 0.5,
                y1=i+0.5 + 0.5,
                fillcolor="rgba(0,0,0,0.025)",
                line=dict(
                    color="rgba(128,128,128,0.2)",
                    width=1,
                ),
                layer="below",
            )
    fig.update_yaxes(
        showgrid=False,
    )
    fig.update_xaxes(
        showgrid=True,
        gridcolor="rgba(128,128,128,0.15)",
    )

    fig.update_layout(
        title="Data Lifespan — Master vs Prod",
        template=THEME,
        legend_title_text="",
        xaxis_title="Date",
        yaxis=dict(
            tickmode="array",
            tickvals=[i + 0.5 for i in range(len(tables))],
            ticktext=ticktext,
            title="Table",
            range=[-0.5, len(tables)+0.5],
        ),
        height=max(125, 35 * len(tables)),
    )

    return fig

# ── Indicators ────────────────────────────────────────────────────────────────
def _indicator_bar(df, stat_name, value_col, title, target, invert=False):
    data = df[df["stat"] == stat_name].copy()
    if data.empty:
        fig = go.Figure()
        fig.update_layout(title=f"{title} — no data", template=THEME)
        return fig
    data = data.sort_values(by='table')

    def _color(row):
        breached = (row[value_col] < target) if invert else (row[value_col] > target)
        return ERR_COLOR if breached else COLOR_MAP.get(row["database"], "#aaa")

    data["color"] = data.apply(_color, axis=1)
    yaxis_title = value_col.replace("_", " ").title()
    fig = go.Figure()
    for db in [DB_MASTER]:
        sub = data[data["database"] == db]
        fig.add_trace(go.Bar(
            x=sub["table"].tolist(), y=sub[value_col].tolist(),
            name=db, marker_color=sub["color"].tolist(),
            text=sub[value_col].tolist(), textposition="auto",
        ))
    fig.update_layout(
        template=THEME, barmode="group",
        title=title, legend_title_text="",
        xaxis_tickangle=-45,
        yaxis_title=yaxis_title,
        yaxis_range=[0, None],
    )
    fig.update_traces(
        hovertemplate=
        "Table: %{x}<br>"
        f"{yaxis_title}: %{{text:s}}<br>"
        "<extra></extra>"
    )
    return fig


def within_bbox(df):
    return _indicator_bar(
        df, "features_within_bounding_box", "within_bbox_ratio",
        title="Features within Bounding Box",
        target=1.0, invert=True,
    )


def map_outside_features(issues_gdf):

    if issues_gdf is None or issues_gdf.empty:
        fig = go.Figure()
        fig.update_layout(title="Features Outside Bounding Box — no data")
        return fig

    issues_gdf = issues_gdf[issues_gdf["database"] == "ltvt_master"].copy()

    if issues_gdf.empty:
        fig = go.Figure()
        fig.update_layout(title="Features Outside Bounding Box — all features inside!")
        return fig

    gdf_4326 = issues_gdf.to_crs(epsg=4326)
    fig = go.Figure()
    all_lons, all_lats = [], []
    center_lon, center_lat = 8.2, 46.8

    for table_name, group in gdf_4326.groupby("table"):
        first_trace = True

        # ------------------------ POLYGONS ------------------------
        polys = group[group.geometry.type.isin(["Polygon", "MultiPolygon"])]
        for geom in polys.geometry:
            geoms = [geom] if geom.geom_type == "Polygon" else geom.geoms
            for poly in geoms:
                lon, lat = poly.exterior.xy
                lon, lat = list(lon), list(lat)
                all_lons.extend(lon)
                all_lats.extend(lat)
                fig.add_trace(go.Scattermapbox(
                    lon=lon,
                    lat=lat,
                    mode="lines",
                    fill="toself",
                    fillcolor=ERR_COLOR,
                    opacity=0.4,
                    line=dict(width=2, color=ERR_COLOR),
                    name=table_name,
                    legendgroup=table_name,
                    showlegend=first_trace,
                    text=f"Table: {table_name}<br>Type: Polygon",
                    hoverinfo="text",
                ))
                first_trace = False

        # ------------------------ LINES ------------------------
        lines = group[group.geometry.type.isin(["LineString", "MultiLineString"])]
        for geom in lines.geometry:
            geoms = [geom] if geom.geom_type == "LineString" else geom.geoms
            for line in geoms:
                lon, lat = line.xy
                lon, lat = list(lon), list(lat)
                all_lons.extend(lon)
                all_lats.extend(lat)
                fig.add_trace(go.Scattermapbox(
                    lon=lon,
                    lat=lat,
                    mode="lines",
                    line=dict(width=4, color=ERR_COLOR),
                    name=table_name,
                    legendgroup=table_name,
                    showlegend=first_trace,
                    text=f"Table: {table_name}<br>Type: Line",
                    hoverinfo="text",
                ))
                first_trace = False

        # ------------------------ POINTS ------------------------
        points = group[group.geometry.type.isin(["Point", "MultiPoint"])]
        for geom in points.geometry:
            geoms = [geom] if geom.geom_type == "Point" else geom.geoms
            for pt in geoms:
                all_lons.append(pt.x)
                all_lats.append(pt.y)
                fig.add_trace(go.Scattermapbox(
                    lon=[pt.x],
                    lat=[pt.y],
                    mode="markers",
                    marker=dict(size=12, color=ERR_COLOR),
                    name=table_name,
                    legendgroup=table_name,
                    showlegend=first_trace,
                    text=f"Table: {table_name}<br>Type: Point",
                    hoverinfo="text",
                ))
                first_trace = False

    fig.update_layout(
        title="Map of Features Outside Bounding Box",
        mapbox=dict(
            style="carto-positron",
            center=dict(lon=center_lon, lat=center_lat),
            zoom=8
        ),
        margin=dict(l=50, r=0, t=50, b=0),
        height=600
    )
    return fig

def null_geometries(df):
    fig = _indicator_bar(
        df, "null_geometries", "null_geometries",
        title="Null Geometries per Table",
        target=0,
    )
    fig.update_layout(yaxis_title="Count")
    return fig


def duplicate_ids(df):
    fig = _indicator_bar(
        df, "duplicate_ids", "duplicate_ids",
        title="Duplicate Feature IDs per Table",
        target=0,
    )
    fig.update_layout(yaxis_title="Count")
    return fig


# ── KPI strip ─────────────────────────────────────────────────────────────────

def _build_kpis(df):
    # ── 1. The Original Top-Level KPIs ───────────────────────────────────────
    fc = df[df["stat"] == "table_feature_count"]
    nulls = df[df["stat"] == "null_geometries"]
    dupes = df[df["stat"] == "duplicate_ids"]

    m_total = int(fc[fc["database"] == DB_MASTER]["feature_count"].sum()) if not fc.empty else 0
    p_total = int(fc[fc["database"] == DB_PROD]["feature_count"].sum()) if not fc.empty else 0
    delta = m_total - p_total
    n_tabs = fc["table"].nunique() if not fc.empty else 0

    null_n = int(nulls[nulls["database"] == DB_MASTER]["null_geometries"].sum()) if not nulls.empty else 0
    dupe_n = int(dupes[dupes["database"] == DB_MASTER]["duplicate_ids"].sum()) if not dupes.empty else 0
    issues = null_n + dupe_n

    delta_col = COLOR_MAP[DB_MASTER] if delta > 0 else (COLOR_MAP[DB_PROD] if delta < 0 else "#5F5E5A")
    issue_col = ERR_COLOR if issues > 0 else "#3B6D11"
    delta_html = (
        f'<span style="color:{delta_col}">{"▲" if delta > 0 else "▼"} '
        f'{delta:+,} vs prod</span>'
    )

    top_row_html = f"""
    <div class="kpi-row">
      <div class="kpi">
        <div class="kpi-label">Tables</div>
        <div class="kpi-value">{n_tabs}</div>
        <div class="kpi-sub">lbm schema</div>
      </div>
      <div class="kpi">
        <div class="kpi-label">Features · master</div>
        <div class="kpi-value">{m_total:,}</div>
        <div class="kpi-sub">{delta_html}</div>
      </div>
      <div class="kpi">
        <div class="kpi-label">Features · prod</div>
        <div class="kpi-value">{p_total:,}</div>
      </div>
      <div class="kpi">
        <div class="kpi-label">Quality issues</div>
        <div class="kpi-value" style="color:{issue_col}">{issues}</div>
        <div class="kpi-sub">{null_n} null geom · {dupe_n} dupe IDs</div>
      </div>
    </div>
    """

    # ── 2. Dynamic Difference Matrix (Heatmap) ───────────────────────────────
    heatmap_html = ""

    if DB_MASTER in df["database"].values and DB_PROD in df["database"].values:
        test = df.copy()

        # Flatten stats into horizontal metrics, removing the 'stat' designator column
        if "stat" in test.columns:
            test = test.drop("stat", axis=1)

        index_cols = ["table"]
        if "class" in test.columns: index_cols.append("class")
        if "subclass" in test.columns: index_cols.append("subclass")

        flat = test.groupby(index_cols + ["database"], dropna=False).first().reset_index()

        # Reject stats that are already in the top-level KPIs
        reject_cols = [
            "duplicate_ids",
            "null_geometries",
            "total_features",
            "features_within_bounding_box"
        ]

        value_cols = [c for c in flat.columns.difference(index_cols + reject_cols + ["database"]) if c in flat.columns]

        if value_cols:
            wide = flat.pivot(
                index=index_cols,
                columns="database",
                values=value_cols
            )

            if DB_MASTER in wide.columns.get_level_values(1) and DB_PROD in wide.columns.get_level_values(1):
                left = wide.xs(DB_MASTER, level="database", axis=1)
                right = wide.xs(DB_PROD, level="database", axis=1)

                # Boolean matrix identifying anomalies
                flags = ~(left.eq(right) | (left.isna() & right.isna()))

                # Rollup to the table level (True if ANY class/subclass failed sync)
                matrix = flags.groupby(level="table").any()

                # Verify if there is AT LEAST ONE difference before building the plot
                if matrix.any().any():
                    hover = pd.DataFrame("", index=matrix.index, columns=matrix.columns)

                    ticktext = [
                        f"<span style='color:{WARN_COLOR}'><b>{t}</b></span>" if any(matrix.loc[t]) else t
                        for t in list(matrix.index)
                    ]

                    if "class" in flags.index.names:
                        for stat in flags.columns:
                            tmp = (
                                flags[flags[stat]]
                                .reset_index()[["table", "class"]]
                                .dropna(subset=["class"])
                                .groupby("table")["class"]
                                .agg(lambda x: "<br> • ".join(sorted(set(map(str, x)))))
                            )

                            for table, txt in tmp.items():
                                hover.loc[table, stat] = "<br><b>Affected Classes:</b><br> • " + txt

                    # Format column headers for nicer dashboard rendering
                    nice_columns = [c.replace("_", " ").title() for c in matrix.columns]
                    matrix.columns = nice_columns
                    hover.columns = nice_columns

                    # Generate the Plotly chart
                    fig = px.imshow(
                        matrix.astype(int),
                        labels=dict(x="", y="", color="Diff"),
                        aspect="auto",
                        color_continuous_scale=[[0, "rgba(0,0,0,0.0)"], [1, WARN_COLOR]]
                    )

                    # Alternate row shading
                    for i in range(len(matrix.index)):
                        if i % 2 == 0:
                            fig.add_hrect(
                                y0=i - 0.5,
                                y1=i + 0.5,
                                fillcolor="rgba(0,0,0,0.025)",  # adjust to suit your theme
                                line=dict(
                                    color="rgba(128,128,128,0.15)",
                                    width=1,
                                ),
                                layer="below",
                            )
                    fig.update_yaxes(showgrid=False)
                    fig.update_xaxes(showgrid=False)
                    for i in range(len(matrix.columns) + 1):
                        fig.add_vline(
                            x=i - 0.5,
                            line_color="rgba(128,128,128,0.15)",
                            line_width=1,
                            layer="below",
                        )

                    fig.update_traces(
                        customdata=hover.values,
                        hovertemplate=(
                            "<b>Table:</b> %{y}<br>"
                            "<b>Stat:</b> %{x}"
                            "%{customdata}<extra></extra>"
                        ),
                        showscale=False,
                        xgap=2,
                        ygap=2
                    )

                    fig.update_layout(
                        template=THEME,
                        margin=dict(l=0, r=0, t=40, b=0),
                        height=max(250, len(matrix) * 35 + 80),  # Dynamically sizes based on table count!
                        title="Stat Discrepancies Heatmap",
                        coloraxis_showscale=False,
                        yaxis=dict(
                            tickmode="array",
                            tickvals=list(range(len(list(matrix.index)))),
                            ticktext=ticktext,
                            title="Table",
                        )
                    )

                    # Export securely as a fully responsive Plotly DIV block
                    heatmap_html = f'<div class="chart-card" style="margin-top: 1rem;">{fig.to_html(full_html=False, include_plotlyjs=False, config={"responsive": True})}</div>'

    if not heatmap_html:
        heatmap_html = """
                <div class="diff-banner diff-banner-ok" style="margin-top:1rem; margin-bottom: 1.25rem">
                  <i class="ti ti-circle-check" aria-hidden="true"></i>
                  <span>No differences detected between master and prod across any stats.</span>
                </div>
                """

    return top_row_html + heatmap_html

# ── HTML writer ───────────────────────────────────────────────────────────────

def _write_html(built_pages, stats_df, output_path, issues_gdf=None):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    sections = {}
    for p in built_pages:
        sec = p["section"]
        if sec not in sections:
            sections[sec] = {}
        page_lbl = p["page"]
        if page_lbl not in sections[sec]:
            key = re.sub(r'[^a-zA-Z0-9]+', '-', page_lbl).strip('-').lower()
            sections[sec][page_lbl] = {"key": key, "icon": p["icon"],
                                       "label": page_lbl, "figs": []}
        sections[sec][page_lbl]["figs"].append(p["fig"])

    nav_html = ""
    for sec_name, pages in sections.items():
        nav_html += f'<div class="nav-section">{sec_name}</div>\n'
        for page_lbl, page_data in pages.items():
            nav_html += (
                f'<div class="nav-item" id="nav-{page_data["key"]}" '
                f'onclick="show(\'{page_data["key"]}\')">'
                f'<i class="ti {page_data["icon"]}" aria-hidden="true"></i> '
                f'{page_data["label"]}</div>\n'
            )

    def _fig_html(fig):
        return fig.to_html(full_html=False, include_plotlyjs=False,
                           config={"responsive": True}, default_width="100%")

    pages_html = ""
    for sec_name, pages in sections.items():
        for page_lbl, page_data in pages.items():
            charts_html = "".join(
                f'<div class="chart-card">' + _fig_html(fig) + '</div>\n'
                for fig in page_data["figs"]
            )
            pages_html += (
                    f'<div class="page" id="page-{page_data["key"]}">'
                    f'<div class="topbar"><h1>{page_data["label"]}</h1>'
                    f'<div class="topbar-meta">{ts}</div></div>'
                    + charts_html + '</div>\n'
            )

    summary_indicators = "".join(
        f'<div class="chart-card">{_fig_html(fig)}</div>\n'
        for fig in [
            date_range_dumbbell(stats_df),
            within_bbox(stats_df),
            map_outside_features(issues_gdf),
            null_geometries(stats_df),
            duplicate_ids(stats_df),
        ]
    )

    summary_page = (
            f'<div class="page active" id="page-summary">'
            f'<div class="topbar"><h1>Summary</h1>'
            f'<div class="topbar-meta">Generated: {ts}</div></div>'
            + _build_kpis(stats_df)
            + summary_indicators
            + '</div>\n'
    )
    pages_html = summary_page + pages_html

    import plotly
    plotly_version = plotly.__version__.split(".")
    try:
        import plotly.offline as _po
        import re as _re
        _js = _po.get_plotlyjs()
        _m = _re.search(r'plotly\.js v(\d+\.\d+\.\d+)', _js[:300])
        js_version = _m.group(1) if _m else "3.5.0"
    except Exception:
        js_version = "3.5.0"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>PostGIS QA — {ts}</title>
<script src="https://cdn.plot.ly/plotly-{js_version}.min.js"></script>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@latest/tabler-icons.min.css">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:system-ui,sans-serif;background:#F5F5F3;min-height:100vh}}
.shell{{display:flex;min-height:100vh}}
.sidebar{{width:220px;background:#fff;border-right:0.5px solid #e5e5e3;padding:1.5rem 0;
         flex-shrink:0;position:sticky;top:0;height:100vh;overflow-y:auto}}
.sidebar-header{{padding:0 1.25rem 1.25rem;border-bottom:0.5px solid #e5e5e3;margin-bottom:.75rem}}
.sidebar-title{{font-size:14px;font-weight:500}}
.sidebar-meta{{font-size:11px;color:#aaa;margin-top:2px}}
.nav-section{{padding:.6rem .75rem .2rem;font-size:10px;color:#bbb;
              text-transform:uppercase;letter-spacing:.06em;margin-top:.25rem}}
.nav-item{{display:flex;align-items:center;gap:8px;padding:6px 1.25rem;font-size:13px;
           color:#555;cursor:pointer;border-left:2px solid transparent;transition:background .1s}}
.nav-item:hover{{background:#f5f5f3;color:#111}}
.nav-item.active{{color:#185FA5;border-left:2px solid #185FA5;background:#EBF3FC}}
.nav-item i{{font-size:15px}}
.main{{flex:1;padding:2rem;min-width:0;overflow:auto;position:relative;}}
.topbar{{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:1.5rem}}
.topbar h1{{font-size:18px;font-weight:500}}
.topbar-meta{{font-size:12px;color:#aaa}}
.diff-banner{{display:flex;align-items:center;gap:8px;padding:1rem 1.25rem;
             background:#EAF3DE;border-radius:12px;color:#27500A;font-size:14px}}
.diff-banner i{{font-size:18px}}
.kpi-row{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:1.5rem}}
.kpi{{background:#efefed;border-radius:8px;padding:1rem}}
.kpi-label{{font-size:12px;color:#888;margin-bottom:4px}}
.kpi-value{{font-size:22px;font-weight:500}}
.kpi-sub{{font-size:11px;color:#aaa;margin-top:2px}}
.chart-card{{background:#fff;border:0.5px solid #e5e5e3;border-radius:12px;
             padding:1.25rem;margin-bottom:1.25rem}}
.chart-card-title{{font-size:14px;font-weight:500}}

/* SVG Pattern Fix: render pages off-screen instead of display:none so SVG
   patterns have a real size to calculate against */
.page {{
  position: absolute !important;
  left: -9999px !important;
  top: -9999px !important;
  width: 100%;
  display: block;
}}
.page.active {{
  position: relative !important;
  left: 0 !important;
  top: 0 !important;
}}
</style>
</head>
<body>
<div class="shell">
  <nav class="sidebar" aria-label="Dashboard navigation">
    <div class="sidebar-header">
      <div class="sidebar-title">PostGIS QA</div>
      <div class="sidebar-meta">lbm schema · master vs prod</div>
    </div>
    <div class="nav-section">Overview</div>
    <div class="nav-item active" id="nav-summary" onclick="show('summary')">
      <i class="ti ti-layout-dashboard" aria-hidden="true"></i> Summary
    </div>
    {nav_html}
  </nav>
  <main class="main">
    {pages_html}
  </main>
</div>
<script>
function show(key) {{
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById('page-' + key).classList.add('active');
  document.getElementById('nav-' + key).classList.add('active');
  window.dispatchEvent(new Event('resize'));
}}
</script>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[OK] Dashboard written → {output_path}")

# ── Public entry point ────────────────────────────────────────────────────────

def build_dashboard(stats_df: pd.DataFrame, issues_gdf=None, output_path: str = "qa_report.html"):
    """Build and write the QA dashboard from a stats DataFrame."""
    built = [{**p, "fig": p["builder"](stats_df)} for p in PAGES]
    for p in built:
        p["fig"].update_layout(height=520)
    _write_html(built, stats_df, output_path, issues_gdf)
