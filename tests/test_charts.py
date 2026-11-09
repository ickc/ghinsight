"""Tests for chart helpers that carry logic beyond plain plotting."""

from __future__ import annotations

import plotly.graph_objects as go

from ghinsight.charts import SCOPE_LABELS, scope_toggle


def _fig(title: str, n: int) -> go.Figure:
    fig = go.Figure(layout={"title": {"text": title}})
    for i in range(n):
        fig.add_trace(go.Bar(x=["a"], y=[i], name=f"{title}-{i}"))
    return fig


def test_scope_toggle_swaps_trace_visibility():
    fig = scope_toggle(_fig("All", 2), _fig("Private", 3))

    assert [t.visible is not False for t in fig.data] == [True, True, False, False, False]
    (menu,) = fig.layout.updatemenus
    all_button, private_button = menu.buttons
    assert (all_button.label, private_button.label) == SCOPE_LABELS
    assert all_button.args[0]["visible"] == [True, True, False, False, False]
    assert private_button.args[0]["visible"] == [False, False, True, True, True]
    assert private_button.args[1]["title.text"] == "Private · private repositories only"
    assert fig.layout.title.text == "All · all repositories"


def test_scope_toggle_keeps_builder_hidden_traces_hidden():
    all_fig = _fig("All", 2)
    all_fig.data[1].visible = False
    fig = scope_toggle(all_fig, _fig("Private", 1))
    assert fig.layout.updatemenus[0].buttons[0].args[0]["visible"] == [True, False, False]
