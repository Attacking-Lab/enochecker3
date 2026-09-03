from random import Random

from enochecker3 import Enochecker, HavocCheckerTaskMessage


def _task(**overrides) -> HavocCheckerTaskMessage:
    fields = dict(
        task_id=1234,
        address="127.0.0.1",
        team_id=0,
        team_name="team_name",
        current_round_id=0,
        related_round_id=0,
        variant_id=0,
        timeout=15000,
        round_length=60000,
        task_chain_id="task_chain_id",
    )
    fields.update(overrides)
    return HavocCheckerTaskMessage(**fields)


def test_get_random_uses_seed_when_present(checker: Enochecker):
    task = _task(task_id=1234, seed="some-seed")

    assert checker._get_random(task).getstate() == Random("some-seed").getstate()


def test_get_random_without_seed_is_nondeterministic(checker: Enochecker):
    task = _task(task_id=1234)

    first = checker._get_random(task)
    second = checker._get_random(task)

    # No seed: RNG is seeded from system entropy, so it neither repeats across
    # calls nor derives from task_id.
    assert first.getstate() != second.getstate()
    assert first.getstate() != Random(1234).getstate()
