# tests/test_mail.py
from nightshift import mail


class FakeRunner:
    """It answers with the JSON that the schema asks for."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def run(self, prompt, **kw):
        self.calls.append((prompt, kw))
        from nightshift.runner import RunResult
        import json
        return RunResult(True, text=json.dumps(self.payload), cost_usd=0.01)


def test_a_message_that_needs_an_answer_becomes_a_needs_you_item():
    fake = FakeRunner({"messages": [
        {"id": "m1", "title": "Shannon: Deck templates",
         "why": "She asks for 3 layouts.", "needs_action": True,
         "source_url": "https://mail.google.com/mail/u/0/#inbox/m1"}]})
    result = mail.triage(fake, cwd="/tmp")
    assert len(result.items) == 1
    assert result.items[0].bucket == "needs_you"
    assert result.items[0].source_url.startswith("https://mail.google.com")
    assert result.cost_usd == 0.01
    assert result.error is None


def test_a_message_with_no_action_goes_to_the_folded_list():
    fake = FakeRunner({"messages": [
        {"id": "m2", "title": "AWS bill", "why": "It is a receipt.",
         "needs_action": False,
         "source_url": "https://mail.google.com/mail/u/0/#inbox/m2"}]})
    assert mail.triage(fake, cwd="/tmp").items[0].bucket == "no_action"


def test_an_item_with_no_source_is_dropped():
    fake = FakeRunner({"messages": [
        {"id": "m3", "title": "x", "why": "y", "needs_action": True,
         "source_url": ""}]})
    assert mail.triage(fake, cwd="/tmp").items == []


def test_a_failed_call_gives_an_error_and_not_an_empty_list():
    """An empty inbox and a broken agent must not look the same."""
    class Failing:
        def run(self, prompt, **kw):
            from nightshift.runner import RunResult
            return RunResult(False, error="401 OAuth access token has expired")

    result = mail.triage(Failing(), cwd="/tmp")
    assert result.items == []
    assert "401" in result.error


def test_the_draft_call_allows_no_send_tool():
    fake = FakeRunner({})
    item = mail.Item("needs_you", "Shannon: deck", "She asks for 3 layouts.",
                     "https://mail.google.com/mail/u/0/#inbox/m1")
    mail.write_draft(fake, item, cwd="/tmp")
    _, kw = fake.calls[0]
    assert "create_draft" in kw["allowed_tools"]
    assert "send" not in kw["allowed_tools"]


def test_the_draft_prompt_carries_the_message():
    fake = FakeRunner({})
    item = mail.Item("needs_you", "Shannon: deck", "She asks for 3 layouts.",
                     "https://mail.google.com/mail/u/0/#inbox/m1")
    mail.write_draft(fake, item, cwd="/tmp")
    prompt, _ = fake.calls[0]
    assert "Shannon: deck" in prompt
    assert "3 layouts" in prompt
