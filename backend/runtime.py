"""Create an isolated graph using existing node factories and durable checkpoints."""
import sqlite3

def create_runtime(checkpoint_path, on_start):
    from graph import build_graph
    from utils.helpers import create_llm
    from agents.manager import create_manager_node, create_assess_node, create_manager_loop_nodes
    from agents.reviewer import create_reviewer_node
    from agents.coach import create_coach_node
    from agents.analyst import create_analyst_node
    from agents.nutrition import create_nutrition_node
    from agents.career import create_career_node
    from agents.document import create_document_node
    from langgraph.checkpoint.sqlite import SqliteSaver
    llm = create_llm()
    manager_node, checkpoint, manager = create_manager_node(llm)
    revision, replan = create_manager_loop_nodes(manager)
    nodes = {'manager': manager_node, 'intent_checkpoint': checkpoint, 'manager_assess': create_assess_node(manager),
        'manager_revision': revision, 'manager_replan': replan, 'reviewer': create_reviewer_node(llm),
        'coach': create_coach_node(llm), 'analyst': create_analyst_node(llm), 'nutrition': create_nutrition_node(llm),
        'career': create_career_node(llm), 'document': create_document_node(llm)}
    def wrap(name, fn):
        def tracked(state):
            on_start(name, state)
            return fn(state)
        tracked._telemetry_agent = getattr(fn, '_telemetry_agent', None)
        return tracked
    connection = sqlite3.connect(str(checkpoint_path), check_same_thread=False)
    saver = SqliteSaver(connection)
    return build_graph({name: wrap(name, fn) for name, fn in nodes.items()}, checkpointer=saver, interrupt_before=['document']), connection
