import sys
from types import SimpleNamespace

from src.agents.text_utils import build_tavily_cache_spec, make_tavily_cache_key


def _install_fake_litellm(monkeypatch, content: str):
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
    )
    fake = SimpleNamespace(completion=lambda **kwargs: response)
    monkeypatch.setitem(sys.modules, "litellm", fake)


def test_build_tavily_cache_spec_normalizes_model_output(monkeypatch):
    _install_fake_litellm(monkeypatch, """```json
    {
      "cacheable": true,
      "confidence": 0.96,
      "intent": "policy",
      "topic": " โรคพยาธิใบไม้ตับ ",
      "locations": ["ศรีสะเกษ", "ศรีสะเกษ"],
      "years": ["2567", 2567],
      "population": null,
      "qualifiers": ["ควบคุมโรค", "ควบคุมโรค"],
      "latest": false
    }
    ```""")

    spec = build_tavily_cache_spec("ศรีสะเกษมีนโยบายควบคุมพยาธิใบไม้ตับอย่างไร", "key")

    assert spec == {
        "intent": "policy",
        "topic": "โรคพยาธิใบไม้ตับ",
        "locations": ["ศรีสะเกษ"],
        "years": [2567],
        "population": None,
        "qualifiers": ["ควบคุมโรค"],
        "latest": False,
    }


def test_make_tavily_cache_key_is_independent_of_dict_order():
    first = {"intent": "policy", "topic": "โรคพยาธิใบไม้ตับ"}
    second = {"topic": "โรคพยาธิใบไม้ตับ", "intent": "policy"}

    assert make_tavily_cache_key(first) == make_tavily_cache_key(second)


def test_build_tavily_cache_spec_bypasses_low_confidence(monkeypatch):
    _install_fake_litellm(
        monkeypatch,
        '{"cacheable":true,"confidence":0.4,"intent":"other","topic":"ไม่ชัดเจน"}',
    )

    assert build_tavily_cache_spec("ช่วยหาข้อมูลหน่อย", "key") is None


def test_build_tavily_cache_spec_requires_prompt_and_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert build_tavily_cache_spec("", "key") is None
    assert build_tavily_cache_spec("คำถาม", "") is None
