"""Planner fixes after the 1 Nov pair runs (Horizon full: 1 of 9 benign tasks, λ̂ = ∞ from 0 calibration events).

1. query_ai_assistant(query, Schema) failed for EVERY schema (PydanticInvalidForJsonSchema under pydantic 2.14).
2. Gemma copied the tool stubs into its answer → cut off before the closing ``` / "Function definitions".
3. A repaired program could not define its schema class again after a failed attempt.
"""
from typing import TypeVar

from agentdojo import functions_runtime
from pydantic import BaseModel

from horizon.stage2 import camel_adapter as ca  # noqa: F401  (applies the CaMeL patch)
from horizon.stage2.camel_adapter import HorizonPrivilegedLLM, tidy_plan
from camel import quarantined_llm
from camel.interpreter import interpreter
from camel.interpreter import namespace as ns
from camel.interpreter import result as camel_result
from camel.pipeline_elements.agentdojo_function import make_agentdojo_namespace
from camel.security_policy import Allowed

_T = TypeVar("_T")


class _AllowAll:
    policies: list = []
    no_side_effect_tools: set = set()

    def check_policy(self, *a, **k):
        return Allowed()


def _namespace(fn):
    fn.__doc__ = quarantined_llm.query_quarantined_llm.__doc__
    rt = functions_runtime.FunctionsRuntime()
    rt.register_function(fn)
    base = ns.Namespace.with_builtins()
    return base.add_variables(make_agentdojo_namespace(base, rt, functions_runtime.EmptyEnv()))


def test_query_ai_assistant_accepts_a_schema_class_and_a_plain_type():
    seen = []

    def query_ai_assistant(query: str, output_schema: type[_T]) -> _T:
        seen.append(output_schema)
        if isinstance(output_schema, type) and issubclass(output_schema, BaseModel):
            return output_schema(content="a summary")
        return "text"

    code = ('```python\nclass Summary(BaseModel):\n    content: str\n'
            's = query_ai_assistant("summarise", Summary)\nt = query_ai_assistant("q", str)\nprint(s.content)\n```')
    res, *_ = interpreter.parse_and_interpret_code(code, _namespace(query_ai_assistant), [], (),
                                                   interpreter.EvalArgs(_AllowAll(), interpreter.MetadataEvalMode.STRICT))
    assert isinstance(res, camel_result.Ok), res
    assert seen[0].__name__ == "Summary" and seen[1] is str


def test_tidy_plan_closes_a_cut_off_block_and_drops_copied_stubs_and_imports():
    cut = ('```python\nimport datetime\nfrom typing import Any\n\nclass Car(BaseModel):\n    name: str\n\n'
           'def get_channels() -> list[str]:\n    """Get the list of channels."""\n    ...\n\n\n'
           'def read_inbox(user: str) -> list[Message]:\n    """Read the messages.\n\n    :param user: who\n    """\n'
           '    ...\n\n\nchannels = get_channels()\nprint(channels[0])\n\ndef send_direct_message(recipient: str,')
    out = tidy_plan(cut)
    assert out.startswith("```python\n") and out.rstrip().endswith("```") and out.count("```") == 2
    assert "def " not in out and "import" not in out
    assert "class Car(BaseModel):" in out and "channels = get_channels()" in out


def test_tidy_plan_keeps_real_functions_and_plain_answers():
    real = '```python\ndef helper(x):\n    return x + 1\ny = helper(2)\n```'
    assert "def helper" in tidy_plan(real)                 # CaMeL reports it; not hidden
    assert tidy_plan("no code here") == "no code here"
    ok = 'Here:\n```python\nx = get_current_day()\nprint(x)\n```\nDone.'
    assert tidy_plan(ok) == ok


def test_a_repaired_program_may_define_its_schema_class_again():
    p = object.__new__(HorizonPrivilegedLLM)
    p.state = ca.EpisodeState()
    p.security_policy_engine = lambda env: _AllowAll()
    p.eval_mode = interpreter.MetadataEvalMode.STRICT
    namespace = ns.Namespace.with_builtins()
    p._base_names = set(namespace.variables)
    first = "```python\nclass Item(BaseModel):\n    name: str\nx = 1\nraise ValueError('try again')\n```"
    _, _, err, namespace, deps = p.run_code(first, functions_runtime.EmptyEnv(), namespace, ())
    assert err is not None and "Item" in namespace.variables
    again = "```python\nclass Item(BaseModel):\n    name: str\nprint('ok')\n```"
    _, _, err, namespace, _ = p.run_code(again, functions_runtime.EmptyEnv(), namespace, deps)
    assert err is None, err
