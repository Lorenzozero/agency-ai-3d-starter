import json

from hlab import translate as tr

ANTH = {"system": [{"type": "text", "text": "Sei Hermes."}], "max_tokens": 9000, "temperature": 0.2,
        "messages": [{"role": "user", "content": "cerca l'acconto"},
                     {"role": "assistant", "content": [{"type": "text", "text": "cerco"}, {"type": "tool_use", "id": "t1", "name": "memory_search", "input": {"query": "acconto"}}]},
                     {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": "50% alla firma"}]},
                                                  {"type": "text", "text": "riassumi"}]}],
        "tools": [{"name": "memory_search", "description": "cerca", "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}}}],
        "tool_choice": {"type": "any"}}


def test_anthropic_request_becomes_an_openai_chat_request():
    o = tr.to_openai(ANTH, "m", max_tokens=2048)
    roles = [m["role"] for m in o["messages"]]
    assert roles == ["system", "user", "assistant", "tool", "user"]                   # il risultato dello strumento segue subito la chiamata
    assert o["messages"][0]["content"] == "Sei Hermes." and o["messages"][2]["tool_calls"][0]["function"]["name"] == "memory_search"
    assert json.loads(o["messages"][2]["tool_calls"][0]["function"]["arguments"]) == {"query": "acconto"}
    assert o["messages"][3] == {"role": "tool", "tool_call_id": "t1", "content": "50% alla firma"}
    assert o["max_tokens"] == 2048 and o["temperature"] == 0.2 and o["tool_choice"] == "required"
    assert o["tools"][0]["function"]["parameters"]["properties"]["query"]["type"] == "string"
    forced = tr.to_openai({**ANTH, "tool_choice": {"type": "tool", "name": "memory_search"}}, "m")
    assert forced["tool_choice"]["function"]["name"] == "memory_search"


def test_tool_errors_are_marked_and_unsupported_blocks_ignored():
    body = {"messages": [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": "boom", "is_error": True},
                                                      {"type": "image", "source": {}}]}]}
    assert tr.to_openai(body, "m")["messages"] == [{"role": "tool", "tool_call_id": "x", "content": "[errore] boom"}]


def test_openai_response_becomes_an_anthropic_message():
    r = {"id": "abc", "choices": [{"message": {"content": "ok", "tool_calls": [{"id": "c1", "function": {"name": "f", "arguments": "{\"a\": 1}"}},
                                                                             {"id": "c2", "function": {"name": "g", "arguments": "non json"}}]},
                                   "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 7, "completion_tokens": 3}}
    m = tr.from_openai(r, "mod")
    assert m["stop_reason"] == "tool_use" and m["usage"] == {"input_tokens": 7, "output_tokens": 3}
    assert m["content"][0] == {"type": "text", "text": "ok"} and m["content"][1]["input"] == {"a": 1} and m["content"][2]["input"] == {}
    plain = tr.from_openai({"choices": [{"message": {"content": "ciao"}, "finish_reason": "length"}]}, "mod")
    assert plain["stop_reason"] == "max_tokens" and tr.from_openai({"choices": []}, "mod")["content"] == [{"type": "text", "text": ""}]


def test_sse_stream_carries_text_and_tool_input():
    call = {"id": "c1", "function": {"name": "f", "arguments": "{\"q\": \"x\"}"}}
    m = tr.from_openai({"choices": [{"message": {"content": "vado", "tool_calls": [call]}, "finish_reason": "tool_calls"}],
                        "usage": {"prompt_tokens": 5, "completion_tokens": 2}}, "mod")
    events = [(e.split("\n")[0][7:], json.loads(e.split("\n")[1][6:])) for e in tr.to_sse(m).decode().strip().split("\n\n")]
    assert [n for n, _ in events] == ["message_start", "content_block_start", "content_block_delta", "content_block_stop",
                                      "content_block_start", "content_block_delta", "content_block_stop", "message_delta", "message_stop"]
    assert events[2][1]["delta"] == {"type": "text_delta", "text": "vado"}
    assert json.loads(events[5][1]["delta"]["partial_json"]) == {"q": "x"} and events[7][1]["delta"]["stop_reason"] == "tool_use"
    assert tr.estimate_tokens({"a": "x" * 400}) >= 100


def test_assistant_tool_call_messages_never_carry_null_content():
    from hlab.translate import to_openai
    body = {"messages": [{"role": "user", "content": "elenca"}, {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "x", "input": {}}]},
                         {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "[]"}]}]}
    msgs = to_openai(body, "m")["messages"]
    assert msgs[1]["content"] == "" and msgs[1]["tool_calls"][0]["id"] == "t1"            # Cloudflare rifiuta content null


def test_provider_specific_fields_are_added_to_every_tool_call():
    from hlab.translate import to_openai
    body = {"messages": [{"role": "user", "content": "x"}, {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "f", "input": {}}]}]}
    extra = {"extra_content": {"google": {"thought_signature": "skip_thought_signature_validator"}}}
    call = to_openai(body, "m", None, extra)["messages"][1]["tool_calls"][0]
    assert call["extra_content"]["google"]["thought_signature"] == "skip_thought_signature_validator" and call["id"] == "t1"
    assert "extra_content" not in to_openai(body, "m")["messages"][1]["tool_calls"][0]                  # gli altri fornitori non cambiano
