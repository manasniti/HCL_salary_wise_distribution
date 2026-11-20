"""
Plotly chart factory with a single dark "terminal" theme.

Colour rules
* One series → a single accent (electric cyan).
* Categorical (departments) → validated 8-slot palette assigned in a fixed
  order by department *name*, so a department keeps its colour across
  charts and filters. Beyond 7 departments the tail folds into "Other".
* Diverging (correlation) → blue ↔ neutral ↔ orange.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from config import settings

ACCENT = "#22d3ee"
ACCENT_2 = "#3b82f6"
INK = "#e2e8f0"
INK_MUTED = "#94a3b8"
GRID = "rgba(148, 163, 184, 0.12)"
SURFACE = "#0b1426"
OTHER_GREY = "#64748b"

# Validated (CVD + normal-vision + contrast) on the dark surface #0b1426.
CATEGORICAL = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#0ca30c", "#9085e9", "#e66767"]
MAX_CATEGORIES = 7  # 8th slot is reserved; extra departments fold into "Other"

FONT = "Inter, 'Segoe UI', system-ui, sans-serif"


def _cur() -> str:
    return settings.currency_symbol


def money(v: float | None, decimals: int = 0) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{_cur()}{v:,.{decimals}f}"


def compact_money(v: float | None) -> str:
    if v is None or pd.isna(v):
        return "—"
    a = abs(v)
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= div:
            return f"{_cur()}{v / div:,.2f}{suf}"
    return f"{_cur()}{v:,.0f}"


def department_colors(names) -> dict[str, str]:
    """Stable entity→colour map: alphabetical order, palette slots never cycled.

    Departments beyond the palette size render in neutral grey; charts that
    show many departments fold the tail into "Other" before colouring.
    """
    ordered = sorted({str(n) for n in names if str(n) != "Other"})
    colors = {n: (CATEGORICAL[i] if i < len(CATEGORICAL) else OTHER_GREY) for i, n in enumerate(ordered)}
    colors["Other"] = OTHER_GREY
    return colors


def fold_departments(series: pd.Series, weights: pd.Series) -> pd.Series:
    """Keep the largest departments by weight; relabel the rest as "Other"."""
    totals = weights.groupby(series).sum().sort_values(ascending=False)
    if len(totals) <= MAX_CATEGORIES + 1:
        return series
    keep = set(totals.index[:MAX_CATEGORIES])
    return series.where(series.isin(keep), "Other")


def _base(fig: go.Figure, title: str | None = None, height: int = 380, showlegend: bool = False) -> go.Figure:
    fig.update_layout(
        title=dict(text=title, font=dict(size=15, color=INK, family=FONT), x=0, xanchor="left") if title else None,
        height=height,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT, color=INK_MUTED, size=12),
        margin=dict(l=8, r=16, t=48 if title else 16, b=8),
        showlegend=showlegend,
        # Legend below the plot area so it never collides with the title; Plotly
        # grows the bottom margin automatically to fit it.
        legend=dict(orientation="h", yanchor="top", y=-0.2, xanchor="left", x=0,
                    font=dict(color=INK), bgcolor="rgba(0,0,0,0)"),
        hoverlabel=dict(bgcolor="#0f1d36", bordercolor=ACCENT, font=dict(color=INK, family=FONT)),
        bargap=0.35,
        transition=dict(duration=350, easing="cubic-in-out"),
    )
    fig.update_xaxes(gridcolor=GRID, zeroline=False, linecolor=GRID, tickfont=dict(color=INK_MUTED))
    fig.update_yaxes(gridcolor=GRID, zeroline=False, linecolor=GRID, tickfont=dict(color=INK_MUTED))
    return fig


def _fold(summary: pd.DataFrame, value_col: str) -> pd.DataFrame:
    s = summary.sort_values(value_col, ascending=False)
    if len(s) <= MAX_CATEGORIES + 1:
        return s[["department", value_col]]
    head = s.head(MAX_CATEGORIES)[["department", value_col]]
    tail = pd.DataFrame({"department": ["Other"], value_col: [s.iloc[MAX_CATEGORIES:][value_col].sum()]})
    return pd.concat([head, tail], ignore_index=True)


# --------------------------------------------------------------------------
# Department charts
# --------------------------------------------------------------------------
def department_salary_bar(summary: pd.DataFrame) -> go.Figure:
    s = summary.sort_values("total_monthly_salary")
    fig = go.Figure(go.Bar(
        x=s["total_monthly_salary"], y=s["department"], orientation="h",
        marker=dict(color=ACCENT, line=dict(width=0), cornerradius=4),
        text=[compact_money(v) for v in s["total_monthly_salary"]], textposition="outside",
        textfont=dict(color=INK), cliponaxis=False,
        hovertemplate="<b>%{y}</b><br>Monthly payroll: " + _cur() + "%{x:,.0f}<extra></extra>",
    ))
    fig.update_xaxes(title_text=f"Total monthly salary ({_cur()})", range=[0, s["total_monthly_salary"].max() * 1.18])
    return _base(fig, "Monthly payroll by department", height=max(320, 42 * len(s) + 90))


def department_pie(summary: pd.DataFrame) -> go.Figure:
    s = _fold(summary, "total_monthly_salary")
    colors = department_colors(s["department"])
    fig = go.Figure(go.Pie(
        labels=s["department"], values=s["total_monthly_salary"], hole=0.62, sort=False,
        marker=dict(colors=[colors.get(d, OTHER_GREY) for d in s["department"]],
                    line=dict(color=SURFACE, width=2)),
        textinfo="percent", textfont=dict(color="#ffffff"),
        hovertemplate="<b>%{label}</b><br>" + _cur() + "%{value:,.0f}<br>%{percent} of payroll<extra></extra>",
    ))
    total = summary["total_monthly_salary"].sum()
    fig.add_annotation(text=f"<b>{compact_money(total)}</b><br><span style='font-size:11px'>per month</span>",
                       showarrow=False, font=dict(color=INK, size=18))
    fig = _base(fig, "Salary contribution share", height=430, showlegend=True)
    fig.update_layout(legend=dict(orientation="h", yanchor="top", y=-0.04, xanchor="center", x=0.5),
                      margin=dict(l=8, r=8, t=48, b=8))
    return fig


def average_salary_comparison(summary: pd.DataFrame, org_average: float | None) -> go.Figure:
    s = summary.sort_values("average_salary", ascending=False)
    fig = go.Figure(go.Bar(
        x=s["department"], y=s["average_salary"],
        marker=dict(color=ACCENT_2, cornerradius=4),
        hovertemplate="<b>%{x}</b><br>Average: " + _cur() + "%{y:,.0f}<extra></extra>",
    ))
    if org_average:
        fig.add_hline(y=org_average, line=dict(color=ACCENT, width=2, dash="dash"),
                      annotation_text=f"Organisation avg {compact_money(org_average)}",
                      annotation_font=dict(color=INK), annotation_position="top right",
                      annotation_bgcolor="rgba(3,7,15,0.75)")
    fig.update_yaxes(title_text=f"Average monthly salary ({_cur()})")
    return _base(fig, "Average salary vs organisation average")


def employee_count_chart(summary: pd.DataFrame) -> go.Figure:
    s = summary.sort_values("employee_count", ascending=False)
    fig = go.Figure(go.Bar(
        x=s["department"], y=s["employee_count"], marker=dict(color=ACCENT, cornerradius=4),
        text=s["employee_count"], textposition="outside", textfont=dict(color=INK), cliponaxis=False,
        hovertemplate="<b>%{x}</b><br>%{y} employees<extra></extra>",
    ))
    fig.update_yaxes(title_text="Employees", range=[0, s["employee_count"].max() * 1.15])
    return _base(fig, "Headcount by department")


def salary_range_chart(summary: pd.DataFrame) -> go.Figure:
    """Min–max range per department with the average marked."""
    s = summary.sort_values("average_salary")
    fig = go.Figure()
    for _, r in s.iterrows():
        fig.add_trace(go.Scatter(x=[r.minimum_salary, r.maximum_salary], y=[r.department, r.department],
                                 mode="lines", line=dict(color="rgba(34,211,238,0.35)", width=6),
                                 hoverinfo="skip", showlegend=False))
    fig.add_trace(go.Scatter(x=s["minimum_salary"], y=s["department"], mode="markers", name="Minimum",
                             marker=dict(size=10, color=INK_MUTED, line=dict(color=SURFACE, width=2)),
                             hovertemplate="%{y}<br>Min " + _cur() + "%{x:,.0f}<extra></extra>"))
    fig.add_trace(go.Scatter(x=s["average_salary"], y=s["department"], mode="markers", name="Average",
                             marker=dict(size=11, color=ACCENT, symbol="diamond", line=dict(color=SURFACE, width=2)),
                             hovertemplate="%{y}<br>Avg " + _cur() + "%{x:,.0f}<extra></extra>"))
    fig.add_trace(go.Scatter(x=s["maximum_salary"], y=s["department"], mode="markers", name="Maximum",
                             marker=dict(size=10, color=ACCENT_2, line=dict(color=SURFACE, width=2)),
                             hovertemplate="%{y}<br>Max " + _cur() + "%{x:,.0f}<extra></extra>"))
    fig.update_xaxes(title_text=f"Monthly salary ({_cur()})")
    return _base(fig, "Salary range by department", height=max(320, 42 * len(s) + 100), showlegend=True)


# --------------------------------------------------------------------------
# Trends
# --------------------------------------------------------------------------
def payroll_trend(monthly: pd.DataFrame) -> go.Figure:
    fig = go.Figure(go.Scatter(
        x=monthly["pay_period"], y=monthly["total_salary"], mode="lines+markers",
        line=dict(color=ACCENT, width=2, shape="spline"), marker=dict(size=8, color=ACCENT),
        fill="tozeroy", fillcolor="rgba(34,211,238,0.08)",
        customdata=monthly[["paid_employees"]].values,
        hovertemplate="%{x|%b %Y}<br>Payroll " + _cur() + "%{y:,.0f}<br>%{customdata[0]} employees paid<extra></extra>",
    ))
    fig.update_xaxes(dtick="M1", tickformat="%b\n%Y")
    fig.update_yaxes(title_text=f"Total salary ({_cur()})", rangemode="tozero")
    fig.update_layout(hovermode="x unified")
    return _base(fig, "Monthly payroll trend")


def department_trend_chart(trend: pd.DataFrame) -> go.Figure:
    totals = trend.groupby("department")["total_salary"].sum().sort_values(ascending=False)
    keep = set(totals.index[:MAX_CATEGORIES]) if len(totals) > MAX_CATEGORIES + 1 else set(totals.index)
    t = trend.copy()
    t["department"] = t["department"].where(t["department"].isin(keep), "Other")
    t = t.groupby(["pay_period", "department"], as_index=False)["total_salary"].sum()
    colors = department_colors(t["department"])
    fig = go.Figure()
    for dept in [d for d in totals.index if d in keep] + (["Other"] if "Other" in set(t.department) else []):
        d = t[t.department == dept]
        fig.add_trace(go.Scatter(x=d["pay_period"], y=d["total_salary"], name=dept, mode="lines+markers",
                                 line=dict(width=2, color=colors.get(dept, OTHER_GREY)), marker=dict(size=8),
                                 hovertemplate=dept + " · " + _cur() + "%{y:,.0f}<extra></extra>"))
    fig.update_xaxes(dtick="M1", tickformat="%b\n%Y")
    fig.update_yaxes(title_text=f"Monthly salary ({_cur()})", rangemode="tozero")
    fig.update_layout(hovermode="x unified")
    return _base(fig, "Department payroll trend", height=420, showlegend=True)


# --------------------------------------------------------------------------
# Distribution / EDA
# --------------------------------------------------------------------------
def salary_histogram(df: pd.DataFrame, bins: int = 20, mean: float | None = None, median: float | None = None) -> go.Figure:
    fig = go.Figure(go.Histogram(
        x=df["monthly_salary"], nbinsx=bins,
        marker=dict(color=ACCENT, line=dict(color=SURFACE, width=2)), opacity=0.9,
        hovertemplate=_cur() + "%{x}<br>%{y} employees<extra></extra>",
    ))
    if mean is not None:
        fig.add_vline(x=mean, line=dict(color="#f8fafc", width=2, dash="dash"),
                      annotation_text=f"Mean {compact_money(mean)}", annotation_font=dict(color=INK),
                      annotation_position="top right")
    if median is not None:
        fig.add_vline(x=median, line=dict(color=ACCENT_2, width=2, dash="dot"),
                      annotation_text=f"Median {compact_money(median)}", annotation_font=dict(color=INK),
                      annotation_position="top left")
    fig.update_xaxes(title_text=f"Monthly salary ({_cur()})")
    fig.update_yaxes(title_text="Employees")
    return _base(fig, "Salary distribution")


def salary_boxplot(df: pd.DataFrame, by_department: bool = True) -> go.Figure:
    fig = go.Figure()
    if by_department:
        # Departments are named on the x-axis, so one accent colour carries no ambiguity.
        order = df.groupby("department")["monthly_salary"].median().sort_values(ascending=False).index
        for dept in order:
            vals = df.loc[df.department == dept, "monthly_salary"]
            fig.add_trace(go.Box(y=vals, name=dept, boxpoints="outliers", marker=dict(color=ACCENT, size=6),
                                 line=dict(width=2, color=ACCENT), fillcolor="rgba(34,211,238,0.08)",
                                 hovertemplate=dept + "<br>" + _cur() + "%{y:,.0f}<extra></extra>"))
        title = "Salary spread by department"
    else:
        fig.add_trace(go.Box(x=df["monthly_salary"], name="All", boxpoints="outliers", orientation="h",
                             marker=dict(color=ACCENT, size=6), line=dict(color=ACCENT, width=2),
                             boxmean="sd", hovertemplate=_cur() + "%{x:,.0f}<extra></extra>"))
        title = "Organisation-wide salary box plot"
    fig.update_yaxes(title_text=f"Monthly salary ({_cur()})" if by_department else None)
    return _base(fig, title, height=420)


def tenure_scatter(df: pd.DataFrame, label_col: str = "employee_label") -> go.Figure:
    df = df.assign(department=fold_departments(df["department"], df["monthly_salary"]))
    colors = department_colors(df["department"])
    fig = go.Figure()
    for dept, d in df.groupby("department"):
        fig.add_trace(go.Scatter(
            x=d["tenure_years"], y=d["monthly_salary"], mode="markers", name=dept,
            marker=dict(size=9, color=colors[dept], opacity=0.85, line=dict(color=SURFACE, width=1.5)),
            text=d[label_col] if label_col in d else None,
            hovertemplate="%{text}<br>" + dept + "<br>%{x:.1f} yrs · " + _cur() + "%{y:,.0f}<extra></extra>",
        ))
    fig.update_xaxes(title_text="Tenure (years)")
    fig.update_yaxes(title_text=f"Monthly salary ({_cur()})")
    return _base(fig, "Tenure vs salary", height=420, showlegend=True)


def correlation_heatmap(corr: pd.DataFrame) -> go.Figure:
    labels = [c.replace("_", " ").title() for c in corr.columns]
    fig = go.Figure(go.Heatmap(
        z=corr.values, x=labels, y=labels, zmin=-1, zmax=1,
        colorscale=[[0, "#d95926"], [0.5, "#334155"], [1, "#3987e5"]],
        text=[[f"{v:.2f}" for v in row] for row in corr.values], texttemplate="%{text}",
        textfont=dict(color="#ffffff", size=14), hovertemplate="%{x} × %{y}: %{z:.3f}<extra></extra>",
        colorbar=dict(tickfont=dict(color=INK_MUTED), outlinewidth=0),
    ))
    return _base(fig, "Pearson correlation", height=340)


def salary_band_chart(bands: pd.DataFrame) -> go.Figure:
    fig = go.Figure(go.Bar(
        x=bands["band"], y=bands["employees"], marker=dict(color=ACCENT_2, cornerradius=4),
        hovertemplate="%{x}<br>%{y} employees<extra></extra>",
    ))
    fig.update_xaxes(title_text=f"Monthly salary band ({_cur()})")
    fig.update_yaxes(title_text="Employees")
    return _base(fig, "Employees per salary band")


def designation_chart(desig: pd.DataFrame) -> go.Figure:
    d = desig.sort_values("average_salary").tail(15)
    fig = go.Figure(go.Bar(
        x=d["average_salary"], y=d["designation"], orientation="h",
        marker=dict(color=ACCENT, cornerradius=4), customdata=d[["employee_count"]].values,
        hovertemplate="<b>%{y}</b><br>Avg " + _cur() + "%{x:,.0f}<br>%{customdata[0]} employees<extra></extra>",
    ))
    fig.update_xaxes(title_text=f"Average monthly salary ({_cur()})")
    return _base(fig, "Average salary by designation", height=max(300, 34 * len(d) + 90))


def status_chart(df: pd.DataFrame) -> go.Figure:
    c = df["employment_status"].value_counts()
    fig = go.Figure(go.Bar(x=c.index, y=c.values, marker=dict(color=ACCENT_2, cornerradius=4),
                           hovertemplate="%{x}: %{y}<extra></extra>"))
    fig.update_yaxes(title_text="Employees")
    return _base(fig, "Employment status mix", height=320)


PLOTLY_CONFIG = {"displaylogo": False, "modeBarButtonsToRemove": ["lasso2d", "select2d"], "responsive": True}
