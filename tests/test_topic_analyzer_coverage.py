from types import SimpleNamespace


class SessionLike:
    def __init__(self, **values):
        self.values = values
        for key, value in values.items():
            setattr(self, key, value)

    def get(self, key, default=None):
        return self.values.get(key, default)


def test_analyze_topics_filters_owner_archived_and_dedupes_examples():
    from src.topic_analyzer import analyze_topics

    manager = SimpleNamespace(
        sessions={
            "alpha123": {
                "name": "Alpha",
                "owner": "alice",
                "history": [
                    {"role": "user", "content": "Python code has a bug. Please fix this code."},
                    {"role": "assistant", "content": "We can debug the software issue."},
                    {"role": "user", "content": ""},
                    {"role": "user", "content": None},
                ],
            },
            "beta456": SessionLike(
                name="Beta",
                owner="alice",
                archived=False,
                history=[
                    SimpleNamespace(role="user", content="I need to plan a work project timeline."),
                    SimpleNamespace(role="assistant", content="Let's organize the schedule."),
                ],
            ),
            "archived": {"owner": "alice", "archived": True, "history": [{"role": "user", "content": "music art"}]},
            "other": {"owner": "bob", "history": [{"role": "user", "content": "science research"}]},
            "legacy": {"owner": None, "history": [{"role": "user", "content": "family health"}]},
        }
    )

    result = analyze_topics(manager, owner="alice")
    topics = {entry["topic"]: entry for entry in result["topics"]}

    assert "Science" not in topics
    assert "Personal" not in topics
    assert topics["Technology"]["frequency"] >= 3
    assert topics["Troubleshooting"]["session_count"] == 1
    assert topics["Work"]["examples"][0]["session_name"] == "Beta"
    assert topics["Planning"]["examples"][0]["keyword"] in {"plan", "schedule", "organize", "timeline"}
    assert result["topics"][0]["frequency"] >= result["topics"][-1]["frequency"]


def test_analyze_topics_without_owner_includes_legacy_and_uses_fallback_name():
    from src.topic_analyzer import analyze_topics

    manager = SimpleNamespace(
        sessions={
            "abc123456": {
                "history": [
                    {"role": "user", "content": "Family health and exercise matter."},
                    {"role": "assistant", "content": "A science experiment can help."},
                ]
            }
        }
    )

    result = analyze_topics(manager)
    topics = {entry["topic"]: entry for entry in result["topics"]}
    assert topics["Personal"]["examples"][0]["session_name"] == "Session abc123"
    assert topics["Science"]["frequency"] == 2
    assert result["total_topics"] == len(result["topics"])
