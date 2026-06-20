from __future__ import annotations

from transoria.workflows.fake_name import FakeNameRoster, FakeNameSession


def test_empty_roster_is_a_noop() -> None:
    roster = FakeNameRoster.empty()

    assert roster.apply("anything 신해범") == "anything 신해범"
    assert roster.restore("anything 신해범") == "anything 신해범"


def test_apply_replaces_each_listed_character() -> None:
    roster = FakeNameRoster(mapping={"龘": "ZAEZ", "靐": "ZBIZ"})

    masked = roster.apply("rare 龘 and rarer 靐 character")

    assert "ZAEZ" in masked and "ZBIZ" in masked
    assert "龘" not in masked


def test_restore_returns_original_after_round_trip() -> None:
    roster = FakeNameRoster(mapping={"龘": "ZAEZ", "靐": "ZBIZ"})
    original = "rare 龘 and rarer 靐 character"

    restored = roster.restore(roster.apply(original))

    assert restored == original


def test_restore_handles_overlapping_placeholders_longest_first() -> None:
    roster = FakeNameRoster(
        mapping={
            "龘": "ZAB",
            "靐": "ZABEXTRA",  # longer; must restore first.
        }
    )

    original = "龘 and 靐"
    restored = roster.restore(roster.apply(original))

    assert restored == original


def test_fake_name_session_auto_masks_kg_code_tokens_and_restores() -> None:
    session = FakeNameSession()
    original = r"code \n[12] appears twice: \n[12]"

    masked = session.apply(original)
    restored, changed = session.restore(masked)

    assert r"\n[12]" not in masked
    assert "蓝霁云" in masked
    assert restored == original
    assert changed


def test_fake_name_session_auto_masks_rare_cjk_and_serializes() -> None:
    session = FakeNameSession()
    original = "rare 龘 character"

    masked = session.apply(original)
    payload = session.to_dict()
    restored, changed = FakeNameSession.from_dict(payload).restore(masked)

    assert "龘" not in masked
    assert restored == original
    assert changed
