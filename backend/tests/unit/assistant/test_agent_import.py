"""Dependency compatibility smoke tests for the assistant graph."""


def test_assistant_graph_imports_with_pinned_langgraph_stack():
    """The resolved LangGraph packages must be mutually import-compatible."""
    from app.infrastructure.assistant.agent import AgentState, assistant_graph

    assert AgentState is not None
    assert assistant_graph is not None
